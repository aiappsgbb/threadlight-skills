# customer-overrides.yaml schema

## Purpose

Per-customer overrides let an operator flip the **status** of a specific finding in the assessor's report.

Use cases:

- Customer uses an equivalent-but-different control, such as Vault instead of Key Vault, so a FAIL can be flipped to PASS.
- Customer has a stricter policy than threadlight's default, such as mandatory private endpoints, so a PASS can be flipped to FAIL.

## What overrides cannot do

- Cannot override `severity: must-fix` findings — the script exits 2 with a loud error.
- Cannot rewrite a recipe's `severity`; only `status` changes are supported.
- Cannot suppress a finding entirely. There is no `skip` status.
- Cannot substitute one recipe for another. Recipe IDs are stable identifiers.
- Cannot edit the user's repo. Overrides are applied only to the in-memory findings list before report emission.

## File shape

```yaml
customer: <string>        # required; appears as override_customer
overrides:
  - recipe_id: <string>   # required; must match an emitted finding id
    status: pass|fail     # required; only these two values accepted
    reason: <string>      # required; non-empty; appears as override_reason
  - ...
```

## Validation

The loader (`_load_customer_overrides`) uses a stdlib-only mini-YAML parser scoped to this schema. The validator (`_validate_customer_overrides`) rejects:

- Missing or empty `customer` field
- `overrides` not a list
- Any item missing `recipe_id`, `status`, or `reason`
- Status values other than `pass` or `fail`
- Empty reason strings

## Worked example

See `customer-overrides.example.yaml` in this directory.

## Audit trail

Every overridden finding in the manifest/report includes:

- `override_customer: <customer-name>`
- `override_reason: <reason-string>`

This lets downstream automation and humans see exactly which findings were touched and why.

## Operational notes

Run with:

```bash
python scripts/production_ready.py --customer-overrides path/to/customer-overrides.yaml
```

If an override targets a `severity: must-fix` finding, the process exits 2 before writing the assessor outputs.
