# Killer Prompts — Returns Triage

5 ranked wow-prompts (K1–K5) for the live demo. Each `Prompt` is copied **verbatim**
from a happy-path row in `tests/eval_dataset.jsonl` — the same string that scores
green in evals. `infra/scripts/refresh_killer_prompts.py` syncs K1–K3 into
`agent.yaml` as `STARTER_{1,2,3}_TITLE/PROMPT`.

| Rank | Prompt | BR-XXX | Expected anchors | Wow line | Surfaces |
|------|--------|--------|------------------|----------|----------|
| K1 | Triage return RMA-2026-004410 and recommend a decision. | BR-001 | RMA-2026-004410, Glacier Outpost Alpine Insulated Jacket, $189, 6 days since delivery, approve_refund, restock_a | "It pulled the order, checked the 30-day window, and approved with the exact policy clause cited — in one pass." | Workspace · Teams · Foundry playground |
| K2 | Triage return RMA-2026-004425 — should we refund it? | BR-003 | RMA-2026-004425, Cardinal & Stripe Cashmere Travel Coat, $1,180, $250 ceiling, escalate_to_supervisor | "A $1,180 refund is over the $250 auto-approve ceiling, so it escalated to a supervisor instead of paying out silently." | Workspace · Teams · Foundry playground |
| K3 | Triage return RMA-2026-004440 for the damaged item. | BR-004 | RMA-2026-004440, arrived_damaged, no photos, $312 Northwind Home Linen Duvet, request_more_info | "It caught that a damage claim arrived with no photos and asked for exactly what's missing — no wrong refund." | Workspace · Teams · Foundry playground |
| K4 | Triage RMA-2026-004452 — this customer returns a lot. | BR-003 | RMA-2026-004452, lifetime return rate 63%, serial-returner threshold 0.40, escalate_to_supervisor | "It flagged a 63% lifetime return rate against the serial-returner threshold and routed it to a human." | Workspace · Teams |
| K5 | Triage return RMA-2026-004418. | BR-002 | RMA-2026-004418, Solstice Beauty Co., final_sale, $54, deny_refund | "Final-sale item, changed-mind reason — denied with the final-sale clause cited, not a judgement call." | Workspace · Teams · Foundry playground |
