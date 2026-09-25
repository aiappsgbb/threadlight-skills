# Web artifact smoke: a bounded preflight

[`scripts/web_artifact_smoke.py`](../scripts/web_artifact_smoke.py) is a Python
standard-library diagnostic, not a packaging engine, Dockerfile interpreter,
presenter validator or evidence producer. It compares explicitly selected
expected build-output files with the final artifact filesystem. Select the real
entry HTML, imported `.mjs`/CSS/assets, required JSON and promised download files,
not an arbitrary sample. It does not discover transitive imports.

The expected tree is the intended output of the existing build. The built tree
must come from the final artifact stage (or, before an image is available, the
staged web directory clearly labeled as such). Do not compare sources against
themselves and call it a build. Missing JSON after `.dockerignore` and missing
modules after `COPY` are both rejected as missing final files; the helper does
not claim which build instruction caused the omission.

```bash
python3 skills/threadlight-deploy/scripts/web_artifact_smoke.py \
  --expected /path/to/expected-web-output \
  --built /path/to/final-artifact-web-root \
  --file index.html --file app.mjs --file data.json --file report.txt
```

Exit 1 and JSON `errors` indicate a failed check; invalid command inputs exit 2.
`passed` describes only the checks actually executed. `static.status` covers
exact bytes, no asset symlinks, regular files, and conservative portable non-root
permissions: other-read on files and other-execute through directories from the
supplied root. Preserve final-stage mode metadata when extracting. Ownership,
ACLs, container ancestor directories, volumes, labels and actual runtime UID/GID
are **not** proven by this check. Legitimate owner/group-only layouts need their
real runtime access test; do not make private files world-readable to get green.

Optionally check an **already running, authorized local server**:

```bash
python3 skills/threadlight-deploy/scripts/web_artifact_smoke.py \
  --expected /path/to/expected-web-output \
  --built /path/to/final-artifact-web-root \
  --file index.html --file app.mjs --file data.json --file report.txt \
  --origin http://127.0.0.1:8080 --download report.txt
```

Use the real server origin and its actual URL paths, not `file://`, a dev server
that manufactures missing files, or a filesystem path mistaken for a URL.
The narrow helper supports literal loopback HTTP only, disables proxies, and
rejects redirects. It sends no auth tokens. Each request has a five-second socket
inactivity timeout and reads at most expected size plus one byte. Independently,
one **10-second overall deadline** covers the complete HTTP probe across all
selected assets, including header/body reads and slow-drip responses. The HTTP
phase runs in one owned subprocess with no descendants; `subprocess.run(timeout=10)`
kills and reaps that worker on expiry, closing its sockets. The result is a failed
HTTP diagnostic, never partial success or a background retry. The helper does not
stop or own the server. The deadline excludes the preceding local filesystem
preflight and OS process-creation time; those are not network checks.
It is for trusted local fixtures/servers, not hostile-server resource isolation.
It compares exact served
bytes, checks MIME (including JavaScript `.mjs`), rejects HTML SPA fallback, and
requires attachment disposition for explicitly listed downloads. A browser
generated download or authenticated API export instead needs the process's
browser/adapter test; do not replace it with a static file to pass this check.

### Evidence boundary and remaining gate

Both modes always report `image_runtime.status: not-executed` and
`hosted_quality: unproven`. A URL alone cannot establish container provenance or
configured runtime identity. Synthetic loopback tests establish helper behavior
only. No mode writes presenter receipts, edits pins, builds/pulls images or deploys.

Before hosted-quality claims, the process owner must execute the **actual image**
with its real server configuration, default non-root user, startup/import cohort
and relevant mounts, without a source bind-mount hiding missing image files.
Record immutable image digest, source/lock identity, actual UID/GID, server config,
command and outputs. Check module imports in a browser, real routes, download
content/name and authorization, fresh-session history, and the day-after behavior
from [incumbent adoption](presenter-adoption.md).

Then verify the authorized deployed revision actually loads that image/config,
using the existing presenter target and producer receipts. HTTP success alone
does not prove useful business interaction, durable readback or human acceptance.

When local Docker/registry access is unavailable, retain the static diagnostic
and report the image-runtime gate **unexecuted**. Use a separately authorized
existing image/CI or cloud-only digest validation path when available; do not
contact registries, change Docker credential configuration, bypass the keychain,
or launch paid jobs merely to run this helper. No unmerged upstream tooling or
new provider pin is required by this source-only preflight.
