---
kind: repo-edit
summary: Declare a substantive idempotency statement in every skill's operational contract
target_file: src/agent/skills/<skill>/SKILL.md
edit_type: modify
---

## Target file
`src/agent/skills/<skill>/SKILL.md` — the `## Operational contract` block of every
skill the super-agent routes between. The check reads the `- **Idempotency**:` line
and asks whether it says anything a reviewer could act on.

## Edit type
`modify`

## Edit recipe

The failure this prevents is a *double side effect on a real customer*: approver
clicks Approve, the network blips, the caller retries, and the refund is issued
twice. The audit log shows one approval and two actions.

1. For every skill listed in the finding, add or rewrite the idempotency line so it
   either **names how replay is made safe** or **disclaims the side effect**.

   A statement that names the key and the write semantics:

   ```markdown
   - **Idempotency**: writing the same decision for the same `rma_id` is a no-op.
   ```

   ```markdown
   - **Idempotency**: `returns_apply_decision` uses an if-not-exists write keyed
     on `rma_id`; a replay returns the stored decision unchanged.
   ```

   A statement that disclaims the side effect, which is the correct answer for a
   skill that only reads or computes:

   ```markdown
   - **Idempotency**: read-only; safe to re-run.
   ```

   ```markdown
   - **Idempotency**: pure function of inputs.
   ```

2. Reject the box-ticking forms. `Yes`, `Idempotent`, `N/A` and `TBD` restate the
   label and attest nothing — they are reported `should-fix`, not `pass`.

3. Make the statement true in the implementation. The declaration is what this
   check can see; the conditional write is what actually saves you:

   - derive the idempotency key deterministically from the unit of work (the RMA
     id, the approval correlation id) — never a timestamp, never a fresh UUID;
   - store the key **before** performing the action, and use a conditional write
     (`if-not-exists` / ETag / unique index) rather than a blind insert;
   - on a duplicate key, return the stored result instead of re-executing.

4. If the terminal write is performed by a tool you do not own, say so explicitly
   and name the guarantee you are relying on, so the next reviewer can check it:

   ```markdown
   - **Idempotency**: delegated to `returns_apply_decision`, which the tool
     contract declares idempotent on `rma_id`.
   ```

## Verification
Re-run the assessor: `python3 scripts/production_ready.py --root <REPO>`. HITL-006
should report `pass` with the number of contracts inspected. A pilot that publishes
no contracts under `src/agent/skills/` reports `not-verified`, never `pass` — that
is the intended answer, not a passing grade.
