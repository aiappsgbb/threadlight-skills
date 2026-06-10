---
kind: repo-edit | sibling-skill | manual | deferred-to-pipeline
summary: One-line description of what fixes this finding
target_file: relative/path/from/repo/root.bicep
edit_type: replace | insert | append | delete
sibling_skill: optional-skill-name-from-awesome-gbb
---

## Target file
`relative/path/from/repo/root.bicep`

## Edit type
`replace` (concrete: what kind of edit the agent should make)

## Edit recipe
Concrete instructions for the agent — exact strings to find, exact text to insert, or exact sibling-skill name + inputs to invoke.

## Verification
How the agent (or operator) confirms the fix worked. Usually: re-run `python3 scripts/production_ready.py --target-rg <RG> --target-sub <SUB>` and check that the finding flips from `fail` to `pass`.
