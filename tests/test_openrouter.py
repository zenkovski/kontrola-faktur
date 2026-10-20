"""DeepSeek reader via OpenRouter and the daily budget. A fake HTTP layer replaces the network."""
import json
from pathlib import Path

import pytest

from faktury import extract_openrouter as OR
from faktury import service as S
from faktury.extract import ExtractionError
from faktury.service import BudgetExceeded, DailyBudget, Service

DATA = Path(__file__).resolve().parents[1] / "data"
TRUTH = json.loads((DATA / "invoices" / "04-cizi-ucet.json").read_text(encoding="utf-8"))


def reply(content, finish="stop"):
    return {"choices": [{"message": {"content": content}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 800, "completion_tokens": 140, "cost": 0.00025}}


def as_strings(inv):
    out = dict(inv)
    out["lines"] = [{k: str(v) for k, v in l.items()} for l in inv["lines"]]
    return out


@pytest.fixture
def fake(monkeypatch):
    calls = []

    def install(*responses):
        it = iter(responses)

        def post(body, key, timeout=60):
            calls.append(body)
            r = next(it)
            if isinstance(r, Exception):
                raise r
            return r
        monkeypatch.setattr(OR, "_post", post)
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setattr(OR.time, "sleep", lambda s: None)
        return calls
    return install


def test_request_turns_off_hidden_reasoning_and_wraps_text(fake):
    calls = fake(reply(json.dumps(as_strings(TRUTH))))
    inv, raw, s, cached, usage = OR.read_text("FAKTURA 123")
    body = calls[0]
    assert body["model"] == "deepseek/deepseek-v4.1-flash"
    assert body["reasoning"] == {"enabled": False}
    assert "<invoice>\nFAKTURA 123\n</invoice>" in body["messages"][1]["content"]
    assert "tools" not in body
    assert inv.bank_account == "2109876543/3030" and not cached


def test_cache_means_no_second_call(fake, tmp_path):
    calls = fake(reply(json.dumps(as_strings(TRUTH))))
    OR.read_text("same text", tmp_path)
    again = OR.read_text("same text", tmp_path)
    assert again[3] is True and len(calls) == 1


def test_error_with_http_200_is_retried(fake):
    calls = fake({"error": {"message": "overloaded"}}, reply(json.dumps(as_strings(TRUTH))))
    OR.read_text("x")
    assert len(calls) == 2


def test_gives_up_after_three_failures(fake):
    import urllib.error

    err = urllib.error.HTTPError(OR.URL, 503, "busy", {}, None)
    fake(err, err, err)
    with pytest.raises(ExtractionError, match="neodpovídá"):
        OR.read_text("x")


def test_truncated_answer_is_an_error(fake):
    fake(reply('{"supplier_ico": "1', finish="length"))
    with pytest.raises(ExtractionError):
        OR.read_text("x")


def test_garbage_answer_is_an_error(fake):
    fake(reply("Sorry, I cannot help with that."))
    with pytest.raises(ExtractionError):
        OR.read_text("x")


def test_missing_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ExtractionError, match="OPENROUTER_API_KEY"):
        OR.read_text("x")


# --- daily budget -------------------------------------------------------

def test_budget_blocks_after_limit_and_survives_restart(tmp_path):
    f = tmp_path / "b.json"
    b = DailyBudget(2, f)
    b.spend(); b.spend()
    with pytest.raises(BudgetExceeded):
        b.spend()
    assert DailyBudget(2, f).status() == {"limit": 2, "used": 2, "left": 0}  # a restart does not reset it


def test_budget_resets_next_day(tmp_path):
    f = tmp_path / "b.json"
    f.write_text(json.dumps({"day": "2020-01-01", "used": 99}))
    DailyBudget(2, f).spend()
    assert json.loads(f.read_text())["used"] == 1


def test_budget_is_spent_only_on_real_calls(fake, tmp_path):
    calls = fake(reply(json.dumps(as_strings(TRUTH))))
    b = DailyBudget(1, None)
    OR.read_text("t", tmp_path, b.spend)
    OR.read_text("t", tmp_path, b.spend)   # cached: free
    assert b.status()["used"] == 1 and len(calls) == 1


def test_exhausted_budget_makes_read_and_check_review(monkeypatch, tmp_path):
    monkeypatch.setenv("KONTROLA_FAKTUR_READER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(OR, "_post", lambda *a, **k: pytest.fail("must not call the API"))
    svc = Service(str(tmp_path / "h.db"), budget=DailyBudget(0, None))
    pdf = tmp_path / "f.pdf"
    pdf.write_bytes((DATA / "pdf" / "01-v-poradku.pdf").read_bytes() + b"\n%new")  # same text, but not in our cache dir
    monkeypatch.setattr(S, "DATA", DATA)
    monkeypatch.setenv("KONTROLA_FAKTUR_FILES", str(tmp_path))
    monkeypatch.setattr(OR, "read_text", lambda text, cache_dir, before_call: before_call())
    out = svc.read_and_check(pdf)
    assert out["check"]["decision"] == "REVIEW"


def test_public_reader_refuses_photos_without_claude(monkeypatch, tmp_path):
    monkeypatch.setenv("KONTROLA_FAKTUR_READER", "openrouter")
    monkeypatch.setattr(S.shutil, "which", lambda name: None)
    with pytest.raises(ExtractionError, match="PDF"):
        Service().read(DATA / "pdf" / "01-v-poradku.png")


def test_scanned_pdf_without_text_is_refused(monkeypatch):
    monkeypatch.setenv("KONTROLA_FAKTUR_READER", "openrouter")
    monkeypatch.setattr(S, "pdf_text", lambda p: "  ")
    with pytest.raises(ExtractionError, match="sken"):
        Service().read(DATA / "pdf" / "01-v-poradku.pdf")


def test_demo_pdfs_have_cached_deepseek_answers(monkeypatch):
    """The public page must not spend the budget on the 10 demo invoices."""
    monkeypatch.setenv("KONTROLA_FAKTUR_READER", "openrouter")
    monkeypatch.setattr(OR, "_post", lambda *a, **k: pytest.fail("demo invoice not cached"))
    svc = Service(budget=DailyBudget(0, None))
    for d in svc.demo_list():
        assert svc.read(d["pdf"])["cached"], d["id"]
