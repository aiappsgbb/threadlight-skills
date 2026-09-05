"""Hosted audit: Task8 ACK before required effects; local files are retry safety only."""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
import json
import logging
import os
import threading
import uuid

import httpx

from govern_control_plane.client import ReceiptClient
from govern_control_plane.models import DecisionReceipt, canonical, parse
from runtime.evidence import DurableSpool

LOG = logging.getLogger(__name__)


def workload_credential():
    from azure.identity.aio import DefaultAzureCredential
    return DefaultAzureCredential(
        exclude_shared_token_cache_credential=True, exclude_visual_studio_code_credential=True,
        exclude_cli_credential=True, exclude_powershell_credential=True,
        exclude_developer_cli_credential=True, exclude_interactive_browser_credential=True,
        exclude_broker_credential=True)


def wire_receipt(receipt):
    """Only payload-free native evidence; no generated action, timestamp or version on replay."""
    return parse(DecisionReceipt, canonical({
        "receipt_id": receipt["audit_id"], "correlation_id": receipt["correlation_id"],
        "action_id": receipt["action_id"], "action_hash": receipt["action_hash"],
        "policy_digest": receipt["policy_hash"], "decision": receipt["decision"],
        "reason_code": receipt["reason_code"], "agent_version": receipt["agent_version"],
        "image_digest": receipt["image_digest"], "recorded_at": receipt["recorded_at"],
        **({"probe": receipt["probe"]} if receipt.get("probe") is not None else {}),
    })).model_dump(mode="json")


class AuditDelivery(DurableSpool):
    """A separate, owned event loop bridges Task7's synchronous pre-effect append.

    HTTP, credential acquisition, retries and shutdown are bounded. No coroutine
    is submitted to the blocked native-agent loop. A remote ACK, not this directory's
    filesystem type or fsync, establishes required durability on hosted containers.
    """
    def __init__(self, directory, *, base_url, scope, required,
                 credential_factory=workload_credential, transport_factory=None,
                 timeout=5.0, retry_interval=5.0):
        super().__init__(directory)
        ReceiptClient.validate_configuration(base_url, scope, timeout)
        if not 0 < retry_interval <= 60:
            raise ValueError("invalid_audit_retry_interval")
        self.url, self.scope, self.required = base_url, scope, required
        self.timeout, self.retry_interval = timeout, retry_interval
        self.credential_factory, self.transport_factory = credential_factory, transport_factory
        self.worker = None
        self.loop = None
        self.closed = False
        self.failed = False
        self.remote_available = False
        self.replay_failed = False
        self.started = threading.Event()
        self.files = threading.RLock()

    def _directory(self):
        for path in (self.directory, *self.directory.parents):
            if path.is_symlink():
                raise OSError("host_owned_audit_directory_required")
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        metadata = self.directory.stat()
        if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o022:
            raise OSError("host_owned_audit_directory_required")

    def _write(self, receipt, *, replace=False):
        with self.files:
            if not replace:
                receipt["wire_receipt"] = wire_receipt(receipt)
            self._directory()
            super()._write(receipt, replace=replace)

    def append(self, **fields):
        if self.required and not self.running:
            raise OSError("audit_unavailable")
        try:
            audit_id = super().append(**fields)
            if self.required:
                self._submit(self._export(audit_id))
            return audit_id
        except Exception:
            raise OSError("audit_unavailable") from None

    @property
    def running(self):
        return bool(self.worker and self.worker.is_alive() and self.started.is_set()
                    and self.loop and not self.closed and not self.failed)

    def _submit(self, coroutine):
        if not self.running:
            coroutine.close()
            raise OSError("audit_unavailable")
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        try:
            return future.result(timeout=self.timeout + 0.1)
        except Exception:
            future.cancel()
            self.remote_available = False
            raise OSError("audit_unavailable") from None

    async def _export(self, audit_id):
        try:
            async with asyncio.timeout(self.timeout):
                async with self.exports:
                    path = self.directory / f"{audit_id}.json"
                    with self.files:
                        self._directory()
                        if path.is_symlink():
                            raise OSError("invalid_audit_record")
                        receipt = json.loads(path.read_text())
                    if receipt["delivery_status"] == "delivered":
                        return
                    # Old pending files without these fields remain pending, never fabricated.
                    body = receipt["wire_receipt"]
                    if body != wire_receipt(receipt):
                        raise ValueError("audit_record_mismatch")
                    ack = await self.client.append(body)
                    if ack != audit_id:
                        raise OSError("audit_unavailable")
                    receipt["delivery_status"] = "delivered"
                    self._write(receipt, replace=True)
                    self.remote_available = True
        except Exception:
            self.remote_available = False
            raise OSError("audit_unavailable") from None

    async def _replay(self):
        failures = False
        for path in sorted(self.directory.glob("*.json")):
            try:
                with self.files:
                    if path.is_symlink():
                        raise OSError("invalid_audit_record")
                    receipt = json.loads(path.read_text())
                if receipt["delivery_status"] == "pending":
                    await self._export(path.stem)
            except Exception:
                failures = True
        self.replay_failed = failures
        if failures:
            LOG.warning("audit_pending_delivery_failed")

    async def _check(self):
        healthy = await self.client.health()
        self.remote_available = healthy
        with self.files:
            self._directory()
            probe = self.directory / (".probe-" + uuid.uuid4().hex)
            try:
                with probe.open("xb") as stream:
                    stream.flush()
                    os.fsync(stream.fileno())
                descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            finally:
                probe.unlink(missing_ok=True)
            pending = any(json.loads(p.read_text())["delivery_status"] != "delivered"
                          for p in self.directory.glob("*.json"))
        return healthy and not pending and not self.replay_failed

    def check(self):
        try:
            return self._submit(self._check()) is True
        except OSError:
            return False

    async def health(self):
        return await asyncio.to_thread(self.check)

    async def _retry_loop(self):
        while True:
            await self._replay()
            await asyncio.sleep(self.retry_interval)

    async def _close_resource(self, close):
        try:
            async with asyncio.timeout(self.timeout):
                await close()
        except TimeoutError:
            self.failed = True
            LOG.error("audit_resource_close_timeout")

    async def _serve(self):
        self.loop = asyncio.get_running_loop()
        self.exports = asyncio.Lock()
        self.stop = asyncio.Event()
        async with AsyncExitStack() as stack:
            credential = self.credential_factory()
            stack.push_async_callback(self._close_resource, credential.close)
            transport = self.transport_factory() if self.transport_factory else None
            http = httpx.AsyncClient(
                transport=transport, timeout=self.timeout, trust_env=False, follow_redirects=False)
            stack.push_async_callback(self._close_resource, http.aclose)
            self.client = ReceiptClient(base_url=self.url, scope=self.scope,
                                        credential=credential, http=http, timeout=self.timeout)
            retry = asyncio.create_task(self._retry_loop())
            self.started.set()
            stop = asyncio.create_task(self.stop.wait())
            try:
                done, _ = await asyncio.wait({retry, stop}, return_when=asyncio.FIRST_COMPLETED)
                if retry in done:
                    await retry
                    raise OSError("audit_worker_stopped")
            finally:
                retry.cancel()
                stop.cancel()
                await asyncio.gather(retry, stop, return_exceptions=True)

    def _run(self):
        try:
            asyncio.run(self._serve())
        except Exception:
            self.failed = True
            LOG.error("audit_worker_failed")
        finally:
            self.started.set()

    def __enter__(self):
        if self.worker is not None:
            raise RuntimeError("audit_worker_already_started")
        self._directory()
        self.worker = threading.Thread(target=self._run, name="governance-audit", daemon=True)
        self.worker.start()
        if not self.started.wait(self.timeout) or self.failed:
            self.close()
            raise OSError("audit_unavailable")
        return self

    def close(self):
        self.closed = True
        if self.loop and self.worker.is_alive():
            self.loop.call_soon_threadsafe(self.stop.set)
        if self.worker:
            self.worker.join(2 * self.timeout + 0.25)
            if self.worker.is_alive():
                self.failed = True
                raise OSError("audit_shutdown_timeout")

    def __exit__(self, *args):
        self.close()

    async def __aenter__(self):
        return await asyncio.to_thread(self.__enter__)

    async def __aexit__(self, *args):
        await asyncio.to_thread(self.close)
