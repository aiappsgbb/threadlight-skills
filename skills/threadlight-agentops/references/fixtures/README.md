# Offline synthetic fixtures

The bounded fixture builder is `AgentOpsTests.fixture()` in
[`tests/test_agentops_check.py`](../../tests/test_agentops_check.py), with native
0.14.0 JSON shapes checked against tagged source. It creates isolated fixture Git
repositories below the test directory, synthetic RSA signing keys and independently
committed target/environment/threshold policies. Cleanup removes only the fixture
directory created by that test. No live artifacts, targets, credentials or private
keys are checked in; no Azure/native process runs.

Additional ordinary-adoption tests use **no keys or signatures**: a real bounded
Python subprocess writes native-shaped synthetic outputs between in-process
observation begin/finish calls. They verify eval and Doctor capture, read-only
assessment, actual eval/red-team consumers, sequential emission and reload.
These are fake-command technical roundtrips, never Azure/native-runtime proof.

Coverage includes root/no opt-in, equal-basename multi-agent roots, unknown
schemas, private payload sentinels, bounded oversized files, real native
threshold strings and row-metric lists, healthy signed evidence, eval quality
negative + valid blocked Doctor, disabled sources, forged receipts, latest hash
mismatch, stale/future evidence, changed datasets, comparison provenance and
delta integrity, and unversioned red-team per-category/per-strategy coverage.
Discovery regressions also cover authoritative azd/Foundry roots, explicit
service selection under examples, and exclusion of unselected catalog/test/docs
fixtures from recursive fallback.

These are **adapter contract fixtures**, not claims that a paid eval, cloud
Doctor or deployment has executed. Actual native runtime integration and live
readiness remain separate evidence classes.

## Consumer-test helper

Load `skills/threadlight-agentops/tests/fixture_helpers.py` with `importlib` and
call `create_agentops_fixture(repo: Path, *, state="healthy", now=None,
redteam=True) -> dict`. The destination must be empty; the caller owns cleanup.
It emits a real loadable `specs/agentops-manifest.json` and supporting local
synthetic signed evidence. States are `healthy`, `partial`, `blocked`,
`quality-fail`, and `stale`. Healthy includes positive actual red-team bucket
coverage by default; `partial` contains an unbound opt-in without inventing
receipts. `now` should match the consumer test's validation clock.

Add desired project files and commit them **before** minting a different fixture
run: modifying a returned healthy fixture's source/HEAD intentionally invalidates
its binding. Do not patch a manifest verdict or source hash to manufacture green.
