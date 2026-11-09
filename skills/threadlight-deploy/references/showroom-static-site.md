# Static-site showroom deploy (nginx ACA + Easy Auth)

> Part of the [`threadlight-deploy`](../SKILL.md) skill, moved out of the core
> SKILL.md verbatim (relative links rebased). Read it when deploying a static showroom or site behind Easy Auth.

## Reference: Static-Site Showroom Deploy (nginx ACA + Easy Auth)

> **When to use.** A **static showroom** (lobby + catalog + cinematic process
> walkthroughs) is a classic adjacent need to a hosted-agent pilot — the
> seller wants a single URL to put on a slide that gates by Entra ID.
> Same `threadlight-tenant-isolation` discipline, same `acme-shared` ACR, same
> Communication Blue brand bar — but the **runtime is nginx, not an agent**,
> and the **2-phase Easy Auth wiring** below is non-negotiable. This is
> NOT modeled in SPEC § 11c (it's not a per-process agent repo); ship it
> as a sibling `infra/` folder with the static HTML.

### The chicken-and-egg problem

ACA `secrets[].keyVaultUrl` references are validated at **provisioning
time**, not at runtime. So you cannot do this in one Bicep deploy:

```
1. Create KV
2. Create ACA app with secrets:[{ name: 'easyauth-client-secret',
                                  keyVaultUrl: '<kv>/secrets/easyauth-client-secret',
                                  identity: <uami> }]
3. Mint AAD client secret (needs the ACA FQDN as redirect URI…)
4. Write secret to KV
```

Step 2 fails: KV secret doesn't exist yet → `unable to fetch secret`.
Reordering doesn't help — the AAD client secret needs the ACA FQDN to
register the redirect URI, which needs the ACA created first.

### The fix: 2-phase Bicep with `wireAuth` toggle

Add a single `bool` param to your Bicep module and gate two things on it:

```bicep
// infra/modules/site.bicep
@description('Phase 2 toggle: when true, wires the ACA secret reference + Easy Auth. Phase 1 = false.')
param wireAuth bool = false

// ... UAMI, KV, role assignments, ACA env all unconditional ...

resource site 'Microsoft.App/containerApps@2024-10-02-preview' = {
  name: 'showroom-site'
  // ...
  properties: {
    configuration: {
      // GATED: empty array on phase 1, real reference on phase 2
      secrets: wireAuth ? [
        {
          name: 'easyauth-client-secret'
          keyVaultUrl: '${kv.properties.vaultUri}secrets/easyauth-client-secret'
          identity: uami.id
        }
      ] : []
      ingress: { external: true, targetPort: 80 /* ... */ }
      registries: [{ server: '${acrName}.azurecr.io', identity: uami.id }]
    }
    template: {
      containers: [{
        image: imageRef
        name: 'site'
        resources: { cpu: json('0.5'), memory: '1Gi' }
        probes: [
          { type: 'Liveness',  httpGet: { path: '/healthz', port: 80 } }
          { type: 'Readiness', httpGet: { path: '/healthz', port: 80 } }
        ]
      }]
      scale: { minReplicas: 1, maxReplicas: 5 /* ... */ }
    }
  }
}

// GATED: only created on phase 2
resource auth 'Microsoft.App/containerApps/authConfigs@2024-10-02-preview' = if (wireAuth) {
  parent: site
  name: 'current'                // MUST be literal 'current'
  properties: {
    platform: { enabled: true }
    globalValidation: {
      unauthenticatedClientAction: 'RedirectToLoginPage'
      redirectToProvider: 'azureactivedirectory'
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          clientId: easyAuthClientId
          clientSecretSettingName: 'easyauth-client-secret'
          openIdIssuer: 'https://sts.windows.net/${tenant().tenantId}/v2.0'
        }
        validation: { allowedAudiences: easyAuthAllowedAudiences }
      }
    }
    login: { tokenStore: { enabled: true } }
  }
}
```

### deploy.ps1 orchestration (canonical)

```powershell
# Phase 1: foundation (KV, UAMI, RBAC, ACA env, ACA app — NO auth)
az deployment sub create --name "site-p1-$ts" --location $Location `
    --template-file infra/main.bicep `
    --parameters wireAuth=false imageRef=$imageRef <other-params>

# Between phases: grant Secrets Officer + mint + write secret
az role assignment create --assignee-object-id $deployerOid `
    --assignee-principal-type User --role 'Key Vault Secrets Officer' `
    --scope "/subscriptions/$subId/resourceGroups/$rg/providers/Microsoft.KeyVault/vaults/$kvName"
Start-Sleep 30  # RBAC propagation — non-negotiable

$cred = az ad app credential reset --id $clientId `
    --display-name 'showroom-site' --append `
    --end-date (Get-Date).AddMonths(12).ToString('yyyy-MM-ddTHH:mm:ssZ') `
    -o json | ConvertFrom-Json
az keyvault secret set --vault-name $kvName --name easyauth-client-secret `
    --value $cred.password --output none

# Phase 2: re-deploy with auth wired
az deployment sub create --name "site-p2-$ts" --location $Location `
    --template-file infra/main.bicep `
    --parameters wireAuth=true imageRef=$imageRef <other-params>

# Post: register redirect URI (idempotent)
$existing = az ad app show --id $clientId --query 'web.redirectUris' -o json | ConvertFrom-Json
$redirectUri = "https://$siteFqdn/.auth/login/aad/callback"
if ($existing -notcontains $redirectUri) {
    $merged = @($existing + $redirectUri | Select-Object -Unique)
    az ad app update --id $clientId --web-redirect-uris @merged --output none
}
```

### Smoke test the gate (3 expected behaviours)

```powershell
$url = "https://$siteFqdn"
# HEAD: 401 with WWW-Authenticate Bearer + correct authorization_uri (tenant + client_id)
curl -sI $url
# GET (browser-like): 302 → login.windows.net/<tenant>/oauth2/v2.0/authorize?...client_id=<appId>...
curl -s -o NUL -w "HTTP %{http_code} → %{redirect_url}\n" -A "Mozilla/5.0" $url
# GET /healthz: also gated (no path exclusions by default) → 401/302
```

### nginx Dockerfile + nginx.conf (canonical static-site shape)

`Dockerfile`:
```dockerfile
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY . /usr/share/nginx/html/
RUN rm -f /usr/share/nginx/html/Dockerfile /usr/share/nginx/html/nginx.conf \
 && chown -R nginx:nginx /usr/share/nginx/html
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1/healthz || exit 1
```

`nginx.conf`:
```nginx
server {
    listen 80;
    server_tokens off;
    gzip on; gzip_types text/css application/javascript image/svg+xml;

    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options SAMEORIGIN always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;

    location = /healthz { return 200 'ok\n'; add_header Content-Type text/plain; }

    location ~* \.(html?)$ { add_header Cache-Control "public, max-age=300"; }
    location ~* \.(css|js|png|jpe?g|svg|woff2?)$ { add_header Cache-Control "public, max-age=2592000, immutable"; }

    root /usr/share/nginx/html;
    index index.html;
    # NO SPA fallback — multi-page site, 404 should 404
}
```

`.dockerignore` (deny-list — ACR Tasks uploads everything but `COPY .` honors this):
```
.git/
infra/
docs/
00-research/
**/*.md
**/.vscode/
deploy.ps1
```

### Anti-patterns

- ❌ Single Bicep deploy with `secrets:` reference + `authConfigs` + post-deploy KV write → ACA fails to provision
- ❌ Hard-coding role GUIDs (typo Bicep deploys without erroring then fails at role-assignment provisioning) — always look up via `az role definition list --name '<role>' --query '[0].name' -o tsv`
- ❌ `az rest --headers Content-Type=application/json` for redirect URI updates (Windows CLI parses `--headers` inconsistently) — use `az ad app update --web-redirect-uris …` instead
- ❌ `az ad app credential reset` without `--append` (replaces ALL existing secrets — disrupts every other ACA app sharing the same app reg)
- ❌ Forgetting `Start-Sleep 30` after `az role assignment create` for KV Secrets Officer (RBAC propagation is real and `keyvault secret set` will return 403 if you race it)
