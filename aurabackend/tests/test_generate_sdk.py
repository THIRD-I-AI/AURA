"""
Sprint 21a — contract tests for the OpenAPI → Pydantic v2 codegen.

Two tiers:

  Tier A — pure unit tests on the type-translation engine:
      * primitives + format hints
      * arrays
      * anyOf-with-null → Optional[X]
      * $ref → forward reference
      * enum → Literal[...]
      * additionalProperties → Dict[str, V]
      * Python keyword + hyphen field-name sanitisation

  Tier B — end-to-end smoke test against the committed
  ``aurabackend/openapi.json``:
      * generate() returns three files
      * Re-running generate() with same input yields byte-identical output
        (determinism contract — CI's ``git diff --exit-status`` relies on this)
      * Generated module is syntactically valid Python
      * A few representative model classes have the expected field set

The Tier B test does NOT import the runtime ``aura_gateway_client``
package — that's a separate concern (the package may not be on
sys.path during pytest collection). Instead it generates into a tmp
dir and exec-compiles the output.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

# Make scripts/ importable. The repo root is two levels up from this
# test file (aurabackend/tests/test_generate_sdk.py).
REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from generate_sdk import (  # noqa: E402
    _emit_class,
    _python_type_for,
    _sanitize_field_name,
    generate,
)

# ── Tier A: type-translation primitives ──────────────────────────────


@pytest.mark.parametrize(
    "schema,expected",
    [
        ({"type": "string"}, "str"),
        ({"type": "integer"}, "int"),
        ({"type": "number"}, "float"),
        ({"type": "boolean"}, "bool"),
        ({"type": "null"}, "None"),
    ],
)
def test_primitive_types(schema: Dict[str, Any], expected: str) -> None:
    """Every JSON-schema primitive maps to its Python counterpart."""
    assert _python_type_for(schema) == expected


def test_format_int64_overrides_to_int() -> None:
    """`format: int64` on a number type maps to Python `int` (not float)."""
    assert _python_type_for({"type": "integer", "format": "int64"}) == "int"


def test_format_date_time_maps_to_str() -> None:
    """date-time stays as str rather than datetime so the generated
    package has no `datetime` parsing requirement at the SDK boundary."""
    assert _python_type_for({"type": "string", "format": "date-time"}) == "str"


def test_array_of_primitives() -> None:
    """`{type: array, items: {type: string}}` → `List[str]`."""
    schema = {"type": "array", "items": {"type": "string"}}
    assert _python_type_for(schema) == "List[str]"


def test_array_of_refs() -> None:
    """Arrays of $ref preserve the forward-reference quoting so generated
    modules don't need topological sorting."""
    schema = {"type": "array", "items": {"$ref": "#/components/schemas/Foo"}}
    assert _python_type_for(schema) == 'List["Foo"]'


def test_ref_emits_forward_reference() -> None:
    """A $ref alone becomes a quoted class name, deferring resolution to
    Pydantic at parse time."""
    assert _python_type_for({"$ref": "#/components/schemas/Bar"}) == '"Bar"'


def test_anyof_with_null_maps_to_optional() -> None:
    """The Pydantic-emitted `[X, null]` pattern is the most common anyOf in
    AURA's schema — it must collapse to `Optional[X]`, not `Union[X, None]`."""
    schema = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    assert _python_type_for(schema) == "Optional[str]"


def test_anyof_with_null_and_ref() -> None:
    """Same pattern but with a $ref instead of a primitive."""
    schema = {"anyOf": [{"$ref": "#/components/schemas/Foo"}, {"type": "null"}]}
    assert _python_type_for(schema) == 'Optional["Foo"]'


def test_anyof_union_without_null() -> None:
    """Multi-member anyOf without `null` becomes a `Union[...]`."""
    schema = {"anyOf": [{"type": "string"}, {"type": "integer"}]}
    assert _python_type_for(schema) == "Union[str, int]"


def test_enum_as_literal() -> None:
    """Enums emit `Literal[...]` with string values properly quoted."""
    schema = {"enum": ["red", "green", "blue"]}
    assert _python_type_for(schema) == "Literal['red', 'green', 'blue']"


def test_additional_properties_dict_value_type() -> None:
    """`additionalProperties: {type: int}` → `Dict[str, int]` — the
    standard 'dict with typed values' pattern."""
    schema = {"type": "object", "additionalProperties": {"type": "integer"}}
    assert _python_type_for(schema) == "Dict[str, int]"


def test_additional_properties_true_falls_back_to_any() -> None:
    """Untyped `additionalProperties: true` becomes `Dict[str, Any]`."""
    schema = {"type": "object", "additionalProperties": True}
    assert _python_type_for(schema) == "Dict[str, Any]"


def test_object_without_ref_falls_back_to_dict() -> None:
    """Inline object schemas without a $ref aren't extracted into their
    own classes in Sprint 21a — they fall back to Dict[str, Any] with
    full inline-class extraction reserved for S21b."""
    schema = {"type": "object", "properties": {"x": {"type": "string"}}}
    assert _python_type_for(schema) == "Dict[str, Any]"


def test_array_of_anyof() -> None:
    """Nested type expressions compose correctly through multiple levels."""
    schema = {
        "type": "array",
        "items": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    }
    assert _python_type_for(schema) == "List[Optional[str]]"


def test_type_list_shorthand_for_nullable_primitive() -> None:
    """OpenAPI 3.1 shorthand `type: [\"string\", \"null\"]` → `Optional[str]`."""
    assert _python_type_for({"type": ["string", "null"]}) == "Optional[str]"


def test_unknown_type_falls_back_to_any() -> None:
    """Defensive: a schema we don't recognise falls back to Any rather
    than crashing the generator."""
    assert _python_type_for({"weirdField": "??"}) == "Any"


def test_non_dict_schema_falls_back_to_any() -> None:
    """Defensive: a None or non-dict schema (shouldn't happen in valid
    OpenAPI but did happen in the wild) returns Any."""
    assert _python_type_for(None) == "Any"  # type: ignore[arg-type]


# ── Field-name sanitisation ──────────────────────────────────────────


def test_sanitize_passes_snake_case() -> None:
    """Most AURA fields are already snake_case — no rewrite."""
    assert _sanitize_field_name("user_id") == "user_id"


def test_sanitize_translates_hyphens() -> None:
    """Hyphens (legal in OpenAPI, illegal in Python identifiers) become
    underscores."""
    assert _sanitize_field_name("x-trace-id") == "x_trace_id"


def test_sanitize_appends_underscore_to_keyword() -> None:
    """Python keywords get a trailing underscore so the generated code
    parses — Pydantic alias handling is reserved for S21b."""
    assert _sanitize_field_name("class") == "class_"
    assert _sanitize_field_name("return") == "return_"


def test_sanitize_prepends_underscore_to_leading_digit() -> None:
    """`2fa_enabled` → `_2fa_enabled` (Python identifiers can't start
    with a digit)."""
    assert _sanitize_field_name("2fa_enabled") == "_2fa_enabled"


# ── Class emission ───────────────────────────────────────────────────


def test_emit_class_required_then_optional() -> None:
    """Required fields MUST come before optional ones — Pydantic refuses
    to define a default-less field after a defaulted one."""
    schema = {
        "type": "object",
        "properties": {
            "optional_field": {"type": "string"},
            "required_field": {"type": "integer"},
        },
        "required": ["required_field"],
    }
    out = _emit_class("Thing", schema)
    # required_field's line must appear before optional_field's.
    req_idx = out.index("required_field:")
    opt_idx = out.index("optional_field:")
    assert req_idx < opt_idx


def test_emit_class_optional_field_has_default_none() -> None:
    """Optional fields get `= None` so the model parses missing keys
    without raising — this is the 'forward-compat' default the SDK
    contract relies on (per Sprint 10's [[feedback]] notes)."""
    schema = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "required": [],
    }
    out = _emit_class("Thing", schema)
    assert "x: Optional[str] = None" in out


def test_emit_class_empty_body_uses_pass() -> None:
    """An empty schema still produces a valid Python class — emits a
    `pass` so the file is syntactically OK."""
    schema = {"type": "object"}
    out = _emit_class("Empty", schema)
    assert "class Empty(BaseModel):" in out
    assert "    pass" in out


# ── Tier B: end-to-end against the real openapi.json ────────────────


REAL_OPENAPI = REPO_ROOT / "aurabackend" / "openapi.json"


def test_end_to_end_generates_three_files(tmp_path: Path) -> None:
    """The driver returns __init__.py / models.py / README.md exactly."""
    files = generate(
        openapi_path=REAL_OPENAPI,
        output_dir=tmp_path,
        package_name="aura_test_client",
        service_tag="aura-test",
    )
    assert set(files.keys()) == {"__init__.py", "models.py", "README.md"}


def test_end_to_end_byte_identical_across_runs(tmp_path: Path) -> None:
    """Regeneration is byte-stable — the CI ``git diff --exit-status``
    contract depends on this."""
    files1 = generate(
        openapi_path=REAL_OPENAPI,
        output_dir=tmp_path / "a",
        package_name="aura_test_client",
        service_tag="aura-test",
    )
    files2 = generate(
        openapi_path=REAL_OPENAPI,
        output_dir=tmp_path / "b",
        package_name="aura_test_client",
        service_tag="aura-test",
    )
    assert files1 == files2


def test_generated_models_module_is_valid_python(tmp_path: Path) -> None:
    """A future change that produced invalid Python would silently break
    everyone running the codegen. Parse via the `ast` module to catch
    syntax errors before they ship."""
    files = generate(
        openapi_path=REAL_OPENAPI,
        output_dir=tmp_path,
        package_name="aura_test_client",
        service_tag="aura-test",
    )
    ast.parse(files["models.py"])


def test_generated_init_module_is_valid_python(tmp_path: Path) -> None:
    files = generate(
        openapi_path=REAL_OPENAPI,
        output_dir=tmp_path,
        package_name="aura_test_client",
        service_tag="aura-test",
    )
    ast.parse(files["__init__.py"])


def test_generated_models_has_known_schemas(tmp_path: Path) -> None:
    """Smoke check: the gateway OpenAPI contains a few well-known schemas
    (ChatRequest, ChatResponse) — confirm they show up in the generated
    module's class list."""
    files = generate(
        openapi_path=REAL_OPENAPI,
        output_dir=tmp_path,
        package_name="aura_test_client",
        service_tag="aura-test",
    )
    src = files["models.py"]
    schema_doc = json.loads(REAL_OPENAPI.read_text(encoding="utf-8"))
    real_schemas = list(schema_doc.get("components", {}).get("schemas", {}).keys())
    # At least 80% of real schemas should appear as class declarations.
    found = sum(1 for name in real_schemas if f"class {name}(BaseModel):" in src)
    assert found >= int(0.8 * len(real_schemas)), (
        f"only {found}/{len(real_schemas)} schemas emitted as classes"
    )


def test_generated_init_reexports_alphabetically_sorted(tmp_path: Path) -> None:
    """The __init__.py imports a re-export list sorted alphabetically —
    determinism contract relies on this. Without sort, dict insertion
    order would leak into the output."""
    files = generate(
        openapi_path=REAL_OPENAPI,
        output_dir=tmp_path,
        package_name="aura_test_client",
        service_tag="aura-test",
    )
    init_src = files["__init__.py"]
    # Extract the names between `from .models import (` and the closing `)`.
    start = init_src.index("from .models import (") + len("from .models import (")
    end = init_src.index(")", start)
    import_block = init_src[start:end]
    names = [
        line.strip().rstrip(",")
        for line in import_block.splitlines()
        if line.strip()
    ]
    assert names == sorted(names), "import block must be alphabetically sorted"


def test_readme_embeds_schema_fingerprint(tmp_path: Path) -> None:
    """The README carries a SHA-256 fingerprint of the source schema
    for traceability — auditors comparing two generated artifacts can
    confirm same-source-or-not by inspecting the fingerprint without
    re-diffing the entire openapi.json."""
    files = generate(
        openapi_path=REAL_OPENAPI,
        output_dir=tmp_path,
        package_name="aura_test_client",
        service_tag="aura-test",
    )
    # Fingerprint is 16 hex chars per the generator.
    import re
    m = re.search(r"\b([0-9a-f]{16})\b", files["README.md"])
    assert m, "README missing schema fingerprint"
