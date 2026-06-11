# Sibling-skill flip protocol

When an upstream sibling skill referenced in `references/sibling-skills-map.md`
lands in `aiappsgbb/awesome-gbb`, flip the corresponding recipe(s) from
`kind: manual` → `kind: sibling-skill`.

## Pre-conditions

1. The upstream skill is merged to `awesome-gbb:main` and listed in the awesome-gbb
   plugin manifest.
2. The skill has a stable `SKILL.md` slug — verify by running the Skill tool against
   it from a Copilot CLI session.
3. The threadlight-production-ready CHANGELOG has a slot for a `feat(recipes):`
   entry in the upcoming version.

## Procedure

1. Open `references/sibling-skills-map.md` and remove the `*(planned — awesome-gbb#NNN)*`
   italic marker from the recipe's row. If a `Notes` cell references the planned status,
   replace it with a brief mention of which version flipped it (e.g. "shipped v0.5.x").
2. Open `references/remediation-recipes/<RECIPE-ID>.md`. Change front-matter:
   - `kind: manual` → `kind: sibling-skill`
   - `sibling_skill_status: planned` → `sibling_skill_status: built`
   - Keep `sibling_skill: <skill-name>` as-is.
3. Replace the `## Edit recipe` body's "manual today" preamble with a `sibling-skill`
   dispatch block — copy the shape from any existing `kind: sibling-skill` recipe
   (e.g. NET-501).
4. Run `cd skills/threadlight-production-ready && python3 tests/test_sibling_skill_map.py`
   to confirm the planned-sibling gate no longer flags this recipe and the map still
   covers every `kind: sibling-skill` recipe.
5. Add a CHANGELOG entry under the next version's `Changed` section:
   `flip <RECIPE-ID> to kind: sibling-skill (awesome-gbb#NNN landed)`.

## Rollback

If the upstream skill is reverted, repeat the procedure in reverse (re-add `*(planned)*`
to the map, flip `kind` back to `manual`, restore the manual preamble) and add a
`Reverted` entry to the CHANGELOG.
