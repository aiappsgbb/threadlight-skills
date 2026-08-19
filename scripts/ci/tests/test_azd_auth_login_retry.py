"""Guard the bounded retry on the initial `azd auth login (federated OIDC)` step.

Run #32296600274: this exact command waited ~47s and then failed with
`ClientAssertionCredential: fetching federated token: expected 200 response,
got: 503` — a transient failure of GitHub's OIDC token endpoint, not a
config problem (a prior full run against the same client/tenant/provider had
already logged in cleanly, and this is a design-only run so nothing else in
the job was even exercised). Every downstream phase was skipped because the
step had zero retry.

Two jobs, same as the sibling reliability guards in this directory:

1. **Structural assertions** on the step's `run:` body — bounded attempts,
   the transient-signature grep, the 15s/30s backoff, immediate failure on
   non-transient errors, `--check-status` gating success, and no
   `continue-on-error` escape hatch.
2. **Behavioural (mutation) proof** — the extracted `run:` body is executed
   for real under `bash`, with a fake `azd` and a fake `sleep` on `PATH`, so
   the retry/backoff/non-transient-immediate-failure logic is proven to
   actually work end to end, not just to contain the right substrings.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "threadlight-e2e-foundry.yml"

STEP_NAME = "azd auth login (federated OIDC)"


def load_step() -> dict:
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = doc["jobs"]["e2e"]["steps"]
    return next(s for s in steps if s.get("name") == STEP_NAME)


def step_body() -> str:
    return load_step()["run"]


# ── structural assertions ───────────────────────────────────────────────


def test_step_exists():
    assert load_step() is not None


def test_log_path_defaults_to_tmp_but_is_overridable():
    """Production behaviour is unchanged (defaults to /tmp, matching this
    workflow's existing /tmp/copilot-logs, /tmp/run-copilot-phase.sh
    convention); the `${VAR:-default}` form only adds a test seam."""
    body = step_body()
    assert 'LOG="${AZD_AUTH_LOGIN_LOG:-/tmp/azd-auth-login-attempt.log}"' in body


def test_step_has_no_continue_on_error():
    """A retry loop that also has `continue-on-error: true` hides the final
    failure from the job — the exact opposite of 'no hiding final output'."""
    assert "continue-on-error" not in load_step()


def test_bounded_three_attempts_no_infinite_loop():
    body = step_body()
    assert "for attempt in 1 2 3; do" in body
    # Bounded — not `while true`/`while :` which would need an external
    # break condition to avoid hanging the whole 90-minute job.
    assert not re.search(r"while\s+(true|:)\b", body)
    assert "done" in body


def test_attempt_number_is_printed_each_try():
    body = step_body()
    assert "attempt $attempt of 3" in body


def test_output_is_captured_and_printed_not_swallowed():
    body = step_body()
    # `tee -a` both writes to the log used for the transient-signature grep
    # AND streams to stdout — a bare redirect (`> "$LOG"`) would hide output
    # from the live job log.
    assert re.search(r"\|\s*tee -a \"\$LOG\"", body)
    assert "> /dev/null" not in body


def test_no_login_token_printed():
    body = step_body()
    # The step must never explicitly echo a token/credential value. (azd's
    # own `auth login` output does not print one either — this guards
    # against a future edit adding one, e.g. for debugging.)
    assert not re.search(r"echo.*token", body, re.IGNORECASE)
    assert "::add-mask::" not in body  # nothing worth masking here


@pytest.mark.parametrize(
    "signature",
    [
        "503",
        "502",
        "504",
        "timeout",
        "timed out",
        "temporarily unavailable",
    ],
)
def test_transient_grep_covers_signature(signature):
    body = step_body()
    grep_line = next(line for line in body.splitlines() if "grep -qEi" in line)
    # Build the exact same regex the step uses and confirm it actually
    # matches a realistic message containing this signature — a substring
    # check on the pattern text would pass even if the regex were malformed.
    match = re.search(r"grep -qEi '([^']+)'", grep_line)
    assert match, f"could not extract grep pattern from: {grep_line!r}"
    pattern = match.group(1)
    sample = f"some upstream error: {signature} happened"
    assert re.search(pattern, sample, re.IGNORECASE), (
        f"transient grep pattern {pattern!r} does not match a message "
        f"containing {signature!r}"
    )


def test_transient_grep_covers_fetching_federated_token_5xx():
    body = step_body()
    grep_line = next(line for line in body.splitlines() if "grep -qEi" in line)
    match = re.search(r"grep -qEi '([^']+)'", grep_line)
    pattern = match.group(1)
    # The exact evidence string from run #32296600274.
    sample = (
        "ClientAssertionCredential: fetching federated token: "
        "expected 200 response, got: 503"
    )
    assert re.search(pattern, sample, re.IGNORECASE)
    # Also cover a 5xx code other than 502/503/504 riding the same
    # azd-emitted wrapper phrase, per the "expected 200 5xx" requirement.
    other_5xx = (
        "fetching federated token: expected 200 response, got: 599"
    )
    assert re.search(pattern, other_5xx, re.IGNORECASE)


def test_nontransient_message_does_not_match_transient_grep():
    body = step_body()
    grep_line = next(line for line in body.splitlines() if "grep -qEi" in line)
    match = re.search(r"grep -qEi '([^']+)'", grep_line)
    pattern = match.group(1)
    for sample in (
        "ERROR: AADSTS700016: Application not found in the directory",
        "ERROR: az: tenant not found",
        "invalid client secret provided",
        "no federated-credential subject configured for this repo/environment",
    ):
        assert not re.search(pattern, sample, re.IGNORECASE), (
            f"non-transient message {sample!r} incorrectly matched as transient"
        )


def test_nontransient_failure_exits_immediately_before_third_attempt_check():
    """The non-transient `exit 1` must come before the `attempt -eq 3` check,
    otherwise a config error on attempt 1 would still sleep/retry twice."""
    body = step_body()
    nontransient_idx = body.index("Non-transient failure")
    third_attempt_idx = body.index('"$attempt" -eq 3')
    assert nontransient_idx < third_attempt_idx


def test_backoff_is_15_then_30():
    body = step_body()
    assert re.search(r'if \[ "\$attempt" -eq 1 \]; then\s*\n\s*BACKOFF=15', body)
    assert "BACKOFF=30" in body
    # Only two possible sleep durations — 15 for the first retry, 30 for
    # the second. A third failure exits instead of sleeping again.
    backoff_values = set(re.findall(r"BACKOFF=(\d+)", body))
    assert backoff_values == {"15", "30"}
    # BACKOFF is only ever assigned once we already know attempt < 3 (the
    # `-eq 3` exit-1 branch runs first), so the assignment/sleep block must
    # come strictly after the third-attempt exit check in program order.
    assert body.index('"$attempt" -eq 3') < body.index("BACKOFF=15")


def test_third_failure_exits_nonzero_without_sleeping_again():
    body = step_body()
    # The block guarded by `attempt -eq 3` must contain `exit 1` and must
    # end (at the closing `fi`) before any BACKOFF assignment or `sleep`
    # call — i.e. a third failure exits instead of falling through to the
    # backoff/sleep logic.
    third_check_idx = body.index('"$attempt" -eq 3')
    fi_idx = body.index("\n  fi\n", third_check_idx)
    third_attempt_block = body[third_check_idx:fi_idx]
    assert "exit 1" in third_attempt_block
    assert "sleep" not in third_attempt_block
    assert "BACKOFF" not in third_attempt_block
    # And the backoff/sleep logic (reached only when attempt != 3) appears
    # after this block closes.
    assert body.index("BACKOFF=15") > fi_idx
    assert body.index("sleep \"$BACKOFF\"") > fi_idx


def test_success_requires_check_status_inside_the_same_conditional():
    """`--check-status` must gate `exit 0` via `&&`, not run unconditionally
    after the loop — otherwise a step that merely printed a login prompt
    without confirming an actual session would still report success."""
    body = step_body()
    success_block = body[: body.index("exit 0")]
    assert "azd auth login --check-status" in success_block
    assert re.search(r"&&\s*azd auth login --check-status", success_block)


def test_same_client_tenant_provider_flags_are_unchanged():
    body = step_body()
    assert '--client-id "$AZURE_CLIENT_ID"' in body
    assert "--federated-credential-provider github" in body
    assert '--tenant-id "$AZURE_TENANT_ID"' in body


def test_step_env_still_uses_the_same_secrets():
    step = load_step()
    assert step["env"] == {
        "AZURE_CLIENT_ID": "${{ secrets.AZURE_CLIENT_ID }}",
        "AZURE_TENANT_ID": "${{ secrets.AZURE_TENANT_ID }}",
    }


def test_isolated_config_dirs_step_is_unmodified():
    """The retry fix must not touch the AZURE_CONFIG_DIR/AZD_CONFIG_DIR
    isolation step (run #26999100945's fix) — it only changes the auth
    step's own retry behaviour."""
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = doc["jobs"]["e2e"]["steps"]
    isolate = next(
        s for s in steps if s.get("name") == "Setup isolated az/azd config dirs"
    )
    assert "AZURE_CONFIG_DIR" in isolate["run"]
    assert "AZD_CONFIG_DIR" in isolate["run"]


def test_azure_login_action_untouched():
    """The `azure/login@v3.0.0` OIDC step (a different auth chain than azd's
    own) must not be touched by this fix."""
    doc = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = doc["jobs"]["e2e"]["steps"]
    az_login = next(s for s in steps if s.get("uses") == "azure/login@v3.0.0")
    assert az_login["with"] == {
        "client-id": "${{ secrets.AZURE_CLIENT_ID }}",
        "tenant-id": "${{ secrets.AZURE_TENANT_ID }}",
        "subscription-id": "${{ secrets.AZURE_SUBSCRIPTION_ID }}",
    }


# ── behavioural (mutation) proof: run the real script under bash ───────
#
# These execute the *actual* extracted `run:` body with a fake `azd` and a
# fake `sleep` on PATH, so the assertions below prove the loop really does
# retry/backoff/fail as intended rather than merely containing the right
# substrings. Fast: `sleep` is faked to be instantaneous while still
# recording the requested duration.


AZD_MOCK = """#!/usr/bin/env bash
set -euo pipefail
# Detect the `azd auth login --check-status` call vs. the plain login call.
for arg in "$@"; do
  if [ "$arg" = "--check-status" ]; then
    if [ "${AZD_MOCK_CHECK_STATUS_FAIL:-0}" = "1" ]; then
      echo "ERROR: not logged in"
      exit 1
    fi
    echo "Logged in to Azure."
    exit 0
  fi
done

COUNT=$(cat "$AZD_MOCK_COUNTER" 2>/dev/null || echo 0)
COUNT=$((COUNT + 1))
echo "$COUNT" > "$AZD_MOCK_COUNTER"

case "$AZD_MOCK_MODE" in
  transient_then_success)
    if [ "$COUNT" -lt 3 ]; then
      echo "ERROR: ClientAssertionCredential: fetching federated token: expected 200 response, got: 503"
      exit 1
    fi
    echo "Logged in to Azure as githubactions."
    exit 0
    ;;
  non_transient)
    echo "ERROR: AADSTS700016: Application with identifier was not found in the directory"
    exit 1
    ;;
  all_transient)
    echo "ERROR: ClientAssertionCredential: fetching federated token: expected 200 response, got: 503"
    exit 1
    ;;
  *)
    echo "unknown AZD_MOCK_MODE: $AZD_MOCK_MODE" >&2
    exit 2
    ;;
esac
"""

SLEEP_MOCK = """#!/usr/bin/env bash
# Record the requested duration but don't actually wait — keeps the test
# fast while still proving the exact backoff values used.
echo "$1" >> "$SLEEP_LOG"
exit 0
"""


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _run_step(tmp_path: Path, mode: str, check_status_fail: bool = False):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_executable(bin_dir / "azd", AZD_MOCK)
    _write_executable(bin_dir / "sleep", SLEEP_MOCK)

    script_path = tmp_path / "step.sh"
    script_path.write_text(step_body(), encoding="utf-8")

    counter = tmp_path / "azd_login_calls"
    sleep_log = tmp_path / "sleep_log"
    auth_log = tmp_path / "azd-auth-login-attempt.log"

    env = dict(os.environ)
    env.update(
        {
            "PATH": f"{bin_dir}:{env['PATH']}",
            "AZURE_CLIENT_ID": "fake-client-id",
            "AZURE_TENANT_ID": "fake-tenant-id",
            "AZD_MOCK_MODE": mode,
            "AZD_MOCK_COUNTER": str(counter),
            "AZD_MOCK_CHECK_STATUS_FAIL": "1" if check_status_fail else "0",
            "SLEEP_LOG": str(sleep_log),
            # The step defaults this to /tmp in production (matching the
            # existing /tmp/copilot-logs, /tmp/run-copilot-phase.sh
            # convention elsewhere in this workflow); the test overrides it
            # to stay inside pytest's own sandboxed tmp_path.
            "AZD_AUTH_LOGIN_LOG": str(auth_log),
        }
    )
    result = subprocess.run(
        ["bash", str(script_path)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    login_calls = int(counter.read_text().strip()) if counter.exists() else 0
    sleeps = sleep_log.read_text().split() if sleep_log.exists() else []
    return result, login_calls, sleeps


def test_mutation_transient_then_success_retries_and_succeeds(tmp_path):
    result, login_calls, sleeps = _run_step(tmp_path, "transient_then_success")
    assert result.returncode == 0, result.stdout + result.stderr
    assert login_calls == 3
    assert sleeps == ["15", "30"]
    assert "attempt 3 succeeded" in result.stdout


def test_mutation_non_transient_fails_immediately_no_retry_no_sleep(tmp_path):
    result, login_calls, sleeps = _run_step(tmp_path, "non_transient")
    assert result.returncode != 0
    assert login_calls == 1, "non-transient failure must not retry"
    assert sleeps == [], "non-transient failure must not sleep/backoff"
    assert "Non-transient failure" in result.stdout


def test_mutation_all_transient_exhausts_three_attempts_and_fails(tmp_path):
    result, login_calls, sleeps = _run_step(tmp_path, "all_transient")
    assert result.returncode != 0
    assert login_calls == 3, "must attempt exactly 3 times, no more"
    assert sleeps == ["15", "30"], "exactly two backoffs, 15 then 30"
    assert "all 3 attempts exhausted" in result.stdout


def test_mutation_check_status_failure_is_treated_as_attempt_failure(tmp_path):
    """Login succeeds but `--check-status` fails: the attempt must still
    count as a failure (transient login output is what's inspected — a
    login-successful/check-status-failed combo has no transient signature
    in this mock, so it must fail immediately as non-transient rather than
    silently reporting overall success)."""
    result, login_calls, sleeps = _run_step(
        tmp_path, "transient_then_success", check_status_fail=True
    )
    # login succeeds on attempt 3 (COUNT reaches 3), but check-status then
    # fails every time -- so the loop must exhaust all 3 attempts and fail,
    # never reporting success off of login alone.
    assert result.returncode != 0
    assert "attempt 3 succeeded" not in result.stdout
    assert login_calls == 3
