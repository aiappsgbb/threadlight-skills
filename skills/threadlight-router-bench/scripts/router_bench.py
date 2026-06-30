#!/usr/bin/env python3
"""threadlight-router-bench — CLI dispatcher.

Two independent modes:

  learn  <run_id>              PRIMARY. Single-run self-improvement cold-path.
                               Harvests one CI run, classifies anomalies into a
                               grounded learnings digest. NO baseline required —
                               works on ANY run, green or red.

  bench  <candidate> <base>    OPTIONAL. Paired cost/efficiency scorecard of a
                               model-router run vs a baseline-model run, using
                               Azure Monitor token metrics. (built in score/*)

Repo convention: flat scripts/, stdlib-only, sibling imports via sys.path.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import harvest  # noqa: E402
import findings as findings_mod  # noqa: E402
import report  # noqa: E402

Runner = Callable[[list[str]], str]


def run_learn(run_id: int, repo: str = "aiappsgbb/threadlight-skills",
              outdir: Path | None = None, model_deployment: str = "",
              with_legs: bool = False, runner: Runner | None = None) -> dict[str, Any]:
    """Harvest one run and emit a grounded learnings digest (JSON + Markdown)."""
    meta = harvest.fetch_view(run_id, repo=repo, runner=runner)
    conclusion = meta.get("conclusion") or "unknown"
    branch = meta.get("headBranch") or ""
    title = meta.get("displayTitle") or ""
    window = {"start": meta.get("startedAt") or "", "end": meta.get("updatedAt") or ""}

    jobs = harvest.fetch_jobs(run_id, repo=repo, runner=runner)
    phase_parity = harvest.parse_phase_parity(jobs)

    log_body = harvest.fetch_logs(run_id, conclusion=conclusion, repo=repo, runner=runner)
    lines = [ln for ln in log_body.splitlines() if ln.strip()]
    # PRECISION: failures use `--log-failed` (failing steps only) → full taxonomy is safe.
    # Success runs only have the noisy full `--log`, so restrict to low-sev warning
    # categories — a naive full-taxonomy scan of a green run is ~all false positives.
    if conclusion == "failure":
        phase_hint, allow = "failed-steps", None
    else:
        phase_hint, allow = "all-steps", {"retry", "slow_turn", "router_fallback"}
    findings = findings_mod.scan_lines(lines, run_id=run_id, phase=phase_hint,
                                       source=f"run-{run_id}", allow=allow)

    legs: dict[str, Any] = {"govern": {}, "evals": {}, "redteam": {}}
    if with_legs:
        bundle = harvest.download_run(run_id, Path(outdir or ".") / f"bundle-{run_id}",
                                      repo=repo, runner=runner)
        specs = harvest.find_specs_dir(bundle)
        if specs is not None:
            legs = harvest.load_leg_manifests(specs)

    digest = report.build_digest(
        run_id=run_id, conclusion=conclusion, branch=branch, title=title,
        window=window, phase_parity=phase_parity, legs=legs, findings=findings,
        model_deployment=model_deployment)

    if outdir is not None:
        out = Path(outdir)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"learnings-{run_id}.json").write_text(
            json.dumps(digest, indent=2), encoding="utf-8")
        (out / f"learnings-{run_id}.md").write_text(
            report.render_markdown(digest), encoding="utf-8")
    return digest


def _cmd_learn(args: argparse.Namespace, runner: Runner | None) -> int:
    digest = run_learn(args.run_id, repo=args.repo, outdir=Path(args.out),
                       model_deployment=args.deployment, with_legs=args.with_legs,
                       runner=runner)
    print(report.render_markdown(digest))
    s = digest["summary"]
    print(f"\n[router-bench] wrote learnings-{args.run_id}.(json|md) to {args.out} "
          f"— {s['total']} findings (high={s['high']}).", file=sys.stderr)
    return 0


def _cmd_bench(args: argparse.Namespace, runner: Runner | None) -> int:
    # Paired scorecard lives in score.py; dispatched here once built.
    try:
        import score  # noqa: F401
    except ImportError:
        print("[router-bench] bench mode not yet available in this build.", file=sys.stderr)
        return 1
    return score.run_bench(args, runner=runner)  # pragma: no cover


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="router-bench", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    learn = sub.add_parser("learn", help="single-run learnings digest (primary)")
    learn.add_argument("run_id", type=int, help="GitHub Actions run id")
    learn.add_argument("--repo", default="aiappsgbb/threadlight-skills")
    learn.add_argument("--out", default="router-bench-out", help="output dir")
    learn.add_argument("--deployment", default="", help="model deployment name (label only)")
    learn.add_argument("--with-legs", action="store_true",
                       help="download artifact bundle to attach Phase-5 KPI legs")
    learn.set_defaults(func=_cmd_learn)

    bench = sub.add_parser("bench", help="paired cost/efficiency scorecard (optional)")
    bench.add_argument("candidate", type=int, help="model-router run id")
    bench.add_argument("baseline", type=int, help="baseline-model run id")
    bench.add_argument("--repo", default="aiappsgbb/threadlight-skills")
    bench.add_argument("--out", default="router-bench-out")
    bench.set_defaults(func=_cmd_bench)
    return p


def main(argv: list[str] | None = None, runner: Runner | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    return args.func(args, runner)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
