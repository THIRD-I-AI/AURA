"""BUG-192: DB source loader must not silently truncate or hide query errors."""
import pytest

from connectors.sql_limit import has_trailing_limit


@pytest.mark.parametrize("q", [
    "SELECT * FROM t LIMIT 5", "select * from t limit 5;", "SELECT * FROM t LIMIT 5 OFFSET 2",
    "SELECT * FROM t LIMIT 2, 5", "SELECT * FROM t LIMIT ALL",
])
def test_real_trailing_limit_is_detected(q):
    assert has_trailing_limit(q)


@pytest.mark.parametrize("q", [
    "SELECT rate_limit FROM t", "SELECT 'limit' AS x FROM t", "SELECT * FROM limits",
    "SELECT * FROM t WHERE note = 'no limit here'",
])
def test_word_limit_elsewhere_is_not_a_limit_clause(q):
    assert not has_trailing_limit(q)


class _Conn:
    def __init__(self, rows=None, err=None):
        self.rows, self.err, self.kw = rows, err, None

    async def connect(self):
        return True

    async def disconnect(self):
        pass

    async def execute_query(self, query, limit=1000, raise_errors=False):
        self.kw = (limit, raise_errors)
        if self.err:
            raise self.err
        return self.rows


async def _load(monkeypatch, conn):
    import duckdb

    import connectors
    from pipeline.engine import PipelineEngine
    from pipeline.models import PipelineSource, SourceType

    monkeypatch.setattr(connectors, "PostgreSQLConnector", lambda cfg: conn)
    src = PipelineSource(type=SourceType.POSTGRESQL, connection={}, query="SELECT 1")
    return await PipelineEngine()._load_db_source(duckdb.connect(":memory:"), src)


@pytest.mark.asyncio
async def test_query_error_surfaces_instead_of_reporting_no_data(monkeypatch):
    conn = _Conn(err=RuntimeError('relation "nope" does not exist'))
    with pytest.raises(RuntimeError, match="does not exist"):
        await _load(monkeypatch, conn)
    assert conn.kw[1] is True


@pytest.mark.asyncio
async def test_source_over_the_cap_is_rejected_not_truncated(monkeypatch):
    import pipeline.engine as eng

    monkeypatch.setattr(eng, "_MAX_SOURCE_ROWS", 3)
    conn = _Conn(rows=[{"a": i} for i in range(4)])
    with pytest.raises(ValueError, match="more than 3 rows"):
        await _load(monkeypatch, conn)
    assert conn.kw[0] == 4


@pytest.mark.asyncio
async def test_source_at_the_cap_loads(monkeypatch):
    import pipeline.engine as eng

    monkeypatch.setattr(eng, "_MAX_SOURCE_ROWS", 3)
    assert await _load(monkeypatch, _Conn(rows=[{"a": i} for i in range(3)]))
