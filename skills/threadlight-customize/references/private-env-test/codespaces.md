# Test inside the customer env — GitHub Codespaces

> **The quick cloud dev box.** GitHub Codespaces gives you a disposable,
> browser- or VS Code-attached dev environment that is fast to stand up and
> easy to standardize with a `devcontainer.json`. In the field it helped
> **to some extent** with private environments — but be honest about its limit:
> **a standard Codespace does not natively join the customer's Azure VNet.**
> For a *fully-private, no-egress* target, prefer the Azure ML compute-instance
> pattern ([`azure-ml-vscode.md`](azure-ml-vscode.md)). Use Codespaces when the
> customer permits a controlled connectivity path, or for the non-private parts
> of the loop.

## When Codespaces is the right call

- The customer allows **outbound to their private resources via a sanctioned
  path** (point-to-site / site-to-site VPN from the Codespace, a Dev Tunnel
  from inside the boundary, or a bastion/jump pattern), **or**
- You are iterating on the **agent/code** (SPEC, skills, mocks) where you do not
  yet need the customer's private endpoints, **or**
- The customer's org has enabled **Azure private networking for GitHub
  Codespaces** (an enterprise configuration that creates Codespaces inside an
  Azure VNet — verify current availability and that the customer has it; do not
  assume it).

## Steps

1. **Define the dev environment as code.** Add a `devcontainer.json` to the
   engagement fork pinning the toolchain (azd, Python, the Threadlight plugin)
   so every SE gets an identical box.
2. **Create the Codespace** on the engagement fork.
3. **Establish connectivity to the customer's private resources** by the
   sanctioned path above. **If there is none, stop** — switch to the Azure ML
   compute-instance pattern; do not try to punch a hole in the customer's
   network.
4. **Run the private-VNet pre-flight** —
   [`private-vnet-checklist.md`](private-vnet-checklist.md) — to confirm private
   DNS + endpoint reachability over whatever path you established.
5. **Run the Threadlight loop** (`threadlight-local-test`, then
   `threadlight-deploy`) once the pre-flight is green.

## Honest limits

- A vanilla Codespace reaches resources over the **public internet**; private
  endpoints are not reachable without an explicit connectivity path or the
  enterprise Azure-private-networking configuration.
- Codespaces network configuration / private networking is an **org/enterprise
  admin** capability and may be preview or gated — confirm the customer has it
  before you design around it.
- When in doubt for a no-egress target, the in-VNet Azure ML compute instance is
  the lower-friction, more honest choice.

## References

- [GitHub Codespaces documentation](https://docs.github.com/en/codespaces) —
  dev containers, networking, and (under enterprise admin) Azure private
  networking for hosted compute. **Verify current availability** of private
  networking with the customer's GitHub Enterprise admin.
