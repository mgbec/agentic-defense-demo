"""Tool: issue_refund. Side-effecting, but sandboxed: it records the refund in the sink table and
moves no money. It checks that the order exists and that the refund is positive and no more than
the order total, like any real back end would. It does NOT enforce the support assistant's own
$50 limit. That business rule belongs to the gateway policy, which is what the demo shows.

Its IAM role can write to the sink table and nothing else.
"""

from __future__ import annotations

import uuid
from typing import Any

from common import sink
from common.constants import KIND_REFUND
from common.data import ORDERS


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    order_id = str(event.get("order_id", "")).strip().upper()
    reason = str(event.get("reason", "")).strip()

    try:
        amount = int(event.get("amount"))
    except (TypeError, ValueError):
        return {"status": "error", "error": "amount must be a whole number of dollars"}

    order = ORDERS.get(order_id)
    if order is None:
        return {"status": "error", "error": f"Unknown order {order_id or '(empty)'}"}
    if amount <= 0:
        return {"status": "error", "error": "amount must be greater than zero"}
    if amount > order["total"]:
        return {"status": "error", "error": "amount exceeds the order total"}

    sink.write(KIND_REFUND, order_id, order_id=order_id, amount=amount, reason=reason)
    return {"status": "refunded", "refund_id": uuid.uuid4().hex, "order_id": order_id,
            "amount": amount}
