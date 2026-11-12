# `case-list` workspace shape — runnable reference

A queue list with a detail pane for case-managed and decision/approval
queues. Vanilla HTML, CSS and JS: no build step, no framework, no CDN, no
external fonts. It runs from any static server and works offline.

```
case-list/
├── index.html                  # Shell: top bar, filter rail, case table, detail pane
├── workspace.css               # Layout, status badges, responsive rules (accent from data)
├── workspace.js                # Data loading, filter/sort/search, detail, panel wiring
├── data/cases.json             # Sample queue (synthetic) — replace with your data
├── components/                 # Byte-identical copies of ../../hitl-panels/*.html
│   ├── decision-pane.html
│   ├── action-toolbar.html
│   └── audit-viewer.html
└── README.md
```

## Run it

```bash
cp -R skills/threadlight-workspace-ui/references/shapes/case-list src/workspace
cd src/workspace && python3 -m http.server 8000
# open http://localhost:8000/
```

Browsers block `fetch` from `file://`, so opening `index.html` directly shows
a banner that tells you to serve the folder. Use nginx for hosting (see the
parent SKILL.md "Hosting" section); `python3 -m http.server` is for local
preview only.

## What it does

- **Queue**: sortable columns (case, status, amount, decision, risk, SLA),
  free-text search, status and risk filter pills with counts. A
  `Pending approval` badge marks items held by the governed gateway.
- **Keyboard**: every case id is a button (Tab to reach, Enter to open,
  Arrow Up/Down to move through the queue); column headers are buttons with
  `aria-sort`; Escape closes the audit drawer and returns focus.
- **Detail pane**: summary fields, the pending-approval card (when present),
  the `decision-pane` panel with the `action-toolbar` in its slot, and a
  collapsible reasoning trace. `Audit log` opens the `audit-viewer` drawer.
- **Panels**: `workspace.js` fetches `components/*.html`, inserts the markup
  and runs each panel's own script, then fills `window.threadlight` exactly as
  described in `../../hitl-panels/README.md`. The accent from the data file is
  applied to the page and to each panel root (`--accent`).

## Approval boundary: this page never approves

A `pending_approval` item shows the operation id, the action and the proposed
arguments. The page itself never records a decision, never keeps one in
`localStorage` or memory, and hides the action toolbar for pending items.
The approver never needs a terminal; `config.approval` selects how the card
reads:

| `config.approval.mode` | Pending card | Who records the decision |
|---|---|---|
| `email` (`recipient`) | "Approval email sent to <recipient>" | The approver clicks **Approve** or **Reject** in the native Outlook approval email |
| `panel` (`url`, `token`) | **Approve** / **Reject** buttons | The authenticated approval endpoint at `url` (for example the governed-write kit's `approval_panel.py`), which signs the approver in as themselves and calls the control plane's one-use decision. The page posts only `{operation_id, approved}` with the session `token` |
| absent | `threadlight-review-action` command with **Copy command** | The operator fallback: an authorised reviewer runs it, signed in as themselves, with the exact pending result saved privately from the host (`governance_pending` frame), not this display copy |

In every mode the agent's later resume verifies and consumes the recorded
decision; nothing executes when the button is clicked.

The action toolbar appears only for statuses listed in
`config.actionable_statuses`. Its buttons call `window.threadlight.onAction`,
which POSTs `{action, case_id, reason}` to `config.writeback_url` (your agent
or a thin proxy in front of it). With no `writeback_url` the call fails with
"Not recorded" and nothing is written; the page never keeps decisions in
`localStorage` or memory as if they were recorded. If the writeback returns a
gateway `pending_approval` result, the case switches to the pending card.

## Data contract — `data/cases.json`

`schema` must be `threadlight-workspace-cases/v1`. A `{ "_meta", "records" }`
wrapper (from `threadlight-demo-data-factory`) is also accepted in place of
`cases`; `_meta` is never rendered.

| Field | Type | Notes |
|---|---|---|
| `workspace.process_name`, `workspace.agent_name` | string | Top bar and page title (replace `__PROCESS_NAME__` / `__AGENT_NAME__`) |
| `workspace.accent` | hex | One accent from `../../palettes.md` |
| `workspace.currency` | ISO 4217 | Amount formatting |
| `workspace.as_of` | ISO 8601, optional | Demo clock for SLA chips; omit to use the current time |
| `config.writeback_url` | string or `null` | Agent writeback endpoint; `null` keeps the page read-only |
| `config.actionable_statuses` | string[] | Statuses that show the action toolbar (default `awaiting_review`) |
| `config.approval` | object, optional | `{mode: "email", recipient}` or `{mode: "panel", url, token}`; see the approval boundary above |
| `config.review_command` | object | Operator fallback only (no `config.approval`): `control_plane_url`, `scope`, `tenant`, `client_id`, `role` placeholders for the reviewer command. Use real values only in a private deployment config, never in a shared example |
| `actions[]` | object | Action toolbar contract (`id`, `label`, `br`, `needsReason`, `destructive?`, `hint?`) |
| `cases[].id`, `customer`, `item`, `owner` | string | Row and summary |
| `cases[].status` | string | `pending_approval`, `awaiting_review`, `escalated`, `approved`, `completed`, `rejected` |
| `cases[].amount` | number | Amount column |
| `cases[].decision`, `risk` | string | Decision column; risk `high`/`medium`/`low` |
| `cases[].opened_at`, `sla_due` | ISO 8601 | SLA chip turns red under 10 minutes or when overdue |
| `cases[].recommendation` | object | `decision-pane` contract (`summary`, `confidence`, `evidence[]`, `citations[]`) |
| `cases[].reasoning[]` | `{tool, text}` | "Show why" trace |
| `cases[].audit[]` | object | `audit-viewer` contract, from your audit read API |
| `cases[].pending` | object, optional | Gateway pending result: `status: "pending_approval"`, `operation_id`, `proposed_arguments{case_id, decision, expected_etag, reason}`, `review_context{action, …}` |
| `cases[].pending_file` | string, optional | Where the reviewer saved the exact pending result (default `pending.json`) |

## Tailoring (Step 3 of the parent SKILL.md)

- Columns, filters and detail fields map to spec § 8b; edit `index.html`
  headers and the `renderRows` / `renderDetail` functions in `workspace.js`.
- Point `DATA_URL` at your sample data or an authenticated read endpoint.
- Keep the panels in `components/` byte-identical to `../../hitl-panels/`;
  restyle with `--accent`, not by editing panel CSS.

## Framework mapping

| Stack | `index.html` shell | Queue (`renderRows`) | Detail (`renderDetail`) | Panels | State |
|---|---|---|---|---|---|
| React | `Workspace.tsx` | `CaseTable` | `CaseDetail` | one component per panel, props from the `window.threadlight` contract | TanStack Query for data, local state for filters |
| Angular | `WorkspaceComponent` | `CaseTableComponent` | `CaseDetailComponent` | child components | signals or NgRx |
| Vue 3 | `WorkspaceView.vue` | `CaseTable.vue` | `CaseDetail.vue` | child SFCs | Pinia |
| Blazor | `Workspace.razor` | `CaseTable.razor` | `CaseDetail.razor` | child Razor components | `[Parameter]` + cascading state |

The API boundary is `DATA_URL` (read) and `config.writeback_url` (write to
the agent). Approval stays outside the UI: a rebuilt frontend may call the
control plane's decide endpoint only with the reviewer's own delegated token,
as described in the governance control-plane README.
