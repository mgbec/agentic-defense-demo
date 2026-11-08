"""Tool: send_email. Side-effecting, but sandboxed: it records the message in the sink table and
sends nothing. The tool itself accepts any recipient. Restricting recipients is the job of the
gateway policy, which is what the demo shows.

Its IAM role can write to the sink table and nothing else.
"""

from __future__ import annotations

import uuid
from typing import Any

from common import sink
from common.constants import KIND_OUTBOX


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    to = str(event.get("to", "")).strip()
    subject = str(event.get("subject", "")).strip()
    body = str(event.get("body", ""))

    if "@" not in to:
        return {"status": "error", "error": "A valid recipient address is required"}

    sink.write(KIND_OUTBOX, to, to=to.lower(), subject=subject, body=body)
    return {"status": "queued", "message_id": uuid.uuid4().hex, "to": to}
