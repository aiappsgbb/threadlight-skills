"""Synthetic filesystem/HTTP checks, never image-runtime or hosted proof."""
import importlib.util
import json
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
from threading import Event, Thread
from time import monotonic

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts/web_artifact_smoke.py"


@pytest.fixture
def smoke():
    spec = importlib.util.spec_from_file_location("web_artifact_smoke", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def trees(tmp_path):
    expected, built = tmp_path / "expected", tmp_path / "built"
    for root in (expected, built):
        root.mkdir(mode=0o755)
        for name, body in {
            "index.html": b"<script type=module src='/app.mjs'></script>",
            "app.mjs": b"export const version = 1;",
            "data.json": b'{"records":[]}',
            "report.txt": b"synthetic saved draft",
        }.items():
            (root / name).write_bytes(body)
            (root / name).chmod(0o644)
    return expected, built


@contextmanager
def server(root, *, bad_mime=False, fallback=False, redirect=False, attachment=True):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if redirect:
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/never-follow")
                self.end_headers()
                return
            name = self.path.lstrip("/")
            path = root / ("index.html" if fallback else name)
            if not path.is_file():
                self.send_error(404)
                return
            self.send_response(200)
            mime = {".html": "text/html", ".json": "application/json",
                    ".mjs": "text/plain" if bad_mime else "text/javascript",
                    ".txt": "text/plain"}[path.suffix]
            self.send_header("Content-Type", mime)
            if name == "report.txt" and attachment:
                self.send_header("Content-Disposition", 'attachment; filename="report.txt"')
            self.end_headers()
            self.wfile.write(path.read_bytes())

        def log_message(self, *_):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=httpd.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}"
    finally:
        httpd.shutdown()
        thread.join()
        httpd.server_close()


FILES = ["index.html", "app.mjs", "data.json", "report.txt"]


def test_static_success_cannot_claim_runtime_proof(smoke, trees):
    report = smoke.check(*trees, files=FILES)
    assert report["passed"]
    assert report["static"]["status"] == "passed"
    assert report["http"]["status"] == "not-executed"
    assert report["image_runtime"]["status"] == "not-executed"
    assert report["hosted_quality"] == "unproven"


@pytest.mark.parametrize("omission", ["dockerignore-json", "copy-module"])
def test_missing_built_file_is_rejected(smoke, trees, omission):
    name = "data.json" if omission == "dockerignore-json" else "app.mjs"
    (trees[1] / name).unlink()
    report = smoke.check(*trees, files=FILES)
    assert not report["passed"]
    assert any(name in error for error in report["errors"])
    (trees[1] / name).write_bytes((trees[0] / name).read_bytes())
    (trees[1] / name).chmod(0o644)
    assert smoke.check(*trees, files=FILES)["passed"]


def test_changed_bytes_rejected(smoke, trees):
    (trees[1] / "data.json").write_text("{}")
    assert not smoke.check(*trees, files=FILES)["passed"]


@pytest.mark.parametrize("directory", [False, True])
def test_nonroot_portable_readability(smoke, trees, directory):
    path = trees[1] if directory else trees[1] / "data.json"
    path.chmod(0o700 if directory else 0o600)
    assert not smoke.check(*trees, files=FILES)["passed"]
    path.chmod(0o755 if directory else 0o644)
    assert smoke.check(*trees, files=FILES)["passed"]


@pytest.mark.parametrize("name", ["../outside", "/etc/passwd", "a/../data.json", "data.json?x=1"])
def test_unsafe_asset_paths_rejected(smoke, trees, name):
    with pytest.raises(ValueError):
        smoke.check(*trees, files=[name])


def test_symlink_rejected(smoke, trees):
    (trees[1] / "data.json").unlink()
    (trees[1] / "data.json").symlink_to(trees[0] / "data.json")
    assert not smoke.check(*trees, files=FILES)["passed"]


@pytest.mark.parametrize("origin", ["https://example.com", "http://localhost:80",
                                    "http://127.0.0.1:80/path", "http://user@127.0.0.1:80",
                                    "http://127.0.0.1:80?x=1"])
def test_nonliteral_or_nonlocal_origin_rejected(smoke, trees, origin):
    with pytest.raises(ValueError):
        smoke.check(*trees, files=FILES, origin=origin)


def test_real_loopback_origin_and_download_bytes(smoke, trees):
    with server(trees[1]) as origin:
        report = smoke.check(*trees, files=FILES, origin=origin, downloads=["report.txt"])
    assert report["passed"]
    assert report["http"]["status"] == "passed"
    assert report["http"]["origin"] == origin
    assert report["image_runtime"]["status"] == "not-executed"
    assert len(report["assets"]) == 4


@pytest.mark.parametrize("fault", ["bad_mime", "fallback", "redirect", "attachment"])
def test_served_failures_not_masked_by_http_200(smoke, trees, fault):
    with server(trees[1], **{fault: fault != "attachment"}) as origin:
        report = smoke.check(*trees, files=FILES, origin=origin, downloads=["report.txt"])
    assert not report["passed"]
    assert report["http"]["status"] == "failed"


def test_http_not_run_after_static_failure(smoke, trees):
    (trees[1] / "data.json").unlink()
    report = smoke.check(*trees, files=FILES, origin="http://127.0.0.1:1")
    assert report["http"]["status"] == "not-executed"


def test_cli_failure_is_machine_readable(trees):
    (trees[1] / "data.json").unlink()
    result = subprocess.run([sys.executable, str(SCRIPT),
                             "--expected", str(trees[0]), "--built", str(trees[1]),
                             "--file", "data.json"], capture_output=True, text=True)
    assert result.returncode == 1
    assert json.loads(result.stdout)["passed"] is False


@pytest.mark.parametrize("phase", ["headers", "body", "aggregate"])
def test_overall_deadline_stops_slow_drip_and_reaps_worker(smoke, trees, monkeypatch, phase):
    stop = Event()
    finished = Event()
    started = Event()
    processes = []
    original_popen = subprocess.Popen

    def tracked_popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process

    class SlowHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            started.set()
            body = (trees[1] / "report.txt").read_bytes()
            try:
                if phase == "aggregate":
                    stop.wait(0.2)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if phase == "headers":
                    payload = b"HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\n\r\n" + body
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    payload = body
                for byte in payload:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    if stop.wait(0.04):
                        break
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                finished.set()

        def log_message(self, *_):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    httpd.daemon_threads = False
    thread = Thread(target=httpd.serve_forever)
    thread.start()
    monkeypatch.setattr(smoke, "HTTP_PROBE_DEADLINE_SECONDS", 0.3)
    monkeypatch.setattr(smoke.subprocess, "Popen", tracked_popen)
    try:
        files = ["report.txt"]
        if phase == "aggregate":
            for name in ("second.txt", "third.txt"):
                for root in trees:
                    (root / name).write_bytes((root / "report.txt").read_bytes())
                    (root / name).chmod(0o644)
                files.append(name)
        start = monotonic()
        report = smoke.check(*trees, files=files,
                             origin=f"http://127.0.0.1:{httpd.server_port}")
        elapsed = monotonic() - start
        assert started.is_set(), "The regression must reach the slow local response"
        assert elapsed < 1.5
        assert not report["passed"]
        assert report["static"]["status"] == "passed"
        assert report["http"]["status"] == "failed"
        assert report["image_runtime"]["status"] == "not-executed"
        assert any("deadline" in error for error in report["errors"])
        assert len(processes) == 1
        assert processes[0].poll() is not None
        assert processes[0].returncode != 0
        assert finished.wait(1), "Killed worker must close its connection"
    finally:
        stop.set()
        httpd.shutdown()
        thread.join()
        httpd.server_close()
    assert not thread.is_alive()
    assert finished.is_set()
