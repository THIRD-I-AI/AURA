import hashlib
from datetime import datetime, timezone
from typing import Any, Dict

from ..models import LedgerEntry


class WorkdayAdapter:
    """
    Adapter for Workday Financial Management payloads.
    Maps Workday's schema to AURA's unified LedgerEntry.
    """
    SYSTEM_ORIGIN = "Workday"

    @classmethod
    def _generate_erc(cls, tenant_id: str, wd_id: str, cost_center: str) -> str:
        """
        Cryptographically hashes Workday attributes to generate a globally unique ERC.
        """
        raw_key = f"{tenant_id}-{wd_id}-{cost_center}"
        return hashlib.sha256(raw_key.encode('utf-8')).hexdigest()

    @classmethod
    def normalize(cls, tenant_id: str, raw_payload: Dict[str, Any]) -> LedgerEntry:
        """
        Converts a Workday accounting line into AURA's standard LedgerEntry.
        """
        wd_id = raw_payload.get("WorkdayID", "")
        cost_center = raw_payload.get("CostCenterID", "DEFAULT")

        erc = cls._generate_erc(tenant_id, wd_id, cost_center)

        amount = float(raw_payload.get("BaseAmount", 0.0))
        currency = raw_payload.get("CurrencyCode", "USD")

        # Parse date
        date_str = raw_payload.get("AccountingDate")
        try:
            posted_at = datetime.fromisoformat(date_str)
        except (ValueError, AttributeError, TypeError):
            posted_at = datetime.now(timezone.utc)

        return LedgerEntry(
            erc=erc,
            system_origin=cls.SYSTEM_ORIGIN,
            amount=amount,
            currency=currency,
            account_code=str(raw_payload.get("LedgerAccount", "UNKNOWN")),
            posted_at=posted_at,
            metadata={
                "workday_id": wd_id,
                "cost_center": cost_center,
                "company": raw_payload.get("CompanyReference", ""),
                "journal_source": raw_payload.get("JournalSource", "")
            }
        )
