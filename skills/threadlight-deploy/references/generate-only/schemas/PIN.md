# Vendored azure.yaml schema

`azure.yaml.schema.json` is a verbatim copy of
`schemas/v1.0/azure.yaml.json` from
[Azure/azure-dev](https://github.com/Azure/azure-dev) at commit
`4763e8e35249dce1dc31dcc0743e4f276f5f21b3` (MIT License,
Copyright (c) Microsoft Corporation).

It is vendored so `scripts/generate_only_selfcheck.py` can validate a
generated `azure.yaml` with no network access. Refresh it deliberately (new
commit SHA here) when azd adds fields the pilot scaffold needs; never fetch it
at self-check time.
