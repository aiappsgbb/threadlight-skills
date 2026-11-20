#!/usr/bin/env python3
"""threadlight-router-bench — CLI dispatcher.

Independent modes:

  learn  <run_id>              PRIMARY. Single-run self-improvement cold-path.
                               Harvests one CI run, classifies anomalies into a
                               grounded learnings digest. NO baseline required —
                               works on ANY run, green or red.

  bench  <candidate> <base>    OPTIONAL. Paired cost/efficiency scorecard of a
                               model-router run vs a baseline-model run, using
                               Azure Monitor token metrics, attributed to the
                               served model across deployments. (score/*)

  validate --dispatch|--ingest OPTIONAL. 3-arm e2e quality matrix.

  workload --endpoint|--ingest OPTIONAL. Deterministic task mix through model
                               router (per-request modes) vs fixed models on the
                               Responses API; estimated $ per 1k tasks.

  pool <sub> <region>          Pre-flight: list the model router pool (ARM).

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
import pool as pool_mod  # noqa: E402
import workload as workload_mod  # noqa: E402
import workload_tasks as tasks_mod  # noqa: E402

_WORKLOADS_ROOT = HERE.parents[2] / ".github" / "workloads"
ROUTER_SUBSET = ("gpt-6-luna", "gpt-6.1-terra", "gpt-6.1-sol")

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
              az_runner: Runner | None = None,
              candidate_serves_via: list[str] | None = None) -> dict[str, Any]:
    """Paired cost/efficiency scorecard: model-router run vs a baseline-model run.

    Token usage has no run-id dimension in Azure Monitor, so each run's metric
    window (startedAt→updatedAt) bounds its `az monitor metrics list` query.
    Metrics are fetched for every deployment and then scoped to the candidate
    deployment plus `candidate_serves_via` (own deployments that serve routed
    requests first), attributed by served model.
    """
    price_table = prices_mod.load_prices(prices_path)

    cand_meta = harvest.fetch_view(candidate, repo=repo, runner=gh_runner)
    cand_metrics = metrics_mod.fetch_metrics(
        resource_id, cand_meta.get("startedAt", ""), cand_meta.get("updatedAt", ""),
        runner=az_runner)
    cand_served = metrics_mod.served_usage(
        cand_metrics, deployments={candidate_deployment, *(candidate_serves_via or [])})
    candidate_usage = cand_served["usage"]

    baseline_usage: dict[str, Any] | None = None
    if baseline_deployment:
        base_meta = harvest.fetch_view(baseline, repo=repo, runner=gh_runner)
        base_metrics = metrics_mod.fetch_metrics(
            resource_id, base_meta.get("startedAt", ""), base_meta.get("updatedAt", ""),
            runner=az_runner)
        baseline_usage = metrics_mod.served_usage(
            base_metrics, deployments={baseline_deployment})["usage"]

    card = score_mod.scorecard(candidate_usage=candidate_usage,
                               baseline_model=baseline_model, prices=price_table,
                               baseline_usage=baseline_usage,
                               candidate_router=True)
    card["candidate_unattributed_tokens"] = cand_served["unattributed"]
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
    phases_ok = all(v in ("success", "skipped") for v in phases.values())
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
            runner=az_runner)
        usage = metrics_mod.served_usage(
            doc, deployments=set(matrix_mod.cell_deployments(cell)))["usage"]
        cost = score_mod.cost_of(usage, prices_mod.load_prices(None),
                                 router=cell.get("arm") == "router")
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
        pack = _WORKLOADS_ROOT / cell["workload"] / "rubric.yml"
        if pack.is_file():
            cell["_rubric"] = rubric_mod.load_rubric(pack)
        else:
            print(f"[router-bench] WARNING: no rubric pack at {pack}; arm "
                  f"'{cell.get('arm')}' workload '{cell['workload']}' scores rubric=0.0",
                  file=sys.stderr)
        arm = cell.get("arm", "?")
        if cell.get("run_id") is None:
            print(f"[router-bench] WARNING: cell arm '{arm}' workload "
                  f"'{cell['workload']}' has no run_id (run never registered); "
                  f"scoring it falls-behind", file=sys.stderr)
            scored = {"arm": arm, "phases_ok": False, "rounds": 0,
                      "rubric": 0.0, "cost_usd": 0.0}
        else:
            try:
                scored = _score_cell(cell, repo=repo, resource_id=resource_id,
                                     runner=runner, az_runner=az_runner)
            except Exception as exc:  # one bad cell must not sink the matrix
                print(f"[router-bench] WARNING: cell arm '{arm}' workload "
                      f"'{cell['workload']}' run {cell.get('run_id')} failed to "
                      f"score ({exc}); scoring it falls-behind", file=sys.stderr)
                scored = {"arm": arm, "phases_ok": False, "rounds": 0,
                          "rubric": 0.0, "cost_usd": 0.0}
        by_wl[cell["workload"]].append(scored)
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
    if not args.dispatch:
        print("router-bench validate: pass --ingest <manifest> to score, "
              "or --dispatch to fire a live matrix", file=sys.stderr)
        return 2
    # Router subset {gpt-6-luna, gpt-6.1-terra, gpt-6.1-sol}, each with its own
    # deployment in the same resource, vs fixed gpt-6-luna and gpt-6.1-sol.
    # GitHub Copilot BYOK reaches model router over Chat Completions, so the
    # router arm keeps the `completions` wire.
    arms = [
        {"arm": "small", "model_deployment": "gpt-6-luna", "wire_api": "responses"},
        {"arm": "router", "model_deployment": "model-router", "wire_api": "completions",
         "serves_via": list(ROUTER_SUBSET)},
        {"arm": "strong", "model_deployment": "gpt-6.1-sol", "wire_api": "responses"},
    ]
    cells = matrix_mod.dispatch_matrix(args.workloads, arms, repo=args.repo, ref=args.ref, runner=runner)
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
        gh_runner=runner, az_runner=az_runner,
        candidate_serves_via=args.candidate_serves_via)
    print(score_mod.render_scorecard(card))
    print(f"\n[router-bench] wrote scorecard-{args.candidate}-vs-{args.baseline}.(json|md) "
          f"to {args.out} — verdict={card['verdict']} delta=${card['delta_usd']}.",
          file=sys.stderr)
    return 0


def _cmd_workload(args: argparse.Namespace) -> int:
    out = Path(args.out)
    if args.live_prices:
        items = prices_mod.fetch_retail_prices(args.region, timeout=args.price_timeout)
        table = prices_mod.retail_table(items, scope=args.scope)
    else:
        table = prices_mod.load_prices(args.prices, scope=args.scope)
    if args.ingest:
        records = workload_mod.load_records(args.ingest)
        summary = workload_mod.summarize(records, table)
        workload_mod.write_outputs(summary, None, out)
    elif args.endpoint:
        arms = [workload_mod.router_arm(f"router-{m}", deployment=args.router_deployment, mode=m,
                                        models=args.router_models or None)
                for m in args.modes]
        arms += [workload_mod.fixed_arm(d) for d in args.fixed]
        post = workload_mod.http_post(args.endpoint, workload_mod.entra_token_provider())
        out.mkdir(parents=True, exist_ok=True)
        records = workload_mod.run_workload(arms, tasks_mod.build(args.taskset), args.reps, post,
                                            concurrency=args.concurrency,
                                            exec_code=not args.no_code_exec)
        summary = workload_mod.summarize(records, table)
        workload_mod.write_outputs(summary, records, out)
    else:
        print("router-bench workload: pass --endpoint <responses url> to run, "
              "or --ingest <records.jsonl> to score", file=sys.stderr)
        return 2
    print(workload_mod.render_markdown(summary))
    print(f"[router-bench] wrote router-workload.(json|md) to {out}", file=sys.stderr)
    return 0


def _cmd_pool(args: argparse.Namespace) -> int:
    pool = pool_mod.parse_router_pool(pool_mod.fetch_pool(args.subscription, args.region),
                                      version=args.version)
    print(json.dumps(pool, indent=2))
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
    bench.add_argument("--candidate-serves-via", nargs="*", default=[],
                       help="own deployments that serve the router's routed requests first")
    bench.add_argument("--prices", type=Path, default=None,
                       help="price override JSON or Retail Prices API export")
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

    w = sub.add_parser("workload", help="router vs fixed models on a deterministic task mix")
    w.add_argument("--endpoint", help="Responses API URL, e.g. "
                   "https://<resource>.services.ai.azure.com/openai/v1/responses")
    w.add_argument("--ingest", help="score recorded router-workload records (JSONL)")
    w.add_argument("--router-deployment", default="model-router",
                   help="model router deployment name, or 'auto'")
    w.add_argument("--modes", nargs="*", default=["balanced"],
                   choices=["balanced", "cost", "quality"])
    w.add_argument("--router-models", nargs="*", default=[],
                   help="optional per-request routing_config.models")
    w.add_argument("--fixed", nargs="*", default=["gpt-6-luna", "gpt-6.1-sol"],
                   help="fixed-model baseline deployments")
    w.add_argument("--taskset", default="easy", choices=["easy", "hard", "all"])
    w.add_argument("--reps", type=int, default=2)
    w.add_argument("--concurrency", type=int, default=4)
    w.add_argument("--no-code-exec", action="store_true",
                   help="do not execute model-written code; code tasks stay ungraded")
    w.add_argument("--prices", type=Path, default=None,
                   help="price override JSON or Retail Prices API export")
    w.add_argument("--live-prices", action="store_true",
                   help="pull current list prices from the Azure Retail Prices API")
    w.add_argument("--region", default="swedencentral", help="region for --live-prices")
    w.add_argument("--scope", default="global", choices=["global", "datazone"])
    w.add_argument("--price-timeout", type=float, default=20.0)
    w.add_argument("--out", default="router-workload-out")
    w.set_defaults(func=lambda a, r=None: _cmd_workload(a))

    pl = sub.add_parser("pool", help="list the model router pool via the ARM models API")
    pl.add_argument("subscription")
    pl.add_argument("region")
    pl.add_argument("--version", default=pool_mod.DEFAULT_ROUTER_VERSION)
    pl.set_defaults(func=lambda a, r=None: _cmd_pool(a))
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
