"""Consumer-test fixtures only: synthetic native evidence, never deployment proof."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace


def create_agentops_fixture(repo: Path, *, state="healthy", now=None, redteam=True) -> dict:
    """Populate an empty caller-owned directory and emit a loadable manifest.

    States: healthy, partial, blocked, quality-fail, stale. Healthy includes real
    synthetic per-category/per-strategy red-team counts unless redteam=False.
    The caller owns cleanup. Existing nonempty directories are never changed.
    """
    if state not in {"healthy", "partial", "blocked", "quality-fail", "stale"}:
        raise ValueError("Unknown synthetic fixture state")
    repo = Path(repo)
    if repo.is_symlink() or (repo.exists() and (not repo.is_dir() or any(repo.iterdir()))):
        raise ValueError("Fixture destination must be an empty directory")
    repo.mkdir(parents=True, exist_ok=True)
    repo = repo.resolve()
    now = now or datetime.now(timezone.utc)
    spec = importlib.util.spec_from_file_location("threadlight_agentops_fixture_source",
        Path(__file__).with_name("test_agentops_check.py"))
    source = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source)
    fixture = source.AgentOpsTests(methodName="runTest")
    fixture.repo = repo
    fixture.git("init", "-q")
    fixture.git("config", "user.email", "fixture@example.invalid")
    fixture.git("config", "user.name", "Offline fixture")
    if state == "partial":
        fixture.optin()
        fixture.git("add", ".")
        fixture.git("commit", "-qm", "Synthetic unbound opt-in")
    else:
        fixture.fixture(quality=state not in {"blocked", "quality-fail"},
                        doctor_blocked=state == "blocked",
                        age_hours=48 if state == "stale" else 1,
                        redteam=redteam, now=now)
    manifest = source.check.assess(repo, now=now)
    path = repo / "specs/agentops-manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n")
    source.contract.load_manifest(repo, now=now)
    return manifest


@contextmanager
def native_observer_fixture():
    """Yield a real bounded synthetic runner and scoped owner-approval helper.

    Only installed-package/executable discovery is simulated. The observer,
    subprocess, receipt writer, validation and normalization are real. The
    context owns its synthetic repository and restores all patches on exit.
    """
    spec = importlib.util.spec_from_file_location("threadlight_reusable_runtime_fixture",
        Path(__file__).with_name("test_runtime_integration.py"))
    source = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source)
    case = source.RuntimeIntegrationTests(methodName="runTest")
    try:
        case.setUp()
        yield SimpleNamespace(
            repo=case.repo, observer=case.observer, runtime=case.runtime,
            run_command=case.observer.contract.bounded_command, approve=case.approve)
    finally:
        case.doCleanups()


__all__ = ["create_agentops_fixture", "native_observer_fixture"]
