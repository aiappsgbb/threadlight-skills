"""Install the unmodified OPA binary only after checking the shared release hash."""
import hashlib
import json
from pathlib import Path
import urllib.request


def main():
    pin = json.loads(Path("skills/_shared/governance-upstream-pin.json").read_text())["opa"]
    with urllib.request.urlopen(
        f"https://github.com/open-policy-agent/opa/releases/download/v{pin['version']}/opa_linux_amd64_static",
        timeout=120,
    ) as response:
        binary = response.read()
    if hashlib.sha256(binary).hexdigest() != pin["linux_amd64_static_sha256"]:
        raise ValueError("opa_release_hash_mismatch")
    target = Path("/usr/local/bin/opa")
    target.write_bytes(binary)
    target.chmod(0o755)


if __name__ == "__main__":
    main()
