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
import metrics as metrics_mod  # noqa: E402
import prices as prices_mod  # noqa: E402
import score as score_mod  # noqa: E402
import rubric as rubric_mod  # noqa: E402
import matrix as matrix_mod  # noqa: E402

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


def run_bench(candidate: int, baseline: int, resource_id: str,
              baseline_model: str = "gpt-5.4-mini",
              candidate_deployment: str = "model-router",
              baseline_deployment: str = "",
              repo: str = "aiappsgbb/threadlight-skills",
              outdir: Path | None = None, prices_path: Path | None = None,
              gh_runner: Runner | None = None,
              az_runner: Runner | None = None) -> dict[str, Any]:
    """Paired cost/efficiency scorecard: model-router run vs a baseline-model run.

    Token usage has no run-id dimension in Azure Monitor, so each run's metric
    window (startedAt→updatedAt) bounds its `az monitor metrics list` query.
    """
    price_table = prices_mod.load_prices(prices_path)

    cand_meta = harvest.fetch_view(candidate, repo=repo, runner=gh_runner)
    cand_metrics = metrics_mod.fetch_metrics(
        resource_id, cand_meta.get("startedAt", ""), cand_meta.get("updatedAt", ""),
        deployment=candidate_deployment, runner=az_runner)
    candidate_usage = metrics_mod.parse_metrics(cand_metrics)

    baseline_usage: dict[str, Any] | None = None
    if baseline_deployment:
        base_meta = harvest.fetch_view(baseline, repo=repo, runner=gh_runner)
        base_metrics = metrics_mod.fetch_metrics(
            resource_id, base_meta.get("startedAt", ""), base_meta.get("updatedAt", ""),
            deployment=baseline_deployment, runner=az_runner)
        baseline_usage = metrics_mod.parse_metrics(base_metrics)

    card = score_mod.scorecard(candidate_usage=candidate_usage,
                               baseline_model=baseline_model, prices=price_table,
                               baseline_usage=baseline_usage)
    card["candidate_run"] = candidate
    card["baseline_run"] = baseline

    if outdir is not None:
        out = Path(outdir)
        out.mkdir(parents=True, exist_ok=True)
        stem = f"scorecard-{candidate}-vs-{baseline}"
        (out / f"{stem}.json").write_text(json.dumps(card, indent=2), encoding="utf-8")
        (out / f"{stem}.md").write_text(score_mod.render_scorecard(card), encoding="utf-8")
    return card


def _score_cell(cell: dict[str, Any], repo: str = "aiappsgbb/threadlight-skills",
                resource_id: str | None = None, runner: Runner | None = None,
                az_runner: Runner | None = None) -> dict[str, Any]:
    """Harvest one matrix cell into scorecard-arm shape. Network-bound; stubbed in tests."""
    import tempfile
    run_id = cell["run_id"]
    jobs = harvest.fetch_jobs(run_id, repo, runner=runner)
    phases = harvest.parse_phase_parity(jobs)
    phases_ok = all(v == "success" for v in phases.values())
    with tempfile.TemporaryDirectory() as td:
        bundle = Path(td)
        harvest.download_run(run_id, bundle, runner=runner)
        phase_logs = list(bundle.rglob("phase-*.log"))
        rounds = harvest.count_rounds(phase_logs)["total"]
        specs = harvest.find_specs_dir(bundle)
        rubric_doc = cell.get("_rubric") or {"checks": []}
        rubric_res = rubric_mod.score_rubric(specs or bundle, rubric_doc)
    cost = cell.get("_cost_usd", 0.0)
    if resource_id:
        meta = harvest.fetch_view(run_id, repo=repo, runner=runner)
        doc = metrics_mod.fetch_metrics(
            resource_id, meta.get("startedAt", ""), meta.get("updatedAt", ""),
            deployment=cell.get("model_deployment", ""), runner=az_runner)
        usage = metrics_mod.parse_metrics(doc)
        cost = score_mod.cost_of(usage, prices_mod.load_prices(None))
    return {"arm": cell["arm"], "phases_ok": phases_ok, "rounds": rounds,
            "rubric": rubric_res["score"], "cost_usd": cost}


def run_validate(manifest_path: str, out_dir: str,
                 repo: str = "aiappsgbb/threadlight-skills",
                 resource_id: str | None = None, runner: Runner | None = None,
                 az_runner: Runner | None = None) -> int:
    import collections
    cells = json.loads(Path(manifest_path).read_text(encoding="utf-8"))["cells"]
    by_wl: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for cell in cells:
        pack = Path(".github/workloads") / cell["workload"] / "rubric.yml"
        if pack.is_file():
            cell["_rubric"] = rubric_mod.load_rubric(pack)
        by_wl[cell["workload"]].append(
            _score_cell(cell, repo=repo, resource_id=resource_id,
                        runner=runner, az_runner=az_runner))
    cards = [score_mod.validation_scorecard(wl, arms) for wl, arms in by_wl.items()]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "router-validation.json").write_text(json.dumps(cards, indent=2), encoding="utf-8")
    (out / "router-validation.md").write_text(
        report.render_validation_matrix(cards), encoding="utf-8")
    print(f"[router-bench] wrote router-validation.(json|md) to {out}")
    return 0


def _cmd_validate(args: argparse.Namespace, runner: Runner | None = None,
                  az_runner: Runner | None = None) -> int:
    if args.ingest:
        return run_validate(args.ingest, args.out, repo=args.repo,
                            resource_id=args.resource, runner=runner,
                            az_runner=az_runner)
    arms = [
        {"arm": "mini", "model_deployment": "gpt-5.4-mini", "wire_api": "responses"},
        {"arm": "router", "model_deployment": "model-router", "wire_api": "completions"},
        {"arm": "strong", "model_deployment": "gpt-5.4", "wire_api": "responses"},
    ]
    cells = matrix_mod.dispatch_matrix(args.workloads, arms, repo=args.repo, ref=args.ref)
    Path(args.out).mkdir(parents=True, exist_ok=True)
    matrix_mod.write_manifest(cells, Path(args.out) / "matrix-manifest.json")
    print(f"[router-bench] dispatched {len(cells)} runs; manifest in {args.out}")
    return 0


def _cmd_learn(args: argparse.Namespace, runner: Runner | None) -> int:
    digest = run_learn(args.run_id, repo=args.repo, outdir=Path(args.out),
                       model_deployment=args.deployment, with_legs=args.with_legs,
                       runner=runner)
    print(report.render_markdown(digest))
    s = digest["summary"]
    print(f"\n[router-bench] wrote learnings-{args.run_id}.(json|md) to {args.out} "
          f"— {s['total']} findings (high={s['high']}).", file=sys.stderr)
    return 0


def _cmd_bench(args: argparse.Namespace, runner: Runner | None,
               az_runner: Runner | None) -> int:
    card = run_bench(
        args.candidate, args.baseline, resource_id=args.resource,
        baseline_model=args.baseline_model,
        candidate_deployment=args.candidate_deployment,
        baseline_deployment=args.baseline_deployment,
        repo=args.repo, outdir=Path(args.out), prices_path=args.prices,
        gh_runner=runner, az_runner=az_runner)
    print(score_mod.render_scorecard(card))
    print(f"\n[router-bench] wrote scorecard-{args.candidate}-vs-{args.baseline}.(json|md) "
          f"to {args.out} — verdict={card['verdict']} delta=${card['delta_usd']}.",
          file=sys.stderr)
    return 0


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
    bench.add_argument("--resource", required=True,
                       help="AI Services account resource id for token metrics")
    bench.add_argument("--baseline-model", default="gpt-5.4-mini",
                       help="model name for the counterfactual reprice")
    bench.add_argument("--candidate-deployment", default="model-router")
    bench.add_argument("--baseline-deployment", default="",
                       help="if set, also fetch the baseline run's actual usage")
    bench.add_argument("--prices", type=Path, default=None, help="price override JSON")
    bench.set_defaults(func=_cmd_bench)

    v = sub.add_parser("validate", help="run/ingest the router validation matrix")
    v.add_argument("--ingest", help="score an existing matrix-manifest.json")
    v.add_argument("--dispatch", action="store_true", help="dispatch a fresh matrix")
    v.add_argument("--workloads", nargs="+", default=["returns-triage", "fsi-kyc-aml"])
    v.add_argument("--ref", default="unsafecode-automatic-fiesta")
    v.add_argument("--repo", default="aiappsgbb/threadlight-skills")
    v.add_argument("--resource", help="Azure AI Services resource id for cost")
    v.add_argument("--out", default="router-validation-out")
    v.set_defaults(func=_cmd_validate)
    return p


def main(argv: list[str] | None = None, runner: Runner | None = None,
         az_runner: Runner | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    if args.command == "bench":
        return _cmd_bench(args, runner, az_runner)
    if args.command == "validate":
        return _cmd_validate(args, runner, az_runner)
    return args.func(args, runner)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
