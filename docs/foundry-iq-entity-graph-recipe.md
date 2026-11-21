# Foundry IQ entity graph recipe (preview)

> **Preview.** Foundry IQ entity graphs use the Azure AI Search
> `2026-11-01-preview` API. Use them only when the SPEC graph-or-not rule
> (`skills/threadlight-design/references/speckit-template.md`, § 7) says so.
> This recipe adds no Threadlight skill: the mechanics belong to Microsoft's
> official [`foundry-iq-skills`](https://github.com/microsoft/GitHub-Copilot-for-Azure/tree/main/plugins/foundry-iq-skills)
> plugin (`/plugin install foundry-iq-skills@azure-skills`). Threadlight adds the
> order of the steps, what to check and how to record it.

Labels: **Documented** = official page cited; **Tested** = observed in the
validation run below; **Advice** = our recommendation.

## When to use it

Only when all four conditions of the graph-or-not rule hold: questions chain
facts across documents, every knowledge-base reader may read the whole corpus,
relations live in prose rather than structured data, and preview status and
build time are acceptable. Graph triples do not inherit document permissions,
so an ACL source with a graph is `GRD-005` must-fix in `threadlight-ground`
(**Documented**: the
[entity graph how-to](https://learn.microsoft.com/azure/search/agentic-knowledge-source-how-to-entity-graph)
lists no ACL ingestion support for this workflow).

## Steps

1. **Derive the ontology from the SPEC acceptance questions.** Only the entity
   types and relations those questions need (**Documented**: "Design your
   ontology around the key questions you expect users to ask"). The ontology is
   constrained JSON-LD with `owl:Class` and `owl:ObjectProperty` entries.
2. **Put naming rules in each class `rdfs:comment`**, for example "Always name
   it by its CMP code only, for example CMP-101, never with its description".
   Entity naming decides whether one real entity becomes one node or two
   (**Advice**, from the issue evidence in #219).
3. **Canonical IDs first.** If a supplier master, product catalogue or CMDB
   exists, build a grounding index with the plugin's `grounding-build` and pass
   `--grounding-index` when planning the build.
4. **Choose the ontology before it is attached.** Without a
   reprocessing-enabled cache set at creation, the ontology cannot change later,
   and an attached ontology cannot be removed (**Documented**, entity graph
   how-to). The plugin's default `--ontology deferred` builds with the
   enrichment cache on and attaches the ontology once; changing it afterwards
   means a rebuild (**Documented**, plugin `deferred-ontology.md`). Settle the
   ontology with a sample trial first.
5. **Run a sample trial** with `entity-graph-build plan --sample <n>` and
   `trial-report` before the full build when the corpus is large. A trial is
   never publishable and must be retired.
6. **Validate with direct GQL against ground truth before building on it.**
   Keep a small ground-truth file (expected triples and the expected answer to
   each relational acceptance question) and compare it with
   `entity-graph-consume query --graph --query-mode gql`. Report extracted,
   correct, missing and false-positive triples.
7. **Graph + text, `low` effort.** Pair the graph source with a text source;
   graph-only knowledge bases failed thematic questions in the #217 test.
   Microsoft recommends `gpt-5.6-luna` with low retrieval reasoning effort
   (**Documented**, entity graph how-to). Add `retrievalInstructions` that send
   relationship questions to the graph.
8. **Wire the knowledge base to the agent through the KB MCP tool**, as for any
   Foundry IQ knowledge base.
9. **Size TPM for graph retrieval.** NL→GQL used about 12.6k input tokens per
   graph retrieval in the #217 test; size the query model's TPM for that and
   watch for 429s.
10. **Do not filter on dates or numbers** unless the source text is normalized;
    extracted literals keep their original format.

After the build, declare the source in SPEC `knowledge_sources[]` with
`enrichments: [entity_graph]` and, if the source was ACL-protected,
`audience: uniform` plus a justification, so `threadlight-ground` can assess it.

## Cleanup

Use the plugin's retire workflow, never ad hoc deletes:
`entity-graph-build generations --descriptor <serving descriptor>`, then
`plan-retire --generation <id>` (with `--storage-endpoint` and
`--storage-resource-id` to include staged copies), then
`retire --plan-id <id> --confirm-writes`. Every write is approved from the plan
card. Retire removes trials and generations that no serving descriptor binds;
it refuses to plan without a protecting `--descriptor` unless only trials are
retired, and it leaves the enrichment cache in the cache storage account for
manual deletion (**Tested**). To remove the serving generation, stop serving it
first; to end a test, retire the trials and then delete the resource group,
only if it was created for the test.

## Validation run (November 2026)

**Tested** once on a fully synthetic, non-confidential corpus, with the
official plugin at commit `147c0813` (version 0.3): ontology, build, direct GQL
against ground truth, knowledge-base questions and cleanup. The coverage limits
are listed at the end of this section.

| Item | Value |
|---|---|
| Region, Search tier | Sweden Central, Serverless (the plugin's default for new resources) |
| Models | `gpt-5.6-luna` (extraction and KB), `text-embedding-3-small`, both Global Standard |
| Corpus | 160 text files: 40 fact documents (40 relations among 12 components, 7 suppliers, 4 plants, 6 incidents (an eighth supplier code appears only in distractors)) and 120 distractors that mention the same codes without relations |
| Ontology | 4 classes, 3 relations, naming rules in `rdfs:comment`, applied at creation (`inline_jsonld`) |
| Build | apply about 1 minute; verify: 160 content documents, 38 graph triples |
| GQL ground truth | 38/40 relations correct, 2 missing (`usedAt` facts), 0 false positives, 0 triples from the 120 distractors; one node per entity code |
| Relational questions (GQL) | 2-hop: 4/4 suppliers; negation: 3/3 components; 3-hop: 2/3 plants, the gap traced to one missing `usedAt` edge |
| Same questions through the plugin's KB mode | 2-hop and negation correct; 3-hop missed the same plant. Each call warned that the graph result "did not fit within the configured retrieval output limits"; latency 6.8–9.6 s |
| Sample trial | `--sample 10` after the full build: synchronized, then blocked with "managed content or graph index has insufficient documents" (cause not established; 120 of 160 documents carry no relations) |
| Cleanup | plugin `retire` of the trial generation (Search resources and 10 staged blobs removed), then deletion of the test resource group and purge of the soft-deleted Azure OpenAI account |

What the run taught us (**Tested**):

- Code-only naming rules gave one node per code and no triples from
  distractors, but extraction still missed 2 of 40 relations. Always compare
  with ground truth; a plausible answer can hide a missing edge.
- The KB can drop the graph result when it exceeds its retrieval output limit.
  Check `warnings` in every KB response during evaluation.
- If subscription policy disables public network access on new storage
  accounts, the plugin's "new resources" path stops. Use its "connect existing
  resources" path with resources your policy allows.
- With "connect existing", use an Azure OpenAI (`kind: OpenAI`) account: the
  plugin's data-plane probe calls `<properties.endpoint>/openai/v1/models`,
  which an `AIServices` account's `cognitiveservices.azure.com` endpoint
  rejected with 401 while `*.openai.azure.com` returned 200.

- The plugin's `plan-resume` after a stopped "new resources" apply failed with
  "Search has missing or unexpected attached identities" when Search used its
  system-assigned identity. We continued with "connect existing" rather than
  editing the plugin.

Not covered by this run: the deferred-then-attach path (the ontology was set
at creation), a successful trial report, a grounding index, a separate text
source next to the graph, the agent MCP wiring, TPM sizing under load, and a
plugin retire of a serving (non-trial) generation.
