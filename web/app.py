"""Web demo + REST API for agents. Run: python -m uvicorn web.app:app --port 8765

- /            the "Zkus to oklamat" page (internal endpoints under /api)
- /v1/...      the public API for agents and other programs, documented at /docs
Both call faktury.service.Service, so the page, the API and the MCP server share one logic.

Reading a file uses the AI (locally through the Claude Code CLI). Checking never does.
No endpoint pays anything: the result is a decision for a person or another system.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from faktury.extract import ExtractionError
from faktury.schemas import CheckOut, InvoiceIn, ReadOut
from faktury.service import RULES, Service

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TMP = ROOT / ".tmp"
for d in ("uploads", "renders"):
    (TMP / d).mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "scripts"))

# The demo page keeps history in memory (reset button). Set KONTROLA_FAKTUR_DB to keep it in a file.
service = Service(os.environ.get("KONTROLA_FAKTUR_DB", ":memory:"))
render_lock = threading.Lock()
MAX_UPLOAD = 10_000_000

# Optional API key for /v1. Without it the API is open, which is fine on localhost only.
API_KEY = os.environ.get("KONTROLA_FAKTUR_API_KEY")

# Every AI read costs money or quota: at most 2 at once and 20 per hour per client.
AI_SLOTS = threading.BoundedSemaphore(2)
AI_PER_HOUR = int(os.environ.get("KONTROLA_FAKTUR_READS_PER_HOUR", "20"))
_reads: dict[str, deque] = defaultdict(deque)
_reads_lock = threading.Lock()


def require_key(x_api_key: str | None = Header(None)) -> None:
    if API_KEY and not (x_api_key and secrets.compare_digest(x_api_key, API_KEY)):
        raise HTTPException(401, "Missing or wrong X-API-Key header.")


def ai_budget(request: Request) -> None:
    client = request.client.host if request.client else "unknown"
    now = time.monotonic()
    with _reads_lock:
        q = _reads[client]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= AI_PER_HOUR:
            raise HTTPException(429, f"Limit {AI_PER_HOUR} čtení faktury za hodinu. Zkus to později.")
        q.append(now)

app = FastAPI(
    title="Kontrola faktur API",
    version="1.0.0",
    description="Invoice checks where AI reads and code decides. "
                "`/v1/check` is deterministic and free. `/v1/read` uses AI to turn a PDF or photo into data. "
                "Nothing here pays an invoice: the API returns APPROVE, REVIEW or STOP with reasons.",
)
app.mount("/doc", StaticFiles(directory=DATA / "pdf"), name="doc")
app.mount("/tmpdoc", StaticFiles(directory=TMP), name="tmpdoc")


def _save_upload(body: bytes) -> Path:
    if not body or len(body) > MAX_UPLOAD:
        raise HTTPException(413, "Soubor je prázdný nebo větší než 10 MB.")
    if body[:4] == b"%PDF":
        ext = ".pdf"
    elif body[:3] == bytes([0xFF, 0xD8, 0xFF]):
        ext = ".jpg"
    elif body[:8] == bytes([0x89]) + b"PNG" + bytes([13, 10, 26, 10]):
        ext = ".png"
    elif body[8:12] == b"WEBP":
        ext = ".webp"
    else:
        raise HTTPException(415, "Nahraj fotku (JPG, PNG, WEBP) nebo PDF.")
    path = TMP / "uploads" / (hashlib.sha256(body).hexdigest()[:20] + ext)
    path.write_bytes(body)
    return path


def _read(path: Path) -> dict:
    if not AI_SLOTS.acquire(timeout=120):
        raise HTTPException(503, "AI teď čte jiné faktury. Zkus to za chvíli.")
    try:
        return service.read(path)
    except ExtractionError as e:
        raise HTTPException(422, str(e))
    except (PermissionError, ValueError) as e:
        raise HTTPException(400, str(e))
    finally:
        AI_SLOTS.release()


def _read_and_check(path: Path) -> dict:
    if not AI_SLOTS.acquire(timeout=120):
        raise HTTPException(503, "AI teď čte jiné faktury. Zkus to za chvíli.")
    try:
        return service.read_and_check(path)
    finally:
        AI_SLOTS.release()


def _check(data: dict, document_text: str | None = None, source: str | None = None) -> dict:
    try:
        InvoiceIn(**data)  # same validation as the public API: no floats, sane formats
        return service.check(data, document_text, source)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(422, f"Fakturu nejde načíst: {e}")


# ---------------- public API for agents ----------------

@app.get("/health", tags=["meta"])
def health():
    return {"ok": True}


@app.get("/v1/rules", tags=["v1"], dependencies=[Depends(require_key)], summary="How decisions are made (all checks and codes)")
def v1_rules():
    return {"rules": RULES}


@app.post("/v1/check", tags=["v1"], response_model=CheckOut, dependencies=[Depends(require_key)],
          summary="Check invoice data. Deterministic, no AI, instant.")
def v1_check(invoice: InvoiceIn, document_text: str | None = None,
             source: Literal["pdf", "photo", "manual"] | None = None):
    """Runs all checks and records the invoice in history (for duplicate detection).
    Sending the exact same invoice again returns the first result with `replay: true`.
    `source=photo` caps the decision at REVIEW: digits read from images are not trusted for payment."""
    return _check(invoice.model_dump(), document_text, source)


@app.post("/v1/read", tags=["v1"], response_model=ReadOut, dependencies=[Depends(require_key), Depends(ai_budget)],
          summary="AI reads a PDF or photo (raw file in the request body) and returns invoice data")
async def v1_read(request: Request):
    path = _save_upload(await request.body())
    return await run_in_threadpool(_read, path)


@app.post("/v1/read-and-check", tags=["v1"], dependencies=[Depends(require_key), Depends(ai_budget)],
          summary="Upload a PDF or photo: AI reads it, code checks it")
async def v1_read_and_check(request: Request):
    """If the AI cannot read the document, the answer is REVIEW with EXTRACTION_FAILED, never APPROVE."""
    path = _save_upload(await request.body())
    return await run_in_threadpool(_read_and_check, path)


@app.get("/v1/history", tags=["v1"], dependencies=[Depends(require_key)],
         summary="Audit trail: every decision with its reasons, source and rules version")
def v1_history(limit: int = 50):
    return service.history(max(1, min(limit, 500)))


@app.get("/v1/demo", tags=["v1"], dependencies=[Depends(require_key)], summary="List the 10 demo invoices")
def v1_demo():
    return service.demo_list()


@app.get("/v1/demo/{demo_id}", tags=["v1"], response_model=InvoiceIn, dependencies=[Depends(require_key)],
         summary="Data of one demo invoice")
def v1_demo_one(demo_id: str):
    try:
        return service.demo_invoice(demo_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.get("/v1/reference", tags=["v1"], dependencies=[Depends(require_key)],
         summary="Company master data: suppliers, orders, goods receipts")
def v1_reference():
    return service.reference()


# ---------------- endpoints for the demo page ----------------

@app.get("/", include_in_schema=False)
def index():
    return FileResponse(Path(__file__).parent / "index.html")


@app.get("/api/samples", include_in_schema=False)
def samples():
    return [{**d, "image": f"/doc/{d['id']}.png", "pdf": f"/doc/{d['id']}.pdf"} for d in service.demo_list()]


@app.get("/api/books", include_in_schema=False)
def get_books():
    return service.reference()


@app.post("/api/check", include_in_schema=False)
def check(payload: dict):
    # {"invoice": {...}, "document_text": "...", "source": "pdf"|"photo"}; a bare invoice also works
    if "invoice" in payload:
        src = payload.get("source")
        return _check(payload["invoice"], payload.get("document_text"), src if src in ("pdf", "photo") else None)
    return _check(payload)


@app.post("/api/reset", include_in_schema=False)
def reset():
    service.reset()
    return {"ok": True}


@app.post("/api/read/{sample_id}", include_in_schema=False)
def read_sample(sample_id: str):
    path = DATA / "pdf" / f"{Path(sample_id).name}.pdf"
    if not path.exists():
        raise HTTPException(404, "Taková faktura není.")
    return _read(path)


@app.post("/api/upload", include_in_schema=False, dependencies=[Depends(ai_budget)])
async def upload(request: Request):
    path = _save_upload(await request.body())
    preview = f"/tmpdoc/uploads/{path.name}"
    if path.suffix == ".pdf":
        import fitz

        with fitz.open(path) as doc:
            png = path.with_suffix(".png")
            doc[0].get_pixmap(dpi=110).save(png)
        preview = f"/tmpdoc/uploads/{png.name}"
    result = await run_in_threadpool(_read, path)
    return {**result, "image": preview}


@app.post("/api/render", include_in_schema=False)
def render_doc(payload: dict):
    """Draw the edited invoice again, so the visitor sees the forged version."""
    import render_invoices as R

    try:
        payload = InvoiceIn(**payload).model_dump()  # never draw unvalidated input
    except Exception as e:
        raise HTTPException(422, f"Fakturu nejde překreslit: {e}")
    key = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:20]
    out = TMP / "renders" / f"{key}.png"
    if not out.exists():
        from playwright.sync_api import sync_playwright

        with render_lock, sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": 794, "height": 1123}, device_scale_factor=1.6)
            page.set_content(R.html(payload), wait_until="load")
            page.screenshot(path=str(out))
            browser.close()
    return {"image": f"/tmpdoc/renders/{out.name}"}
