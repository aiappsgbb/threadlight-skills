# Workspace shape references — availability

Only shapes marked **runnable reference** ship copyable files. Every other
shape is **not shipped**: generate it from the spec using the parent
SKILL.md Step 2 "from-spec path", and say so in the hand-off. Never report a
missing template as reused. The authoritative list is the shape availability
index in the parent `SKILL.md`; `tests/test_workspace_shapes.py` keeps this
table, that index and the files on disk in agreement.

| Shape | Availability | Reference | Notes |
|-------|--------------|-----------|-------|
| `case-list` | runnable reference | `references/shapes/case-list/` | Queue + detail pane with the three HITL panels; shows `pending_approval` items with a one-click approval channel (email notice or authenticated panel), reviewer command as operator fallback; never approves itself |
| `inbox` | not shipped — generate from spec | — | Chronological queue; start from the case-list detail pane |
| `dashboard` | not shipped — generate from spec | — | KPI tiles + drill-down; panels as a drawer |
| `console` | not shipped — generate from spec | — | Live event stream + action toolbar |
| `kanban` | not shipped — generate from spec | — | Stage-based flow; panels in the card drawer |
| `map` | not shipped — generate from spec | — | Geographic; panels in the click-through drawer |
| `chat` | not shipped — generate from spec | — | Conversational; backend pattern in the parent SKILL.md |

A shape becomes a runnable reference only when its folder contains a working
`index.html`, every asset it references, a `README.md` and the HITL panels,
and it is added to the SKILL.md index in the same change.
