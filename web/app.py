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
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
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
    try:
        return service.read(path)
    except ExtractionError as e:
        raise HTTPException(422, str(e))


def _check(data: dict, document_text: str | None = None) -> dict:
    try:
        return service.check(data, document_text)
    except Exception as e:
        raise HTTPException(422, f"Fakturu nejde načíst: {e}")


# ---------------- public API for agents ----------------

@app.get("/health", tags=["meta"])
def health():
    return {"ok": True}


@app.get("/v1/rules", tags=["v1"], summary="How decisions are made (all checks and codes)")
def v1_rules():
    return {"rules": RULES}


@app.post("/v1/check", tags=["v1"], response_model=CheckOut,
          summary="Check invoice data. Deterministic, no AI, instant.")
def v1_check(invoice: InvoiceIn, document_text: str | None = None):
    """Runs all checks and records the invoice in history (for duplicate detection).
    Sending the exact same invoice again returns the first result with `replay: true`."""
    return _check(invoice.model_dump(), document_text)


@app.post("/v1/read", tags=["v1"], response_model=ReadOut,
          summary="AI reads a PDF or photo (raw file in the request body) and returns invoice data")
async def v1_read(request: Request):
    path = _save_upload(await request.body())
    return await run_in_threadpool(_read, path)


@app.post("/v1/read-and-check", tags=["v1"],
          summary="Upload a PDF or photo: AI reads it, code checks it")
async def v1_read_and_check(request: Request):
    path = _save_upload(await request.body())
    read = await run_in_threadpool(_read, path)
    return {"read": read, "check": _check(read["invoice"])}


@app.get("/v1/demo", tags=["v1"], summary="List the 10 demo invoices")
def v1_demo():
    return service.demo_list()


@app.get("/v1/demo/{demo_id}", tags=["v1"], response_model=InvoiceIn, summary="Data of one demo invoice")
def v1_demo_one(demo_id: str):
    try:
        return service.demo_invoice(demo_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.get("/v1/reference", tags=["v1"], summary="Company master data: suppliers, orders, goods receipts")
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


@app.post("/api/upload", include_in_schema=False)
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
