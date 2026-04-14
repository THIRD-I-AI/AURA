"""
AURA Integration Tests
End-to-end testing of profiling, semantic modeling, and insights
"""

import asyncio
import csv
import json
import os
import tempfile
from typing import Any, Dict, List

import pytest

# Test fixtures and utilities


@pytest.fixture
def sample_csv_file():
    """Create a temporary CSV file for testing"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        writer = csv.writer(f)
        writer.writerow(['Date', 'Product', 'Revenue', 'Quantity', 'Region'])
        writer.writerow(['2024-01-01', 'Widget A', '1000.50', '10', 'North'])
        writer.writerow(['2024-01-01', 'Widget B', '2000.75', '20', 'South'])
        writer.writerow(['2024-01-02', 'Widget A', '1500.50', '15', 'North'])
        writer.writerow(['2024-01-02', 'Widget B', '2500.75', '25', 'South'])
        writer.writerow(['2024-01-03', 'Widget A', None, '12', 'East'])
        f.flush()
        yield f.name
    os.unlink(f.name)


@pytest.fixture
def test_data() -> List[Dict[str, Any]]:
    """Test data for analysis"""
    return [
        {'date': '2024-01-01', 'product': 'Widget A', 'revenue': 1000, 'quantity': 10},
        {'date': '2024-01-02', 'product': 'Widget B', 'revenue': 2000, 'quantity': 20},
        {'date': '2024-01-03', 'product': 'Widget A', 'revenue': 1500, 'quantity': 15},
        {'date': '2024-01-04', 'product': 'Widget B', 'revenue': 2500, 'quantity': 25},
        {'date': '2024-01-05', 'product': 'Widget A', 'revenue': 1200, 'quantity': 12},
    ]


# ==================== Test File Service ====================

def test_file_service_profiling():
    """Test CSV file profiling"""
    pd = pytest.importorskip("pandas", reason="pandas not installed")
    pytest.importorskip("numpy", reason="numpy not installed")
    from shared.file_service import FileService

    # Create test CSV
    data = {
        'name': ['Alice', 'Bob', 'Charlie'],
        'age': [25, 30, 35],
        'salary': [50000.0, 60000.0, 70000.0],
    }
    df = pd.DataFrame(data)

    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        df.to_csv(f, index=False)
        f.flush()
        tmp_name = f.name

    try:
        # Profile the file
        file_service = FileService()
        profile = file_service._profile_dataframe(df)

        assert profile is not None
        assert 'columns' in profile
        assert profile['columns'] == 3
        assert 'rows' in profile
        assert profile['rows'] == 3
        assert 'columns_profile' in profile

        # Check column profiles
        col_profile = profile['columns_profile']
        assert 'name' in col_profile
        assert col_profile['name']['non_null'] == 3
    finally:
        os.unlink(tmp_name)


# ==================== Test Semantic Builder ====================

@pytest.mark.skip(reason="semantic_builder module not yet in codebase")
def test_semantic_model_generation():
    """Test semantic model auto-generation"""
    import uuid

    from semantic_builder import SemanticModelBuilder

    # Create a sample profile
    profile = {
        'table_name': 'sales',
        'rows': 1000,
        'columns': 3,
        'columns_profile': {
            'product_id': {
                'data_type': 'integer',
                'non_null': 1000,
                'distinct': 50,
                'samples': [1, 2, 3],
            },
            'product_name': {
                'data_type': 'string',
                'non_null': 1000,
                'distinct': 50,
                'samples': ['Widget A', 'Widget B'],
            },
            'revenue': {
                'data_type': 'numeric',
                'non_null': 950,
                'distinct': 900,
                'min': 10.0,
                'max': 10000.0,
                'mean': 500.0,
                'samples': [100.0, 200.0, 300.0],
            },
        }
    }

    builder = SemanticModelBuilder()
    model = builder.generate_model_from_profile(profile)

    assert model is not None
    assert model.name == 'sales'
    assert len(model.fields) == 3

    # Check field classification
    field_by_name = {f.name: f for f in model.fields}
    assert field_by_name['product_id'].field_type == 'dimension'
    assert field_by_name['product_name'].field_type == 'dimension'
    assert field_by_name['revenue'].field_type == 'measure'
    assert field_by_name['revenue'].aggregation == 'sum'


# ==================== Test SQL Safety ====================

def test_sql_validator_safe_query():
    """Test validation of safe SQL"""
    from safety import QueryRiskLevel, SQLSafetyValidator

    validator = SQLSafetyValidator()
    query = "SELECT * FROM sales WHERE date >= '2024-01-01' LIMIT 100"

    result = validator.validate(query)

    assert result.is_valid
    # SELECT * triggers "inefficient: select specific columns" warning → LOW_RISK
    assert result.risk_level == QueryRiskLevel.LOW_RISK


def test_sql_validator_dangerous_query():
    """Test validation catches dangerous queries"""
    from safety import QueryRiskLevel, SQLSafetyValidator

    validator = SQLSafetyValidator()
    query = "DELETE FROM sales WHERE 1=1; DROP TABLE users;"

    result = validator.validate(query)

    assert not result.is_valid
    assert result.risk_level == QueryRiskLevel.CRITICAL
    assert len(result.errors) > 0


def test_sql_validator_missing_limit():
    """Test warning for missing LIMIT"""
    from safety import QueryRiskLevel, SQLSafetyValidator

    validator = SQLSafetyValidator()
    query = "SELECT * FROM sales"

    result = validator.validate(query)

    assert result.is_valid  # Still valid, but has warnings
    assert result.risk_level == QueryRiskLevel.LOW_RISK
    assert any("LIMIT" in w for w in result.warnings)
    assert result.suggested_query is not None


def test_sql_validator_dry_run_mode():
    """Test dry-run mode only allows SELECT"""
    from safety import SQLSafetyValidator

    validator = SQLSafetyValidator(dry_run_only=True)

    # SELECT should pass
    result = validator.validate("SELECT * FROM sales LIMIT 10")
    assert result.is_valid

    # INSERT should fail
    result = validator.validate("INSERT INTO sales VALUES (1, 'test', 100)")
    assert not result.is_valid


# ==================== Test Insights ====================

def test_insights_engine_analysis(test_data):
    """Test insight generation from data"""
    from insights import InsightsEngine

    engine = InsightsEngine()
    query = "SELECT * FROM sales"

    analysis = engine.analyze(query, test_data)

    assert 'insights' in analysis
    assert 'charts' in analysis
    assert 'narrative' in analysis
    assert 'row_count' in analysis
    assert analysis['row_count'] == 5


def test_insights_chart_generation(test_data):
    """Test automatic chart generation"""
    from insights import ChartType, InsightsEngine

    engine = InsightsEngine()
    analysis = engine.analyze("SELECT * FROM sales", test_data)

    charts = analysis['charts']
    assert len(charts) > 0

    # Check chart has required fields
    chart = charts[0]
    assert 'type' in chart
    assert 'title' in chart
    assert 'data' in chart


def test_anomaly_detector():
    """Test anomaly detection"""
    from aurabackend.insights.engine import AnomalyDetector

    values = [10, 12, 11, 13, 100, 12, 11]  # 100 is an outlier
    anomalies = AnomalyDetector.detect_anomalies(values, threshold=2.0)

    assert len(anomalies) > 0
    assert any(idx == 4 for idx, _ in anomalies)  # Index 4 has value 100


def test_alert_generator():
    """Test alert generation from rules"""
    from insights import AlertGenerator

    data = [
        {'temperature': 25.0},
        {'temperature': 26.0},
        {'temperature': 45.0},  # Alert: exceeds threshold
    ]

    rules = [
        {
            'name': 'High Temperature',
            'metric': 'temperature',
            'operator': '>',
            'threshold': 40.0,
        }
    ]

    alerts = AlertGenerator.generate_alerts(data, rules)
    assert len(alerts) == 1
    assert alerts[0]['value'] == 45.0


# ==================== Test Connectors ====================

@pytest.mark.asyncio
async def test_postgresql_connector_interface():
    """Test PostgreSQL connector interface"""
    from connectors import ConnectorConfig, PostgreSQLConnector, SourceType

    config = ConnectorConfig(
        source_type=SourceType.POSTGRESQL,
        name="test-pg",
        host="localhost",
        port=5432,
        username="postgres",
        password="password",
        database="testdb",
    )

    connector = PostgreSQLConnector(config)

    # Verify interface
    assert hasattr(connector, 'connect')
    assert hasattr(connector, 'disconnect')
    assert hasattr(connector, 'list_tables')
    assert hasattr(connector, 'get_table_schema')
    assert hasattr(connector, 'execute_query')
    assert hasattr(connector, 'profile_table')


@pytest.mark.asyncio
async def test_mysql_connector_interface():
    """Test MySQL connector interface"""
    from connectors import ConnectorConfig, MySQLConnector, SourceType
    if MySQLConnector is None:
        pytest.skip("aiomysql not installed")

    config = ConnectorConfig(
        source_type=SourceType.MYSQL,
        name="test-mysql",
        host="localhost",
        port=3306,
        username="root",
        password="password",
        database="testdb",
    )

    connector = MySQLConnector(config)

    # Verify interface
    assert hasattr(connector, 'connect')
    assert hasattr(connector, 'disconnect')
    assert hasattr(connector, 'list_tables')


# ==================== Test Query Planner ====================

def test_query_planner_estimation():
    """Test query execution time estimation"""
    from safety import QueryPlanner

    # Simple query
    time1, explanation1 = QueryPlanner.estimate_execution_time("SELECT * FROM sales LIMIT 100")
    assert time1 > 0
    assert "Full table scan" in explanation1

    # Complex query
    time2, explanation2 = QueryPlanner.estimate_execution_time(
        "SELECT * FROM sales JOIN customers WHERE status='active' ORDER BY date GROUP BY product"
    )
    assert time2 > time1  # Complex query should be slower
    assert any(word in explanation2 for word in ["join", "grouping", "sorting"])


# ==================== Integration Tests ====================

@pytest.mark.asyncio
async def test_upload_to_profile_pipeline():
    """Test complete pipeline: upload → profile → store"""
    pd = pytest.importorskip("pandas", reason="pandas not installed")
    pytest.importorskip("numpy", reason="numpy not installed")
    from shared.file_service import FileService

    # Create sample data
    data = {
        'customer_id': [1, 2, 3],
        'customer_name': ['Alice', 'Bob', 'Charlie'],
        'purchase_amount': [100.0, 200.0, 150.0],
    }
    df = pd.DataFrame(data)

    # Profile it
    file_service = FileService()
    profile = file_service._profile_dataframe(df)

    assert profile is not None
    assert profile['rows'] == 3
    assert profile['columns'] == 3


@pytest.mark.skip(reason="semantic_builder module not yet in codebase")
@pytest.mark.asyncio
async def test_semantic_modeling_pipeline():
    """Test complete semantic modeling pipeline"""
    from semantic_builder import SemanticModelBuilder

    # Create realistic profile
    profile = {
        'table_name': 'e_commerce_orders',
        'rows': 50000,
        'columns': 5,
        'columns_profile': {
            'order_id': {
                'data_type': 'integer',
                'non_null': 50000,
                'distinct': 50000,
            },
            'customer_name': {
                'data_type': 'string',
                'non_null': 50000,
                'distinct': 10000,
            },
            'order_date': {
                'data_type': 'date',
                'non_null': 50000,
                'distinct': 365,
            },
            'total_amount': {
                'data_type': 'numeric',
                'non_null': 50000,
                'distinct': 49000,
                'min': 10.0,
                'max': 9999.0,
                'mean': 150.0,
            },
            'status': {
                'data_type': 'string',
                'non_null': 50000,
                'distinct': 5,
                'samples': ['pending', 'shipped', 'delivered'],
            },
        }
    }

    builder = SemanticModelBuilder()
    model = builder.generate_model_from_profile(profile)

    # Verify model structure
    assert model.name == 'e_commerce_orders'
    assert len(model.fields) == 5

    # Verify fields are classified correctly
    measures = [f for f in model.fields if f.field_type == 'measure']
    dimensions = [f for f in model.fields if f.field_type == 'dimension']

    assert len(measures) > 0  # total_amount should be a measure
    assert len(dimensions) > 0  # customer_name, status should be dimensions


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
