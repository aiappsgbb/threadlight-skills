# Provenance

- **Status:** threadlight-owned. Threadlight maintains this copy; it is not synchronised with the source.
- **Source:** `aiappsgbb/awesome-gbb` commit [`7f1de882d5386e5a27852c91d3a89523eef218d0`](https://github.com/aiappsgbb/awesome-gbb/tree/7f1de882d5386e5a27852c91d3a89523eef218d0/skills/foundry-observability), path `skills/foundry-observability/`, source version 1.2.5.
- **Why ported:** the official `azure@azure-skills` plugin (microsoft/azure-skills `v1.2.77`, commit `74f27068b21b85807e35ae69ab3976756b060c03`) covers reading traces (`microsoft-foundry` `foundry-agent/trace`, including `references/kql-templates.md`), troubleshooting, generic App Insights Bicep (`appinsights-instrumentation/examples/appinsights.bicep`) and generic `configure_azure_monitor()` instrumentation; those stay official. It does not ship what Threadlight-generated infrastructure consumes: a postprovision script that creates the Foundry account-level App Insights connection for Bicep that Threadlight generates (the official azd project template wires its own), a guarded `init_telemetry()` helper (the official Python guidance calls `configure_azure_monitor()` unguarded at entry, which is the hosted-agent `server_error` failure mode recorded as gap O-011 in the hosted-agent reference), the single-workspace Bicep composition whose outputs the generators wire, and the safe-check first-trace probe and KQL probe helpers. The evidence is recorded in `skills/_shared/skill-dependencies.json`.
- **Changes at port:** renamed into a Threadlight reference (SKILL.md became README.md; frontmatter removed and replaced by a Threadlight-owned banner); sibling-skill references rewritten to Threadlight or official azure-skills equivalents. The official `microsoft-foundry` trace/troubleshoot/observe guidance and `appinsights-instrumentation` remain the default for reading traces and generic instrumentation.

## Licence of the source

MIT License

Copyright (c) 2026 AI Global Black Belts — Microsoft

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
