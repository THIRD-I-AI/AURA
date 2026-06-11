import hashlib
from datetime import datetime, timezone
from typing import Any, Dict

from ..models import LedgerEntry


class NetSuiteAdapter:
    """
    Adapter for NetSuite SuiteScript JSON payloads.
    Maps NetSuite's schema to AURA's unified LedgerEntry.
    """
    SYSTEM_ORIGIN = "NetSuite"

    @classmethod
    def _generate_erc(cls, tenant_id: str, internal_id: str, transaction_type: str) -> str:
        """
        Cryptographically hashes NetSuite attributes to generate a globally unique ERC.
        """
        raw_key = f"{tenant_id}-{internal_id}-{transaction_type}"
        return hashlib.sha256(raw_key.encode('utf-8')).hexdigest()

    @classmethod
    def normalize(cls, tenant_id: str, raw_payload: Dict[str, Any]) -> LedgerEntry:
        """
        Converts a single NetSuite Journal Entry line into AURA's standard LedgerEntry.
        """
        internal_id = raw_payload.get("internalId", "")
        tran_type = raw_payload.get("tranType", "Journal")

        erc = cls._generate_erc(tenant_id, internal_id, tran_type)

        # Parse amount. NetSuite often stores credit/debit in separate fields
        debit = float(raw_payload.get("debit", 0.0) or 0.0)
        credit = float(raw_payload.get("credit", 0.0) or 0.0)
        amount = debit - credit

        # Parse date
        tran_date_str = raw_payload.get("tranDate")
        try:
            posted_at = datetime.fromisoformat(tran_date_str.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            posted_at = datetime.now(timezone.utc)

        return LedgerEntry(
            erc=erc,
            system_origin=cls.SYSTEM_ORIGIN,
            amount=amount,
            currency=raw_payload.get("currency", "USD"),
            account_code=str(raw_payload.get("accountNumber", "UNKNOWN")),
            posted_at=posted_at,
            metadata={
                "netsuite_internal_id": internal_id,
                "memo": raw_payload.get("memo", ""),
                "department": raw_payload.get("department", ""),
                "class": raw_payload.get("class", "")
            }
        )
