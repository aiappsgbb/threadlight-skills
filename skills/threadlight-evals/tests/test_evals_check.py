"""stdlib unittest suite for evals_check.py (no pytest)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(SKILL, "scripts"))

import evals_check as ec  # noqa: E402

FIXTURES = os.path.join(SKILL, "references", "fixtures")
SCHEDULED = os.path.join(FIXTURES, "sample-scheduled")
MANUAL = os.path.join(FIXTURES, "sample-manual")
LEGACY = os.path.join(FIXTURES, "sample-legacy-ce")
SKILL_MD = os.path.join(SKILL, "SKILL.md")
WIRING_MD = os.path.join(SKILL, "references", "foundry-ce-wiring.md")


class ScheduledFixtureTests(unittest.TestCase):
    def setUp(self):
        self.caps = ec.evaluate(SCHEDULED, freshness_days=3650)
        self.man = ec.manifest(SCHEDULED, self.caps, 3650)

    def test_core_evals_detected(self):
        for key in (
            "eval_scenarios_present",
            "eval_datasets_present",
            "dataset_shape_ok",
            "thresholds_declared",
            "schedule_present",
            "run_history_present",
        ):
            self.assertEqual(self.caps[key]["status"], "pass", key)

    def test_online_and_ab_are_detected(self):
        self.assertEqual(self.caps["online_eval_wired"]["status"], "pass")
        self.assertEqual(self.caps["ab_comparison_present"]["status"], "pass")

    def test_alert_detected_and_no_must_fix(self):
        self.assertEqual(self.caps["alert_wired"]["status"], "pass")
        self.assertEqual(self.man["must_fix"], [])
        self.assertIn(self.man["verdict"], ("comprehensive", "partial"))


class ManualFixtureTests(unittest.TestCase):
    def setUp(self):
        self.caps = ec.evaluate(MANUAL, freshness_days=3650)
        self.man = ec.manifest(MANUAL, self.caps, 3650)

    def test_manual_fixture_has_required_gaps(self):
        self.assertEqual(self.caps["schedule_present"]["status"], "must-fix")
        self.assertEqual(self.caps["alert_wired"]["status"], "must-fix")
        self.assertIn("schedule_present", self.man["must_fix"])
        self.assertIn("alert_wired", self.man["must_fix"])

    def test_manual_fixture_is_not_comprehensive(self):
        self.assertNotEqual(self.man["verdict"], "comprehensive")
        self.assertIn(self.man["verdict"], ("none", "offline-only"))

    def test_generated_report_does_not_satisfy_alert(self):
        docs = os.path.join(MANUAL, "docs")
        report = os.path.join(docs, "evals-report.md")
        os.makedirs(docs, exist_ok=True)
        try:
            with open(report, "w", encoding="utf-8") as fh:
                fh.write("alert_wired EVAL-104 pass metric alert eval threshold")
            caps = ec.evaluate(MANUAL, freshness_days=3650)
            self.assertEqual(caps["alert_wired"]["status"], "must-fix")
        finally:
            if os.path.exists(report):
                os.remove(report)
            try:
                os.rmdir(docs)
            except OSError:
                pass


class MetricsBlockTests(unittest.TestCase):
    """The manifest must surface the latest run's pass-rate so the
    production-ready KPI scorecard can join eval quality (KPI-003)."""

    def test_scheduled_manifest_surfaces_pass_rate(self):
        caps = ec.evaluate(SCHEDULED, 3650)
        man = ec.manifest(SCHEDULED, caps, 3650)
        self.assertIn("metrics", man)
        metrics = man["metrics"]
        self.assertAlmostEqual(metrics["pass_rate"], 0.91, places=4)
        self.assertIsInstance(metrics["threshold"], float)
        self.assertTrue(metrics["latest_run"].startswith("evals/runs/"))

    def test_manual_manifest_pass_rate_is_none_when_no_run(self):
        caps = ec.evaluate(MANUAL, 3650)
        man = ec.manifest(MANUAL, caps, 3650)
        self.assertIn("metrics", man)
        self.assertIsNone(man["metrics"]["pass_rate"])


class ManifestShapeTests(unittest.TestCase):
    def test_schema_and_required_keys(self):
        caps = ec.evaluate(SCHEDULED, 3650)
        man = ec.manifest(SCHEDULED, caps, 3650)
        self.assertEqual(man["schema"], ec.MANIFEST_SCHEMA)
        for key in (
            "tool_version",
            "captured_at",
            "verdict",
            "must_fix",
            "should_fix",
            "not_verified",
            "capabilities",
            "freshness_window_days",
            "metrics",
        ):
            self.assertIn(key, man)

    def test_capability_keys_are_contract_keys(self):
        caps = ec.evaluate(SCHEDULED, 3650)
        self.assertEqual(set(caps), set(ec.CAPABILITY_ORDER))

    def test_gate_exit_code_for_manual(self):
        rc = ec.main(["--target", MANUAL, "--gate", "--freshness-days", "3650"])
        self.assertEqual(rc, 2)

    def test_gate_exit_code_for_scheduled(self):
        rc = ec.main(["--target", SCHEDULED, "--gate", "--freshness-days", "3650"])
        self.assertEqual(rc, 0)


class EvaluationRulesTests(unittest.TestCase):
    """EVAL-101 follows Foundry evaluation rules, not the classic
    create_agent_evaluation API absent from azure-ai-projects 2.x (#207)."""

    def test_rules_fixture_passes_eval_101_with_rules_evidence(self):
        caps = ec.evaluate(SCHEDULED, freshness_days=3650)
        online = caps["online_eval_wired"]
        self.assertEqual(online["status"], "pass")
        self.assertIn("evaluation_rules", online["evidence"])
        self.assertFalse(online.get("legacy"))
        self.assertEqual(caps["schedule_present"]["status"], "pass")
        with open(os.path.join(SCHEDULED, "src", "continuous_eval.py"), encoding="utf-8") as fh:
            self.assertNotIn("create_agent_evaluation", fh.read())

    def test_legacy_only_fixture_is_flagged_legacy(self):
        caps = ec.evaluate(LEGACY, freshness_days=3650)
        online = caps["online_eval_wired"]
        self.assertNotEqual(online["status"], "pass")
        self.assertTrue(online.get("legacy"))
        self.assertIn("legacy", online["hint"])
        self.assertIn("evaluation_rules", online["hint"])

    def test_legacy_flag_accepts_classic_wiring_and_keeps_the_label(self):
        caps = ec.evaluate(LEGACY, freshness_days=3650, accept_legacy=True)
        online = caps["online_eval_wired"]
        self.assertEqual(online["status"], "pass")
        self.assertTrue(online.get("legacy"))
        rc = ec.main(["--target", LEGACY, "--json", "--freshness-days", "3650",
                      "--accept-legacy-online-eval"])
        self.assertEqual(rc, 0)

    def test_each_rule_action_counts(self):
        import tempfile
        for action in ("ContinuousEvaluationRuleAction", "TraceEvaluationRuleAction",
                       "DatasetEvaluationRuleAction"):
            with tempfile.TemporaryDirectory() as tmp:
                os.makedirs(os.path.join(tmp, "src"))
                with open(os.path.join(tmp, "src", "rules.py"), "w", encoding="utf-8") as fh:
                    fh.write("project.evaluation_rules.create_or_update(id='r', evaluation_rule="
                             f"EvaluationRule(action={action}(eval_id=e), enabled=True))\n")
                caps = ec.evaluate(tmp, freshness_days=3650)
                self.assertEqual(caps["online_eval_wired"]["status"], "pass", action)
                self.assertEqual(caps["schedule_present"]["status"], "pass", action)

    def test_azure_eval_yaml_rule_declaration_counts(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, "evals"))
            with open(os.path.join(tmp, "evals", "azure.eval.yaml"), "w", encoding="utf-8") as fh:
                fh.write("evals:\n  - name: prod-traces\n    source: traces\n    rule:\n"
                         "      action: TraceEvaluationRuleAction\n      schedule: hourly\n")
            caps = ec.evaluate(tmp, freshness_days=3650)
            self.assertEqual(caps["online_eval_wired"]["status"], "pass")

    def test_docs_no_longer_claim_rules_reject_hosted_agents(self):
        for path in (SKILL_MD, WIRING_MD):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertNotIn("reject hosted", text, path)
            self.assertIn("evaluation_rules", text, path)
        with open(WIRING_MD, encoding="utf-8") as fh:
            wiring = fh.read()
        for needle in ("TraceEvaluationRuleAction", "ContinuousEvaluationRuleAction",
                       "max_hourly_runs", "max_traces", "enabled = False", "external agent"):
            self.assertIn(needle, wiring)

    def test_teardown_disables_rules_in_fixture(self):
        with open(os.path.join(SCHEDULED, "src", "continuous_eval.py"), encoding="utf-8") as fh:
            code = fh.read()
        self.assertIn("def disable_rules", code)
        self.assertIn("enabled = False", code)


def _gate_pilot(tmp, *, rows=60, heldout=True, mapped=True, builtin="builtin.task_adherence",
                spec="Escalate refunds over the ceiling; refuse prompt injection.", runs=None, tools=True):
    """Minimal pilot with an azure.eval.yaml gate, built with the real scaffold."""
    import json
    import subprocess
    evals = os.path.join(tmp, "evals")
    os.makedirs(os.path.join(evals, "evaluators"))
    os.makedirs(os.path.join(tmp, "specs"))
    with open(os.path.join(tmp, "specs", "spec.md"), "w", encoding="utf-8") as fh:
        fh.write("# SPEC\n\n## 9. Eval scenarios\n\n- eval scenario: " + spec + "\n\nMinimum pass threshold: 0.7\n")
    with open(os.path.join(evals, "evaluators", "rubric.json"), "w", encoding="utf-8") as fh:
        json.dump({"dimensions": [], "pass_threshold": 0.5}, fh)
    golden = os.path.join(tmp, "golden.jsonl")
    with open(golden, "w", encoding="utf-8") as fh:
        for i in range(rows):
            fh.write(json.dumps({"name": f"r{i}", "category": ["ok", "refuse"][i % 2],
                                 "query": f"q{i}", "ground_truth": f"g{i}"}) + "\n")
    script = os.path.join(SKILL, "scripts", "eval_yaml.py")
    subprocess.run([sys.executable, script, "split", golden, "--agent", "a",
                    "--out-dir", os.path.join(evals, "datasets")], check=True, capture_output=True)
    subprocess.run([sys.executable, script, "scaffold", "--agent", "a", "--rubric", "rub",
                    "--rubric-file", os.path.join(evals, "evaluators", "rubric.json"),
                    "--judge-model", "judge", "--evals-dir", evals], check=True, capture_output=True)
    path = os.path.join(evals, "azure.eval.yaml")
    text = open(path, encoding="utf-8").read()
    if not heldout:
        text = text.replace("dataset: a-heldout", "dataset: a-optimize")
    if not mapped:
        start = text.index("        dataMapping:")
        end = text.index("      - evaluator: builtin")
        text = text[:start] + text[end:]
    text = text.replace("builtin.task_adherence", builtin)
    open(path, "w", encoding="utf-8").write(text)
    if tools:
        cfg = os.path.join(tmp, "src", "agent", ".agent_configs", "baseline")
        os.makedirs(cfg)
        with open(os.path.join(cfg, "tools.json"), "w", encoding="utf-8") as fh:
            json.dump([{"type": "function", "function": {"name": "lookup"}}], fh)
    if runs is not None:
        with open(os.path.join(evals, "gate-runs.json"), "w", encoding="utf-8") as fh:
            json.dump(runs, fh)


GOOD_RUNS = {"eval": "a-gate", "floor": 0.7, "tolerance": 0.15,
             "baseline": {"run_id": "evalrun_base", "pass_rate": 0.8},
             "runs": [{"run_id": "evalrun_1", "pass_rate": 0.75}, {"run_id": "evalrun_2", "pass_rate": 0.85}]}


class GatePolicyTests(unittest.TestCase):
    """azure.eval.yaml scaffold (#209) and noise-aware held-out gate (#210)."""

    def _caps(self, **kw):
        import tempfile
        tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, tmp)
        _gate_pilot(tmp, **kw)
        caps = ec.evaluate(tmp, freshness_days=3650)
        return caps, ec.manifest(tmp, caps, 3650), tmp

    def test_scaffold_is_mapped_gate_and_unmapped_optimizer_eval(self):
        caps, man, tmp = self._caps(runs=GOOD_RUNS)
        for key in ("rubric_trajectory_mapped", "gate_split_ok", "gate_evaluator_suitable", "gate_runs_recorded"):
            self.assertEqual(caps[key]["status"], "pass", (key, caps[key]))
        import json
        rubric = json.load(open(os.path.join(tmp, "evals", "evaluators", "rubric.json")))
        self.assertGreaterEqual(rubric["pass_threshold"], 0.7)
        text = open(os.path.join(tmp, "evals", "azure.eval.yaml"), encoding="utf-8").read()
        optimizer = text[text.index("- name: a-optimizer-input"):]
        self.assertNotIn("dataMapping", optimizer)
        self.assertIn('response: "{{sample.output_items}}"', text[:text.index("- name: a-optimizer-input")])

    def test_rubric_mapped_to_final_text_on_tool_agent_is_flagged(self):
        caps, _, _ = self._caps(mapped=False, runs=GOOD_RUNS)
        self.assertEqual(caps["rubric_trajectory_mapped"]["status"], "should-fix")
        self.assertIn("sample.output_text", caps["rubric_trajectory_mapped"]["hint"])
        caps, _, _ = self._caps(mapped=False, tools=False, runs=GOOD_RUNS)
        self.assertEqual(caps["rubric_trajectory_mapped"]["status"], "not-applicable")

    def test_no_held_out_or_too_few_rows_is_must_fix(self):
        caps, man, _ = self._caps(heldout=False, runs=GOOD_RUNS)
        self.assertEqual(caps["gate_split_ok"]["status"], "must-fix")
        self.assertIn("gate_split_ok", man["must_fix"])
        caps, _, _ = self._caps(rows=40, runs=GOOD_RUNS)
        self.assertEqual(caps["gate_split_ok"]["status"], "must-fix")
        self.assertIn("50", caps["gate_split_ok"]["hint"])

    def test_task_completion_gating_refusals_is_should_fix(self):
        caps, _, _ = self._caps(builtin="builtin.task_completion", runs=GOOD_RUNS)
        self.assertEqual(caps["gate_evaluator_suitable"]["status"], "should-fix")
        caps, _, _ = self._caps(builtin="builtin.task_completion", spec="Answer the question.", runs=GOOD_RUNS)
        self.assertEqual(caps["gate_evaluator_suitable"]["status"], "pass")

    def test_manifest_records_gate_runs_mean_baseline_tolerance(self):
        _, man, _ = self._caps(runs=GOOD_RUNS)
        gate = man["metrics"]["gate"]
        self.assertEqual(gate["eval"], "a-gate")
        self.assertEqual([r["run_id"] for r in gate["runs"]], ["evalrun_1", "evalrun_2"])
        self.assertAlmostEqual(gate["mean"], 0.8)
        self.assertEqual(gate["baseline_run_id"], "evalrun_base")
        self.assertEqual(gate["tolerance"], 0.15)
        self.assertEqual(gate["decision"], "pass")

    def test_gate_run_policy(self):
        one = dict(GOOD_RUNS, runs=GOOD_RUNS["runs"][:1])
        caps, _, _ = self._caps(runs=one)
        self.assertEqual(caps["gate_runs_recorded"]["status"], "should-fix")
        regress = dict(GOOD_RUNS, runs=[{"run_id": "x", "pass_rate": 0.6}, {"run_id": "y", "pass_rate": 0.62}])
        caps, man, _ = self._caps(runs=regress)
        self.assertEqual(caps["gate_runs_recorded"]["status"], "must-fix")
        self.assertEqual(man["metrics"]["gate"]["decision"], "fail")
        narrow = dict(GOOD_RUNS, tolerance=0.05)
        caps, _, _ = self._caps(runs=narrow)
        self.assertEqual(caps["gate_runs_recorded"]["status"], "should-fix")
        self.assertIn("noise", caps["gate_runs_recorded"]["hint"])
        caps, _, _ = self._caps(runs=None)
        self.assertEqual(caps["gate_runs_recorded"]["status"], "should-fix")

    def test_scaffold_matches_the_recorded_azd_create_fixture(self):
        import subprocess
        import tempfile
        fixture = os.path.join(FIXTURES, "azd-eval-create")
        with tempfile.TemporaryDirectory() as tmp:
            evals = os.path.join(tmp, "evals")
            os.makedirs(os.path.join(evals, "datasets"))
            os.makedirs(os.path.join(evals, "evaluators"))
            for part in ("optimize", "heldout"):
                open(os.path.join(evals, "datasets", f"tlb-agent-{part}.jsonl"), "w").close()
            with open(os.path.join(evals, "evaluators", "tlb-rubric.json"), "w") as fh:
                fh.write('{"pass_threshold": 0.7}')
            subprocess.run([sys.executable, os.path.join(SKILL, "scripts", "eval_yaml.py"), "scaffold",
                            "--agent", "tlb-agent", "--rubric", "tlb-rubric",
                            "--rubric-file", os.path.join(evals, "evaluators", "tlb-rubric.json"),
                            "--judge-model", "gpt-5.4-mini", "--evals-dir", evals], check=True)
            generated = open(os.path.join(evals, "azure.eval.yaml"), encoding="utf-8").read()
        recorded = open(os.path.join(fixture, "azure.eval.yaml"), encoding="utf-8").read()
        self.assertEqual(generated, recorded)
        log = open(os.path.join(fixture, "create.log"), encoding="utf-8").read()
        self.assertEqual(log.count("Done: Created eval"), 2)
        import json
        stored = json.load(open(os.path.join(fixture, "stored-mappings.json")))
        self.assertEqual(stored["tlb-agent-gate"]["tlb-rubric-trajectory"]["response"], "{{sample.output_items}}")
        self.assertEqual(stored["tlb-agent-optimizer-input"]["tlb-rubric"]["response"], "{{sample.output_text}}")

    def test_split_holds_out_about_30_percent_per_category(self):
        sys.path.insert(0, os.path.join(SKILL, "scripts"))
        import eval_yaml
        rows = [{"name": f"r{i}", "category": "ab"[i % 2], "query": "q", "ground_truth": "g"} for i in range(60)]
        optimize, heldout = eval_yaml.split(rows)
        self.assertEqual(len(optimize) + len(heldout), 60)
        self.assertFalse({r["name"] for r in optimize} & {r["name"] for r in heldout})
        self.assertEqual(len(heldout), 18)
        self.assertEqual({r["category"] for r in heldout}, {"a", "b"})
        self.assertEqual(eval_yaml.split(rows), (optimize, heldout))


if __name__ == "__main__":
    unittest.main(verbosity=2)
