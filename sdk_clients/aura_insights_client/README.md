# aura_insights_client

Auto-generated SDK package for the **insights** service.

This package contains Pydantic v2 models for every schema component
defined in the source OpenAPI document. Sprint 21a (current) ships
the model layer only; operation methods (typed Client + AsyncClient
with httpx) land in Sprint 21b once the model layer is proven.

## Schema fingerprint

```
7ab0c21c40fa7f67
```

## Regenerate

```sh
python scripts/generate_sdk.py \
    --openapi aurabackend/openapi.json \
    --output sdk_clients/aura_insights_client \
    --package-name aura_insights_client \
    --service-tag insights
```

The CI lane ``sdk-codegen-sync`` runs this command and fails if the
committed code drifts from what regeneration produces. Always commit
after running the generator.

## Coverage

Generated from 7 component schemas. The codegen tool
handles `type` / `$ref` / `anyOf` (incl. nullability) / `enum` /
`additionalProperties` / `format` (int64/float/date-time/uuid).
Inline object schemas without a `$ref` fall back to `Dict[str, Any]`
— see the codegen tool's docstring for the full coverage matrix.

## Stability

This file is BYTE-IDENTICAL across regenerations given the same
source `openapi.json`. The codegen tool sorts schemas alphabetically
and emits fields in `required-then-optional` order so the output is
a deterministic function of the input.
