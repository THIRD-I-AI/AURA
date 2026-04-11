"""
Pipeline Models Unit Tests
============================
Tests for Pipeline, PipelineSource, PipelineSink, ProcessingStep,
PipelineRun, and all enum types.
"""


from pipeline.models import (
    Pipeline,
    PipelineRun,
    PipelineSink,
    PipelineSource,
    PipelineStatus,
    ProcessingStep,
    SinkType,
    SourceType,
    StepType,
)

# ── Enum coverage ────────────────────────────────────────────────────────────

class TestEnums:
    def test_source_types(self):
        assert SourceType.FILE == "file"
        assert SourceType.POSTGRESQL == "postgresql"
        assert SourceType.DUCKDB == "duckdb"

    def test_sink_types(self):
        assert SinkType.FILE == "file"
        assert SinkType.PREVIEW == "preview"

    def test_step_types(self):
        assert StepType.FILTER == "filter"
        assert StepType.SORT == "sort"
        assert StepType.CUSTOM_SQL == "custom_sql"

    def test_pipeline_status(self):
        assert PipelineStatus.DRAFT == "draft"
        assert PipelineStatus.RUNNING == "running"
        assert PipelineStatus.SUCCESS == "success"
        assert PipelineStatus.FAILED == "failed"


# ── PipelineSource ───────────────────────────────────────────────────────────

class TestPipelineSource:
    def test_file_source(self):
        src = PipelineSource(type=SourceType.FILE, file_name="data.csv")
        assert src.type == SourceType.FILE
        assert src.file_name == "data.csv"
        assert src.label() == "data.csv"

    def test_db_source(self):
        src = PipelineSource(type=SourceType.POSTGRESQL, table="orders")
        assert src.label() == "postgresql://orders"

    def test_query_source(self):
        src = PipelineSource(
            type=SourceType.DUCKDB,
            query="SELECT * FROM raw_data",
        )
        assert "query" in src.label()

    def test_file_source_no_name(self):
        src = PipelineSource(type=SourceType.FILE)
        assert src.label() == "unknown_file"


# ── ProcessingStep ───────────────────────────────────────────────────────────

class TestProcessingStep:
    def test_auto_id(self):
        step = ProcessingStep(type=StepType.FILTER)
        assert step.id.startswith("step_")

    def test_config_defaults_empty(self):
        step = ProcessingStep(type=StepType.SORT)
        assert step.config == {}

    def test_with_config(self):
        step = ProcessingStep(
            type=StepType.FILTER,
            description="Keep high revenue",
            config={"column": "revenue", "operator": ">", "value": 1000},
        )
        assert step.config["column"] == "revenue"
        assert step.description == "Keep high revenue"

    def test_join_step_with_source(self):
        from pipeline.models import JoinSource
        step = ProcessingStep(
            type=StepType.JOIN,
            join_source=JoinSource(type=SourceType.FILE, file_name="lookup.csv"),
        )
        assert step.join_source is not None
        assert step.join_source.file_name == "lookup.csv"


# ── PipelineSink ─────────────────────────────────────────────────────────────

class TestPipelineSink:
    def test_file_sink_defaults(self):
        sink = PipelineSink(type=SinkType.FILE)
        assert sink.format == "csv"
        assert sink.if_exists == "replace"

    def test_preview_sink(self):
        sink = PipelineSink(type=SinkType.PREVIEW)
        assert sink.type == SinkType.PREVIEW

    def test_db_sink(self):
        sink = PipelineSink(
            type=SinkType.POSTGRESQL,
            table="output_table",
            if_exists="append",
        )
        assert sink.table == "output_table"
        assert sink.if_exists == "append"


# ── Pipeline ─────────────────────────────────────────────────────────────────

class TestPipeline:
    def _simple_pipeline(self, **kwargs):
        return Pipeline(
            name="test-pipeline",
            source=PipelineSource(type=SourceType.FILE, file_name="input.csv"),
            sink=PipelineSink(type=SinkType.PREVIEW),
            **kwargs,
        )

    def test_auto_id(self):
        p = self._simple_pipeline()
        assert p.id.startswith("pipe_")

    def test_default_status(self):
        p = self._simple_pipeline()
        assert p.status == PipelineStatus.DRAFT

    def test_created_at_populated(self):
        p = self._simple_pipeline()
        assert p.created_at is not None
        assert "T" in p.created_at  # ISO format

    def test_with_steps(self):
        p = self._simple_pipeline(
            steps=[
                ProcessingStep(type=StepType.FILTER, config={"column": "x", "value": 1}),
                ProcessingStep(type=StepType.SORT, config={"column": "y", "order": "desc"}),
            ]
        )
        assert len(p.steps) == 2

    def test_tags(self):
        p = self._simple_pipeline(tags=["etl", "daily"])
        assert "etl" in p.tags

    def test_generated_prompt(self):
        p = self._simple_pipeline(generated_from_prompt="filter products by price")
        assert p.generated_from_prompt == "filter products by price"

    def test_serialization_roundtrip(self):
        p = self._simple_pipeline(description="Test pipeline")
        d = p.model_dump()
        p2 = Pipeline(**d)
        assert p2.name == p.name
        assert p2.source.type == SourceType.FILE


# ── PipelineRun ──────────────────────────────────────────────────────────────

class TestPipelineRun:
    def test_auto_run_id(self):
        run = PipelineRun(pipeline_id="pipe_abc123")
        assert run.run_id.startswith("run_")

    def test_default_status_running(self):
        run = PipelineRun(pipeline_id="pipe_abc123")
        assert run.status == PipelineStatus.RUNNING

    def test_counters_default_zero(self):
        run = PipelineRun(pipeline_id="pipe_abc123")
        assert run.rows_read == 0
        assert run.rows_written == 0
        assert run.steps_executed == 0
        assert run.duration_ms == 0.0

    def test_error_field(self):
        run = PipelineRun(
            pipeline_id="pipe_abc123",
            status=PipelineStatus.FAILED,
            error="Column 'foo' not found",
        )
        assert run.error == "Column 'foo' not found"
        assert run.status == PipelineStatus.FAILED

    def test_output_fields(self):
        run = PipelineRun(
            pipeline_id="pipe_abc123",
            rows_read=1000,
            rows_written=950,
            output_file="result.csv",
            columns_in=["a", "b", "c"],
            columns_out=["a", "b"],
        )
        assert run.rows_read == 1000
        assert run.output_file == "result.csv"
        assert len(run.columns_out) == 2
