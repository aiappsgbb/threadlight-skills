"""Task8 local SDK/JWKS fixture. Real service/auth/signatures; never Azure evidence."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import importlib
import json

TENANT = "11111111-1111-1111-1111-111111111111"
WORKLOAD = "22222222-2222-2222-2222-222222222222"
HUMAN = "33333333-3333-3333-3333-333333333333"
APP = "44444444-4444-4444-4444-444444444444"
UI = "55555555-5555-5555-5555-555555555555"
KEY = "https://test.vault.azure.net/keys/bundle/" + "a" * 32


def module(name):
    return importlib.import_module("govern_control_plane." + name)


class MemoryStore:
    """Task8 read/create/CAS protocol; local, shared across independent clients."""
    def __init__(self):
        self.blobs, self.docs, self.etag = {}, {}, 0
        self.failed = False

    def check(self):
        if self.failed:
            raise OSError("local-storage-unavailable")

    async def health(self):
        self.check()

    async def blob_create(self, name, body):
        self.check()
        if name in self.blobs:
            raise module("storage").Conflict()
        self.blobs[name] = bytes(body)

    async def blob_read(self, name):
        self.check()
        if name not in self.blobs:
            raise module("storage").Missing()
        return self.blobs[name]

    async def read(self, scope, key):
        self.check()
        if (scope, key) not in self.docs:
            raise module("storage").Missing()
        body, etag = self.docs[(scope, key)]
        return deepcopy(body), etag

    async def create(self, scope, key, body):
        self.check()
        if (scope, key) in self.docs:
            raise module("storage").Conflict()
        self.etag += 1
        self.docs[(scope, key)] = deepcopy(body), str(self.etag)

    async def replace(self, scope, key, body, etag):
        self.check()
        await asyncio.sleep(0)
        if self.docs[(scope, key)][1] != etag:
            raise module("storage").Conflict()
        self.etag += 1
        self.docs[(scope, key)] = deepcopy(body), str(self.etag)


class LocalSigner:
    def __init__(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    async def sign(self, digest):
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, utils
        return self.key.sign(digest, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))

    async def verify(self, digest, signature):
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, utils
        try:
            self.key.public_key().verify(
                signature, digest, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))
            return True
        except InvalidSignature:
            return False

    async def health(self):
        pass


class ControlFixture:
    def __init__(self, *, agent_id, policy_id):
        import httpx
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update(kid="local-key", use="sig", alg="RS256")
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"keys": [jwk]})))
        self.settings = module("auth").Settings(
            tenant_id=TENANT, audience="api://governance", key_id=KEY,
            workloads={WORKLOAD: {"client_id": APP, "agent_id": agent_id, "policies": [policy_id]}},
            human_clients=[UI], approver_subjects=[HUMAN], auditor_subjects=[HUMAN],
            approver_roles=["Approver"])
        self.store, self.signer = MemoryStore(), LocalSigner()
        self.auth = module("auth").EntraAuth(self.settings, self.http)
        self.service = module("app").ControlPlane(self.settings, self.store, self.signer)
        self.app = module("app").create_app(service=self.service, auth=self.auth)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                      base_url="https://control.example")

    def token(self, *, human=False, changes=None):
        import jwt
        now = int(datetime.now(timezone.utc).timestamp())
        claims = dict(iss=f"https://login.microsoftonline.com/{TENANT}/v2.0",
                      aud="api://governance", tid=TENANT, ver="2.0",
                      oid=HUMAN if human else WORKLOAD, azp=UI if human else APP,
                      exp=now + 300, nbf=now - 5, iat=now - 5,
                      roles=["Approver", "Governance.Auditor"] if human else ["Governance.Workload"])
        claims.update({"scp": "Governance.Approve Governance.Read"} if human else {"idtyp": "app"})
        claims.update(changes or {})
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "local-key"})

    def headers(self, **kwargs):
        return {"Authorization": "Bearer " + self.token(**kwargs)}

    async def close(self):
        await self.client.aclose()
        await self.http.aclose()
