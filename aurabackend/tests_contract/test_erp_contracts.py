import pytest
from datetime import datetime

from ingestion_service.erp_adapters.netsuite import NetSuiteAdapter
from ingestion_service.erp_adapters.workday import WorkdayAdapter
from ingestion_service.main import LedgerEntry

# --- Mock ERP Payloads ---

NETSUITE_MOCK = {
    "internalId": "10001",
    "tranType": "Journal",
    "debit": 1500.00,
    "credit": 0.0,
    "tranDate": "2026-06-09T10:00:00Z",
    "currency": "USD",
    "accountNumber": "4000",
    "memo": "Software License Revenue",
    "department": "Sales",
    "class": "Software"
}

WORKDAY_MOCK = {
    "WorkdayID": "WD-889900",
    "CostCenterID": "CC-101",
    "BaseAmount": -500.00,
    "CurrencyCode": "USD",
    "AccountingDate": "2026-06-09T10:00:00+00:00",
    "LedgerAccount": "6000",
    "CompanyReference": "AURA_LLC",
    "JournalSource": "ExpenseReport"
}


# --- Contract Tests ---

def test_netsuite_adapter_contract():
    """
    Validates that the NetSuite adapter correctly maps the expected ERP payload
    into AURA's unified LedgerEntry without losing data or throwing validation errors.
    """
    tenant_id = "tenant-test-123"
    entry = NetSuiteAdapter.normalize(tenant_id, NETSUITE_MOCK)
    
    assert isinstance(entry, LedgerEntry)
    assert entry.amount == 1500.00
    assert entry.system_origin == "NetSuite"
    assert entry.account_code == "4000"
    assert entry.metadata["netsuite_internal_id"] == "10001"
    
    # ERC must be 64-character SHA256 hex string
    assert len(entry.erc) == 64

def test_workday_adapter_contract():
    """
    Validates that the Workday adapter correctly maps the expected ERP payload.
    """
    tenant_id = "tenant-test-123"
    entry = WorkdayAdapter.normalize(tenant_id, WORKDAY_MOCK)
    
    assert isinstance(entry, LedgerEntry)
    assert entry.amount == -500.00
    assert entry.system_origin == "Workday"
    assert entry.account_code == "6000"
    assert entry.metadata["workday_id"] == "WD-889900"
    
    assert len(entry.erc) == 64

def test_erc_determinism():
    """
    Ensures that identical inputs produce identical ERC hashes, and different inputs produce different hashes.
    """
    tenant_id = "tenant-test-123"
    erc1 = NetSuiteAdapter._generate_erc(tenant_id, "100", "Journal")
    erc2 = NetSuiteAdapter._generate_erc(tenant_id, "100", "Journal")
    erc3 = NetSuiteAdapter._generate_erc(tenant_id, "101", "Journal")
    
    assert erc1 == erc2
    assert erc1 != erc3
