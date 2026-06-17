# Test inside the customer env — Azure ML compute instance + VS Code

> **The stronger pattern for fully-private environments.** An Azure Machine
> Learning **compute instance** lives **inside a VNet** and can be created with
> **no public IP**, so it natively reaches the customer's **private endpoints**
> (Foundry/AOAI, ACR, Cosmos, Storage, Key Vault) without any public egress.
> You attach **VS Code (Desktop or Web)** to it and run the Threadlight
> `local-test` / `deploy` loop from *inside the boundary*. This is what made the
> field's fully-private telco engagement work.

## Why this one for fully-private

- The compute instance is a managed VM placed in a subnet of the customer's
  VNet (or the workspace's managed VNet). With **no-public-IP** it has no
  inbound public surface; outbound private-endpoint traffic stays on the VNet.
- VS Code Remote connects to it through the Azure ML extension, so your editor,
  terminal, and the Threadlight CLI all execute **on the in-VNet box** — DNS
  and routing are the customer's, exactly like prod.

## Prerequisites

- An Azure ML **workspace** secured with a VNet / managed VNet and private
  endpoints (the customer's platform team usually owns this).
- A **compute instance** in the workspace, created **in the VNet** with
  **no public IP** where policy requires it.
- VS Code + the **Azure Machine Learning** extension (and the Azure account
  extension), or use **VS Code for the Web** from the Azure ML studio.

## Steps

1. **Provision the compute instance in the VNet.** Platform team (or you, if
   permitted) creates it in the workspace's subnet, no public IP. Confirm it
   reaches required Azure control-plane endpoints (the workspace VNet docs list
   what must be allowed).
2. **Connect VS Code to it.** From Azure ML studio → Compute → your instance →
   **VS Code (Desktop)** or **VS Code (Web)**; or from VS Code, use the Azure ML
   extension to connect to the compute instance.
3. **Clone the engagement fork** onto the compute instance (the pinned upstream
   + overlay from `fork-runbook.md`).
4. **Run the private-VNet pre-flight** —
   [`private-vnet-checklist.md`](private-vnet-checklist.md). Do not skip; a
   half-resolved private DNS zone fails the deploy as what looks like an auth
   error.
5. **Run the Threadlight loop** from the instance terminal: `threadlight-local-test`
   to boot, then `threadlight-deploy` against the customer landing zone. All
   traffic now traverses the customer's private endpoints.

## Gotchas

- **No-public-IP requires the workspace/VNet to be set up for it** — outbound
  to a few Azure control-plane endpoints must still be allowed (service tags or
  a managed VNet). Get the platform team to confirm before you debug "it can't
  start."
- **The compute instance is single-user** — assign it to the SE who runs the
  loop; don't share sessions.
- **Stop it when idle** to control cost; the overlay/fork lives in git, not on
  the box.

## References (Microsoft Learn)

- [Start VS Code integrated with Azure Machine Learning](https://learn.microsoft.com/azure/machine-learning/how-to-launch-vs-code-remote?view=azureml-api-2)
- [Work in VS Code remotely connected to a compute instance](https://learn.microsoft.com/azure/machine-learning/how-to-work-in-vs-code-remote?view=azureml-api-2)
- [Secure a training environment with VNets — compute instance with no public IP](https://learn.microsoft.com/azure/machine-learning/how-to-secure-training-vnet?view=azureml-api-2#compute-instance-cluster-or-serverless-compute-with-no-public-ip)
- [Secure an Azure ML workspace by using virtual networks](https://learn.microsoft.com/azure/machine-learning/how-to-secure-workspace-vnet?view=azureml-api-2)
