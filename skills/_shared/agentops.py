"""Bounded AgentOps evidence and local observed-run provenance.

Read-only assessment never executes native code. Explicit runtime capture binds
actual new outputs to committed scope and observed inputs, not Azure attestation.
Existing-owner signatures remain an optional stronger authentication policy.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import signal
import stat
import subprocess
import time
from urllib.parse import urlsplit

if __package__:
    from .manifest import build_envelope, validate_envelope, validate_iso8601_timestamp
else:
    import importlib.util
    _manifest_spec = importlib.util.spec_from_file_location(
        "_threadlight_agentops_manifest", Path(__file__).with_name("manifest.py"))
    _manifest = importlib.util.module_from_spec(_manifest_spec)
    _manifest_spec.loader.exec_module(_manifest)
    build_envelope = _manifest.build_envelope
    validate_envelope = _manifest.validate_envelope
    validate_iso8601_timestamp = _manifest.validate_iso8601_timestamp

SCHEMA = "threadlight-agentops-manifest/v1"
TOOL_VERSION = "0.1.0"
NATIVE_VERSION = "0.14.0"
UPSTREAM_SHA = "fb5c93eee489c71ef4084fa209adae24f762e3d7"
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_FILES = 20000
DOMAINS = frozenset({"evals", "redteam", "govern"})
CAPABILITIES = ("config", "pin", "binding", "integrity", "doctor_freshness",
                "release_consistency", "workflow")
METRICS = frozenset({"relevance", "coherence", "fluency", "groundedness",
    "similarity", "f1_score", "exact_match", "task_adherence", "intent_resolution",
    "tool_call_accuracy", "response_completeness", "hate_unfairness", "violence",
    "sexual", "self_harm", "protected_material", "indirect_attack", "bleu",
    "rouge", "meteor", "accuracy", "pass_rate", "quality"})
CORE_CATEGORIES = frozenset({"hate_unfairness", "violence", "sexual", "self_harm"})
CATEGORIES = CORE_CATEGORIES | frozenset({"protected_material", "indirect_attack",
    "code_vulnerability", "ungrounded_attributes", "prohibited_actions",
    "sensitive_data_leakage"})
STRATEGIES = frozenset({"baseline", "easy", "moderate", "difficult", "jailbreak",
    "base64", "rot13", "morse", "unicode_confusable", "character_space",
    "flip", "suffix", "prefix", "tense", "leetspeak", "caesar", "ascii_art",
    "Base64", "ROT13", "UnicodeConfusable", "CharacterSpace", "Morse",
    "Flip", "Suffix", "Prefix", "Tense", "Leetspeak", "Caesar", "AsciiArt",
    "Url", "Atbash", "Text", "CharacterSwap"})
_HASH = re.compile(r"[0-9a-f]{64}")
DISCOVERY_EXCLUDED_DIRS = frozenset({
    ".git", ".agentops", ".azure", ".venv", ".threadlight", ".work", ".pytest_cache",
    "venv", "node_modules", "__pycache__", "dist", "build", "coverage",
    "test", "tests", "fixture", "fixtures", "example", "examples", "doc", "docs",
    "sample", "samples", "skills", "catalog",
})
GENERATED_OUTPUTS = frozenset({
    "specs/agentops-manifest.json", "specs/evals-manifest.json",
    "specs/redteam-manifest.json", "specs/governance-manifest.json",
    "docs/evals-report.md", "docs/redteam-report.md", "docs/agt-governance-report.md",
    ".threadlight/auto-state.json", ".threadlight/auto-next.json",
    "tests/production-readiness-manifest.json", "docs/production-readiness-report.md",
    "tests/mcp-sbom.json", "tests/agent-identity.json",
})
_OBSERVATIONS = {}


class AgentOpsValidationError(ValueError):
    """Invalid/unprovable evidence; messages contain fixed codes, not payloads."""


def _require(condition, code="invalid-artifact"):
    if not condition:
        raise AgentOpsValidationError(code)


def safe_path(root: Path, relative: str, *, exists=True) -> Path:
    _require(isinstance(relative, str) and 0 < len(relative) <= 512, "unsafe-path")
    part = PurePosixPath(relative)
    _require(not part.is_absolute() and "\\" not in relative
             and ".." not in part.parts and not any(ord(c) < 32 for c in relative),
             "unsafe-path")
    _require(re.fullmatch(r"[A-Za-z0-9_. /-]+", relative), "unsafe-path")
    base = Path(root).resolve()
    current = base
    for component in part.parts:
        current /= component
        _require(not current.is_symlink(), "symlink-path")
    _require(current.resolve().is_relative_to(base), "unsafe-path")
    if exists:
        _require(current.is_file(), "missing-artifact")
    return current


def read_bytes(root, relative, *, limit=MAX_ARTIFACT_BYTES):
    path = safe_path(root, relative)
    try:
        _require(path.stat().st_size <= limit, "artifact-too-large")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            _require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "unsafe-file")
            data = stream.read(limit + 1)
        _require(len(data) <= limit, "artifact-too-large")
        return data
    except OSError:
        raise AgentOpsValidationError("unreadable-artifact") from None


def _pairs(items):
    result = {}
    for key, value in items:
        _require(key not in result, "duplicate-json-key")
        result[key] = value
    return result


def parse_json(raw):
    try:
        return json.loads(raw, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              AgentOpsValidationError("nonfinite-number")))
    except (ValueError, UnicodeError, RecursionError):
        raise AgentOpsValidationError("malformed-json") from None


def read_json(root, relative, *, limit=MAX_ARTIFACT_BYTES):
    return parse_json(read_bytes(root, relative, limit=limit))


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical_hash(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                            allow_nan=False).encode())


def bounded_command(argv, *, cwd, timeout=10, max_bytes=65536, env=None):
    """Drain both pipes with a shared size budget; never capture unbounded output."""
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=os.name == "posix")
    except OSError:
        raise AgentOpsValidationError("command-unavailable") from None
    buffers = {process.stdout: bytearray(), process.stderr: bytearray()}
    selector = selectors.DefaultSelector()
    for stream in buffers:
        selector.register(stream, selectors.EVENT_READ)
    deadline, size = time.monotonic() + timeout, 0
    try:
        while selector.get_map():
            _require(time.monotonic() < deadline, "command-timeout")
            for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                chunk = os.read(key.fileobj.fileno(), min(8192, max_bytes + 1 - size))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                size += len(chunk)
                _require(size <= max_bytes, "command-output-too-large")
                buffers[key.fileobj].extend(chunk)
        process.wait(timeout=max(0.01, deadline - time.monotonic()))
        return process.returncode, bytes(buffers[process.stdout]), bytes(buffers[process.stderr])
    except subprocess.TimeoutExpired:
        raise AgentOpsValidationError("command-timeout") from None
    finally:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.poll() is None:
            process.kill()
        process.wait()
        selector.close()
        for stream in buffers:
            stream.close()


def _git(repo, *args):
    code, out, _ = bounded_command(["git", "-C", str(repo), *args], cwd=repo)
    _require(code == 0, "repository-unavailable")
    return out


def repository_state(repo):
    try:
        commit = _git(repo, "rev-parse", "--verify", "HEAD").decode().strip()
        dirty = bool(_git(repo, "status", "--porcelain", "--untracked-files=all", "--", ".",
                         *(f":(exclude){path}" for path in sorted(GENERATED_OUTPUTS))))
    except AgentOpsValidationError:
        commit, dirty = None, True
    remote = None
    try:
        raw = _git(repo, "remote", "get-url", "origin").decode().strip()
        parsed = urlsplit(raw)
        if parsed.scheme in {"http", "https", "ssh"} and parsed.hostname:
            path = parsed.path
            host = parsed.hostname
        elif re.fullmatch(r"[^@\s]+@[A-Za-z0-9.-]+:[A-Za-z0-9_./-]+", raw):
            host, path = raw.split("@", 1)[1].split(":", 1)
        else:
            host, path = "", ""
        # Restrict to a host/owner/repo identifier; drop credentials, query and fragments.
        if re.fullmatch(r"/?[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", path) and host:
            remote = f"https://{host}/{path.lstrip('/')}"
    except (AgentOpsValidationError, UnicodeError, ValueError):
        pass
    return {"remote": remote, "commit": commit, "dirty": dirty}


def _azd_agent_roots(root):
    path = root / "azure.yaml"
    if not path.exists() and not path.is_symlink():
        return {}
    raw = read_bytes(root, "azure.yaml", limit=65536)
    try:
        import yaml
    except ImportError:
        raise AgentOpsValidationError("azd-metadata-parser-unavailable") from None

    class MetadataLoader(yaml.SafeLoader):
        nodes = 0

        def compose_node(self, parent, index):
            self.nodes += 1
            _require(self.nodes <= 4096 and not self.check_event(yaml.AliasEvent),
                     "unsupported-azd-metadata")
            return super().compose_node(parent, index)

        def construct_mapping(self, node, deep=False):
            mapping = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                _require(isinstance(key, str) and key not in mapping, "invalid-azd-mapping")
                mapping[key] = self.construct_object(value_node, deep=deep)
            return mapping

    try:
        metadata = yaml.load(raw, Loader=MetadataLoader)
    except (yaml.YAMLError, ValueError, UnicodeError, RecursionError):
        raise AgentOpsValidationError("invalid-azd-metadata") from None
    _require(isinstance(metadata, dict), "invalid-azd-metadata")
    services = metadata.get("services", {})
    _require(isinstance(services, dict) and len(services) <= 256, "invalid-azd-services")
    roots = {}
    for name, service in services.items():
        _require(isinstance(service, dict), "invalid-azd-service")
        if service.get("host") != "azure.ai.agent":
            continue
        _require(isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", name),
                 "invalid-azd-service-name")
        project = service.get("project", ".")
        candidate = safe_path(root, project, exists=False)
        relative = candidate.relative_to(root).as_posix()
        roots.setdefault(relative, []).append(name)
    return roots


def _has_optin_marker(root):
    # Check presence before reading unrelated deployment metadata. Include
    # examples/tests here because an explicit azd project may select those roots.
    caches = {".git", ".agentops", ".azure", ".venv", ".threadlight", ".work", ".pytest_cache",
              "venv", "node_modules", "__pycache__"}
    pending, seen = [root], 0
    while pending:
        try:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    seen += 1
                    _require(seen <= MAX_FILES, "discovery-limit")
                    if entry.name == "agentops.yaml":
                        return True
                    if (entry.name.lower() not in caches
                            and entry.is_dir(follow_symlinks=False)):
                        pending.append(Path(entry.path))
        except OSError:
            raise AgentOpsValidationError("discovery-unreadable") from None
    return False


def discover_opted_in_agents(repo: Path) -> list[dict]:
    """Prioritize azd/Foundry roots; use guarded metadata-only fallback otherwise.

    agentops.yaml is always opaque. Only root azure.yaml is parsed. Foundry
    metadata filenames are inspected without reading their contents.
    """
    root = Path(repo).resolve()
    _require(root.is_dir(), "repository-unavailable")
    if not _has_optin_marker(root):
        return []
    pending, markers, foundry, seen = [root], set(), set(), 0
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    seen += 1
                    _require(seen <= MAX_FILES, "discovery-limit")
                    if entry.name == "agentops.yaml":
                        _require(not entry.is_symlink() and entry.is_file(follow_symlinks=False),
                                 "symlink-path")
                        relative = directory.relative_to(root).as_posix()
                        safe_path(root, relative, exists=False)
                        markers.add(relative)
                    elif entry.name == ".foundry" and entry.is_dir(follow_symlinks=False):
                        with os.scandir(entry.path) as metadata:
                            for item in metadata:
                                seen += 1
                                _require(seen <= MAX_FILES, "discovery-limit")
                                if item.name.startswith("agent-metadata") and item.name.endswith(".yaml"):
                                    _require(not item.is_symlink() and item.is_file(follow_symlinks=False),
                                             "symlink-foundry-metadata")
                                    foundry.add(directory.relative_to(root).as_posix())
                    elif (entry.name.lower() not in DISCOVERY_EXCLUDED_DIRS and not entry.name.startswith(".")
                          and entry.is_dir(follow_symlinks=False)):
                        pending.append(Path(entry.path))
        except OSError:
            raise AgentOpsValidationError("discovery-unreadable") from None
    azd = _azd_agent_roots(root)
    candidates = set(azd) | foundry | {"."} if azd or foundry else markers | {"."}
    found = []
    for relative in sorted(candidates):
        directory = safe_path(root, relative, exists=False)
        marker = directory / "agentops.yaml"
        if not marker.exists() and not marker.is_symlink():
            continue
        safe_path(directory, "agentops.yaml")
        agent = {"agent_key": "agent-" + sha256(relative.encode())[:20], "root": relative}
        if relative in azd and len(azd[relative]) == 1:
            agent["service"] = azd[relative][0]
        found.append(agent)
    return found


def _timestamp(value):
    try:
        validate_iso8601_timestamp(value, "timestamp")
        return datetime.fromisoformat(value[:-1] + "+00:00" if value[-1:] in {"Z", "z"} else value)
    except (ValueError, TypeError):
        raise AgentOpsValidationError("invalid-timestamp") from None


def _fresh(value, now, hours):
    stamp = _timestamp(value)
    _require(stamp <= now, "future-evidence")
    return stamp >= now - timedelta(hours=hours)


def _count(value):
    _require(type(value) is int and 0 <= value <= 10000000, "invalid-count")
    return value


def _number(value):
    _require(type(value) in {int, float} and math.isfinite(value), "invalid-number")
    return value


def _rate(actual, numerator, denominator, *, rounded=False):
    _number(actual)
    expected = numerator / denominator if denominator else 1.0
    _require(0 <= actual <= 1 and abs(actual - expected) <= (0.000051 if rounded else 1e-6),
             "count-rate-mismatch")


def _version(data):
    _require(isinstance(data, dict), "invalid-artifact")
    if type(data.get("version")) is not int or data["version"] != 1:
        raise AgentOpsValidationError("unsupported-native-version")


def _committed_bytes(repo, relative):
    local = read_bytes(repo, relative, limit=65536)
    committed = _git(repo, "show", f"HEAD:{relative}")
    _require(local == committed, "uncommitted-trust-policy")
    return local


def _verify_local_observation(record, policy, private, now):
    observation = record.get("observation")
    _require(isinstance(observation, dict), "missing-local-observation")
    _require(observation.get("schema") == "threadlight-agentops-observation/v1",
             "unsupported-observation-version")
    _require(observation.get("producer_sha256") == sha256(
        read_bytes(Path(__file__).parent, Path(__file__).name)), "unsupported-observer-version")
    unsigned = {key: value for key, value in record.items() if key != "observation"}
    _require(observation.get("record_sha256") == canonical_hash(unsigned), "observation-record-mismatch")
    _require(observation.get("scope_sha256") == canonical_hash(policy), "observation-scope-mismatch")
    start, end = _timestamp(observation.get("started_at")), _timestamp(observation.get("finished_at"))
    _require(start <= end <= now and end == _timestamp(record["finished_at"]), "observation-time-mismatch")
    _require(type(observation.get("exit_code")) is int and observation["exit_code"] in {0, 2},
             "unusable-observed-exit")
    before = observation.get("before")
    _require(isinstance(before, dict), "missing-observation-inputs")
    for role in ("dataset", "baseline", "workflow", "doctor_config"):
        if role in record["artifacts"]:
            _require(before.get(role) == record["artifacts"][role]["sha256"],
                     "observation-input-changed")
    operation = observation.get("operation")
    _require(operation in {"eval", "doctor"} and record.get("operation") == operation, "observation-operation-mismatch")
    result = parse_json(private["result"])
    if observation["exit_code"] == 2:
        if operation == "eval":
            _require(_eval_summary(result, policy)["overall_passed"] is False,
                     "observed-exit-artifact-mismatch")
        else:
            _require({"evidence", "history"} <= private.keys(), "missing-observed-doctor")
            evidence = parse_json(private["evidence"])
            _doctor(evidence, private["history"], policy.get("required_doctor_sources"), now, 24)
            _require(any(evidence["doctor"]["counts"][severity] > 0
                         for severity in ("critical", "warning", "info")),
                     "observed-exit-artifact-mismatch")
    if operation == "eval":
        _require(_timestamp(result["started_at"]) >= start
                 and _timestamp(result["finished_at"]) <= end, "unobserved-eval-output")
        for role in ("result", "latest"):
            _require(before.get(role) != record["artifacts"][role]["sha256"], "unchanged-observed-output")
    else:
        previous = observation.get("previous_receipt")
        _require(isinstance(previous, dict), "missing-previous-eval-binding")
        for field in ("repository_commit", "root", "config_sha256", "target_sha256", "environment_sha256"):
            _require(previous.get(field) == record.get(field), "changed-previous-eval-binding")
        for role in ("result", "latest", "dataset", "analysis"):
            _require(previous.get("artifacts", {}).get(role) == record["artifacts"].get(role)
                     and before.get(role) == record["artifacts"][role]["sha256"],
                     "changed-previous-eval-binding")
        _require({"evidence", "history"} <= private.keys(), "missing-observed-doctor")
    for role in ("evidence", "history"):
        if role in private:
            _require(before.get(role) != record["artifacts"][role]["sha256"], "unchanged-observed-output")
            value = (parse_json(private[role])["generated_at"] if role == "evidence" else
                     parse_json(private[role].splitlines()[-1])["timestamp"])
            _require(start <= _timestamp(value) <= end, "unobserved-doctor-output")
    if "redteam" in private:
        if operation == "doctor":
            _require(observation["previous_receipt"].get("artifacts", {}).get("redteam")
                     == record["artifacts"]["redteam"], "unobserved-redteam-output")
        else:
            _require(before.get("redteam") != record["artifacts"]["redteam"]["sha256"],
                     "unobserved-redteam-output")
            _require(start <= _timestamp(parse_json(private["redteam"])["generated_at"]) <= end,
                     "unobserved-redteam-output")


def verify_receipt(repo, root, *, now, hours, receipt_path=".agentops/threadlight/receipt.json",
                   signature_path=".agentops/threadlight/receipt.sig"):
    """Verify observed local provenance, or an explicitly selected owner signature."""
    relative_root = root.relative_to(repo).as_posix()
    policy_path = (PurePosixPath(relative_root) / ".threadlight/agentops-binding.json").as_posix()
    policy = parse_json(_committed_bytes(repo, policy_path))
    _require(isinstance(policy, dict) and type(policy.get("require_signature", False)) is bool,
             "invalid-binding-policy")
    _require(policy.get("schema") == "threadlight-agentops-binding/v1", "unsupported-binding-policy")
    record_raw = read_bytes(root, receipt_path, limit=65536)
    record = parse_json(record_raw)
    _require(isinstance(record, dict), "invalid-receipt")
    signed = policy.get("require_signature") is True or "observation" not in record
    if signed:
        key_path = policy.get("trust_key")
        _require(isinstance(key_path, str), "missing-local-observation")
        key = safe_path(repo, key_path)
        _committed_bytes(repo, key_path)
        read_bytes(root, signature_path, limit=8192)
        code, _, _ = bounded_command(
            ["openssl", "dgst", "-sha256", "-verify", str(key), "-signature",
             str(safe_path(root, signature_path)), str(safe_path(root, receipt_path))],
            cwd=root, max_bytes=8192)
        _require(code == 0, "invalid-receipt-signature")
    _require(record.get("schema") == "threadlight-agentops-run-receipt/v1"
             and record.get("producer") == "observed-run", "unsupported-receipt")
    state = repository_state(repo)
    _require(record.get("config_sha256") == sha256(read_bytes(root, "agentops.yaml")),
             "config-hash-mismatch")
    _require(state["commit"] and not state["dirty"], "dirty-or-unbound-repository")
    _require(record.get("repository_commit") == state["commit"]
             and record.get("root") == relative_root, "receipt-repository-mismatch")
    for name in ("target_sha256", "environment_sha256"):
        _require(isinstance(policy.get(name), str) and _HASH.fullmatch(policy[name]),
                 "unbound-scope")
        _require(record.get(name) == policy[name], "receipt-scope-mismatch")
    for name in ("run_id_sha256", "approval_sha256"):
        _require(isinstance(record.get(name), str) and _HASH.fullmatch(record[name]),
                 "unbound-run")
    start, end = _timestamp(record.get("started_at")), _timestamp(record.get("finished_at"))
    _require(start <= end <= now, "receipt-time-mismatch")
    artifacts = record.get("artifacts")
    _require(isinstance(artifacts, dict) and len(artifacts) <= 16, "invalid-receipt-artifacts")
    _require({"result", "latest", "analysis", "dataset"} <= artifacts.keys(),
             "missing-receipt-artifacts")
    _require(("evidence" in artifacts) == ("history" in artifacts), "incomplete-doctor-artifacts")
    _require(set(artifacts) <= {"result", "latest", "evidence", "history", "analysis",
                              "dataset", "baseline", "redteam", "workflow", "doctor_config"},
             "invalid-receipt-artifacts")
    doctor_config = root / ".agentops/agent.yaml"
    if doctor_config.exists() or doctor_config.is_symlink():
        _require("doctor_config" in artifacts
                 and artifacts["doctor_config"].get("path") == ".agentops/agent.yaml",
                 "unbound-doctor-config")
    private = {}
    for role, ref in artifacts.items():
        _require(isinstance(ref, dict) and set(ref) == {"path", "sha256"}, "invalid-artifact-reference")
        body = read_bytes(repo if role == "workflow" else root, ref["path"])
        _require(ref["sha256"] == sha256(body), "artifact-hash-mismatch")
        private[role] = body
    _require(artifacts["latest"]["path"] == ".agentops/results/latest/results.json", "wrong-native-artifact-path")
    if "evidence" in artifacts:
        _require(artifacts["evidence"]["path"] == ".agentops/release/latest/evidence.json"
                 and artifacts["history"]["path"] == ".agentops/agent/history.jsonl", "wrong-native-artifact-path")
    _require(artifacts["result"]["path"] != artifacts["latest"]["path"]
             and artifacts["result"]["path"].startswith(".agentops/results/")
             and "/latest/" not in artifacts["result"]["path"], "unanchored-latest")
    _require(private["result"] == private["latest"], "latest-hash-mismatch")
    if not signed:
        _verify_local_observation(record, policy, private, now)
    return record, policy, private, sha256(record_raw), _fresh(record["finished_at"], now, hours)


def begin_observation(repo, root, *, operation, run_id_sha256, approval_sha256, artifact_paths):
    """Start an explicit runtime observation; this alone authorizes nothing."""
    repo, root = Path(repo).resolve(), Path(root).resolve()
    _require(root.is_relative_to(repo) and operation in {"eval", "doctor"}, "invalid-observation-scope")
    _require(len(_OBSERVATIONS) < 16, "observation-limit")
    _require(not any(item["root"] == root for item in _OBSERVATIONS.values()), "observation-already-active")
    relative = root.relative_to(repo).as_posix()
    policy = parse_json(_committed_bytes(repo, str(PurePosixPath(relative) / ".threadlight/agentops-binding.json")))
    _require(isinstance(policy, dict) and type(policy.get("require_signature", False)) is bool,
             "invalid-binding-policy")
    _require(policy.get("schema") == "threadlight-agentops-binding/v1", "unsupported-binding-policy")
    _require(policy.get("require_signature") is not True, "existing-owner-signature-required")
    state = repository_state(repo)
    _require(state["commit"] and not state["dirty"], "dirty-or-unbound-repository")
    for digest in (run_id_sha256, approval_sha256, policy.get("target_sha256"), policy.get("environment_sha256")):
        _require(isinstance(digest, str) and _HASH.fullmatch(digest), "unbound-observation")
    _require(isinstance(artifact_paths, dict), "missing-observation-paths")
    _require(set(artifact_paths) <= {"result", "latest", "analysis", "dataset", "evidence", "history",
                                   "redteam", "baseline", "workflow", "doctor_config"}, "invalid-observation-paths")
    started = datetime.now(timezone.utc)
    previous = None
    paths, before = dict(artifact_paths), {}
    if operation == "doctor":
        original = verify_receipt(repo, root, now=started, hours=8760)[0]
        previous = {key: original[key] for key in ("repository_commit", "root", "config_sha256",
            "target_sha256", "environment_sha256", "artifacts", "started_at")}
        for role in ("result", "latest", "analysis", "dataset"):
            paths.setdefault(role, previous["artifacts"][role]["path"])
            _require(paths[role] == previous["artifacts"][role]["path"], "changed-previous-eval-binding")
        for role in ("baseline", "redteam", "workflow"):
            if role in previous["artifacts"]:
                paths.setdefault(role, previous["artifacts"][role]["path"])
    _require({"latest", "analysis", "dataset"} <= paths.keys(), "missing-observation-paths")
    if (root / ".agentops/agent.yaml").exists():
        paths["doctor_config"] = ".agentops/agent.yaml"
    for role, name in paths.items():
        base = repo if role == "workflow" else root
        path = safe_path(base, name, exists=False)
        before[role] = sha256(read_bytes(base, name)) if path.exists() else None
    for role in ("analysis", "dataset"):
        if role == "dataset":
            _require(before[role] is not None, "missing-observation-dataset")
    candidates = _snapshot_result_candidates(root) if operation == "eval" and "result" not in paths else {}
    token = object()
    _OBSERVATIONS[token] = {
        "repo": repo, "root": root, "state": state, "policy": policy, "paths": paths,
        "started": started, "before": before, "previous": previous,
        "result_candidates": candidates,
        "record": {"schema": "threadlight-agentops-run-receipt/v1", "producer": "observed-run",
            "operation": operation, "repository_commit": state["commit"], "root": relative,
            "config_sha256": sha256(read_bytes(root, "agentops.yaml", limit=65536)),
            "target_sha256": policy["target_sha256"], "environment_sha256": policy["environment_sha256"],
            "package_version": NATIVE_VERSION, "upstream_sha": UPSTREAM_SHA,
            "run_id_sha256": run_id_sha256, "approval_sha256": approval_sha256},
    }
    return token


def cancel_observation(token):
    _OBSERVATIONS.pop(token, None)


def _snapshot_result_candidates(root):
    directory = safe_path(root, ".agentops/results", exists=False)
    if not directory.exists():
        return {}
    _require(directory.is_dir(), "unsafe-results-directory")
    snapshots, scanned, total = {}, 0, 0
    with os.scandir(directory) as entries:
        for entry in entries:
            scanned += 1
            _require(scanned <= 4096, "result-discovery-limit")
            if entry.name == "latest":
                continue
            _require(not entry.is_symlink(), "symlink-result-candidate")
            if not entry.is_dir(follow_symlinks=False):
                continue
            relative = f".agentops/results/{entry.name}/results.json"
            path = safe_path(root, relative, exists=False)
            if not path.is_file():
                continue
            total += path.stat().st_size
            _require(total <= 32 * 1024 * 1024, "result-discovery-limit")
            snapshots[relative] = sha256(read_bytes(root, relative))
    return snapshots


def _observed_result_path(root, latest, started, previous):
    """Find a unique recent native run mirror, never invent a timestamped path."""
    data = parse_json(latest)
    _version(data)
    _require(_timestamp(data.get("started_at")) >= started, "unobserved-eval-output")
    _require(sha256(latest) not in previous.values(), "unchanged-observed-output")
    directory = safe_path(root, ".agentops/results", exists=False)
    _require(directory.is_dir(), "missing-observed-result")
    found, scanned, total = [], 0, 0
    with os.scandir(directory) as entries:
        for entry in entries:
            scanned += 1
            _require(scanned <= 4096, "result-discovery-limit")
            if entry.name == "latest" or not entry.is_dir(follow_symlinks=False):
                continue
            relative = f".agentops/results/{entry.name}/results.json"
            path = safe_path(root, relative, exists=False)
            if not path.is_file():
                continue
            metadata = path.stat()
            if metadata.st_size != len(latest) or metadata.st_mtime < started.timestamp() - 1:
                continue
            total += metadata.st_size
            _require(total <= 32 * 1024 * 1024, "result-discovery-limit")
            if read_bytes(root, relative) == latest:
                found.append(relative)
    _require(len(found) == 1, "unanchored-observed-latest")
    return found[0]


def finish_observation(token, *, exit_code):
    """Collect new outputs after a real bounded invocation; never refresh old timestamps."""
    capture = _OBSERVATIONS.pop(token, None)
    _require(capture is not None, "inactive-observation")
    _require(type(exit_code) is int and exit_code in {0, 2}, "unusable-observed-exit")
    repo, root, record = capture["repo"], capture["root"], capture["record"]
    end = datetime.now(timezone.utc)
    _require(repository_state(repo) == capture["state"], "observation-source-changed")
    _require(sha256(read_bytes(root, "agentops.yaml", limit=65536)) == record["config_sha256"],
             "observation-config-changed")
    current_policy = parse_json(_committed_bytes(repo, str(
        PurePosixPath(record["root"]) / ".threadlight/agentops-binding.json")))
    _require(current_policy == capture["policy"], "observation-scope-changed")
    if "result" not in capture["paths"]:
        latest = read_bytes(root, capture["paths"]["latest"])
        selected = _observed_result_path(root, latest, capture["started"], capture["result_candidates"])
        capture["paths"]["result"] = selected
        capture["before"]["result"] = capture["result_candidates"].get(selected)
    private, refs = {}, {}
    for role, name in capture["paths"].items():
        base = repo if role == "workflow" else root
        if not safe_path(base, name, exists=False).exists() and role in {"evidence", "history", "redteam"}:
            continue
        body = read_bytes(base, name)
        private[role] = body
        refs[role] = {"path": name, "sha256": sha256(body)}
    if record["operation"] == "eval" and not all(
            role in refs and capture["before"].get(role) != refs[role]["sha256"] for role in ("evidence", "history")):
        for role in ("evidence", "history"):
            refs.pop(role, None)
            private.pop(role, None)
    if "redteam" in refs and (
            record["operation"] == "eval" and capture["before"].get("redteam") == refs["redteam"]["sha256"]
            or record["operation"] == "doctor" and
            capture["previous"].get("artifacts", {}).get("redteam") != refs["redteam"]):
        refs.pop("redteam")
        private.pop("redteam")
    result = parse_json(private["result"])
    _eval_summary(result, capture["policy"])
    _require(canonical_hash(result["target"]) == record["target_sha256"], "native-target-mismatch")
    record.update(started_at=(capture["previous"]["started_at"] if capture["previous"]
                              else capture["started"].isoformat()),
                  finished_at=end.isoformat(), artifacts=refs)
    observation = {"schema": "threadlight-agentops-observation/v1",
        "producer_sha256": sha256(read_bytes(Path(__file__).parent, Path(__file__).name)),
        "operation": record["operation"], "started_at": capture["started"].isoformat(),
        "finished_at": end.isoformat(), "exit_code": exit_code,
        "scope_sha256": canonical_hash(capture["policy"]), "before": capture["before"],
        "previous_receipt": capture["previous"], "record_sha256": canonical_hash(record)}
    record["observation"] = observation
    _verify_local_observation(record, capture["policy"], private, end)
    path = safe_path(root, ".agentops/threadlight/receipt.json", exists=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        old = read_bytes(root, ".agentops/threadlight/receipt.json", limit=65536)
        archive = safe_path(root, f".agentops/threadlight/receipts/{sha256(old)}.json", exists=False)
        archive.parent.mkdir(parents=True, exist_ok=True)
        if not archive.exists():
            with os.fdopen(os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
                stream.write(old)
    encoded = json.dumps(record, sort_keys=True, indent=2, allow_nan=False).encode()
    _require(len(encoded) <= 65536, "observation-too-large")
    staged = path.with_name("receipt.pending")
    try:
        with os.fdopen(os.open(staged, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)
    verify_receipt(repo, root, now=end, hours=24)
    return record


def _eval_summary(data, policy=None):
    _version(data)
    _require({"started_at", "finished_at", "duration_seconds", "target", "dataset_path",
              "summary", "rows", "thresholds", "aggregate_metrics", "evaluators", "config"} <= data.keys())
    _require(set(data) <= {"version", "started_at", "finished_at", "duration_seconds", "target",
        "dataset_path", "summary", "rows", "thresholds", "aggregate_metrics", "evaluators",
        "config", "comparison"}, "unexpected-native-field")
    _require(_timestamp(data["started_at"]) <= _timestamp(data["finished_at"]), "eval-time-mismatch")
    _require(_number(data["duration_seconds"]) >= 0)
    target = data["target"]
    _require(isinstance(target, dict) and isinstance(target.get("raw"), str)
             and target.get("kind") in {"foundry_prompt", "foundry_hosted", "http_json",
                                        "model_deployment", "model_direct"})
    rows, summary, thresholds = data["rows"], data["summary"], data["thresholds"]
    _require(isinstance(rows, list) and len(rows) <= 10000 and isinstance(summary, dict))
    _require(isinstance(thresholds, list) and len(thresholds) <= 128)
    _require(isinstance(data["evaluators"], list) and bool(data["evaluators"]), "missing-evaluators")
    total = _count(summary.get("items_total"))
    passed = _count(summary.get("items_passed_all"))
    _require(total == len(rows) and passed <= total, "row-count-mismatch")
    successful = 0
    for row in rows:
        _require(isinstance(row, dict) and isinstance(row.get("metrics"), list))
        metrics = row["metrics"]
        _require(all(isinstance(metric, dict) for metric in metrics))
        successful += int(not row.get("error") and all(not metric.get("error") for metric in metrics))
        for metric in metrics:
            if metric.get("value") is not None:
                _number(metric["value"])
    _require(len({row.get("row_index") for row in rows}) == total
             and all(type(row.get("row_index")) is int and row["row_index"] >= 0 for row in rows),
             "row-index-mismatch")
    _require(successful == passed, "row-error-count-mismatch")
    _rate(summary.get("items_pass_rate"), passed, total)
    _require(_count(summary.get("thresholds_total")) == len(thresholds), "threshold-count-mismatch")
    normalized = []
    for threshold in thresholds:
        _require(isinstance(threshold, dict) and type(threshold.get("passed")) is bool)
        _require(threshold.get("metric") in METRICS, "unreviewed-metric")
        criteria = threshold.get("criteria")
        expected_raw, actual_raw = threshold.get("expected"), threshold.get("actual")
        _require(criteria in {">=", "<=", ">", "<", "==", "true", "false"}, "unreviewed-threshold")
        _require(isinstance(expected_raw, str) and isinstance(actual_raw, str))
        metric_value = data["aggregate_metrics"].get(threshold["metric"])
        if metric_value is not None:
            _number(metric_value)
        if criteria in {"true", "false"}:
            _require(expected_raw == criteria)
            expected = 1 if criteria == "true" else 0
            actual = None if metric_value is None else int(metric_value == 1.0)
            _require(actual_raw == ("missing" if actual is None else "true" if actual else "false"),
                     "threshold-metric-mismatch")
            valid = actual is not None and actual == expected
        else:
            _require(expected_raw.startswith(criteria)
                     and re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?",
                                      expected_raw[len(criteria):]), "unreviewed-threshold")
            expected = _number(float(expected_raw[len(criteria):]))
            actual = metric_value
            _require(actual_raw == ("missing" if actual is None else f"{actual:g}"), "threshold-metric-mismatch")
            valid = actual is not None and {
                ">=": lambda: actual >= expected, "<=": lambda: actual <= expected,
                ">": lambda: actual > expected, "<": lambda: actual < expected,
                "==": lambda: actual == expected,
            }[criteria]()
        _require(threshold["passed"] == valid, "threshold-outcome-mismatch")
        normalized.append({"metric": threshold["metric"], "criteria": criteria,
                           "expected": expected, "actual": actual, "passed": valid})
    threshold_passed = sum(item["passed"] for item in normalized)
    _require(_count(summary.get("thresholds_passed")) == threshold_passed, "threshold-count-mismatch")
    _rate(summary.get("threshold_pass_rate"), threshold_passed, len(thresholds))
    _require(type(summary.get("overall_passed")) is bool
             and summary["overall_passed"] == bool(total and passed and threshold_passed == len(thresholds)),
             "overall-count-mismatch")
    aggregate = data["aggregate_metrics"]
    _require(isinstance(aggregate, dict) and len(aggregate) <= 128)
    safe_metrics = {key: _number(value) for key, value in aggregate.items() if key in METRICS}
    _require(bool(normalized) and bool(safe_metrics), "missing-eval-policy")
    for item in normalized:
        _require(item["metric"] in safe_metrics or item["actual"] is None, "threshold-metric-mismatch")
    if policy is not None:
        declared = [{k: item[k] for k in ("metric", "criteria", "expected")} for item in normalized]
        _require(declared == policy.get("thresholds"), "eval-policy-mismatch")
    clean = {key: summary[key] for key in ("items_total", "items_passed_all", "items_pass_rate",
             "thresholds_total", "thresholds_passed", "threshold_pass_rate", "overall_passed")}
    clean.update(finished_at=data["finished_at"], aggregate_metrics=safe_metrics, thresholds=normalized,
                 comparison={"status": "not-verified", "regressions": 0})
    return clean


def _redteam_summary(data):
    _require(isinstance(data, dict) and "version" not in data, "unsupported-native-version")
    total, successful = _count(data.get("total_attempts")), _count(data.get("successful_attacks"))
    _require(total > 0 and successful <= total, "empty-or-invalid-scan")
    _require(_count(data.get("num_objectives")) > 0)
    _rate(data.get("attack_success_rate"), successful, total, rounded=True)
    _timestamp(data.get("generated_at"))
    _require(isinstance(data.get("target_fingerprint"), str)
             and _HASH.fullmatch(data["target_fingerprint"]), "unbound-redteam")
    _require(isinstance(data.get("target"), dict) and bool(data["target"]), "unbound-redteam")
    categories, strategies = data.get("risk_categories"), data.get("attack_strategies")
    _require(isinstance(categories, list) and bool(categories)
             and all(isinstance(x, str) and x in CATEGORIES for x in categories), "unreviewed-category")
    _require(len(set(categories)) == len(categories), "duplicate-category")
    _require(isinstance(strategies, list) and bool(strategies)
             and all(isinstance(x, str) and x in STRATEGIES for x in strategies), "unreviewed-strategy")
    buckets = data.get("per_category")
    _require(isinstance(buckets, dict) and set(buckets) == set(categories), "missing-category-coverage")
    normalized = {}
    for category, bucket in buckets.items():
        _require(isinstance(bucket, dict))
        count, success = _count(bucket.get("total")), _count(bucket.get("successful"))
        _require(count > 0 and success <= count, "missing-category-coverage")
        _rate(bucket.get("attack_success_rate"), success, count, rounded=True)
        normalized[category] = {key: bucket[key] for key in ("total", "successful", "attack_success_rate")}
    _require(sum(x["total"] for x in normalized.values()) == total
             and sum(x["successful"] for x in normalized.values()) == successful, "category-count-mismatch")
    by_strategy = data.get("per_strategy")
    _require(isinstance(by_strategy, dict) and set(by_strategy) == set(strategies), "missing-strategy-coverage")
    for bucket in by_strategy.values():
        _require(isinstance(bucket, dict))
        count, success = _count(bucket.get("total")), _count(bucket.get("successful"))
        _require(count > 0 and success <= count, "missing-strategy-coverage")
        _rate(bucket.get("attack_success_rate"), success, count, rounded=True)
    _require(sum(x["total"] for x in by_strategy.values()) == total
             and sum(x["successful"] for x in by_strategy.values()) == successful, "strategy-count-mismatch")
    threshold = data.get("fail_threshold")
    _require(threshold is not None and 0 <= _number(threshold) <= 1, "missing-redteam-policy")
    fingerprint = canonical_hash({"target": data["target"],
        "risk_categories": sorted({x.strip().lower() for x in categories}),
        "attack_strategies": sorted({x.strip().lower() for x in strategies}),
        "num_objectives": data["num_objectives"], "fail_threshold": round(float(threshold), 6)})
    _require(data["target_fingerprint"] == fingerprint, "redteam-fingerprint-mismatch")
    _require(type(data.get("has_violations")) is bool
             and data["has_violations"] == (data["attack_success_rate"] > threshold), "redteam-outcome-mismatch")
    return {key: data[key] for key in ("total_attempts", "successful_attacks",
            "attack_success_rate", "num_objectives", "risk_categories", "attack_strategies",
            "fail_threshold", "generated_at")} | {"per_category": normalized,
            "core_category_coverage": CORE_CATEGORIES <= set(normalized)}


def _comparison_summary(current, baseline_raw, record, policy, now, hours, *, root):
    baseline = parse_json(baseline_raw)
    _eval_summary(baseline)
    _require(sha256(baseline_raw) == policy.get("baseline_sha256"), "baseline-policy-mismatch")
    _require(record["artifacts"]["dataset"]["sha256"] == policy.get("baseline_dataset_sha256"),
             "baseline-dataset-mismatch")
    _require(canonical_hash(baseline["target"]) == policy.get("baseline_target_sha256"),
             "baseline-target-mismatch")
    comparison = current["comparison"]
    _require(isinstance(comparison, dict), "invalid-comparison")
    baseline_ref = record["artifacts"]["baseline"]["path"]
    reported_path = comparison.get("baseline_path")
    _require(isinstance(reported_path, str) and
             reported_path in {baseline_ref, str(safe_path(root, baseline_ref))},
             "comparison-baseline-mismatch")
    _require(comparison.get("baseline_started_at") == baseline["started_at"]
             and comparison.get("baseline_overall_passed") == baseline["summary"]["overall_passed"],
             "comparison-baseline-mismatch")
    metrics = comparison.get("metrics")
    _require(isinstance(metrics, list) and len(metrics) <= 128)
    names = set(current["aggregate_metrics"]) | set(baseline["aggregate_metrics"])
    _require(len(metrics) == len(names) and all(isinstance(item, dict) for item in metrics)
             and {item.get("metric") for item in metrics} == names, "comparison-metric-mismatch")
    regressions = 0
    for metric in metrics:
        key = metric["metric"]
        actual, old = current["aggregate_metrics"].get(key), baseline["aggregate_metrics"].get(key)
        expected_delta = actual - old if actual is not None and old is not None else None
        expected_direction = ("unchanged" if actual is None or old is None or actual == old
                              else "improved" if actual > old else "regressed")
        _require(metric.get("current") == actual and metric.get("baseline") == old
                 and metric.get("delta") == expected_delta and metric.get("direction") == expected_direction,
                 "comparison-delta-mismatch")
        regressions += int(expected_direction == "regressed")
    rows = comparison.get("rows")
    _require(isinstance(rows, list) and len(rows) == len(current["rows"])
             and all(isinstance(row, dict) for row in rows), "comparison-row-mismatch")
    previous = {row["row_index"]: row for row in baseline["rows"]}
    reported = {row.get("row_index"): row for row in rows}
    _require(len(reported) == len(rows), "comparison-row-mismatch")
    # Mirror the tagged comparison algorithm; these directions are descriptive,
    # not organizational policy (the native metric direction always treats higher as better).
    def passed(row):
        return row.get("error") is None and all(
            metric.get("value") is not None or metric.get("error") is None for metric in row["metrics"])
    for row in current["rows"]:
        old = previous.get(row["row_index"])
        current_passed, baseline_passed = passed(row), passed(old) if old is not None else None
        direction = ("new" if old is None else "improved" if current_passed and not baseline_passed
                     else "regressed" if baseline_passed and not current_passed else "unchanged")
        _require(reported.get(row["row_index"]) == {"row_index": row["row_index"],
            "current_passed": current_passed, "baseline_passed": baseline_passed, "direction": direction},
            "comparison-row-mismatch")
        regressions += int(direction == "regressed")
    fresh = _fresh(baseline["finished_at"], now, hours)
    return {"status": "verified" if fresh else "not-verified", "regressions": regressions}


def _release_shape(evidence):
    _version(evidence)
    _require(evidence.get("status") in {"ready", "ready_with_warnings", "blocked"})
    _timestamp(evidence.get("generated_at"))
    _require(isinstance(evidence.get("workspace"), str))
    for key in ("checks", "blockers", "warnings", "ready", "links"):
        _require(isinstance(evidence.get(key), list))
    for key in ("blockers", "warnings", "ready"):
        _require(all(isinstance(item, str) for item in evidence[key]))
    _require(bool(evidence["checks"]), "empty-release-checks")
    for item in evidence["checks"]:
        _require(isinstance(item, dict) and item.get("status") in {"ready", "warning", "blocked", "unknown"})
        _require(isinstance(item.get("name"), str) and isinstance(item.get("summary"), str))


def _doctor(evidence, history, required_sources, now, hours):
    _release_shape(evidence)
    doctor = evidence.get("doctor")
    _require(isinstance(doctor, dict) and isinstance(doctor.get("counts"), dict), "missing-doctor")
    counts = {key: _count(doctor["counts"].get(key)) for key in ("critical", "warning", "info")}
    _require(_count(doctor.get("findings_total")) == sum(counts.values()), "doctor-count-mismatch")
    lines = history.splitlines()
    _require(bool(lines) and len(lines) <= 10000, "invalid-doctor-history")
    records = [parse_json(line) for line in lines if line.strip()]
    _require(bool(records) and all(isinstance(item, dict) for item in records), "invalid-doctor-history")
    record = records[-1]
    _require(record.get("findings_by_severity") == counts
             and record.get("findings_total") == doctor["findings_total"]
             and record.get("max_severity") == doctor.get("max_severity"), "doctor-history-mismatch")
    raw_findings = record.get("findings")
    _require(isinstance(raw_findings, list) and len(raw_findings) == doctor["findings_total"], "doctor-finding-count-mismatch")
    observed = {key: 0 for key in counts}
    for finding in raw_findings:
        _require(isinstance(finding, dict) and finding.get("severity") in counts, "invalid-doctor-finding")
        observed[finding["severity"]] += 1
    _require(observed == counts, "doctor-finding-count-mismatch")
    maximum = next((key for key in ("critical", "warning", "info") if counts[key]), None)
    _require(doctor.get("max_severity") == maximum, "doctor-severity-mismatch")
    generated, captured = _timestamp(evidence.get("generated_at")), _timestamp(record.get("timestamp"))
    _require(abs((generated - captured).total_seconds()) <= 300, "doctor-history-time-mismatch")
    _require(isinstance(required_sources, list) and bool(required_sources), "missing-required-sources")
    enabled = record.get("sources_enabled")
    _require(isinstance(enabled, (list, dict)), "missing-doctor-sources")
    available = doctor.get("status") == "ok"
    for source in required_sources:
        _require(source in {"foundry", "monitor"}, "unknown-doctor-source")
        section = evidence.get("monitoring" if source == "monitor" else source)
        on = source in enabled if isinstance(enabled, list) else enabled.get(source) is True
        available &= on and isinstance(section, dict) and section.get("status") in {"ok", "ready"}
        if source == "monitor" and isinstance(section, dict):
            diagnostics = section.get("diagnostics")
            available &= isinstance(diagnostics, dict) and all(
                diagnostics.get(field) == "ok" for field in ("status", "safety_status", "token_status", "rate_limit_status"))
    fresh = _fresh(evidence["generated_at"], now, hours) and _fresh(record["timestamp"], now, hours)
    blocked = bool(evidence["status"] == "blocked" or evidence["blockers"] or counts["critical"]
                   or any(item["status"] == "blocked" for item in evidence["checks"]))
    warning = bool(evidence["warnings"] or counts["warning"]
                   or any(item["status"] in {"warning", "unknown"} for item in evidence["checks"]))
    _require(not blocked or evidence["status"] == "blocked", "release-status-mismatch")
    return fresh, bool(available), blocked, warning


def _finding(code, *, owner="agentops", severity="should-fix"):
    return {"code": code, "owner": owner, "severity": severity}


def _source_blockers(evidence, domains, *, fresh, doctor_findings=()):
    """Assign only specifically represented native blockers to a domain owner."""
    remaining = list(evidence["blockers"])
    critical = [finding for finding in doctor_findings if finding["severity"] == "critical"]
    eval_code = _finding("AOPS-EVAL-QUALITY", owner="evals", severity="must-fix")
    eval_critical = (
        fresh and evidence["doctor"]["counts"]["critical"] == 1 and len(critical) == 1
        and critical[0].get("id") == "opex.release.latest_eval_failed"
        and domains["evals"]["status"] == "verified" and domains["evals"]["verdict"] == "fail"
        and domains["evals"]["summary"].get("overall_passed") is False
        and eval_code in domains["evals"]["blockers"]
    )
    unmatched = evidence["doctor"]["counts"]["critical"] > 0 and not eval_critical
    critical_represented = evidence["doctor"]["counts"]["critical"] == 0
    mapped = []
    for check in evidence["checks"]:
        if check["status"] != "blocked":
            continue
        owner = None
        if (check["name"] == "Latest eval gate"
                and check["summary"] == "Latest evaluation failed one or more thresholds."
                and domains["evals"]["summary"].get("overall_passed") is False):
            owner = "evals"
            code = "AOPS-EVAL-QUALITY"
        elif (check["name"] == "Doctor readiness" and eval_critical
              and check["summary"] == "Doctor reported critical findings."
              and check.get("evidence") == evidence["doctor"]):
            owner = "evals"
            code = "AOPS-EVAL-QUALITY"
        elif check["name"] == "Red team readiness":
            detail = check.get("evidence")
            summary = domains["redteam"]["summary"]
            if (isinstance(detail, dict) and summary
                    and detail.get("state") == "threshold_breach"
                    and detail.get("target_verified") is True
                    and detail.get("attack_success_rate") == summary["attack_success_rate"]
                    and detail.get("threshold") == summary["fail_threshold"]
                    and summary["attack_success_rate"] > summary["fail_threshold"]):
                owner = "redteam"
                code = "AOPS-REDTEAM-QUALITY"
        represented = (
            fresh and owner is not None and domains[owner]["status"] == "verified"
            and domains[owner]["verdict"] == "fail"
            and _finding(code, owner=owner, severity="must-fix") in domains[owner]["blockers"]
            and check["summary"] in remaining
        )
        if represented:
            # Remove one matching occurrence only; duplicate/unmatched native
            # blockers must not disappear because one recognized check exists.
            remaining.remove(check["summary"])
            if check["name"] == "Doctor readiness" and eval_critical:
                critical_represented = True
            finding = _finding(code, owner=owner, severity="must-fix")
            if finding not in mapped:
                mapped.append(finding)
        else:
            unmatched = True
    unmatched |= bool(remaining)
    unmatched |= evidence["status"] == "blocked" and not mapped
    unmatched |= not critical_represented
    return mapped, unmatched


def _empty_domain():
    return {"status": "not-verified", "verdict": "unknown", "summary": {}, "blockers": []}


def _agent(repo, identity, state, now, hours):
    root = repo if identity["root"] == "." else safe_path(repo, identity["root"], exists=False)
    capabilities = {key: {"status": "not-verified"} for key in CAPABILITIES}
    result = dict(identity, verdict="partial", capabilities=capabilities,
                  domains={key: _empty_domain() for key in sorted(DOMAINS)}, findings=[],
                  provenance={"commit": state["commit"], "config_sha256": None,
                    "target_sha256": None, "environment_sha256": None,
                    "receipt_sha256": None, "source_oldest_at": None, "artifacts": {}})
    findings = result["findings"]
    def consume(name, *, limit=MAX_ARTIFACT_BYTES):
        body = read_bytes(root, name, limit=limit)
        result["provenance"]["artifacts"][(PurePosixPath(identity["root"]) / name).as_posix()] = sha256(body)
        return body
    try:
        result["provenance"]["config_sha256"] = sha256(read_bytes(root, "agentops.yaml", limit=65536))
        requirements = consume("requirements.txt", limit=65536).decode() if (root / "requirements.txt").exists() else ""
        if re.search(r"(?m)^\s*agentops-accelerator\s*==\s*0\.14\.0\s*(?:#.*)?$", requirements):
            capabilities["pin"]["status"] = "verified"
        else:
            findings.append(_finding("AOPS-PIN-UNVERIFIED"))
        # Inspect available native bytes even without binding; malformed evidence is not absent evidence.
        for name, domain in ((".agentops/results/latest/results.json", "evals"),
                              (".agentops/redteam/latest.json", "redteam")):
            if (root / name).exists() or (root / name).is_symlink():
                raw = parse_json(consume(name))
                try:
                    _eval_summary(raw) if domain == "evals" else _redteam_summary(raw)
                except AgentOpsValidationError as error:
                    if str(error).startswith(("unsupported-", "unreviewed-", "missing-eval-policy",
                                              "missing-redteam-policy", "empty-or-invalid-scan")):
                        findings.append(_finding("AOPS-DOMAIN-UNVERIFIED", owner=domain))
                    else:
                        raise
        evidence_path = ".agentops/release/latest/evidence.json"
        if (root / evidence_path).exists() or (root / evidence_path).is_symlink():
            try:
                _release_shape(parse_json(consume(evidence_path)))
            except AgentOpsValidationError as error:
                if str(error) == "unsupported-native-version":
                    findings.append(_finding("AOPS-RELEASE-UNVERIFIED"))
                else:
                    raise
        record_path = root / ".agentops/threadlight/receipt.json"
        if not record_path.exists():
            findings.append(_finding("AOPS-BINDING-UNVERIFIED"))
            return result
        record, policy, private, receipt_hash, receipt_fresh = verify_receipt(repo, root, now=now, hours=hours)
        provenance = result["provenance"]
        provenance.update(target_sha256=record["target_sha256"],
                          environment_sha256=record["environment_sha256"], receipt_sha256=receipt_hash)
        for role, ref in record["artifacts"].items():
            relative = ref["path"] if role == "workflow" else (PurePosixPath(identity["root"]) / ref["path"]).as_posix()
            provenance["artifacts"][relative] = ref["sha256"]
        receipt_files = [".agentops/threadlight/receipt.json", ".threadlight/agentops-binding.json"]
        if policy.get("require_signature") is True or "observation" not in record:
            receipt_files.append(".agentops/threadlight/receipt.sig")
            provenance["artifacts"][policy["trust_key"]] = sha256(read_bytes(repo, policy["trust_key"]))
        for name in receipt_files:
            provenance["artifacts"][(PurePosixPath(identity["root"]) / name).as_posix()] = sha256(read_bytes(root, name))
        capabilities["binding"]["status"] = "verified"
        capabilities["integrity"]["status"] = "verified"
        if record.get("package_version") != NATIVE_VERSION or record.get("upstream_sha") != UPSTREAM_SHA:
            capabilities["pin"]["status"] = "not-verified"
            findings.append(_finding("AOPS-PIN-UNVERIFIED"))
            return result
        analysis = parse_json(private["analysis"])
        _version(analysis)
        _require({"directory", "classification", "config_status", "dataset_status",
            "target_kind", "scenario_hint", "complexity", "requires_copilot_adaptation",
            "copilot_skills_installed", "signals", "warnings", "recommended_skills",
            "recommended_commands", "next_steps"} <= analysis.keys(), "incomplete-native-analysis")
        _require(analysis.get("directory") in {str(root), "."}, "analysis-root-mismatch")
        config_ready = analysis.get("config_status") == "ready" and analysis.get("dataset_status") == "ready"
        config_ready &= analysis.get("requires_copilot_adaptation") is False
        if config_ready:
            capabilities["config"]["status"] = "verified"
        else:
            findings.append(_finding("AOPS-CONFIG-UNVERIFIED"))
        native = parse_json(private["result"])
        clean = _eval_summary(native, policy)
        dataset_lines = private["dataset"].splitlines()
        _require(len(dataset_lines) <= 10000, "dataset-too-large")
        dataset_rows = [parse_json(line) for line in dataset_lines if line.strip()]
        _require(len(dataset_rows) == clean["items_total"] and
                 all(isinstance(item, dict) for item in dataset_rows), "dataset-row-count-mismatch")
        _require(analysis.get("target_kind") == native["target"]["kind"], "analysis-target-mismatch")
        _require(canonical_hash(native["target"]) == record["target_sha256"], "native-target-mismatch")
        dataset_ref = record["artifacts"]["dataset"]["path"]
        _require(native["dataset_path"] in {dataset_ref, str(safe_path(root, dataset_ref))},
                 "dataset-path-mismatch")
        _require(_timestamp(record["started_at"]) <= _timestamp(native["started_at"])
                 <= _timestamp(native["finished_at"]) <= _timestamp(record["finished_at"]), "run-time-mismatch")
        fresh = receipt_fresh and _fresh(native["finished_at"], now, hours)
        status = "verified" if fresh and config_ready and capabilities["pin"]["status"] == "verified" else ("stale" if not fresh else "not-verified")
        eval_domain = result["domains"]["evals"]
        eval_domain.update(status=status, summary=clean)
        if status == "verified":
            good = clean["overall_passed"] and clean["items_total"] == clean["items_passed_all"] and all(
                row["metrics"] and all(metric.get("value") is not None and not metric.get("error")
                                      for metric in row["metrics"]) for row in native["rows"])
            eval_domain["verdict"] = "pass" if good else "fail"
            if not good:
                eval_domain["blockers"].append(_finding("AOPS-EVAL-QUALITY", owner="evals", severity="must-fix"))
        if native.get("comparison") is not None:
            if "baseline" in private and status == "verified":
                clean["comparison"] = _comparison_summary(
                    native, private["baseline"], record, policy, now, hours, root=root)
            if clean["comparison"]["status"] != "verified":
                findings.append(_finding("AOPS-COMPARISON-UNVERIFIED", owner="evals"))
        evidence, blocked, doctor_fresh = None, False, False
        provenance["source_oldest_at"] = native["finished_at"]
        if "evidence" in private:
            evidence = parse_json(private["evidence"])
            _require(isinstance(evidence, dict) and evidence.get("workspace") == str(root), "release-workspace-mismatch")
            doctor_fresh, available, blocked, warnings = _doctor(evidence, private["history"],
                policy.get("required_doctor_sources"), now, hours)
            provenance["source_oldest_at"] = min(native["finished_at"], evidence["generated_at"],
                                                 record["finished_at"], key=_timestamp)
            _require(evidence.get("target") == native["target"]["raw"], "release-target-mismatch")
            latest = evidence.get("latest_eval")
            _require(isinstance(latest, dict), "missing-release-eval")
            _require(latest.get("passed") == native["summary"]["overall_passed"]
                     and latest.get("started_at") == native["started_at"]
                     and latest.get("items_total") == native["summary"]["items_total"]
                     and latest.get("items_passed_all") == native["summary"]["items_passed_all"]
                     and latest.get("threshold_count") == len(native["thresholds"])
                     and latest.get("target") == native["target"]["raw"], "release-eval-mismatch")
            capabilities["release_consistency"]["status"] = "verified"
            capabilities["doctor_freshness"]["status"] = ("verified" if doctor_fresh and available
                                                           else "stale" if not doctor_fresh else "not-verified")
            if warnings:
                findings.append(_finding("AOPS-DOCTOR-WARNING"))
            govern = result["domains"]["govern"]
            govern.update(status="verified" if doctor_fresh else "stale", verdict="supplementary",
                summary={"native_status": evidence["status"], "runtime_verified": False,
                         "policy_verified": False, "attestation_verified": False})
        if "workflow" in private:
            # Receipt proves the existing run used these exact committed workflow bytes.
            workflow = record["artifacts"]["workflow"]["path"]
            _committed_bytes(repo, workflow)
            _require((workflow.startswith((".github/workflows/", ".azuredevops/pipelines/"))
                      and workflow.endswith((".yml", ".yaml")))
                     or workflow in {"azure-pipelines.yml", "azure-pipelines.yaml"}, "unbound-workflow")
            capabilities["workflow"]["status"] = "verified"
        if "redteam" in private:
            redteam = parse_json(private["redteam"])
            summary = _redteam_summary(redteam)
            _require(redteam["target_fingerprint"] == policy.get("redteam_fingerprint"), "redteam-fingerprint-mismatch")
            _require(summary["fail_threshold"] == policy.get("redteam_fail_threshold"), "redteam-policy-mismatch")
            rt_fresh = receipt_fresh and _fresh(redteam["generated_at"], now, hours)
            rt = result["domains"]["redteam"]
            rt.update(status="verified" if rt_fresh else "stale", summary=summary)
            if rt_fresh:
                good = summary["attack_success_rate"] <= summary["fail_threshold"] and summary["core_category_coverage"]
                rt["verdict"] = "pass" if good else "fail"
                if not good:
                    rt["blockers"].append(_finding("AOPS-REDTEAM-QUALITY", owner="redteam", severity="must-fix"))
        if blocked:
            history_record = parse_json([line for line in private["history"].splitlines() if line.strip()][-1])
            mapped, unmatched = _source_blockers(
                evidence, result["domains"], fresh=doctor_fresh, doctor_findings=history_record["findings"])
            findings.extend(mapped)
            if unmatched:
                findings.append(_finding("AOPS-DOCTOR-BLOCKED", severity="must-fix"))
        if not fresh:
            findings.append(_finding("AOPS-EVIDENCE-STALE"))
    except (AgentOpsValidationError, UnicodeError, KeyError, TypeError, AttributeError) as error:
        code = str(error) if isinstance(error, AgentOpsValidationError) else "invalid-artifact"
        if code.startswith(("unsupported-", "unreviewed-")) or code in {
            "missing-artifact", "repository-unavailable", "dirty-or-unbound-repository",
            "command-unavailable", "missing-local-observation"}:
            findings.append(_finding("AOPS-EVIDENCE-UNVERIFIED"))
        else:
            capabilities["integrity"]["status"] = "invalid"
            findings.append(_finding("AOPS-INTEGRITY-INVALID", severity="must-fix"))
        # A failed cross-check invalidates all previously normalized acceptance.
        for domain in result["domains"].values():
            domain.update(status="invalid" if capabilities["integrity"]["status"] == "invalid" else "not-verified",
                          verdict="unknown", summary={}, blockers=[])
    hard = any(item["severity"] == "must-fix" for item in findings)
    result["verdict"] = ("blocked" if hard else "operational" if
        all(item["status"] == "verified" for item in capabilities.values()) and not findings else "partial")
    return result


def assess_repository(repo: Path, *, now=None, freshness_hours=24, generated_at=None):
    repo = Path(repo).resolve()
    now = now or datetime.now(timezone.utc)
    _require(type(freshness_hours) is int and 1 <= freshness_hours <= 8760, "invalid-freshness")
    identities = discover_opted_in_agents(repo)
    state = repository_state(repo)
    agents = [_agent(repo, item, state, now, freshness_hours) for item in identities]
    findings = [dict(item, agent_key=agent["agent_key"]) for agent in agents for item in agent["findings"]]
    verdict = ("not-applicable" if not agents else "blocked" if
        any(agent["verdict"] == "blocked" for agent in agents) else "operational" if
        all(agent["verdict"] == "operational" for agent in agents) else "partial")
    source_times = [agent["provenance"]["source_oldest_at"] for agent in agents
                    if agent["provenance"]["source_oldest_at"] is not None]
    return build_envelope(schema=SCHEMA, tool_version=TOOL_VERSION,
        generated_at=generated_at or now.isoformat(), valid_for_hours=freshness_hours,
        source_oldest_at=min(source_times, key=_timestamp) if source_times else None,
        status="complete" if verdict in {"operational", "not-applicable"} else "partial",
        findings=findings, payload={"repository": state, "verdict": verdict, "agents": agents,
            "summary": {"agents_total": len(agents),
                        "operational": sum(agent["verdict"] == "operational" for agent in agents),
                        "partial": sum(agent["verdict"] == "partial" for agent in agents),
                        "blocked": sum(agent["verdict"] == "blocked" for agent in agents)}})


def validate_manifest(data, *, repo: Path, now=None) -> dict:
    """Re-derive accepted metadata from current local evidence, not producer imports.

    Exact comparison rejects unknown payload fields, edited verdicts, omitted roots
    and stale/copied claims. No JSON schema validator or native SDK is required.
    """
    now = now or datetime.now(timezone.utc)
    try:
        _require(isinstance(data, dict), "invalid-manifest")
        validate_envelope(data)
        _require(data.get("schema") == SCHEMA and data.get("tool_version") == TOOL_VERSION,
                 "unsupported-manifest-version")
        _require(_fresh(data["generated_at"], now, data["freshness"]["valid_for_hours"]), "stale-manifest")
        expected = assess_repository(repo, now=now, freshness_hours=data["freshness"]["valid_for_hours"],
                                     generated_at=data["generated_at"])
        _require(canonical_hash(data) == canonical_hash(expected), "manifest-evidence-mismatch")
        return data
    except (ValueError, TypeError, KeyError, RecursionError):
        raise AgentOpsValidationError("invalid-or-stale-manifest") from None


def load_manifest(repo: Path, *, now=None) -> dict:
    return validate_manifest(read_json(repo, "specs/agentops-manifest.json", limit=MAX_MANIFEST_BYTES),
                             repo=repo, now=now)


__all__ = ["AgentOpsValidationError", "discover_opted_in_agents", "validate_manifest",
           "load_manifest", "assess_repository", "DOMAINS", "DISCOVERY_EXCLUDED_DIRS",
           "GENERATED_OUTPUTS", "begin_observation", "finish_observation", "cancel_observation"]
