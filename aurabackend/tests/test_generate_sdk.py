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
    _emit_one_operation,
    _python_type_for,
    _python_type_for_response,
    _sanitize_field_name,
    _shorten_operation_id,
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


def test_end_to_end_generates_four_files(tmp_path: Path) -> None:
    """The driver returns __init__.py / models.py / client.py / README.md.
    Sprint 21b added client.py; the count went from 3 to 4."""
    files = generate(
        openapi_path=REAL_OPENAPI,
        output_dir=tmp_path,
        package_name="aura_test_client",
        service_tag="aura-test",
    )
    assert set(files.keys()) == {"__init__.py", "models.py", "client.py", "README.md"}


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


# ── Sprint 21b: operation method emission ────────────────────────────


def test_shorten_operation_id_strips_api_v1_prefix() -> None:
    """FastAPI auto-IDs like `chat_endpoint_api_v1_chat_post` → `chat_endpoint`.
    The path-and-verb noise after `_api_v1_` is stripped."""
    assert _shorten_operation_id("chat_endpoint_api_v1_chat_post") == "chat_endpoint"


def test_shorten_operation_id_handles_path_params() -> None:
    """Operations with path parameters embed them in the ID:
    `approve_job_api_v1_jobs__job_id__approve_post` → `approve_job`."""
    assert (
        _shorten_operation_id("approve_job_api_v1_jobs__job_id__approve_post")
        == "approve_job"
    )


def test_shorten_operation_id_strips_trailing_verb_when_no_prefix() -> None:
    """Operations without `_api_v1_` (e.g. /health) strip just the verb."""
    assert _shorten_operation_id("health_health_get") == "health"


def test_shorten_operation_id_collapses_duplicate_trailing_segment() -> None:
    """`metrics_metrics_get` (FastAPI mounts /metrics as a top-level route)
    → `metrics`, not `metrics_metrics`."""
    assert _shorten_operation_id("metrics_metrics_get") == "metrics"


def test_response_type_for_ref_returns_quoted_class_and_model() -> None:
    """A $ref'd response narrows the return type to that model class
    AND returns the model name so the emitter can `model_validate()`
    after the HTTP call."""
    components = {"Foo": {}}
    schema = {"$ref": "#/components/schemas/Foo"}
    ret_expr, model = _python_type_for_response(schema, components)
    assert ret_expr == '"Foo"'
    assert model == "Foo"


def test_response_type_for_untyped_falls_back_to_dict() -> None:
    """No-schema and inline-schema responses both return
    `Dict[str, Any]` — Sprint 21c will extend to typed inline."""
    assert _python_type_for_response({}, {}) == ("Dict[str, Any]", None)
    assert _python_type_for_response({"type": "object"}, {}) == ("Dict[str, Any]", None)


def test_response_type_for_unknown_ref_falls_back_to_dict() -> None:
    """A $ref to a schema that doesn't exist in components is a
    malformed OpenAPI; emit Dict[str, Any] rather than a forward-ref
    to a non-existent class."""
    components = {}  # empty
    schema = {"$ref": "#/components/schemas/MissingModel"}
    ret_expr, model = _python_type_for_response(schema, components)
    assert ret_expr == "Dict[str, Any]"
    assert model is None


def test_emit_operation_basic_get_with_path_param() -> None:
    """Path params are positional, no body, no query. The URL is
    f-string-formatted with the path-param name from the spec
    (NOT the sanitised Python identifier)."""
    op = {
        "summary": "Get a job",
        "parameters": [
            {"name": "job_id", "in": "path", "schema": {"type": "string"}},
        ],
        "responses": {"200": {"content": {"application/json": {"schema": {}}}}},
    }
    src = _emit_one_operation("get_job", "get", "/jobs/{job_id}", op, {})
    assert "def get_job(self, job_id: str) -> Dict[str, Any]:" in src
    assert '"""Get a job"""' in src
    assert 'url = f"/jobs/{job_id}".format(job_id=job_id)' in src
    assert 'self._request("GET"' in src


def test_emit_operation_post_with_body() -> None:
    """Body is keyword with a typed Pydantic model when the request
    references a component schema."""
    op = {
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/CreateJobRequest"},
                },
            },
        },
        "responses": {"200": {"content": {"application/json": {"schema": {}}}}},
    }
    src = _emit_one_operation("create_job", "post", "/jobs", op, {})
    assert 'def create_job(self, body: "CreateJobRequest") -> Dict[str, Any]:' in src
    assert "json_body = body.model_dump" in src


def test_emit_operation_with_query_params() -> None:
    """Query params are keyword with `Optional[T] = None` defaults so
    callers can pass only what they care about. The body assembles a
    dict and strips None values."""
    op = {
        "parameters": [
            {"name": "limit", "in": "query", "schema": {"type": "integer"}},
            {"name": "offset", "in": "query", "schema": {"type": "integer"}},
        ],
        "responses": {"200": {"content": {"application/json": {"schema": {}}}}},
    }
    src = _emit_one_operation("list_jobs", "get", "/jobs", op, {})
    assert "limit: Optional[int] = None" in src
    assert "offset: Optional[int] = None" in src
    assert '"limit": limit,' in src
    assert "params = {k: v for k, v in params.items() if v is not None}" in src


def test_emit_operation_typed_return_uses_model_validate() -> None:
    """When the 200 response is a $ref, the method body parses the
    response via `Model.model_validate(...)` so the caller gets a
    typed instance instead of a raw dict."""
    components = {"JobStatus": {}}
    op = {
        "responses": {
            "200": {
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/JobStatus"},
                    },
                },
            },
        },
    }
    src = _emit_one_operation("get_status", "get", "/status", op, components)
    assert '-> "JobStatus":' in src
    assert "from .models import JobStatus" in src
    assert "return JobStatus.model_validate(response)" in src


def test_emit_operation_argument_order_path_then_body_then_query() -> None:
    """Argument order convention: path params (positional, required) →
    body (keyword, required if requestBody) → query params (keyword,
    optional with None defaults). This convention is what callers
    rely on — getting it backwards would break every call site."""
    op = {
        "parameters": [
            {"name": "job_id", "in": "path", "schema": {"type": "string"}},
            {"name": "verbose", "in": "query", "schema": {"type": "boolean"}},
        ],
        "requestBody": {
            "content": {"application/json": {"schema": {"type": "object"}}},
        },
        "responses": {"200": {"content": {"application/json": {"schema": {}}}}},
    }
    src = _emit_one_operation("update_job", "put", "/jobs/{job_id}", op, {})
    sig_line = next(line for line in src.split("\n") if line.startswith("    def "))
    # Path param (job_id) before body before query (verbose).
    assert sig_line.index("job_id") < sig_line.index("body")
    assert sig_line.index("body") < sig_line.index("verbose")


def test_generated_client_module_is_valid_python(tmp_path: Path) -> None:
    """The generated client.py must parse as valid Python AST.
    Without this, downstream consumers would get an ImportError at
    `from aura_gateway_client import Client` — a silent breakage."""
    files = generate(
        openapi_path=REAL_OPENAPI,
        output_dir=tmp_path,
        package_name="aura_test_client",
        service_tag="aura-test",
    )
    ast.parse(files["client.py"])


def test_generated_client_emits_one_method_per_operation(tmp_path: Path) -> None:
    """The committed openapi.json has 101 operations across all paths
    × methods; the generated Client class must have a method for each.
    Without this contract, a future change could silently drop methods."""
    files = generate(
        openapi_path=REAL_OPENAPI,
        output_dir=tmp_path,
        package_name="aura_test_client",
        service_tag="aura-test",
    )
    schema_doc = json.loads(REAL_OPENAPI.read_text(encoding="utf-8"))
    op_count = 0
    for path, methods in schema_doc["paths"].items():
        for m in methods:
            if m in {"get", "post", "put", "delete", "patch"}:
                op_count += 1
    # Each method emits as `    def <name>(self, ...):` — count those.
    method_def_count = files["client.py"].count("\n    def ") - 4
    # Subtract 4 for __init__, __enter__, __exit__, _url, _request,
    # _handle_response (6 private methods on the Client class). The
    # exact subtraction depends on the template — verify against the
    # real op count.
    method_def_count = sum(
        1 for line in files["client.py"].split("\n")
        if line.startswith("    def ")
        and not line.startswith("    def _")
        and not line.startswith("    def __")
    )
    assert method_def_count == op_count, (
        f"expected {op_count} operation methods, got {method_def_count}"
    )


def test_generated_client_byte_identical_across_runs(tmp_path: Path) -> None:
    """client.py must be byte-stable — same as models.py — so the
    CI git-diff drift check works for the new file too."""
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
    assert files1["client.py"] == files2["client.py"]


# ── Sprint 21c: AsyncClient emission ──────────────────────────────────


def test_emit_operation_async_def_prefix() -> None:
    """is_async=True emits `async def` instead of `def` and uses
    `await self._request(...)`. Argument list + return type are
    identical to the sync version — pure mechanical translation."""
    op = {
        "parameters": [
            {"name": "job_id", "in": "path", "schema": {"type": "string"}},
        ],
        "responses": {"200": {"content": {"application/json": {"schema": {}}}}},
    }
    sync_src = _emit_one_operation("get_job", "get", "/jobs/{job_id}", op, {})
    async_src = _emit_one_operation(
        "get_job", "get", "/jobs/{job_id}", op, {}, is_async=True,
    )
    assert "    async def get_job(" in async_src
    assert "    def get_job(" in sync_src and "    async def get_job" not in sync_src
    assert "await self._request" in async_src
    assert "await self._request" not in sync_src


def test_async_client_emitted_in_generated_module(tmp_path: Path) -> None:
    """The generated client.py must contain BOTH Client and AsyncClient
    classes. Consumers should be able to pick either based on their
    runtime (sync script vs async FastAPI / Jupyter)."""
    files = generate(
        openapi_path=REAL_OPENAPI,
        output_dir=tmp_path,
        package_name="aura_test_client",
        service_tag="aura-test",
    )
    src = files["client.py"]
    assert "class Client:" in src
    assert "class AsyncClient:" in src
    # AsyncClient uses async/await context manager.
    assert "async def __aenter__" in src
    assert "async def __aexit__" in src
    assert "await self._http.aclose()" in src


def test_async_client_methods_use_await_self_request(tmp_path: Path) -> None:
    """Every AsyncClient operation method must `await` the dispatch
    helper. A bare `self._request(...)` in an async method would
    return a coroutine instead of the parsed response."""
    files = generate(
        openapi_path=REAL_OPENAPI,
        output_dir=tmp_path,
        package_name="aura_test_client",
        service_tag="aura-test",
    )
    src = files["client.py"]
    # Find the AsyncClient section (everything after the class header).
    async_section = src.split("class AsyncClient:", 1)[1]
    # Every operation method in AsyncClient must use `await self._request`.
    sync_calls_in_async_section = async_section.count("\n        response = self._request(")
    assert sync_calls_in_async_section == 0, (
        f"AsyncClient has {sync_calls_in_async_section} non-awaited _request calls"
    )


def test_async_client_section_byte_identical_across_runs(tmp_path: Path) -> None:
    """AsyncClient byte-stability — same drift contract as Client + models."""
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
    assert files1["client.py"] == files2["client.py"]


def test_init_exports_both_client_and_async_client(tmp_path: Path) -> None:
    """__init__.py re-exports BOTH Client and AsyncClient so consumers
    can `from aura_gateway_client import AsyncClient`."""
    files = generate(
        openapi_path=REAL_OPENAPI,
        output_dir=tmp_path,
        package_name="aura_test_client",
        service_tag="aura-test",
    )
    init_src = files["__init__.py"]
    assert "Client," in init_src
    assert "AsyncClient," in init_src


def test_generated_init_module_exports_client_class(tmp_path: Path) -> None:
    """The package __init__.py must surface Client + exceptions so
    consumers can `from aura_gateway_client import Client`."""
    files = generate(
        openapi_path=REAL_OPENAPI,
        output_dir=tmp_path,
        package_name="aura_test_client",
        service_tag="aura-test",
    )
    init_src = files["__init__.py"]
    assert "from .client import" in init_src
    for sym in ("Client", "APIError", "NotFoundError", "RetryPolicy"):
        assert sym in init_src


# ── Sprint S21d: multi-service generated clients ─────────────────────


SDK_CLIENTS_DIR = REPO_ROOT / "sdk_clients"


# (package_name, service_dir, expected min method count) — the count
# is a floor so adding endpoints to a service doesn't break the test;
# REMOVING endpoints would. If a sprint legitimately reduces a
# service's surface, update the floor here in the same PR.
EXPECTED_CLIENTS = [
    ("aura_causal_client",            "causal_service",          3),
    ("aura_code_generation_client",   "code_generation_service", 2),
    ("aura_connectors_client",        "connectors",              14),
    ("aura_dar_client",               "dar_service",             7),
    ("aura_execution_sandbox_client", "execution_sandbox",       2),
    ("aura_gateway_client",           None,                      101),
    ("aura_ingestion_client",         "ingestion_service",       4),
    ("aura_insights_client",          "insights",                4),
    ("aura_metadata_store_client",    "metadata_store",          9),
    ("aura_orchestration_client",     "orchestration_service",   3),
    ("aura_scheduler_client",         "scheduler_service",       15),
]


@pytest.mark.parametrize("package_name,service_dir,min_methods", EXPECTED_CLIENTS)
def test_generated_client_imports_and_has_expected_methods(
    package_name: str, service_dir: str, min_methods: int,
) -> None:
    """Every generated SDK client must import cleanly + expose at
    least the expected number of operation methods on Client +
    AsyncClient. Catches: silently truncated codegen, missing
    classes in __all__, etc."""
    import importlib
    import sys as _sys

    if str(SDK_CLIENTS_DIR) not in _sys.path:
        _sys.path.insert(0, str(SDK_CLIENTS_DIR))

    module = importlib.import_module(package_name)
    # Both sync + async client classes must be exported.
    assert hasattr(module, "Client"), f"{package_name} missing Client"
    assert hasattr(module, "AsyncClient"), f"{package_name} missing AsyncClient"
    assert hasattr(module, "APIError"), f"{package_name} missing APIError"

    # Method-count floor — counts public methods on Client (one per
    # OpenAPI operation). AsyncClient has the same shape so checking
    # one is enough.
    c = module.Client(base_url="http://test.invalid")
    methods = [
        name for name in dir(c)
        if not name.startswith("_") and callable(getattr(c, name))
    ]
    assert len(methods) >= min_methods, (
        f"{package_name} has {len(methods)} methods, expected >= {min_methods}"
    )


def test_every_service_with_a_main_has_a_committed_schema() -> None:
    """If a developer adds a new service with a main.py, they should
    also add it to scripts/regen_all_sdks.py and commit the resulting
    aurabackend/<service>/openapi.json + sdk_clients/aura_<service>_client/.
    This test fails if a NEW service shows up without an openapi.json
    next to it — a forced reminder to register it with the codegen
    orchestrator."""
    import sys as _sys

    if str(REPO_ROOT / "scripts") not in _sys.path:
        _sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from regen_all_sdks import SERVICES

    registered_dirs = {service_dir for service_dir, *_ in SERVICES}
    # Plus the special cases that don't go through the orchestrator.
    registered_dirs.update({
        "api_gateway",            # has its own pipeline + repo-root openapi.json
        "counterfactual_service", # has hand-written aura-counterfactual SDK
    })

    backend = REPO_ROOT / "aurabackend"
    services_with_main = set()
    for child in backend.iterdir():
        if not child.is_dir():
            continue
        if (child / "main.py").exists():
            services_with_main.add(child.name)

    missing = services_with_main - registered_dirs
    assert not missing, (
        f"Service(s) with main.py NOT registered in regen_all_sdks.py "
        f"and not in the special-cases list: {sorted(missing)}. "
        f"Add to SERVICES in scripts/regen_all_sdks.py and regenerate."
    )


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
