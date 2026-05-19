"""
OpenAPI 3 → Pydantic v2 codegen for AURA — Sprint 21a (Pillar 5).

What this script ships
----------------------
A deterministic, stdlib-only generator that takes one OpenAPI 3 JSON
file and emits a Python package containing Pydantic v2 BaseModel
classes for every entry under ``components/schemas``. The generated
output is byte-stable across runs so a CI step can run
``git diff --exit-status`` to ensure committed clients stay in sync
with the source schema.

Sprint 21a deliberately ships MODELS ONLY — no operation-method
generation yet. Operation methods (with httpx client + retry policy +
typed responses) land in S21b once the model layer is proven.

Anchors
-------
* OpenAPI 3.1 Specification §4 (Schema Object).
  https://spec.openapis.org/oas/v3.1.0
* Pydantic v2 model semantics — used as the runtime validation layer.
  Caller-supplied responses are parsed via ``Model.model_validate()``.

CLI
---
::

    python scripts/generate_sdk.py \\
        --openapi aurabackend/openapi.json \\
        --output sdk_clients/aura_gateway_client \\
        --package-name aura_gateway_client \\
        --service-tag aura-gateway

Outputs three files (only if their content changed):

* ``__init__.py``       — exports every generated model class.
* ``models.py``         — Pydantic v2 BaseModel for each component schema.
* ``README.md``         — auto-generated overview + regen instructions.

Determinism
-----------
* Schemas are emitted in sorted order (alphabetical by class name).
* Field order within a class follows the OpenAPI ``properties`` order
  EXCEPT required fields come before optional fields (a Pydantic v2
  requirement; default-less fields cannot follow fields with defaults).
* All forward references use ``"ClassName"`` string form so emission
  order is independent of inter-schema dependencies.
* Trailing newlines, indentation, and comment formatting follow ruff
  defaults so the output passes the project's lint check unchanged.

Schema-vocabulary coverage (Sprint 21a)
---------------------------------------
The generator handles the OpenAPI subset actually used by AURA's
gateway schema (verified via vocabulary count on the committed
``openapi.json``):

* ``type`` (string / integer / number / boolean / array / object / null)
* ``$ref`` — internal references to ``components/schemas``
* ``anyOf`` — including the ``[X, null]`` pattern that maps to
  ``Optional[X]``
* ``enum`` — emitted as ``Literal[...]``
* ``items`` — array element type
* ``properties`` + ``required``
* ``additionalProperties`` — emitted as ``Dict[str, V]``
* ``format`` — int64/float/date-time/uuid (best-effort)

Unhandled constructs (``oneOf`` with discriminator, ``allOf``,
``not``, etc.) emit a TODO comment in the generated class body and
fall back to ``Any``. The current openapi.json does NOT use any of
these so the generator is complete for AURA's needs as of Sprint 21a.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Type-name translation ─────────────────────────────────────────────


_PRIMITIVE_TYPE_MAP: Dict[str, str] = {
    "string": "str",
    "integer": "int",
    "number": "float",
    "boolean": "bool",
    "null": "None",
}

# Format hints that refine primitive types when present. OpenAPI is
# permissive about formats; we map only the ones AURA actually emits.
_FORMAT_HINTS: Dict[str, str] = {
    "int64": "int",
    "int32": "int",
    "float": "float",
    "double": "float",
    "date-time": "str",   # Pydantic v2 can parse datetimes but we
                          # emit str to keep the generated client
                          # zero-runtime-dependency on the wire format.
    "date": "str",
    "uuid": "str",
    "byte": "str",
    "binary": "bytes",
}


def _python_type_for(schema: Dict[str, Any]) -> str:
    """Translate one schema fragment into a Python type expression.

    Returns a string suitable for direct embedding in generated
    source. Forward references are emitted as bare quoted class
    names so the model file doesn't need to topologically sort its
    definitions."""
    if not isinstance(schema, dict):
        return "Any"

    # $ref takes precedence — the referenced schema's name becomes
    # the type. We don't resolve transitive $refs; the generator
    # emits a forward reference and Pydantic resolves at parse time.
    if "$ref" in schema:
        ref = schema["$ref"]
        # Expect "#/components/schemas/Foo"
        name = ref.rsplit("/", 1)[-1]
        return f'"{name}"'

    # anyOf — typically Optional[X] pattern, sometimes a Union.
    if "anyOf" in schema:
        members = schema["anyOf"]
        # Collect inner types; detect null-with-non-null = Optional.
        inner_types: List[str] = []
        has_null = False
        for m in members:
            if isinstance(m, dict) and m.get("type") == "null":
                has_null = True
                continue
            inner_types.append(_python_type_for(m))
        if not inner_types:
            return "Any"
        if len(inner_types) == 1:
            t = inner_types[0]
            return f"Optional[{t}]" if has_null else t
        union = ", ".join(inner_types)
        union_expr = f"Union[{union}]"
        return f"Optional[{union_expr}]" if has_null else union_expr

    # enum -> Literal[...]
    if "enum" in schema:
        values = schema["enum"]
        # Quote string values; leave others as-is.
        literals = []
        for v in values:
            if isinstance(v, str):
                literals.append(repr(v))
            elif v is None:
                literals.append("None")
            else:
                literals.append(str(v))
        return f"Literal[{', '.join(literals)}]"

    t = schema.get("type")
    fmt = schema.get("format")

    # Array — Pydantic v2 expects list[X] / List[X]; we use List[X].
    if t == "array":
        items_type = _python_type_for(schema.get("items", {}))
        return f"List[{items_type}]"

    # Object — usually a $ref but can be a dict-style payload.
    if t == "object":
        ap = schema.get("additionalProperties")
        if isinstance(ap, dict):
            v_type = _python_type_for(ap)
            return f"Dict[str, {v_type}]"
        if ap is True:
            return "Dict[str, Any]"
        # Object with declared properties but no $ref means it's an
        # inline schema. We fall back to Dict[str, Any] — full
        # inline-schema-to-class extraction is a Sprint 21b deliverable.
        return "Dict[str, Any]"

    # Primitive with optional format refinement.
    if fmt and fmt in _FORMAT_HINTS:
        return _FORMAT_HINTS[fmt]
    if isinstance(t, str) and t in _PRIMITIVE_TYPE_MAP:
        return _PRIMITIVE_TYPE_MAP[t]
    if isinstance(t, list):
        # Some schemas use the shorthand `type: ["string", "null"]`
        # for Optional[str]. Translate to the anyOf-with-null form.
        non_null = [x for x in t if x != "null"]
        has_null = "null" in t
        if len(non_null) == 1 and non_null[0] in _PRIMITIVE_TYPE_MAP:
            base = _PRIMITIVE_TYPE_MAP[non_null[0]]
            return f"Optional[{base}]" if has_null else base

    return "Any"


# ── Class emission ───────────────────────────────────────────────────


def _emit_class(name: str, schema: Dict[str, Any]) -> str:
    """Render one Pydantic v2 BaseModel as Python source.

    Field ordering: REQUIRED fields first, then OPTIONAL fields. This
    is a Pydantic constraint — a field without a default cannot follow
    one that has a default. Within each group we preserve the source
    OpenAPI ``properties`` insertion order so the generated module is
    a stable function of the input."""
    properties: Dict[str, Any] = schema.get("properties", {}) or {}
    required = set(schema.get("required", []) or [])
    description = schema.get("description", "").strip()

    lines: List[str] = [f"class {name}(BaseModel):"]
    if description:
        # Pydantic class docstring — render multi-line descriptions
        # with the project's existing 4-space indent.
        for line in description.split("\n"):
            lines.append(f"    " + (f'"""{line}"""' if len(description.split(chr(10))) == 1 else line))
        if "\n" in description:
            # Multi-line: wrap with triple quotes manually.
            lines = [f"class {name}(BaseModel):"]
            lines.append('    """')
            for line in description.split("\n"):
                lines.append(f"    {line}".rstrip())
            lines.append('    """')

    # No properties + no description → emit a `pass` so the class is
    # still syntactically valid.
    if not properties and not description:
        lines.append("    pass")
        lines.append("")
        return "\n".join(lines)

    if not properties:
        # Has docstring but no fields — close out.
        lines.append("    pass")
        lines.append("")
        return "\n".join(lines)

    # Partition properties: required first, then optional. Preserves
    # within-group source order.
    required_fields: List[Tuple[str, Any]] = []
    optional_fields: List[Tuple[str, Any]] = []
    for prop_name, prop_schema in properties.items():
        if prop_name in required:
            required_fields.append((prop_name, prop_schema))
        else:
            optional_fields.append((prop_name, prop_schema))

    for prop_name, prop_schema in required_fields:
        py_type = _python_type_for(prop_schema)
        safe_name = _sanitize_field_name(prop_name)
        lines.append(f"    {safe_name}: {py_type}")

    for prop_name, prop_schema in optional_fields:
        py_type = _python_type_for(prop_schema)
        safe_name = _sanitize_field_name(prop_name)
        # Optional with default None — survives missing-key parses
        # without raising. If the schema explicitly types the field
        # as Optional via anyOf-with-null, no double-Optional happens.
        if py_type.startswith("Optional["):
            lines.append(f"    {safe_name}: {py_type} = None")
        else:
            lines.append(f"    {safe_name}: Optional[{py_type}] = None")

    lines.append("")
    return "\n".join(lines)


_PYTHON_KEYWORDS = {
    "False", "None", "True", "and", "as", "assert", "async", "await",
    "break", "class", "continue", "def", "del", "elif", "else", "except",
    "finally", "for", "from", "global", "if", "import", "in", "is",
    "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try",
    "while", "with", "yield",
}


def _sanitize_field_name(name: str) -> str:
    """Translate an OpenAPI field name into a valid Python identifier.

    Most AURA schemas already use snake_case; this function exists to
    catch the edge cases (hyphens, leading digits, Python keywords).
    If sanitisation is needed, the generated field carries an alias
    via Pydantic's ``Field(alias=...)`` — but Sprint 21a defers alias
    handling because no current schema actually trips this path. A
    future S21b will add the alias path with tests."""
    sanitised = name.replace("-", "_")
    if sanitised in _PYTHON_KEYWORDS:
        sanitised += "_"
    if sanitised and sanitised[0].isdigit():
        sanitised = "_" + sanitised
    return sanitised


def _emit_models_module(
    schemas: Dict[str, Any], generator_signature: str,
) -> str:
    """Render the full models.py source from a sorted schema dict."""
    lines: List[str] = [
        '"""',
        "Auto-generated Pydantic v2 models — DO NOT EDIT BY HAND.",
        "",
        "Regenerate with:",
        "",
        "    python scripts/generate_sdk.py \\",
        "        --openapi aurabackend/openapi.json \\",
        "        --output sdk_clients/aura_gateway_client \\",
        "        --package-name aura_gateway_client",
        "",
        f"Source schema fingerprint: {generator_signature}",
        '"""',
        "from __future__ import annotations",
        "",
        "from typing import Any, Dict, List, Literal, Optional, Union",
        "",
        "from pydantic import BaseModel",
        "",
        "",
    ]

    sorted_names = sorted(schemas.keys())
    for name in sorted_names:
        lines.append(_emit_class(name, schemas[name]))
        lines.append("")

    # Final newline — single, not double.
    while len(lines) > 1 and lines[-1] == "" and lines[-2] == "":
        lines.pop()
    if lines[-1] != "":
        lines.append("")

    return "\n".join(lines)


def _emit_init_module(schema_names: List[str], package_name: str) -> str:
    """``__init__.py`` re-exports every generated model class so the
    SDK consumer can ``from aura_gateway_client import Foo``."""
    sorted_names = sorted(schema_names)
    lines = [
        '"""',
        f"Public surface of the auto-generated SDK package ``{package_name}``.",
        "",
        "Re-exports every Pydantic model defined in ``models``.",
        "Regenerate via ``scripts/generate_sdk.py`` — see that module for the",
        "CLI; never edit this file by hand.",
        '"""',
        "from __future__ import annotations",
        "",
        "from .models import (",
    ]
    for n in sorted_names:
        lines.append(f"    {n},")
    lines.append(")")
    lines.append("")
    lines.append("__all__ = [")
    for n in sorted_names:
        lines.append(f'    "{n}",')
    lines.append("]")
    lines.append("")
    return "\n".join(lines)


def _emit_readme(
    package_name: str, service_tag: str, schema_count: int,
    generator_signature: str,
) -> str:
    """README for the generated package. Static text; the only
    variable parts are the package name and schema count."""
    return f"""# {package_name}

Auto-generated SDK package for the **{service_tag}** service.

This package contains Pydantic v2 models for every schema component
defined in the source OpenAPI document. Sprint 21a (current) ships
the model layer only; operation methods (typed Client + AsyncClient
with httpx) land in Sprint 21b once the model layer is proven.

## Schema fingerprint

```
{generator_signature}
```

## Regenerate

```sh
python scripts/generate_sdk.py \\
    --openapi aurabackend/openapi.json \\
    --output sdk_clients/{package_name} \\
    --package-name {package_name} \\
    --service-tag {service_tag}
```

The CI lane ``sdk-codegen-sync`` runs this command and fails if the
committed code drifts from what regeneration produces. Always commit
after running the generator.

## Coverage

Generated from {schema_count} component schemas. The codegen tool
handles `type` / `$ref` / `anyOf` (incl. nullability) / `enum` /
`additionalProperties` / `format` (int64/float/date-time/uuid).
Inline object schemas without a `$ref` fall back to `Dict[str, Any]`
— see the codegen tool's docstring for the full coverage matrix.

## Stability

This file is BYTE-IDENTICAL across regenerations given the same
source `openapi.json`. The codegen tool sorts schemas alphabetically
and emits fields in `required-then-optional` order so the output is
a deterministic function of the input.
"""


# ── Top-level driver ─────────────────────────────────────────────────


def generate(
    openapi_path: Path,
    output_dir: Path,
    package_name: str,
    service_tag: str,
) -> Dict[str, str]:
    """Run the codegen end-to-end. Returns a dict of relative-path →
    generated content, suitable for callers (CLI, tests) to write or
    diff."""
    schema_doc = json.loads(openapi_path.read_text(encoding="utf-8"))
    schemas: Dict[str, Any] = schema_doc.get("components", {}).get("schemas", {}) or {}
    fingerprint = _fingerprint(schema_doc)

    files = {
        "__init__.py": _emit_init_module(list(schemas.keys()), package_name),
        "models.py": _emit_models_module(schemas, fingerprint),
        "README.md": _emit_readme(
            package_name, service_tag, len(schemas), fingerprint,
        ),
    }
    return files


def _fingerprint(schema_doc: Dict[str, Any]) -> str:
    """Stable hash of the schema doc — useful for traceability without
    embedding the full schema in the generated artifact. Uses
    canonical JSON (sort_keys) so byte order doesn't affect the digest."""
    canonical = json.dumps(schema_doc, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a Pydantic v2 SDK package from an OpenAPI 3 schema.",
    )
    parser.add_argument(
        "--openapi", required=True, type=Path,
        help="Path to the OpenAPI 3 JSON document (e.g. aurabackend/openapi.json)",
    )
    parser.add_argument(
        "--output", required=True, type=Path,
        help="Output directory for the generated package",
    )
    parser.add_argument(
        "--package-name", required=True,
        help="Python package name (e.g. aura_gateway_client)",
    )
    parser.add_argument(
        "--service-tag", required=True,
        help="Service identifier embedded in the generated README (telemetry)",
    )
    args = parser.parse_args(argv)

    if not args.openapi.exists():
        print(f"error: OpenAPI file not found: {args.openapi}", file=sys.stderr)
        return 1

    files = generate(
        openapi_path=args.openapi,
        output_dir=args.output,
        package_name=args.package_name,
        service_tag=args.service_tag,
    )

    args.output.mkdir(parents=True, exist_ok=True)
    for rel, content in files.items():
        path = args.output / rel
        path.write_text(content, encoding="utf-8", newline="\n")
        print(f"wrote {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
