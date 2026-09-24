"""Materialize the real returns/MCP source closure without Azure calls or credentials."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile


ROOT = Path(__file__).resolve().parents[4]
REFERENCE = Path("skills/threadlight-deploy/references/governance")
DIRECTORIES = (
    "skills/_shared",
    "skills/threadlight-govern/references/control-plane",
    "skills/threadlight-govern/references/gateway",
    "skills/threadlight-govern/references/runtime",
    "skills/threadlight-govern/scripts",
    "skills/threadlight-governed-actions/scripts",
    str(REFERENCE),
)
EFFECT = Path("examples/returns-triage-governed/src/agent/cosmos_effect.py")
SKILL_APPROVAL = Path("skills/threadlight-local-test/references/quickstart/threadlight_quickstart/skill_approval.py")
FILES = (EFFECT, SKILL_APPROVAL)


def materialize(output):
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError("refusing_to_overwrite_preserved_package")
    for relative in (*DIRECTORIES, *FILES):
        source = ROOT / relative
        if not source.exists() or source.is_symlink():
            raise ValueError("real_runtime_source_required")
        if source.is_dir() and any(path.is_symlink() for path in source.rglob("*")):
            raise ValueError("symlinked_runtime_source_unsupported")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".returns-package-", dir=output.parent) as scratch:
        stage = Path(scratch) / "source"
        stage.mkdir()
        for relative in DIRECTORIES:
            shutil.copytree(
                ROOT / relative, stage / relative,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.egg-info", ".pytest_cache"),
            )
        for relative in FILES:
            (stage / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, stage / relative)
        shutil.copy2(ROOT / REFERENCE / "returns-mcp.Dockerfile", stage / "Dockerfile")
        (stage / "README.md").write_text(
            "# Returns through governed MCP\n\n"
            "See the [execution and preservation runbook]"
            "(skills/threadlight-deploy/references/governance/returns-mcp-demo.md).\n"
            "This is a source package, not deployment or live evidence.\n"
        )
        (stage / "agent-dependencies").mkdir()
        shutil.copy2(ROOT / REFERENCE / "pyproject-maf.toml", stage / "agent-dependencies/pyproject.toml")
        files = {
            str(path.relative_to(stage)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(stage.rglob("*")) if path.is_file()
        }
        (stage / "source-package.json").write_text(json.dumps({
            "schema": "threadlight-returns-mcp-source/v1",
            "status": "source-only-not-deployment-proof", "files": files,
        }, indent=2) + "\n")
        stage.rename(output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(materialize(args.output))


if __name__ == "__main__":
    main()
