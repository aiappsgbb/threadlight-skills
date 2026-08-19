"""
Dated local model catalog (`threadlight.model-catalog/v1`).

Replaces the old hard-coded ``MODEL_SWAPS`` table in ``projectors/aoai.py`` with
a dated, source-bearing JSON catalog (``references/model-catalog.json``). The
loader:

  * parses ``checked_at`` (a plain ``YYYY-MM-DD`` date) and exposes a staleness
    check — a catalog older than :data:`STALE_AFTER_DAYS` (90) days is stale;
  * preserves ``null`` rates as ``None`` (a missing rate is *never* silently
    turned into ``0.0`` — a projector that reads ``None`` must emit
    ``not-priceable``, never a free line);
  * exposes model comparisons **only within the same ``comparison_group``** —
    an absent comparison group (or a singleton group) yields no alternatives,
    so we never recommend a model swap the catalog can't justify.

Stdlib only; no network. The catalog ships in-repo and is vendored into the
Cowork runtime zip so the same code path works in both execution contexts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
import json

STALE_AFTER_DAYS = 90
SCHEMA_ID = "threadlight.model-catalog/v1"

_DEFAULT_CATALOG_PATH = (
    Path(__file__).resolve().parent.parent / "references" / "model-catalog.json"
)


class ModelCatalogError(ValueError):
    """Raised when the model catalog is malformed (bad schema, unparseable date)."""


def _num_or_none(value: Any) -> float | None:
    """Coerce to float, but keep ``None`` as ``None`` — never 0.0.

    A missing price must stay missing so downstream projectors surface
    ``not-priceable`` instead of billing a free line.
    """
    if value is None:
        return None
    if isinstance(value, bool):  # guard: bool is an int subclass
        raise ModelCatalogError(f"expected a number, got bool {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    raise ModelCatalogError(f"expected a number or null, got {value!r}")


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ModelCatalogError(f"expected an int, got bool {value!r}")
    if isinstance(value, (int, float)):
        return int(value)
    raise ModelCatalogError(f"expected an int or null, got {value!r}")


@dataclass(frozen=True)
class ModelEntry:
    id: str
    family: str | None
    comparison_group: str | None
    input_per_1k_usd: float | None
    output_per_1k_usd: float | None
    cached_input_per_1k_usd: float | None
    batch_discount: float | None
    throughput_tokens_per_min: int | None
    price_source: str | None
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ModelEntry":
        model_id = data.get("id")
        if not isinstance(model_id, str) or not model_id:
            raise ModelCatalogError(f"model entry missing 'id': {data!r}")
        return cls(
            id=model_id,
            family=data.get("family"),
            comparison_group=data.get("comparison_group"),
            input_per_1k_usd=_num_or_none(data.get("input_per_1k_usd")),
            output_per_1k_usd=_num_or_none(data.get("output_per_1k_usd")),
            cached_input_per_1k_usd=_num_or_none(data.get("cached_input_per_1k_usd")),
            batch_discount=_num_or_none(data.get("batch_discount")),
            throughput_tokens_per_min=_int_or_none(data.get("throughput_tokens_per_min")),
            price_source=data.get("price_source"),
            raw=dict(data),
        )


@dataclass
class ModelCatalog:
    schema: str
    checked_at: date
    source: str | None
    models: list[ModelEntry]

    def _by_id(self) -> dict[str, ModelEntry]:
        return {m.id: m for m in self.models}

    def get(self, model_id: str) -> ModelEntry | None:
        return self._by_id().get(model_id)

    def age_days(self, as_of: date | None = None) -> int:
        ref = as_of or date.today()
        return (ref - self.checked_at).days

    def is_stale(self, as_of: date | None = None, max_age_days: int = STALE_AFTER_DAYS) -> bool:
        return self.age_days(as_of) > max_age_days

    def comparisons(self, model_id: str) -> list[ModelEntry]:
        """Return same-``comparison_group`` peers of ``model_id`` (excluding self).

        An unknown model, a model with no ``comparison_group``, or a singleton
        group yields ``[]`` — the caller then makes no swap recommendation.
        """
        base = self.get(model_id)
        if base is None or not base.comparison_group:
            return []
        peers = [
            m
            for m in self.models
            if m.id != base.id and m.comparison_group == base.comparison_group
        ]
        return sorted(peers, key=lambda m: m.id)


def load_model_catalog(path: str | Path | None = None) -> ModelCatalog:
    """Load and validate the dated model catalog.

    ``path`` defaults to the in-repo ``references/model-catalog.json``. In the
    Cowork runtime the caller passes the vendored ``vendor/model-catalog.json``.
    """
    catalog_path = Path(path) if path is not None else _DEFAULT_CATALOG_PATH
    if not catalog_path.exists():
        raise ModelCatalogError(f"model catalog not found: {catalog_path}")
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ModelCatalogError(f"model catalog unreadable: {exc}") from exc

    schema = data.get("schema")
    if schema != SCHEMA_ID:
        raise ModelCatalogError(
            f"unexpected catalog schema {schema!r}; expected {SCHEMA_ID!r}"
        )

    checked_at_raw = data.get("checked_at")
    if not isinstance(checked_at_raw, str) or not checked_at_raw:
        raise ModelCatalogError("model catalog missing 'checked_at' date")
    try:
        checked_at = datetime.strptime(checked_at_raw, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ModelCatalogError(
            f"model catalog 'checked_at' must be YYYY-MM-DD, got {checked_at_raw!r}"
        ) from exc

    models_raw = data.get("models")
    if not isinstance(models_raw, list):
        raise ModelCatalogError("model catalog 'models' must be a list")
    models = [ModelEntry.from_dict(m) for m in models_raw]

    return ModelCatalog(
        schema=schema,
        checked_at=checked_at,
        source=data.get("source"),
        models=models,
    )
