"""CLI tests using click's CliRunner."""
from __future__ import annotations

import json

import httpx
import respx
from click.testing import CliRunner

from aura_counterfactual.cli import cli

BASE = "http://aura.test"


def _invoke(args, *, env=None):
    runner = CliRunner()
    return runner.invoke(cli, ["--base-url", BASE, *args], env=env, catch_exceptions=False)


@respx.mock
def test_info_human_output():
    respx.get(f"{BASE}/api/v1/counterfactual/info").mock(
        return_value=httpx.Response(200, json={
            "engine_version": "0.2.0", "dowhy_available": True,
            "estimators": ["linear_regression"], "refuters": ["placebo"],
            "audiences": ["operator", "auditor", "analyst"],
        })
    )
    res = _invoke(["info"])
    assert res.exit_code == 0
    assert "engine_version" in res.output
    assert "0.2.0" in res.output


@respx.mock
def test_info_json_output():
    respx.get(f"{BASE}/api/v1/counterfactual/info").mock(
        return_value=httpx.Response(200, json={
            "engine_version": "0.2.0", "dowhy_available": True,
            "estimators": [], "refuters": [], "audiences": [],
        })
    )
    res = _invoke(["info", "--json"])
    assert res.exit_code == 0
    parsed = json.loads(res.output)
    assert parsed["engine_version"] == "0.2.0"


@respx.mock
def test_replay_outputs_artifact(sample_artifact):
    record_hash = sample_artifact["audit_record_hash"]
    respx.get(f"{BASE}/api/v1/counterfactual/artifacts/{record_hash}").mock(
        return_value=httpx.Response(200, json=sample_artifact),
    )
    res = _invoke(["replay", record_hash, "--json"])
    assert res.exit_code == 0
    parsed = json.loads(res.output)
    assert parsed["audit_record_hash"] == record_hash


@respx.mock
def test_replay_404_exits_4():
    respx.get(f"{BASE}/api/v1/counterfactual/artifacts/" + "0" * 64).mock(
        return_value=httpx.Response(404, text="not found"),
    )
    res = _invoke(["replay", "0" * 64])
    assert res.exit_code == 4


@respx.mock
def test_verify_succeeded_path():
    record_hash = "a" * 64
    respx.get(f"{BASE}/api/v1/counterfactual/artifacts/{record_hash}/verify").mock(
        return_value=httpx.Response(200, json={
            "record_hash": record_hash, "verified": True,
            "signature_status": "signed", "reason": "ok",
        })
    )
    res = _invoke(["verify", record_hash])
    assert res.exit_code == 0
    assert "OK" in res.output


@respx.mock
def test_verify_failed_path_exits_5():
    record_hash = "a" * 64
    respx.get(f"{BASE}/api/v1/counterfactual/artifacts/{record_hash}/verify").mock(
        return_value=httpx.Response(200, json={
            "record_hash": record_hash, "verified": False,
            "signature_status": "unsigned",
            "reason": "artifact was sealed without a signature",
        })
    )
    res = _invoke(["verify", record_hash])
    assert res.exit_code == 5


@respx.mock
def test_run_writes_artifact_to_save_path(tmp_path, sample_artifact):
    respx.post(f"{BASE}/api/v1/counterfactual/jobs").mock(
        return_value=httpx.Response(200, json={"job_id": "ca_xyz"}),
    )
    respx.get(f"{BASE}/api/v1/counterfactual/jobs/ca_xyz").mock(
        return_value=httpx.Response(200, json={
            "job_id": "ca_xyz", "state": "succeeded",
            "artifact": sample_artifact, "error": None,
        }),
    )
    query_file = tmp_path / "q.json"
    query_file.write_text(json.dumps({
        "question": "x",
        "treatment": {"column": "t", "actual": 1, "counterfactual": 0},
        "outcome": {"column": "y", "agg": "sum", "window": ["2025-01-01", "2025-12-31"]},
        "dag": {"edges": [["t", "y"]]},
        "dataset": {"source_id": "ds"},
        "audience": "analyst",
    }))
    save_path = tmp_path / "art.json"
    res = _invoke(["run", str(query_file),
                    "--save", str(save_path),
                    "--poll", "0.001"])
    assert res.exit_code == 0
    saved = json.loads(save_path.read_text())
    assert saved["audit_record_hash"] == "a" * 64


@respx.mock
def test_report_writes_pdf_to_disk(tmp_path):
    record_hash = "a" * 64
    respx.get(f"{BASE}/api/v1/counterfactual/artifacts/{record_hash}/report.pdf").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.4 test",
                                     headers={"content-type": "application/pdf"}),
    )
    out = tmp_path / "report.pdf"
    res = _invoke(["report", record_hash, "-o", str(out)])
    assert res.exit_code == 0
    assert out.read_bytes().startswith(b"%PDF-")


@respx.mock
def test_run_query_file_with_invalid_json_exits_1(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json at all")
    res = _invoke(["run", str(bad)])
    assert res.exit_code == 1
    assert "parse" in res.output


@respx.mock
def test_public_key_command():
    respx.get(f"{BASE}/api/v1/counterfactual/public-key").mock(
        return_value=httpx.Response(200, json={
            "public_key_pem": "-----BEGIN PUBLIC KEY-----\nABC\n-----END PUBLIC KEY-----\n",
            "key_source": "env_hex",
        }),
    )
    res = _invoke(["public-key"])
    assert res.exit_code == 0
    assert "BEGIN PUBLIC KEY" in res.output
