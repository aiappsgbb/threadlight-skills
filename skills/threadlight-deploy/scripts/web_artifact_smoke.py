#!/usr/bin/env python3
"""Compare selected built web files and optionally their literal loopback origin."""
from __future__ import annotations

import argparse
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import posixpath
import stat
import subprocess
import sys
from time import monotonic
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


MIME = {
    ".html": {"text/html"}, ".css": {"text/css"},
    ".js": {"text/javascript", "application/javascript"},
    ".mjs": {"text/javascript", "application/javascript"},
    ".json": {"application/json"}, ".txt": {"text/plain"},
    ".pdf": {"application/pdf"}, ".zip": {"application/zip"},
    ".svg": {"image/svg+xml"}, ".png": {"image/png"},
}
HTTP_PROBE_DEADLINE_SECONDS = 10
IMPORT_PARSE_DEADLINE_SECONDS = 10
PRIVATE_NAMES = {".git", ".threadlight", ".env", "evidence", "tests", "__pycache__"}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def asset_name(value):
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or str(path) != value
            or any(part in (".", "..") for part in path.parts)
            or any(char in value for char in "\\?#%")
            or any(ord(char) < 32 for char in value)):
        raise ValueError(f"Invalid relative asset path: {value!r}")
    return value


def origin_url(value):
    parts = urlsplit(value)
    if (parts.scheme != "http" or parts.hostname not in ("127.0.0.1", "::1")
            or parts.username is not None or parts.password is not None
            or parts.path not in ("", "/") or parts.query or parts.fragment
            or parts.port is None):
        raise ValueError("Origin must be an explicit http://127.0.0.1:port or http://[::1]:port")
    return value.rstrip("/")


def read_asset(root, name, *, portable_read=False):
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"Artifact root must be a real directory: {root}")
    current = root
    for part in PurePosixPath(name).parts:
        mode = current.stat().st_mode
        if portable_read and not mode & stat.S_IXOTH:
            raise ValueError(f"Missing portable non-root directory traversal: {current}")
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Symlink is not a packaged regular asset: {current}")
    mode = current.stat().st_mode
    if not stat.S_ISREG(mode):
        raise ValueError(f"Not a regular asset: {current}")
    if portable_read and not mode & stat.S_IROTH:
        raise ValueError(f"Missing portable non-root file readability: {current}")
    return current.read_bytes()


class ScriptSources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.sources = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            values = dict(attrs)
            if values.get("src"):
                self.sources.append(values["src"])


def module_closure(expected, files):
    """Resolve static/literal dynamic browser imports without executing project JS."""
    pending, visited = list(files), set()
    deadline = monotonic() + IMPORT_PARSE_DEADLINE_SECONDS
    while pending:
        name = pending.pop(0)
        if name in visited:
            continue
        if len(visited) >= 1000:
            raise ValueError("Module graph exceeds 1000 assets")
        visited.add(name)
        suffix = PurePosixPath(name).suffix.lower()
        if suffix not in (".html", ".js", ".mjs"):
            continue
        source = read_asset(expected, name).decode("utf-8")
        if suffix == ".html":
            parser = ScriptSources()
            parser.feed(source)
            dependencies = parser.sources
        else:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise ValueError("Module discovery overall deadline exceeded")
            parsed = subprocess.run(
                ["node",
                 str(Path(__file__).with_name("module_imports.mjs"))],
                input=source, capture_output=True, text=True, timeout=remaining,
            )
            if parsed.returncode:
                raise ValueError(f"Cannot parse module imports: {name}")
            dependencies = json.loads(parsed.stdout)
        for dependency in dependencies:
            if suffix != ".html" and not dependency.startswith(("./", "../", "/")):
                raise ValueError(f"Resolve nonlocal/bare module import before packaging: {name}")
            if "://" in dependency or dependency.startswith("//"):
                raise ValueError(f"External script is outside packaged artifact: {name}")
            resolved = posixpath.normpath(
                dependency.lstrip("/") if dependency.startswith("/")
                else posixpath.join(posixpath.dirname(name), dependency))
            pending.append(asset_name(resolved))
    return sorted(visited)


def private_files(root):
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Packaged application root must be a real directory")
    found = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_symlink():
            raise ValueError(f"Cannot inventory symlink: {relative.as_posix()}")
        if path.is_file() and any(part in PRIVATE_NAMES or part.startswith(".env.")
                                  for part in relative.parts):
            found.append(relative.as_posix())
    return found


def probe_http(origin, assets, downloads):
    errors = []
    # No environment proxy, credentials or redirects: probe only the chosen local server.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    for asset in assets:
        name = asset["path"]
        try:
            request = Request(f"{origin}/{quote(name, safe='/')}",
                              headers={"Accept-Encoding": "identity"})
            with opener.open(request, timeout=5) as response:
                if response.status != 200:
                    raise ValueError(f"Expected HTTP 200, got {response.status}")
                mime = response.headers.get_content_type()
                allowed = MIME.get(PurePosixPath(name).suffix.lower(),
                                   {"application/octet-stream"})
                if mime not in allowed:
                    raise ValueError(f"Unexpected MIME {mime!r}; expected {sorted(allowed)}")
                if PurePosixPath(name).suffix.lower() in (".js", ".mjs") and (
                        response.headers.get("X-Content-Type-Options", "").lower() != "nosniff"):
                    raise ValueError("JavaScript requires X-Content-Type-Options: nosniff")
                content = response.read(asset["bytes"] + 1)
                if (len(content) != asset["bytes"]
                        or hashlib.sha256(content).hexdigest() != asset["sha256"]):
                    raise ValueError("Served bytes differ from built artifact (possibly SPA fallback)")
                if name in downloads and response.headers.get_content_disposition() != "attachment":
                    raise ValueError("Promised attachment lacks Content-Disposition: attachment")
        except (OSError, ValueError, HTTPError, URLError) as exc:
            errors.append(f"http {name}: {exc}")
    return errors


def check(expected, built, *, files, origin=None, downloads=(), image_app=None):
    expected, built = Path(expected).absolute(), Path(built).absolute()
    files = list(dict.fromkeys(asset_name(name) for name in files))
    downloads = set(asset_name(name) for name in downloads)
    if not files or not downloads.issubset(files):
        raise ValueError("Supply assets; downloads must also be listed as assets")
    origin = origin_url(origin) if origin is not None else None
    report = {
        "passed": False, "static": {"status": "failed"},
        "http": {"status": "not-executed", "origin": origin},
        "image_runtime": {"status": "not-executed"},
        "hosted_quality": "unproven", "assets": [], "errors": [],
        "image_filesystem": {"status": "not-executed"},
    }
    try:
        files = module_closure(expected, files)
        for name in private_files(built):
            report["errors"].append(f"Excluded internal file in built web root: {name}")
        if image_app is not None:
            report["image_filesystem"]["status"] = "failed"
            internal = private_files(Path(image_app).absolute())
            report["errors"].extend(f"Excluded internal file in image application tree: {p}" for p in internal)
            report["image_filesystem"]["status"] = "failed" if internal else "passed"
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        report["errors"].append(f"artifact preflight: {exc}")
        return report
    for name in files:
        try:
            source = read_asset(expected, name)
            artifact = read_asset(built, name, portable_read=True)
            if artifact != source:
                raise ValueError(f"Built bytes differ from expected bytes: {name}")
            report["assets"].append({
                "path": name, "sha256": hashlib.sha256(artifact).hexdigest(),
                "bytes": len(artifact),
            })
        except (OSError, ValueError) as exc:
            report["errors"].append(f"static {name}: {exc}")
    if report["errors"]:
        return report
    report["static"]["status"] = "passed"
    if origin:
        try:
            # One worker, no descendants: run() kills and reaps it on timeout or interruption.
            result = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--http-worker"],
                input=json.dumps({"origin": origin, "assets": report["assets"],
                                  "downloads": sorted(downloads)}),
                capture_output=True, text=True, check=True,
                timeout=HTTP_PROBE_DEADLINE_SECONDS,
            )
            report["errors"].extend(json.loads(result.stdout))
        except subprocess.TimeoutExpired:
            report["errors"].append(
                f"http overall probe deadline exceeded ({HTTP_PROBE_DEADLINE_SECONDS}s); "
                "worker killed and reaped")
        except subprocess.CalledProcessError as exc:
            report["errors"].append(f"http worker failed ({exc.returncode}): {exc.stderr.strip()}")
        except (OSError, ValueError) as exc:
            report["errors"].append(f"http worker: {exc}")
        report["http"]["status"] = "failed" if report["errors"] else "passed"
    report["passed"] = not report["errors"]
    return report


def main():
    if sys.argv[1:] == ["--http-worker"]:
        payload = json.load(sys.stdin)
        payload["origin"] = origin_url(payload["origin"])
        for asset in payload["assets"]:
            asset_name(asset["path"])
        print(json.dumps(probe_http(**payload)))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--built", type=Path, required=True)
    parser.add_argument("--file", action="append", required=True, dest="files")
    parser.add_argument("--origin", help="Explicit loopback origin; does not assert image provenance")
    parser.add_argument("--download", action="append", default=[], dest="downloads")
    parser.add_argument("--image-app", type=Path,
                        help="Application tree extracted from actual image; filesystem scan only, not runtime proof")
    args = parser.parse_args()
    try:
        report = check(**vars(args))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
