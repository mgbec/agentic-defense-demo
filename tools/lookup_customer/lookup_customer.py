"""Tool: lookup_customer. Read-only. Deliberately returns the whole record, sensitive fields
included: over-sharing is the weakness the defenses are meant to contain.

This function needs no database access, and its IAM role has none.
"""

from __future__ import annotations

from typing import Any

from common.data import CUSTOMERS


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    customer_id = str(event.get("customer_id", "")).strip().upper()
    customer = CUSTOMERS.get(customer_id)
    if customer is None:
        return {"found": False, "error": f"No customer with id {customer_id or '(empty)'}"}
    return {"found": True, "customer": customer}
