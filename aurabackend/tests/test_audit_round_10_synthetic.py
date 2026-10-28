"""Audit round 10, synthetic-data area: BUG-536, BUG-537, BUG-538."""
import asyncio
import os

import pytest
from fastapi import HTTPException

from synthetic import ColumnSpec, SyntheticDatasetWriter, TableSchema, parse_size


def _files_under(root):
    return sorted(os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs)


# ── BUG-536: schema.name must not steer the write outside the output dir ──
@pytest.mark.parametrize("name", ["../../escaped", "..\\..\\escaped", "a/../../../escaped"])
def test_dataset_name_cannot_traverse_out_of_the_output_dir(tmp_path, name):
    base = tmp_path / "tenantA" / "job1" / "ds"
    schema = TableSchema(name=name, columns=[ColumnSpec(name="f")])
    writer = SyntheticDatasetWriter(schema, chunk_rows=1000)

    result = writer.generate(str(base), 20_000, dataset_name=name)

    base_real = os.path.realpath(base)
    for f in _files_under(tmp_path):
        assert os.path.commonpath((os.path.realpath(f), base_real)) == base_real, f
    data = [f for f in result.files if f.endswith(".parquet")]
    assert data and all(os.path.exists(f) for f in data)


def test_ordinary_dataset_names_are_unchanged(tmp_path):
    schema = TableSchema(name="orders_2024.v1", columns=[ColumnSpec(name="f")])
    result = SyntheticDatasetWriter(schema, chunk_rows=1000).generate(str(tmp_path), 20_000)
    assert os.path.basename(result.files[0]) == "orders_2024.v1-00000.parquet"


# ── BUG-537: the first (uncalibrated) file must be bounded by the byte target ──
@pytest.mark.parametrize("col", [
    ColumnSpec(name="s", dtype="string", prefix="", str_cardinality=10**15),
    ColumnSpec(name="x", dtype="float"),
])
def test_incompressible_data_does_not_overshoot_the_byte_target(tmp_path, col):
    target = 2_000_000
    writer = SyntheticDatasetWriter(
        TableSchema(name="t", columns=[col]), file_target_bytes=10**9,
    )
    result = writer.generate(str(tmp_path), target)
    # Old code wrote the whole uncalibrated plan into file 0: ~6.5x the target
    # for the string column, ~2.9x for the float column.
    assert result.total_bytes <= 1.15 * target, result.total_bytes
    assert result.total_bytes >= 0.8 * target, result.total_bytes


# ── BUG-538: non-finite / non-positive sizes are a 400, not a 500 ──
@pytest.mark.parametrize("spec", ["infGB", "1e400B", "-5GB", "-infB", "1e300PB", "0B"])
def test_parse_size_rejects_non_finite_and_non_positive_sizes(spec):
    with pytest.raises(ValueError):
        parse_size(spec)


def test_parse_size_still_accepts_normal_sizes():
    assert parse_size("500MB") == 500 * 10**6
    assert parse_size("1.5 GiB") == int(1.5 * 2**30)
    assert parse_size("1048576") == 1048576


@pytest.mark.parametrize("spec", ["infGB", "1e400B", "-5GB"])
def test_plan_route_returns_400_for_bad_sizes(spec):
    from api_gateway.routers.synthetic import PlanRequest, synthetic_plan

    req = PlanRequest(schema={"name": "t", "columns": [{"name": "f"}]}, target_size=spec)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(synthetic_plan(req))
    assert ei.value.status_code == 400
