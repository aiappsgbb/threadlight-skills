# Phase 0: Poly-repo guard rationale and split recipe

> Part of the [`threadlight-deploy`](../SKILL.md) skill, moved out of the core
> SKILL.md verbatim (relative links rebased). Read it when the Phase 0 pre-flight checklist finds a multi-process repo.

### Why

We learned this the hard way in older multi-process repos. Multi-process
repos:
- Inflate Bicep into one giant template with 70% `if` blocks
- Force unrelated processes to share azd env, breaking iteration
- Make customer hand-off awkward (they only want one process; they get all 13)
- Concentrate blast radius — one botched deploy takes down siblings

### How to split

```
Before (rejected):                  After (each is its own azd up):
<multi-process-repo>/             <your-process-repo>/
├── <process-a>/                    ├── specs/
│   ├── specs/                       │   └── SPEC.md
│   └── src/                         ├── src/
├── <process-b>/                    └── azure.yaml
│   ├── specs/
│   └── src/                         retail-pim-enrichment/
└── azure.yaml  (← shared)           ├── specs/
                                     │   └── SPEC.md
                                     ├── src/
                                     └── azure.yaml
```

The `threadlight-design` skill respects this by default — it generates one
self-contained subtree per process. This skill enforces it.
