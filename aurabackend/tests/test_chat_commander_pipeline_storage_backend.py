"""
BUG-035: chat.py's Commander-mode pipeline-intent block (POST /chat, intent ==
"pipeline") used to list available_files via os.listdir(tenant_upload_dir(...))
directly, the same local-filesystem-only bug etl.py and pipelines.py had --
under AURA_STORAGE_BACKEND=s3, a plain upload lands only in S3, so this block
would see zero available files and PipelineGenerator.generate() would build a
pipeline with no real source file to work from.

Deterministically forces intent="pipeline" by monkeypatching IntentAgent.execute
(rather than depending on a real LLM's classification) and captures the kwargs
PipelineGenerator.generate() is actually called with, proving available_files
came from get_storage_backend().list(tenant) -- not a filesystem scan -- and
that a tenant is threaded through.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from agents.base import AgentResult  # noqa: E402
from api_gateway.main import app  # noqa: E402
from pipeline.models import (  # noqa: E402
    Pipeline,
    PipelineSink,
    PipelineSource,
    SinkType,
    SourceType,
)


def test_chat_pipeline_intent_lists_available_files_via_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_UPLOADS_ROOT", str(tmp_path))
    monkeypatch.delenv("AURA_STORAGE_BACKEND", raising=False)
    from shared.storage import get_storage_backend, reset_storage_backend
    reset_storage_backend()
    get_storage_backend().write(
        "default", "chat_sales.csv", b"region,revenue\nN,100\nS,200\n",
    )

    import api_gateway.routers.chat as chat_module

    async def _fake_intent_execute(self, ctx):
        return AgentResult(output={"intent": "pipeline"})

    monkeypatch.setattr(chat_module.IntentAgent, "execute", _fake_intent_execute)

    captured = {}

    async def _fake_generate(self, prompt, available_files=None, schema_context=None, connections=None, tenant=None):
        captured["available_files"] = available_files
        captured["tenant"] = tenant
        return Pipeline(
            name="chat-generated-test",
            source=PipelineSource(type=SourceType.FILE, file_name="chat_sales.csv"),
            sink=PipelineSink(type=SinkType.PREVIEW),
        )

    import pipeline.generator as generator_module
    monkeypatch.setattr(generator_module.PipelineGenerator, "generate", _fake_generate)

    async def _fake_save_pipeline(record):
        return {"id": record["id"], "name": record["name"]}

    monkeypatch.setattr(chat_module, "save_pipeline", _fake_save_pipeline, raising=False)
    import api_gateway.persistence as persistence_module
    monkeypatch.setattr(persistence_module, "save_pipeline", _fake_save_pipeline)

    client = TestClient(app)
    r = client.post("/api/v1/chat", json={"message": "build me a pipeline from chat_sales.csv"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "PipelineCreated", body

    # The real regression proof: available_files came from the backend's list(),
    # not an os.listdir() scan of a local tenant dir, and a tenant was passed
    # through to generate() rather than silently defaulting to None.
    assert captured["available_files"] == ["chat_sales.csv"], captured
    assert captured["tenant"] is None  # unauthenticated request -> "default" bucket
