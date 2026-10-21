"""Regression tests for problems found in the review of 2026-10-20.

Each problem was first confirmed with a probe script against the old code, then fixed.
The common thread: ways an invoice could get APPROVE although it must not be paid.
"""
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from faktury.models import Invoice, account_key, money, number_key
from faktury.pipeline import Books, process
from faktury.service import Service
from faktury.store import Store

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
BOOKS = Books.load(DATA)


def load(name: str) -> Invoice:
    return Invoice.from_dict(json.loads((DATA / "invoices" / f"{name}.json").read_text(encoding="utf-8")))


def consistent(inv: Invoice, **changes) -> Invoice:
    """Apply changes and recompute totals, so only the change under test can trigger a finding."""
    inv = replace(inv, **changes)
    sub = money(sum((l.amount for l in inv.lines), Decimal(0)))
    vat = money(sub * inv.vat_rate)
    return replace(inv, subtotal=sub, vat_amount=vat, total=sub + vat)


def codes(r):
    return {f.code for f in r.findings}


# --- double payment -----------------------------------------------------

@pytest.mark.parametrize("variant", ["fv 2026 0458", "FV2026/0458", "FV-2026-0458 ", "fv-2026-0458"])
def test_same_number_written_differently_is_a_duplicate(variant):
    s = Store()
    process(load("01-v-poradku"), BOOKS, s)
    r = process(replace(load("01-v-poradku"), invoice_number=variant, issue_date="2026-10-14"), BOOKS, s)
    assert r.decision.value == "STOP" and "DUPLICATE_NUMBER" in codes(r)


def test_same_order_billed_twice_under_new_number():
    s = Store()
    process(load("01-v-poradku"), BOOKS, s)
    r = process(replace(load("01-v-poradku"), invoice_number="FV-2026-0999", issue_date="2026-10-18"), BOOKS, s)
    assert r.decision.value != "APPROVE" and "QTY_OVER_RECEIVED" in codes(r)


def test_partial_invoices_up_to_received_quantity_are_fine():
    """Two honest invoices of 20 + 20 for 40 received paper: both approve, a third does not."""
    s = Store()
    base = load("01-v-poradku")
    half = (replace(base.lines[0], qty=Decimal("20")),)
    a = process(consistent(base, invoice_number="P-1", lines=half), BOOKS, s)
    b = process(consistent(base, invoice_number="P-2", lines=half, issue_date="2026-10-13"), BOOKS, s)
    c = process(consistent(base, invoice_number="P-3", lines=half, issue_date="2026-10-14"), BOOKS, s)
    assert [a.decision.value, b.decision.value] == ["APPROVE", "APPROVE"]
    assert c.decision.value == "REVIEW" and "QTY_OVER_RECEIVED" in codes(c)


def test_stopped_invoice_does_not_use_up_received_quantity():
    s = Store()
    base = load("01-v-poradku")
    process(replace(base, bank_account="2109876543/3030"), BOOKS, s)  # STOP: forged account
    assert process(base, BOOKS, s).decision.value == "APPROVE"


def test_line_split_into_two_cannot_exceed_order():
    inv = load("01-v-poradku")
    l0 = inv.lines[0]
    split = consistent(inv, lines=(replace(l0, qty=Decimal("40")), replace(l0, qty=Decimal("40"))) + inv.lines[1:])
    r = process(split, BOOKS, Store())
    assert r.decision.value == "REVIEW" and {"QTY_OVER_ORDERED", "QTY_OVER_RECEIVED"} <= codes(r)


# --- amounts and rounding ------------------------------------------------

def test_zero_invoice_is_not_approved():
    inv = load("01-v-poradku")
    zero = consistent(inv, lines=(replace(inv.lines[0], qty=Decimal("0")),))
    r = process(zero, BOOKS, Store())
    assert r.decision.value == "REVIEW" and "TOTAL_NOT_POSITIVE" in codes(r)


def test_negative_line_is_not_approved():
    inv = load("01-v-poradku")
    neg = consistent(inv, lines=(replace(inv.lines[0], unit_price=Decimal("-119.00")),) + inv.lines[1:])
    r = process(neg, BOOKS, Store())
    assert r.decision.value != "APPROVE" and "LINE_NOT_POSITIVE" in codes(r)


@pytest.mark.parametrize("value,expected", [("0.005", "0.01"), ("0.125", "0.13"), ("2.675", "2.68"), ("-0.125", "-0.13")])
def test_rounding_is_half_up(value, expected):
    assert money(Decimal(value)) == Decimal(expected)


def test_vat_rounded_half_up_is_accepted():
    """Subtotal 0.50 -> VAT 0.105 -> 0.11 on a Czech invoice. Must not be a false VAT_MISMATCH."""
    inv = load("01-v-poradku")
    one = replace(inv, invoice_number="R-1",
                  lines=(replace(inv.lines[0], qty=Decimal("1"), unit_price=Decimal("0.50")),),
                  subtotal=Decimal("0.50"), vat_amount=Decimal("0.11"), total=Decimal("0.61"))
    assert "VAT_MISMATCH" not in codes(process(one, BOOKS, Store()))


# --- bank account -------------------------------------------------------

@pytest.mark.parametrize("written", ["000000-2400123456/2010", "0002400123456/2010", "2400123456/2010"])
def test_same_account_with_leading_zeros_is_the_same(written):
    r = process(replace(load("01-v-poradku"), invoice_number="A-1", bank_account=written), BOOKS, Store())
    assert "BANK_ACCOUNT_CHANGED" not in codes(r)


def test_account_key_keeps_prefix():
    assert account_key("19-4455667788/0100") == "19-4455667788/0100"
    assert account_key("000019-0004455667788/0100") == "19-4455667788/0100"
    assert account_key("19-4455667788/0100") != account_key("4455667788/0100")


def test_number_key():
    assert number_key("FV-2026/0458") == number_key("fv 2026 0458") == "FV20260458"
    assert number_key("FV-2026-0458") != number_key("FV-2026-0459")


# --- AI and sources -----------------------------------------------------

def test_photo_is_never_approved_automatically():
    r = process(load("01-v-poradku"), BOOKS, Store(), source="photo")
    assert r.decision.value == "REVIEW" and "READ_FROM_PHOTO" in codes(r)


def test_pdf_text_layer_reaches_the_guard_even_if_the_ai_hid_it(tmp_path):
    """Invoice 10 hides an instruction for the AI. Even if the reader had left `note` empty,
    the code reads the PDF text itself and the guard finds it."""
    svc = Service(str(tmp_path / "h.db"))
    read = svc.read(DATA / "pdf" / "10-vzkaz-pro-ai.pdf")
    assert read["source"] == "pdf" and "POKYN PRO AI" in read["document_text"]
    hidden = {**read["invoice"], "note": ""}
    out = svc.check(hidden, read["document_text"], "pdf")
    guard = next(s for s in out["steps"] if s["key"] == "guard")
    assert guard["status"] == "REVIEW"


def test_failed_reading_is_review_not_error(tmp_path, monkeypatch):
    from faktury import service as S
    from faktury.extract import ExtractionError

    def broken(*a, **k):
        raise ExtractionError("model returned nothing")

    monkeypatch.setattr(S, "read_file", broken)
    out = Service(str(tmp_path / "h.db")).read_and_check(DATA / "pdf" / "01-v-poradku.pdf")
    assert out["check"]["decision"] == "REVIEW"
    source = next(s for s in out["check"]["steps"] if s["key"] == "source")
    assert source["findings"][0]["code"] == "EXTRACTION_FAILED"


def test_reader_refuses_files_outside_allowed_folders(tmp_path):
    outside = tmp_path / "faktura.pdf"
    outside.write_bytes((DATA / "pdf" / "01-v-poradku.pdf").read_bytes())
    with pytest.raises(PermissionError):
        Service().read(outside)


def test_allowed_folders_can_be_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("KONTROLA_FAKTUR_FILES", str(tmp_path))
    with pytest.raises(FileNotFoundError):  # allowed now, just missing
        Service().read(tmp_path / "missing.pdf")


# --- database -----------------------------------------------------------

def test_old_history_file_is_migrated(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE invoices (id INTEGER PRIMARY KEY, content_hash TEXT NOT NULL UNIQUE,"
        " supplier_ico TEXT NOT NULL, invoice_number TEXT NOT NULL, issue_date TEXT NOT NULL, total TEXT NOT NULL,"
        " decision TEXT NOT NULL, findings_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now')));"
        "CREATE UNIQUE INDEX one_live_number ON invoices (supplier_ico, invoice_number) WHERE decision != 'STOP';"
        "INSERT INTO invoices (content_hash, supplier_ico, invoice_number, issue_date, total, decision, findings_json)"
        " VALUES ('x', '29143705', 'FV-2026-0458', '2026-10-12', '14907.20', 'APPROVE', '[]');")
    con.commit()
    con.close()
    r = process(replace(load("01-v-poradku"), invoice_number="fv 2026 0458"), BOOKS, Store(str(db)))
    assert r.decision.value == "STOP" and "DUPLICATE_NUMBER" in codes(r)


def test_invoice_and_lines_are_saved_together():
    s = Store()
    r = process(load("01-v-poradku"), BOOKS, s)
    n = s.db.execute("SELECT COUNT(*) FROM invoice_lines WHERE invoice_id = ?", (r.invoice_id,)).fetchone()[0]
    assert n == 2


def test_history_keeps_reason_source_and_rules_version():
    s = Store()
    process(load("04-cizi-ucet"), BOOKS, s, source="pdf")
    h = s.history()[0]
    assert h["decision"] == "STOP" and h["source"] == "pdf" and h["rules_version"]
    assert any(r.startswith("BANK_ACCOUNT_CHANGED") for r in h["reasons"])


def test_parallel_requests_create_one_live_invoice():
    from concurrent.futures import ThreadPoolExecutor

    svc = Service()
    base = json.loads((DATA / "invoices" / "01-v-poradku.json").read_text(encoding="utf-8"))
    variants = [{**base, "issue_date": f"2026-10-{d:02d}"} for d in range(1, 13)]  # 12 copies, same number
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(svc.check, variants))
    assert sum(r["decision"] != "STOP" for r in results) == 1


# --- API ----------------------------------------------------------------

def test_api_key_is_enforced_when_set(monkeypatch):
    import web.app as A

    monkeypatch.setattr(A, "API_KEY", "secret")
    c = TestClient(A.app)
    assert c.get("/v1/rules").status_code == 401
    assert c.get("/v1/rules", headers={"X-API-Key": "wrong"}).status_code == 401
    assert c.get("/v1/rules", headers={"X-API-Key": "secret"}).status_code == 200
    assert c.get("/health").status_code == 200  # health stays open


def test_ai_reads_are_rate_limited(monkeypatch):
    import web.app as A

    monkeypatch.setattr(A, "AI_PER_HOUR", 2)
    A._reads.clear()
    c = TestClient(A.app)
    pdf = (DATA / "pdf" / "04-cizi-ucet.pdf").read_bytes()
    assert [c.post("/v1/read", content=pdf).status_code for _ in range(3)] == [200, 200, 429]
    A._reads.clear()


def test_render_refuses_invalid_input():
    import web.app as A

    c = TestClient(A.app)
    inv = json.loads((DATA / "invoices" / "01-v-poradku.json").read_text(encoding="utf-8"))
    inv["issue_date"] = "2026-10-<img src=x>"
    assert c.post("/api/render", json=inv).status_code == 422


def test_history_endpoint():
    import web.app as A

    A.service.reset()
    c = TestClient(A.app)
    c.post("/v1/check", json=json.loads((DATA / "invoices" / "04-cizi-ucet.json").read_text(encoding="utf-8")))
    h = c.get("/v1/history").json()
    assert h[0]["decision"] == "STOP" and h[0]["reasons"]


# --- Red-Team phase 6 (RT-KF-01, 04, 05, 06) --------------------------------

def test_rt_kf_01_invalid_vat_rate_is_review():
    """RT-KF-01: 50 % VAT with matching sums must not be APPROVE."""
    inv = load("01-v-poradku")
    sub = inv.subtotal
    r = process(replace(inv, vat_rate=Decimal("0.50"), vat_amount=money(sub * Decimal("0.5")),
                        total=sub + money(sub * Decimal("0.5"))), BOOKS, Store())
    assert r.decision.value == "REVIEW" and "VAT_RATE_INVALID" in codes(r)


@pytest.mark.parametrize("rate", ["0", "0.12", "0.21", "0.210"])
def test_rt_kf_01_legal_vat_rates_pass(rate):
    inv = consistent(load("01-v-poradku"), vat_rate=Decimal(rate))
    assert "VAT_RATE_INVALID" not in codes(process(inv, BOOKS, Store()))


@pytest.mark.parametrize("field,value", [("qty", "NaN"), ("unit_price", "NaN"), ("qty", "Infinity"),
                                          ("unit_price", "1E+999999"), ("qty", "")])
def test_rt_kf_04_non_finite_line_value_is_clean_error(field, value):
    """RT-KF-04: NaN/Infinity/overflow in a line is a ValueError at the model boundary, not InvalidOperation."""
    d = json.loads((DATA / "invoices" / "01-v-poradku.json").read_text(encoding="utf-8"))
    d["lines"][0][field] = value
    with pytest.raises(ValueError):
        Invoice.from_dict(d)


@pytest.mark.parametrize("field,value", [("total", "NaN"), ("subtotal", ""), ("vat_amount", "-Infinity"),
                                          ("vat_rate", "1E+999999")])
def test_rt_kf_05_non_finite_invoice_value_is_clean_error(field, value):
    """RT-KF-05: same at the invoice level (also vat_rate overflow)."""
    d = json.loads((DATA / "invoices" / "01-v-poradku.json").read_text(encoding="utf-8"))
    d[field] = value
    with pytest.raises(ValueError):
        Invoice.from_dict(d)


def _bad_read(monkeypatch, field, value, tmp_path):
    from faktury import service as sv

    d = json.loads((DATA / "invoices" / "01-v-poradku.json").read_text(encoding="utf-8"))
    d[field] = value
    monkeypatch.setattr(sv, "read_file", lambda p, c: (None, d, 0.1, True))
    return Service(str(tmp_path / "h.db")), DATA / "pdf" / "01-v-poradku.pdf"


def test_rt_kf_05_read_and_check_with_nan_is_review(monkeypatch, tmp_path):
    """RT-KF-05: AI read with NaN -> REVIEW (EXTRACTION_FAILED), no exception (service/MCP path)."""
    svc, pdf = _bad_read(monkeypatch, "total", "NaN", tmp_path)
    out = svc.read_and_check(pdf)
    assert out["check"]["decision"] == "REVIEW"
    assert any(f["code"] == "EXTRACTION_FAILED" for s in out["check"]["steps"] for f in s["findings"])


def test_rt_kf_05_rest_read_endpoints_with_nan(monkeypatch, tmp_path):
    """RT-KF-05: /v1/read -> 422, /v1/read-and-check -> 200 REVIEW, never 500."""
    from web import app as webapp

    svc, pdf = _bad_read(monkeypatch, "vat_rate", "1E+999999", tmp_path)
    monkeypatch.setattr(webapp, "service", svc)
    c = TestClient(webapp.app, raise_server_exceptions=False)
    body = pdf.read_bytes()
    assert c.post("/v1/read", content=body).status_code == 422
    r = c.post("/v1/read-and-check", content=body)
    assert r.status_code == 200 and r.json()["check"]["decision"] == "REVIEW"


@pytest.mark.parametrize("content", [b"", b"%PDF-1.4\ngarbage garbage"])
def test_rt_kf_06_broken_pdf_is_clean_error(tmp_path, content, monkeypatch):
    """RT-KF-06: broken/empty/fake PDF -> REST 422 with Czech message, read_and_check -> REVIEW, no fitz exception."""
    from faktury import service as sv
    from web import app as webapp

    monkeypatch.setenv("KONTROLA_FAKTUR_FILES", str(tmp_path))
    f = tmp_path / "x.pdf"
    f.write_bytes(content)
    svc = Service(str(tmp_path / "h.db"))
    with pytest.raises(sv.ExtractionError):
        svc.read(f)
    assert svc.read_and_check(f)["check"]["decision"] == "REVIEW"
    monkeypatch.setattr(webapp, "service", svc)
    monkeypatch.setattr(webapp, "_save_upload", lambda body: f)
    c = TestClient(webapp.app, raise_server_exceptions=False)
    r = c.post("/v1/read", content=b"%PDF-1.4 x")
    assert r.status_code == 422 and "PDF" in r.text
