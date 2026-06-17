# Private-VNet pre-flight checklist

> **Run this from inside the customer boundary BEFORE `threadlight-deploy`.**
> In a fully-private environment, a half-resolved private DNS zone or a blocked
> egress rule fails the deploy in a way that *looks like an auth error* and
> burns an afternoon. A green pre-flight is the gate to running deploy.
>
> Run it on the box that will run the loop — the Azure ML compute instance or
> the connected Codespace — not on your laptop. Results differ by network
> location; that is the whole point.

## 1. Identity & context

- [ ] `az account show` returns the **expected tenant + subscription**.
- [ ] The deploy identity (UAMI/OIDC per the customer profile) is the one in
      effect, scoped to the **target RG only**.
- [ ] `azd env` is pointed at the customer landing-zone values from the
      customization map (sub, RG, region, env name).

## 2. Private DNS resolution (the usual culprit)

For each private endpoint in scope, confirm the FQDN resolves to a **private IP
(10.x / private range)**, not a public one. From the in-VNet box:

- [ ] Foundry / Azure OpenAI — `*.openai.azure.com` /
      `*.cognitiveservices.azure.com` → private IP
- [ ] Container registry (ACR) — `*.azurecr.io` (and the data endpoint
      `*.<region>.data.azurecr.io`) → private IP
- [ ] Cosmos DB — `*.documents.azure.com` → private IP
- [ ] Storage — `*.blob.core.windows.net` (+ `file`/`queue`/`table` if used) →
      private IP
- [ ] Key Vault — `*.vault.azure.net` → private IP
- [ ] AI Search (if used) — `*.search.windows.net` → private IP

> If any resolves to a **public** IP, the matching `privatelink.*` private DNS
> zone is missing a record or not linked to this VNet. Fix the DNS before
> anything else.

## 3. Endpoint reachability

- [ ] TCP 443 to each private endpoint above succeeds from the box.
- [ ] No corporate proxy is silently MITM-ing 443 (check cert chain if a
      handshake fails).
- [ ] ACR is reachable for **both** login and data-plane pulls (the data
      endpoint is a separate FQDN — a common miss).

## 4. Egress / control-plane access

- [ ] Outbound to required Azure **control-plane** endpoints is allowed (ARM,
      AAD/Entra login, the AML control-plane endpoints if on a compute
      instance). Fully-private ≠ no control-plane; confirm the allow-list.
- [ ] Any package mirrors the loop needs (PyPI, container base images) are
      reachable via the sanctioned path, or pre-baked into the dev image.
- [ ] Firewall/NSG/UDR allow the deploy traffic (check with the platform team
      rather than guessing).

## 5. Threadlight loop dry-run

- [ ] `threadlight-safe-check --phase pre-deploy` is green against the customer
      selectors.
- [ ] `threadlight-local-test` boots the agent on the in-VNet box.
- [ ] Only then: `threadlight-deploy`.

## Sign-off

- **Run from:** _<Azure ML compute instance | Codespace + path>_
- **Date / SE:** _<…>_
- **Failures found & fixed:** _<list>_
- [ ] All checks green → cleared to run `threadlight-deploy`.
