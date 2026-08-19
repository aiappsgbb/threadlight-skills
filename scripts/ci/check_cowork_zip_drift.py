#!/usr/bin/env python3
"""Fail CI when a committed Cowork archive drifted from its source.

The seller-installable bundles under ``docs/downloads/`` are BUILT artifacts
(``scripts/build-cowork-zips.sh``) that must be regenerated whenever the source
they package changes. Nothing otherwise forces that: an author can edit
``skills/threadlight-design/references/speckit-template.md`` or the shared cost
engine and forget to rebuild, shipping a stale bundle to every Cowork seller.

This guard rebuilds the archives into a throwaway directory and compares the
result against the committed ``docs/downloads/`` archives by **content**, not
raw bytes:

  * each archive member is keyed by name + CRC-32 of its UNCOMPRESSED data;
  * ``vendor/cost-runtime.zip`` is recursed into, so a change to the inner cost
    engine is detected even though it is a single member of the outer archive.

A content signature is robust to the two things that legitimately churn zip
bytes without changing what ships: per-member timestamps and the platform's
``zip`` implementation (macOS Apple zip vs. Linux Info-ZIP compress the same
bytes differently). The build itself is made byte-reproducible on a single
machine (fixed mtimes, sorted order, ``-X``) so "rebuild twice" is a no-op; this
check adds the cross-platform-safe gate on top.

Exit 0 when every committed archive matches a fresh build, 1 otherwise.
"""
from __future__ import annotations

import io
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILDER = REPO_ROOT / "scripts" / "build-cowork-zips.sh"
DOWNLOADS = REPO_ROOT / "docs" / "downloads"
DRIFT_OUT = REPO_ROOT / ".cowork-drift-tmp"

# The archives the builder publishes; each must stay in sync with source.
ARCHIVES = ("threadlight-design.zip", "threadlight-qualify.zip")


def zip_content_signature(data: bytes) -> dict:
    """Return a {member_name: crc-or-nested-signature} content signature.

    Directory entries are ignored (parent dirs are implied on extraction).
    A member whose name ends in ``.zip`` is recursed into, so the signature
    captures the inner archive's members rather than the platform-dependent raw
    bytes of the compressed container.
    """
    sig: dict = {}
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            if info.filename.lower().endswith(".zip"):
                sig[info.filename] = zip_content_signature(zf.read(info.filename))
            else:
                sig[info.filename] = info.CRC
    return sig


def flatten_signature(sig: dict, prefix: str = "") -> dict:
    """Flatten a (possibly nested) signature to ``path -> crc`` for diffing."""
    out: dict = {}
    for name, value in sig.items():
        if isinstance(value, dict):
            out.update(flatten_signature(value, prefix + name + "::"))
        else:
            out[prefix + name] = value
    return out


def diff_signatures(committed: dict, fresh: dict) -> list[str]:
    """Human-readable member-level differences between two content signatures."""
    committed_flat = flatten_signature(committed)
    fresh_flat = flatten_signature(fresh)
    problems: list[str] = []
    for name in sorted(set(committed_flat) - set(fresh_flat)):
        problems.append(f"    - committed has extra member (source no longer builds it): {name}")
    for name in sorted(set(fresh_flat) - set(committed_flat)):
        problems.append(f"    - source now builds a member missing from the committed archive: {name}")
    for name in sorted(set(committed_flat) & set(fresh_flat)):
        if committed_flat[name] != fresh_flat[name]:
            problems.append(f"    - member content changed since last build: {name}")
    return problems


def _git_show(repo: Path, rel_path: str) -> bytes | None:
    proc = subprocess.run(
        ["git", "-C", str(repo), "show", f"HEAD:{rel_path}"],
        capture_output=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def build_fresh(out_dir: Path) -> None:
    """Rebuild every archive into ``out_dir`` (never the committed downloads)."""
    if DRIFT_OUT.exists():
        shutil.rmtree(DRIFT_OUT)
    out_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["bash", str(BUILDER)],
        cwd=str(REPO_ROOT),
        env={**_env(), "COWORK_OUT_DIR": str(out_dir)},
        check=True,
        capture_output=True,
    )


def _env() -> dict:
    import os

    return dict(os.environ)


def main() -> int:
    if not BUILDER.is_file():
        print(f"ERROR: {BUILDER.relative_to(REPO_ROOT)} not found", file=sys.stderr)
        return 1

    try:
        build_fresh(DRIFT_OUT)

        drifted = False
        for name in ARCHIVES:
            fresh_path = DRIFT_OUT / name
            if not fresh_path.is_file():
                print(f"ERROR: builder did not produce {name}", file=sys.stderr)
                drifted = True
                continue

            committed_bytes = _git_show(REPO_ROOT, f"docs/downloads/{name}")
            if committed_bytes is None:
                print(
                    f"ERROR: docs/downloads/{name} is not committed at HEAD — "
                    "commit the built archive.",
                    file=sys.stderr,
                )
                drifted = True
                continue

            fresh_sig = zip_content_signature(fresh_path.read_bytes())
            committed_sig = zip_content_signature(committed_bytes)
            if fresh_sig != committed_sig:
                drifted = True
                print(
                    f"ERROR: docs/downloads/{name} is stale — its content no longer "
                    "matches a fresh build from source:",
                    file=sys.stderr,
                )
                for line in diff_signatures(committed_sig, fresh_sig):
                    print(line, file=sys.stderr)
            else:
                print(f"OK: docs/downloads/{name} matches a fresh build ({len(flatten_signature(fresh_sig))} members)")
    finally:
        if DRIFT_OUT.exists():
            shutil.rmtree(DRIFT_OUT)

    if drifted:
        print(
            "\nRegenerate and commit the archives:\n"
            "    bash scripts/build-cowork-zips.sh && git add docs/downloads/",
            file=sys.stderr,
        )
        return 1
    print("OK: all committed Cowork archives are in sync with source.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
