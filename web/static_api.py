"""The demo page's /api/check, /api/reset and /api/render, for the static web version.

Runs in the visitor's browser through Pyodide (Python compiled to WebAssembly).
Same Service and same validation as web/app.py, so the decision comes from the same code.
Built into the site by scripts/build_static.py.
"""
from __future__ import annotations

import json

from faktury.schemas import InvoiceIn
from faktury.service import Service

service = Service(":memory:")


def _check(data: dict, document_text=None, source=None) -> tuple[int, dict]:
    try:
        InvoiceIn(**data)
        return 200, service.check(data, document_text, source)
    except Exception as e:
        return 422, {"detail": f"Fakturu nejde načíst: {e}"}


def check(payload_json: str) -> str:
    payload = json.loads(payload_json)
    if "invoice" in payload:
        src = payload.get("source")
        status, body = _check(payload["invoice"], payload.get("document_text"), src if src in ("pdf", "photo") else None)
    else:
        status, body = _check(payload)
    return json.dumps({"status": status, "body": body})


def reset() -> None:
    service.reset()


def render(payload_json: str) -> str:
    """Invoice HTML of the edited invoice. The browser turns it into an image."""
    import render_invoices as R

    try:
        payload = InvoiceIn(**json.loads(payload_json)).model_dump()
    except Exception as e:
        return json.dumps({"status": 422, "body": {"detail": f"Fakturu nejde překreslit: {e}"}})
    return json.dumps({"status": 200, "body": {"html": R.html(payload)}})
