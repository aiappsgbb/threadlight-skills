# Pages and production-readiness narrative specification

**SPECIFICATION ONLY · 2026-09-14 · proposed, not deployed.**
This Markdown document specifies a bounded copy/navigation change for review.
It does not redesign the site, generate HTML, run a full site rebuild, change a
production workflow or publish anything. Existing source below is observed in
this checkout; proposed text below is **not** presented as current deployed copy.

## Implementation status

### Commercial editorial direction

The latest user direction makes `index.html`, `funnel.html`, `production.html`
and `agent-governance.html` commercial product/architecture pages: useful
autonomy, identity, policy, governance of selected tools and action paths,
human decisions, controlled execution, audit, business outcomes and clear CTAs.
They describe configurable capabilities, not a claim that every customer
deployment or notification/approval integration has already been validated.

Lab versions, pass/fail matrices, call counts, lease timestamps and instance
blockers belong in the linked execution record, not the architecture reading path.
Those technical facts remain unchanged. The older evidence-card proposals and
validation history below are retained design history, **not the commercial page contract**.
The current commercial copy requires its own focused UI checks; prior browser
passes are not silently reassigned to changed content.

### Architecture reader and Production-ready visual

**Third-area framing:** name the area **AgentOps & lifecycle**, not Production
readiness. Explain moving agent changes from development through test into
production: versioned code/prompts/model choices/policy, evaluation, DevSecOps,
authorized deployment, environment-specific configuration, fresh verification
and ongoing operation. Readiness remains a decision/check within that lifecycle.
This is editorial framing, not automatic adoption of the optional native
AgentOps integration or a new promise of turnkey environment promotion.
Existing fragment IDs, the other two areas and the accepted guide stay unchanged.

**Current approved information architecture:** three main areas, each with direct
subsection navigation. Platform/shared services contains the single primary
architecture, Citadel/model-route policy and existing data-access/privacy
responsibilities. Agent action governance retains the accepted returns example
and authorization explanation. AgentOps/lifecycle brings evaluation,
red-team and grounding checks before deployment/go-live, followed by fresh
verification and ongoing operation. Checks repeat after relevant changes.

The duplicated, collapsed platform architecture is removed from the current page;
its `#target` fragment now reaches the one visible platform architecture. Existing
model, information, quality and delivery fragments select their owning area.
Data controls are explicitly not supplied by the new action-governance runtime:
source/application owners enforce them, and readiness reviews their evidence.
The accepted technical guide remains unchanged; its six responsibility domains
are now grouped under three public navigation areas, not replaced or conflated.

**Previous simplification (superseded navigation grouping):**
**Current simplification for colleague review:** restore the general production
overview and retain the six independently navigable subsections. Platform and
network, model governance, quality, information protection, and operations/
deployment use their own domain explanations and diagrams. The return example
belongs only in agent behavior governance: its request, routine/exception cases,
authorization schematic and failure controls remain there. Remove the cross-page
return journey and continuation links. Keep the accepted technical Markdown,
original site design, detailed architecture/assessment references and no-JavaScript
fallback unchanged. This is a bounded review draft, not production publication or
an invitation to redesign the page again before colleague feedback.

**Superseded scenario-led experiment (design history, not current scope):**
the user found the selectable-topic page easier to
navigate but too abstract and visually repetitive. Keep that navigation and the
accepted Markdown; use one illustrative return as the public reading path.
Connect, read, reason, decide, record and improve lead into the six complementary
responsibilities. This is an explanatory order, not six sequential runtime
gates: evaluation starts before release, and privacy/operations apply throughout.
The scenario records a recommendation or supervisor handoff, not a payment.

Each topic now opens with a different incident in that same case. Visual
explanations are content-specific: an access topology with distinct routes and
an excluded direct-write path; a model-use contract beside its change gate; the
existing action-authorization schematic; an acceptance-criteria matrix; a
source/context/disclosure diagram; and a lost-response recovery timeline with
two reconciliation outcomes. Concrete failure/control catalogs expand on
demand. They cover exposure and privilege, model drift, source access and memory,
injection and disclosure, authority and freshness, evaluation coverage,
capacity/cost, observability, supply chain, recovery and sign-off.
Existing detailed architecture and assessment diagrams remain available.
Illustrative criteria are not measured results, and proposed boundaries are not
claims of automatic installation or complete deployed enforcement. The
grounding workflow assesses supplied evidence; it is not a retrieval engine.

**Current navigation decision:** the user accepted the improved architecture
Markdown and selected a single Production page with one topic visible at a time.
The Markdown is unchanged by this revision. A compact connected map introduces
the six responsibilities; the topic selector replaces the long chapter contents
bar. Each topic uses the same reading structure: problem, existing controls,
Threadlight proposal, mechanism and deeper reading.

Assessment/scorecard, business-outcome evidence, platform topology and delivery
material remain available as topic-owned disclosures, not consecutive chapters
on the primary path. Existing fragment links activate their owning topic and
open the required disclosure. Browser history and keyboard selection are
supported. Without JavaScript, the six topics remain readable and native
disclosures remain usable. No shared theme change or remote runtime is needed.

**Editorial correction: complementary production domains.** The prior visual
revision was not accepted as a sufficient explanation. Production is not an
agent-governance landing page. Its introduction now distinguishes platform and
network controls, model governance, agent behavior governance, quality and
evaluation, information protection, and operations/lifecycle. Each responsibility
has its own question, controls, owner, review focus and reading path. These are
editorial groupings, not new scoring pillars or a priority order.

The action-governance subsection must explain its gap before showing mechanics:
a valid identity, approved model and private connection can still carry an
ineligible, stale or repeated business instruction. Explain what stays in the
business API, what the agent-facing boundary adds, and the value of routine
automation, scoped human exceptions and attributable outcomes. Preserve the
existing site's visual system; do not promote this subsection above the chapter.

The technical companion follows those same responsibilities, then deepens the
action story with source-backed factory wiring, selected signed action fields,
strict proposal and resume JSON, independent business validation, conditional
commit, native human authority and recovery. Code fragments are integration
seams, not complete deployable applications or additional execution evidence.
Rendered checks do not establish editorial acceptance.

The chapter's anchor offset follows the masthead and, on compact screens, the
sticky horizontal topic selector. On desktop, navigation stays beside the
reading area. This supersedes the prior wrapping-contents-bar offset while
keeping introductions visible without changing shared assets.

The deep dive now leads with a navigable contents map, complementary production
responsibilities, the action-governance problem and two explicit integration
profiles. Implementation contracts follow that explanation. Historical execution
facts remain in `governed-returns-validation.md`; they are not duplicated as the
architecture's introduction or required as its headings.

The `production.html#effect-authority` section uses a connected static schematic:
propose, authorize, execute, with a separate native Outlook human-decision lane
and distinct audit and read boundaries. A vertical mobile flow and ordered text
equivalent remain readable without JavaScript. Shared Threadlight colors, fonts,
navigation and existing anchors are retained. The primary CTA opens the
architecture report, not a lab-status page.

Report diagrams are committed SVG images with descriptive alternative text and
collapsed Mermaid sources. Two sequence diagrams contained unescaped semicolons
that Mermaid interpreted as statement separators; those descriptions were
corrected. Regenerate images with `node scripts/render-governance-diagrams.mjs`;
use `--check` to detect stale images. The renderer uses the pinned Mermaid and
existing Playwright dependencies in `tests/playwright`; no remote rendering
service or client-side Mermaid dependency is required to read the documents.
This remains draft source and a local preview, not production Pages publication.

**Dedicated page implemented:** [Agent governance](agent-governance.html) now
provides the integrated architecture, business benefits, use case and adoption
journey. Its engineering links retain the exact validation and implementation
boundaries outside the commercial cards. It reuses the existing site assets and
navigation, with desktop/mobile dark/light rendered checks and focused keyboard,
landmark, link and privacy validation. This is a local/PR preview,
**not a production Pages deployment** or a whole-site accessibility certificate.

**Legacy page copy implemented:** `index.html`, `funnel.html` and
`production.html` now include the reviewed claim corrections, effect-authority
and evidence sections, and a link to the dedicated page. The writer released
ownership before integration; no concurrent editor or shared-theme rewrite was
used. Targeted Node/link checks and desktop/mobile browser observations passed.
The implementation session initially lacked the formal test dependency.
On September 14 the publisher restored the existing manifest dependencies and
completed formal validation: **16 targeted Playwright cases passed** across
desktop/mobile (10 new governance cases and six modified existing cases).
The first new-spec run was **8 passed / 2 failed**: axe found insufficient
contrast in page-local eyebrows, source labels and a narrative link. The scoped
`--ink-1` correction preserved underlines/focus without changing shared tokens,
excluding axe findings or weakening thresholds; all 10 new cases then passed.
Separate axe checks of both new sections in **light and dark** reported
**zero violations**. Eleven focused Node/link checks and asset-cache checks
also passed. These are bounded section/browser observations, **not full WCAG**
or whole-site certification, a green result for unrelated CI, or production
Pages deployment.

The source inventory and proposed-copy tables below retain the pre-implementation
review contract. The accepted branch HTML owns its final text; the acceptance
criteria are not a declaration that every future publication check has passed.
No shared theme, process-library generator or production workflow was changed.

The engineering companion is [Agent behavior governance: from access to action authority](agent-governance-deep-dive.md).
The [scenario execution record](governed-returns-validation.md) owns dated
observations. The [production-readiness reference](production-readiness.md)
owns the lifecycle, scorecard and evidence-input contract. These documents have
different purposes and must remain independently navigable.

## Product contract

**The prototype path remains unchanged.** Fast brief → design → local iteration
→ deployed pilot remains the entry experience. Governance is **explicit opt-in**
for selected consequential actions **before the effect boundary**, not a new
mandatory setup tax on every prototype and not a retrospective badge.

Once a real effect is selected, invalid configuration cannot become `off`.
Signed policy, independently authenticated approval where required, trusted
facts and durable audit must be ready before that action executes. Unbound reads
remain usable without ACS. Consequential unbound actions require current scoped
risk acceptance for readiness, not an arbitrary “skip security” switch.

**The model proposes; trusted components authorize effects.** This is the central
explanation, not “the AI is fully controlled.” Keep three propositions separate:

1. **Pilot speed:** a working pilot and its evidence can be produced quickly;
   do not attach an unmeasured performance guarantee.
2. **Effect authority:** a selected host/gateway path can reject unauthorized
   effects using exact identity/policy/approval/fact/audit checks.
3. **Production readiness:** quality, reliability, load, network isolation,
   privacy, recovery, operations and accountable human decisions need additional
   evidence. A working two-tool vertical is not certification.

No current-weekend event, conference or news announcement is needed to support
the narrative. Dates below refer only to the committed execution record.

## Existing entrypoints and source ownership

There is no SPA router or new application implied by this proposal. The site is
hand-authored HTML with shared assets and some Markdown pages handled by legacy
Jekyll. [docs/_config.yml](_config.yml) excludes `superpowers/` planning material
and preserves the standard vendor/cache exclusions. This approved public
specification belongs at top-level `docs/`, not inside that excluded tree.
Do not change Jekyll configuration simply to publish this specification.

Paths in the route column are under the existing `/threadlight-skills/` Pages
base. Links in this document resolve to current repository files; proposed new
fragments are written as code, not advertised as live links.

| Existing source and route | Current responsibility | Proposed responsibility, without renaming |
|---|---|---|
| [docs/index.html](index.html), `/threadlight-skills/` | Demo-led entry; `how-it-works`, historical demonstration, selected-runtime banner | Keep quick entry. Qualify the broad model/governance sentence at [index.html#how-it-works](index.html#how-it-works); link readers to the production authority explanation |
| [docs/funnel.html](funnel.html), `/threadlight-skills/funnel.html` | Lifecycle story, `scene-funnel`, skill chain, production-readiness handoff | Explain progressive evidence stages at [funnel.html#scene-funnel](funnel.html#scene-funnel), not a mandatory production setup before prototyping |
| [docs/production.html](production.html), `/threadlight-skills/production.html` | Production chapter; existing `chapter-top`, `why`, `checks`, `legs`, `proof`, `target`, `ship`, `start`, `chapter-recap` | Primary public authority/readiness explanation. Preserve all existing anchors; add only the explicitly proposed sections below |
| [docs/production-readiness.md](production-readiness.md) | Technical lifecycle, CI inputs, scorecard and remediation reference | Remain authoritative for artifact names, taxonomy and readiness inputs; no duplicate scoring engine in Pages |
| [docs/agent-governance-deep-dive.md](agent-governance-deep-dive.md) | Dedicated engineering treatment introduced with this specification | Expert CTA destination: actual protocols, TCB, states, source/test links and bounded evidence |
| [docs/governed-returns-validation.md](governed-returns-validation.md) | Dated S1/S2/S3 execution record | Evidence CTA destination, never substitute it for the implementation explanation |
| [docs/blueprint.html](blueprint.html), `/threadlight-skills/blueprint.html` | Process selection and artifact preview | No behavioral change; keep producer-owned names |
| [docs/workbook.html](workbook.html), `/threadlight-skills/workbook.html` | Guided adoption | Keep current entry/handoff; no new automatic governance requirement |

Current nav markup lives in each HTML page, not in a generated navigation
manifest. `docs/assets/site.js` enhances the existing DOM (including mobile
navigation, smooth scrolling and chain rails); it does not own page copy.
For this proposal retain the existing **Production-ready** nav label,
`href="./production.html"` and `aria-current="page"` on that page. No nav-wide
rename, asset edit or new top-level tab is required. New in-page sections should
use the existing `data-toc-id` / `data-toc-label` convention.

### CTA and route policy

- The homepage/funnel primary explanatory link should be
  `./production.html#effect-authority` **after** that new section is implemented.
  Until then, retain their existing working production/lifecycle links.
- Preserve [production.html#checks](production.html#checks),
  [production.html#proof](production.html#proof),
  [production.html#ship](production.html#ship) and
  [production.html#start](production.html#start).
- Repository readers use relative Markdown links to this
  [deep dive](agent-governance-deep-dive.md),
  [execution record](governed-returns-validation.md) and
  [runtime lifecycle](production-readiness.md#runtime-governance-lifecycle).
- Proposed **HTML-page** CTAs to long-form Markdown should use the established
  GitHub source pattern:
  `https://github.com/aiappsgbb/threadlight-skills/blob/main/docs/agent-governance-deep-dive.md`
  and
  `https://github.com/aiappsgbb/threadlight-skills/blob/main/docs/governed-returns-validation.md`.
  This avoids assuming a new rendered `.html` route. The files must first be
  present on the published branch. Existing Markdown links are not proof of a
  specific Jekyll-rendered route; verify any later pretty-route addition separately.
- Do not turn `production-readiness-pages-spec.md` into a customer “live proof”
  CTA. It is an implementation specification. Publishing this file is not evidence
  that any proposed HTML copy has shipped.

## Proposed section plan

| Surface / anchor | Existing copy or purpose | Proposed change |
|---|---|---|
| `index.html#how-it-works` | “You describe it. Copilot builds it. Foundry runs & governs it.” | Replace the broad governance claim with copy A below; preserve demo, install and brief entry |
| `funnel.html#scene-funnel` | “From a paragraph to a governed agent — in five named stages.” | Replace with copy B; diagram distinguishes progress from proof |
| `production.html#chapter-top` | “Your pilot works. Now prove it can ship.” | Keep the headline; add copy C as the scope qualifier |
| `production.html#why` | “The scorecard that signs.” | Replace with the human-decision wording in correction I below |
| **New** `production.html#effect-authority` | No dedicated source-level boundary narrative | Copy D, trust-boundary diagram and deep-dive CTA; place after `why` and before `checks` |
| `production.html#checks` | Thirteen-pillar/posture overview | Add copy E; keep network, action governance, output safety and human decisions distinct |
| `production.html#legs` | “Every gap has a skill that closes it.” | Replace with “Every gap needs an owner and verified closure.”; retain skill handoffs and the evidence threshold in correction I |
| `production.html#proof` | Board evidence/outputs | Copy F and dated proof labels; no scenario-to-whole-agent promotion |
| **New** `production.html#evidence-boundaries` | No dedicated S1/S2/S3 comparison | Compact scenario table, authority expiry and human/privacy blockers; source links, not raw logs |
| `production.html#target` | Target architecture | Describe target state as proposed/customer-specific, not as S3's public lab topology |
| `production.html#ship` | Pipeline handoff | Preserve `threadlight-cicd` ownership; no deployment by the page or scorecard |
| `production.html#start` | Four prompts and one scorecard; “ready in ~7m” | Copy G plus correction I's explicit report-generation qualification, not a readiness-time claim |
| `index.html` scorecard beat, summary card and recap | `92/100`, “ship with two waivers” / “ship with 2 waivers”, `92–100` | Apply correction I to all repeated captions and badges, not only the main heading |
| `production.html#chapter-recap` | Summary | Copy H; no new unconditional go-live claim |

The proposed additions have `data-toc-id="effect-authority"` /
`data-toc-label="Effect authority"` and `data-toc-id="evidence-boundaries"` /
`data-toc-label="Evidence limits"`. No source anchor is removed. Diagram content
also needs a plain-text explanation so meaning does not depend on animation,
color or Mermaid rendering.

## Exact proposed copy

The following blocks are **PROPOSED**, not quotations of deployed pages.
They can be reviewed as text before any separate HTML implementation.

### A. Fast entry without an implied universal control plane

> **You describe it. Copilot builds it. Foundry runs it.**
>
> Start with a working pilot. When you connect a consequential action, explicitly
> select its governance boundary before enabling the effect. The model proposes;
> trusted components authorize effects. Reads and prototype iteration do not need
> an automatic all-tools governance layer.

CTA: **“Understand the effect boundary”** → proposed
`./production.html#effect-authority`. Keep the current prototype/install CTA
at least as prominent; this is an explanatory branch, not a prerequisite wizard.

### B. Progressive production funnel

> **From a brief to a working pilot, then a reviewable path to production.**
>
> Build quickly. Select the real actions that need governance. Implement their
> identity, policy, approval and audit contracts before enabling those effects.
> Test locally, collect fresh deployment-bound evidence, and bring remaining
> quality, security, reliability and operational gaps to the production review.
> Each stage adds evidence; none certifies the whole system by itself.

Stage captions: **Prototype → Select effects → Implement controls → Prove the
selected path → Review production readiness**.
These are narrative stages, not a new orchestrator execution order. Existing
manual/live/cost-bearing handoffs remain manual; Auto does not execute them
merely because a funnel card appears.

### C. Production chapter introduction

> Your pilot works. Now prove it can ship.
>
> A working response is the beginning of the evidence, not the go-live decision.
> Selected runtime governance prevents unauthorized effects on explicitly bound
> paths. The production-readiness scorecard makes the remaining gaps, freshness,
> waivers and owners visible across thirteen pillars.

### D. Authority explanation

> **The model proposes; trusted components authorize effects.**
>
> The model does not approve its own write. Trusted host or gateway code validates
> the exact caller, action, policy and business facts; obtains genuine human
> approval when required; and waits for a central durable audit acknowledgement
> before dispatch. A separately authenticated backend checks current state and
> commits the permitted transaction.
>
> Prompts guide behavior. Output guardrails restrict supported returned content.
> Private networks restrict reachability. None replaces authorization of the
> exact business effect. This boundary is scoped to selected tools and does not
> control chain-of-thought or defend against arbitrary compromised trusted code.

CTAs: **“Read the engineering deep dive”** and **“Inspect the dated execution
record”** → the two Markdown source destinations above.

### E. Scorecard scope

> **A scorecard is evidence for a decision, not the decision itself.**
>
> Review the raw score, accepted waivers, verification coverage and evidence
> dates together. A missing permission or unexecuted check is not-verified,
> not a pass. Network posture, effect authority, output safety, human approval,
> recovery and operations are different requirements with different owners.

Source: [status taxonomy](production-readiness.md#5-status-taxonomy).
A `raw_score` or `score_with_waivers` must not hide `verification_coverage`,
`would_fail_hard_gate`, stale evidence or outstanding `must-fix` findings.
The general scorecard is advisory; the separate protected readiness-proof
workflow has explicit evidence gates. Do not silently turn advisory report
generation into a production deploy or redefine the workflow's exit semantics.

### F. Bounded proof

> **Real hosted business execution, with visible limits.**
>
> On September 13, 2026, a separate public-authenticated hosted MAF agent used two
> tools to read a synthetic return and record one Cosmos decision/audit through
> an authenticated governed gateway. Independent records also showed an exact-
> revision domain denial, pending approval with no execution, and an expired
> ungranted same-session attempt with no additional write.
>
> This is not financial settlement, private-network proof, continuous attestation
> or production certification. Successful hosted human-approved resume, positive
> replay and email delivery remain unproved. Read the dated record for current
> observations and the signed authority's expiry.

Do not replace “two tools” with “all tools.” Native before-return read ACK and
post-run 14-call/8-response reconciliation need separate labels below the card.

### G. Two next-step choices

> **Keep building, or prepare the production review.**
>
> For a new idea, start with the existing pilot path. For a consequential tool,
> explicitly select and implement its effect boundary before enabling it.
> For a deployed pilot, collect the missing evidence and produce the readiness
> scorecard. Deployment, human consent and customer go-live approval remain
> separate authorized steps.

CTAs: **“Start a pilot”** → current [funnel entry](funnel.html#scene-cta);
**“Read the readiness contract”** → [production-readiness.md](production-readiness.md);
**“Implement selected governance”** → [deep dive](agent-governance-deep-dive.md).
Do not invent a one-click deployment or automatically trigger a live probe.

### H. Recap

> **Fast prototypes. Explicit effect authority. Reviewable production evidence.**
>
> One selected path can be enforced while another remains unbound or unverified.
> Production requires evidence and accountable owners beyond the demonstration.

### I. Publisher claim-by-claim corrections

These phrases were present in the HTML source reviewed for this specification;
the table records the proposed corrections now implemented in the branch.
The HTML is not yet a production Pages deployment.
Use these exact replacements unless the stricter retention condition in the
last column is independently met and reviewed. This is a copy correction, not
authority to collect new live evidence or change a production workflow.

| Current phrase and location | Exact replacement or qualification | Evidence threshold |
|---|---|---|
| `production.html#why`: “The scorecard that signs.” | **“Evidence for the people who sign.”** Supporting sentence: “The scorecard organizes findings and evidence; accountable reviewers authorize the go-live decision.” | A generated report is sufficient only to claim report generation. An actual sign-off claim needs a dated, authenticated human decision record identifying authorized reviewers, deployment scope, residual risks and accepted conditions. The scorecard never signs on their behalf |
| `production.html#legs`: “Every gap has a skill that closes it.” | **“Every gap needs an owner and verified closure.”** Supporting sentence: “Skills support remediation and evidence collection; fresh verification determines whether a finding is closed.” | A skill recommendation or completed command does not close a finding. Require an accountable owner, completed remediation, the relevant check rerun and current scoped evidence. External permissions, consent and customer decisions may remain blocked; waivers are accepted risk, not technical closure |
| `production.html#start`: “ready in ~7m” | **“Report generation time varies; completion is not readiness.”** Keep the preceding report filename unchanged | A numerical duration may appear only as a dated **LOCAL** or **LIVE-SCOPED** measurement supported by a timestamped report-generation capture, documented start/end definition, inputs, environment, executed/skipped checks and output. Label it report-generation elapsed time, not time to production readiness or an SLA. No such benchmark is established by this specification |
| `index.html#how-it-works`: “Foundry runs & governs it.” | **“Foundry runs it.”** Follow with copy A's explicit opt-in explanation and effect-boundary CTA | Hosting/model execution supports “runs.” An enforcement claim needs current scoped evidence for the selected binding, actual PEP and authenticated effect/denial observations; Foundry hosting or a policy file alone is insufficient |
| `index.html` scorecard beat: `92/100` and “ship with two waivers” | Replace the claim-bearing sentence with: **“Illustrative scorecard — review findings, coverage and dates.”** Follow with: “The report records raw and waiver-adjusted scores, outstanding findings and owners; accountable reviewers make the go-live decision.” | Do not retain the number as earned/current without the corresponding dated manifest, exact deployment/configuration, `raw_score`, `score_with_waivers`, `verification_coverage`, freshness and unresolved findings. A historical value, if verified, must carry **HISTORICAL** scope; it is not a new-pilot score prediction |
| `index.html` scorecard take and summary card, plus `production.html#proof`: “ARB take: ship with 2 waivers” | Replace the take with **“Illustrative review scenario — no go-live approval evidenced here.”** For the index summary badge, replace `92/100` with **“Review evidence”** and its caption with **“Illustrative scorecard — review findings, coverage and dates.”** | To claim an actual ship-with-waivers decision, require the independent authorized human decision record plus explicit waiver owners, scope, compensating controls, expiry/review conditions and current supporting evidence. A score, `would_fail_hard_gate: false`, tool approval grant or illustrative ARB text is not that decision |
| `index.html` recap: `92–100` and “The 13-pillar production-ready scorecard — earned, not asserted.” | Replace the numeric badge with **“Evidence, not a score promise”** and the caption with **“Historical scorecard example — not a promised score range.”** | Retain “historical” only with the dated source capture; if that provenance cannot be established, use **“Illustrative scorecard example — not a promised score range.”** A score range requires a defined cohort of comparable manifests and disclosed coverage/waivers; no range or expected score is established here |

Apply the corrections to **all repeated captions**, numeric badges and ARB-take
labels in the named pages. A disclaimer elsewhere does not cure an unqualified
“ship” or readiness-time claim still visible in a replay frame or recap. If
copy is later reused in metadata or accessible labels, apply the same scope
there. Preserve the distinction between a return supervisor's business-action
approval and a customer's production go-live authorization.

## Diagram requirements

1. **Progressive funnel:** five captions from copy B. Prototype is available
   without a blanket governance setup. Put a visible gate immediately before
   enabling the selected consequential effect, not after the first real write.
   The production-readiness lane branches into quality/load/recovery/privacy/
   operations evidence rather than terminating in a green “secure” badge.
2. **Trust boundary:** model proposal → native host/FunctionTool → MCP gateway
   PEP with local ACS/Rego/OPA → separately authenticated business API → Cosmos
   conditional case/audit transaction. A separate control-plane lane shows
   signed policy, human decision/one-use consumption and durable pre-effect ACK.
   Distinguish Agent Identity, reviewer, gateway, downstream, writer and publisher.
   The unbound read takes its own authenticated path, with optional read audit.
3. **Network versus authority:** two independent axes, “reachability restricted”
   and “selected effect authorized/evidenced.” S3 is public-authenticated
   live-scoped business evidence. S2's earlier controls failed pre-session;
   private BASIC version 7 later produced a model response, followed by separate
   private governed version 4 allow/deny evidence. BASIC is not private governed
   business proof. Neither belongs in a universal “more secure” ranking.
4. **Evidence timeline:** native response/calls, inline read ACK before disclosure,
   central write authorization ACK before effect, transaction ACK, post-run
   reconciliation. Explicitly label 14 calls / 8 responses as a selected snapshot,
   not a continuously intercepted universe.

Use the engineering document's distinct control-plane/gateway state diagrams
as the protocol reference; do not compress them into a fictional shared
`approved → executing → done` API.

## Proof labels and publication boundaries

Labels below are **proposed presentation labels**, not new runtime schema enums.
Always accompany a label with subject/binding, date, source link and limitation.
Do not infer label value from a green screenshot or JSON file presence.

| Label | Admission rule | Example / required caveat |
|---|---|---|
| **OFFLINE** | Source/contract inventory or static assessment only | Policy and generated files exist; no execution implied |
| **LOCAL** | Actual scoped local/native tests executed | Native conformance is not Azure/business proof; skipped cases are not passes |
| **LIVE-SCOPED** | Authenticated deployment/invocation plus independently joined evidence for the stated binding | S3 selected two-tool vertical, public-authenticated, September 13 snapshot |
| **HISTORICAL** | Retained execution whose authority or deployment is no longer current | S1 VM results; signed authority expiry does not erase history |
| **NOT PROVED** | Required evidence absent, ambiguous, expired for current use or blocked | S3 human resume/replay/email; private pending/human/resume/replay/email (distinct from private allow/deny proof) |
| **PROPOSED** | Design/copy/target not implemented or deployed | This specification; customer target architecture diagram |

Preserve actual binding taxonomy: `enforced`, `observed`, `unbound`,
`unverified`, `unsupported`, `bypassable`. Preserve readiness finding taxonomy:
`pass`, `should-fix`, `must-fix`, `not-applicable`, `not-verified`, `waived`.
Do not translate `unbound` into a pass, or a waiver into technical enforcement.
Presentation labels cannot override the source manifests.

### Required dated evidence card

| Scenario | Public card content | Link / exclusion |
|---|---|---|
| S1, September 11 | Historical VM model allow; direct native deny and genuine human resume/replay | [S1](governed-returns-validation.md#s1-vm-hosted-native-maf-and-governed-mcp); not hosted, not an S3 grant |
| S2, September 13 and recorded September 14 update | Private pre-session failure despite attributed project-MI registry Login/Pull 200; sole unchanged canonical version-4 retry again returned `ProvisioningError`, with no native sessions and no overnight recovery observed | [September 14 diagnostic](governed-returns-validation.md#september-14-one-unchanged-private-transient-control-retry); same frozen image/scopes, not complete per-caller blob/unpack/snapshot/root-cause proof. Operator Push and scanner Pull are not project-MI acquisition evidence |
| S2, separate September 14 authorization at 10:55 Italy | Egress inspection found no attached customer NSG/UDR restriction to relax; shared NAT/PIP already existed. There was no customer network change. The separately authorized frozen private-control version 5 also failed before any native session | [Scoped egress observation](governed-returns-validation.md#september-14-scoped-egress-inspection-with-no-network-change); not an A/B relaxation test. Six project-MI registry pulls from the actual hosted subnet and shared NAT counters are not hosted Internet egress proof. Managed effective routes/source VM were not exposed |
| S2, later September 14 BASIC model smoke | Missing project ContainerRegistry connection corrected using native ManagedIdentity configuration; old-image version 6 still failed. The verified digest copy of the public BASIC v1 image produced private version 7, two active GETs and the real model response `Billing Issue`, independently retrieved in its active native session | [Private BASIC result](governed-returns-validation.md#september-14-private-basic-model-smoke-after-registry-binding-and-image-comparison); BASIC is not private governed business proof. The mutable source tag was resolved and the experiment pinned/compared exact digest bytes. This is not a format-only causal proof; do not claim that OCI is universally broken or that connection correction alone solved startup |
| S2, subsequent September 14 private governed proof | Fresh private governed version 4 demonstrated allow and exact deny, a real Cosmos decision/audit, matching central receipts, inline read ACKs and four calls from two responses persisted/read back in the post-run ledger | [Private governed result](governed-returns-validation.md#september-14-private-governed-allow-and-exact-deny-with-fresh-authority); the user was unavailable, so no new private pending intent was created. Human approval/resume/replay/email remain unproved. This is not fully governed in every respect |
| S3, September 13 | Hosted v5 selected allow/one decision-audit, exact-ETag domain deny, pending no execution; expired ungranted same-session generic failure | [S3](governed-returns-validation.md#s3-public-authenticated-foundry-hosted-execution); causal expiry guard not independently isolated; not private isolation |
| S3 evidence planes | Inline read audit ACK before response; separate post-run 14 calls / 8 responses | Not continuous attestation; explicit two-tool inventory |

**Freshness copy required next to the card:** “Public execution snapshot:
September 13, 2026. Documentation review: September 14, 2026. Signed bootstrap
expires **2026-09-14T10:26:48.991423+00:00** (12:26 Italy, UTC+02:00).
Final signed policy expires **2026-09-14T12:03:41.859581+00:00** (14:03 Italy,
UTC+02:00). The UTC values are exact; the Italy labels show the minute.
Historical successful receipts do not imply current executable authorization,
even before either expiry. Fresh checks of the binding, policy/key, identity,
configuration, business facts and required approval/audit authority are mandatory.
Neither expiry nor resource retention renews a grant or signed authority.
No automatic renewal; consult the dated record before claiming current readiness.”

Do not substitute the later policy expiry for the earlier bootstrap lease.
Expiry is not permission to delete or reset preserved resources or pending
records, and it never silently extends a human grant.
The subsequent private governed card has its own bootstrap expiry
`2026-09-15T11:12:28.719119+00:00` and policy expiry
`2026-09-15T11:32:54.753384+00:00`. It does not renew public S3 authority.
Future content updates must change the card only after the record changes, and
must distinguish observation time from page-edit time.

### Human, privacy and production blockers

- S3 genuine human approve/reject, successful hosted resume, positive replay and
  email delivery are **NOT PROVED**. S1 cannot fill those gaps.
- Office 365 needs actual mailbox consent; `Enabled` connection state did not
  mean authenticated. The notification workflow remains `Disabled` in the
  snapshot. Email/reply/click cannot issue a grant.
- The S3 `SecurityControl=Ignore` exception was an authorized public lab choice
  scoped to the new resource group, not production compliance or a recommended
  global default. Do not hide it in fine print or generalize it into a recipe.
- Keep **no private** targets, principal/tenant/subscription IDs, emails,
  credentials, cache paths or raw private logs on public pages. Use aliases and
  placeholders; only artifact basenames/digests already in the sanitized record
  may be repeated. Private evidence hashes do not provide public access to proof.
- Payload-minimized receipts are different from business decision-audits, which
  hold arguments/results. Data minimization, retention, access controls and
  publication approval require a privacy owner.
- Not established: full output/lifecycle coverage, OBO A-to-B, restoration,
  load/SLO behavior, exhaustive bypass resistance or production readiness.
  A separate per-user native MCP auth effort is not equivalent evidence.

## Artifact and generation ownership

[scripts/build_process_library.py](../scripts/build_process_library.py) is
**not a site generator**. It sanitizes an uncommitted raw process library through
`KEEP`, derives playbook metadata and writes
`docs/assets/process-library.json`. Do not use this documentation change to
regenerate that asset or alter skill selection. High-complexity entries can
recommend governance; recommendation is not automatic runtime binding.

[docs/assets/blueprint-logic.js](assets/blueprint-logic.js) consumes the library
and mirrors derivation behavior; its consistency is already tested. Keep
`entry_skill: threadlight-qualify` separate from `build_skills`, `run_skills: []`
and `run_skills_source: generated-by-threadlight-design`.
Runtime skill outputs come from the pilot's design, not a Pages badge.

Preserve these **exact generated artifact names**, rather than introducing
similarly named production scorecards in `specs/`:

| Producer / owner | Artifact names to display |
|---|---|
| `threadlight-govern` | `specs/governance-manifest.json` |
| `threadlight-production-ready` | `docs/production-readiness-report.md`, `tests/production-readiness-manifest.json` |
| `threadlight-cicd` | `.github/workflows/azd-deploy-prod.yml` |
| `threadlight-evals` | `specs/evals-manifest.json` |
| `threadlight-redteam` | `docs/redteam-report.md`, `specs/redteam-manifest.json` |
| `threadlight-consumption-iq` | `docs/cost-projection.md`, `specs/cost-manifest.json` |

These rows come from `SKILL_ARTIFACTS` in the producer. Separately, the runtime
lifecycle owns `tests/governed-actions-manifest.json` and
`.threadlight/governance-live.json`; do not pretend the process-library producer
currently emits either. The current governance manifest is
`threadlight-governance-manifest/v1`. No legacy v2 green verdict conversion.

Scorecard presentation must preserve `raw_score`, `score_with_waivers`,
`would_fail_hard_gate`, `verification_coverage`, `evidence_freshness` and
per-evidence `captured_at`. Display scope and stale/not-verified findings beside
the score, not behind an optional expansion. Pages does not recompute or
reinterpret the canonical validator.

## Build and validation ownership

| Existing source/check | Ownership / later implementation obligation |
|---|---|
| [docs/assets/site.js](assets/site.js), [site.css](assets/site.css) | Shared behavior/style; this proposal needs no theme rewrite |
| [docs/_config.yml](_config.yml) | Existing Jekyll exclusions; preserve them |
| [.github/workflows/docs-blueprint.yml](../.github/workflows/docs-blueprint.yml) | Runs Node publication/link/producer tests, documentation/runtime Python contracts, process-library drift and Pages browser tests; not a new deployment pipeline |
| [.github/workflows/pages-cache-bust.yml](../.github/workflows/pages-cache-bust.yml) | Checks shared-asset query tokens against content hash |
| [docs/ci/sync_cache_bust.py](ci/sync_cache_bust.py) | Owns `?v=<sha256-prefix>` values; only a future asset edit warrants synchronized token changes |
| [tests/blueprint/public-links.test.js](../tests/blueprint/public-links.test.js) | Existing local HTML targets and fragments |
| [tests/blueprint/published-surfaces.test.js](../tests/blueprint/published-surfaces.test.js) | Existing published narrative, skill inventory and metadata expectations; update assertions intentionally if copy changes |
| [tests/blueprint/process-library-generator.test.js](../tests/blueprint/process-library-generator.test.js) | Producer/consumer parity, exact artifact names and deterministic output |
| [tests/playwright/tests/site.spec.mjs](../tests/playwright/tests/site.spec.mjs) | Actual browser navigation/layout/accessibility regression surface for a later site implementation |
| [tests/ci/test_governance_deep_dive_docs.py](../tests/ci/test_governance_deep_dive_docs.py) | Small offline contracts for these two documents; no Azure calls, HTML generation or runtime proof |

Current scope: **no full site rebuild**, no process-library regeneration, no
browser installation, no workflow run and no production workflow modification.
Executed validation is limited to the focused contracts and publisher results
recorded in the implementation status above. Other listed checks remain
acceptance owners, not additional claimed passes from this task.

## Acceptance criteria

1. **Prototype preserved:** existing pilot entry/install/demo links still work
   without new governance configuration. Copy explicitly says governance is
   selected before the consequential effect; it does not place authorization
   after an initial real write.
2. **Falsifiable authority story:** a reader can identify the model proposal,
   trusted PEP/PDP, independent backend, real reviewer, pre-effect ACK and
   transaction. No claim of “100% secure,” “AI fully controlled,” whole-agent
   governance or production readiness derived from two tools is published.
3. **Real protocols only:** deep-dive links resolve; control-plane states remain
   `pending/decided/consumed`, gateway states remain
   `awaiting_approval/pending/rejected/completed`. No invented resume endpoint,
   approval flag or all-framework compatibility claim.
4. **Navigation intact:** all old fragments stay valid; the two proposed
   fragments are added before linking them. Local HTML link tests pass.
   The production nav retains correct `aria-current` and new sections have
   matching IDs/TOC attributes.
5. **Copy is consistently scoped:** title, meta description, Open Graph,
   social description, diagram labels and visible copy agree. Historic demo
   captions stay historic; target architecture stays proposed/customer-specific.
6. **Evidence not borrowed:** every live card includes scenario/date/binding
   and the negative space from the matrix. No S1 grant becomes S3 evidence;
   no private registry event becomes a hosted session; no generic failure
   becomes an independently isolated expiry cause.
7. **Freshness visible:** public September 13 snapshot and September 14 review
   date are separate. Both exact UTC expiries, their Italy reading aids and no
   renewal are adjacent to the proof card. The earlier bootstrap lease cannot
   be replaced by the later policy expiry. Fresh checks are required even before
   either expiry; historical receipts and resource preservation are not
   executable authority.
8. **Human and privacy gates visible:** Office 365 consent/Disabled workflow,
   unproved hosted human completion/replay/email, lab policy exception and
   private-evidence handling are in the main evidence section.
9. **Artifacts unchanged:** the exact names above match `SKILL_ARTIFACTS`;
   generated asset bytes and production workflows have no incidental diff.
   The two additional runtime artifacts retain their actual owner.
10. **Score is not certification:** raw/waivered scores, coverage, timestamps,
    stale evidence, waived gaps and must-fix/not-verified findings remain
    distinguishable. No auto-deploy follows a high score.
11. **Accessible diagrams:** textual equivalents, readable small-screen layout,
    keyboard navigation and non-color-only proof labels pass the existing
    browser checks when HTML is later implemented.
12. **Publish only after separate approval:** a future bounded HTML patch,
    intentional publication-test updates, applicable existing checks and human
    content/privacy review precede publishing. This Markdown specification
    alone satisfies none of those deployed-page acceptance claims.
13. **Publisher claims corrected at every occurrence:** the current signing,
    automatic gap-closure, `~7m`, universal hosting/governance, `92/100`,
    `92–100` and ship-with-waivers claims are replaced or individually qualified
    under correction I. No earned-score, duration or human sign-off claim remains
    without its stated evidence threshold. This acceptance criterion is for the
    later HTML change; the current task does not edit or rebuild the site.

## Handoff and explicit non-goals

The implemented patch covers `docs/index.html`, `docs/funnel.html` and
`docs/production.html`, plus narrowly corresponding publication/link tests.
Future refinements require explicit scope and current evidence. Do not redesign navigation,
change themes, regenerate the library, edit production workflows or deploy
resources as a side effect of that copy work.

Concrete infrastructure, immutable tag/image observation, role assignments,
mailbox/API consents, synthetic case seeding, signing and final binding are
operator-owned; source closure is not an end-to-end one-command deployment.
The `08:12` September 14 private transient-control diagnostic belongs to its
parent/operator record. **No second attempt was made under that earlier
authorization**. The separate **10:55 Italy** egress authorization and private
version-5 observation must retain their own no-network-change scope; they do not
replace the morning history. The later registry-connection/image comparison
adds private BASIC hosting/model evidence, not private business governance.
Its unused placeholder local tool does not make it the strict zero-tool sample.
Further attempts need a **new observed difference
or justified correction** and applicable authority. This specification is ready
for review, not an assertion that a new Pages experience is live.
