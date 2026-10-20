import pytest

from faktury.extract_cli import _json_from, iban_to_account, normalize


@pytest.mark.parametrize("iban,account", [
    ("CZ5630300000002109876543", "2109876543/3030"),
    ("CZ65 0800 0000 1920 0014 5399", "19-2000145399/0800"),
    ("2400123456/2010", "2400123456/2010"),  # already an account: untouched
])
def test_iban_to_account(iban, account):
    assert iban_to_account(iban) == account


def test_iban_round_trip_with_renderer():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from render_invoices import iban

    for acc in ["2400123456/2010", "19-4455667788/0100", "123456789/0300"]:
        assert iban_to_account(iban(acc)) == acc


def test_normalize_formats_but_never_invents():
    raw = {"bank_account": "CZ5630300000002109876543", "subtotal": "12 320,00", "vat_amount": "2587.20",
           "total": "14 907,20 Kč", "vat_rate": "21%", "lines": [{"sku": "PAPÍR-A4", "qty": "40", "unit_price": "119,00"}]}
    d = normalize(raw)
    assert d["bank_account"] == "2109876543/3030"
    assert d["subtotal"] == "12320.00" and d["total"] == "14907.20" and d["vat_rate"] == "0.21"
    assert d["lines"][0] == {"sku": "PAPIR-A4", "qty": "40", "unit_price": "119.00"}
    assert "po_number" not in d  # missing stays missing, nothing is guessed


def test_json_from_code_fence():
    assert _json_from('```json\n{"a": 1}\n```') == {"a": 1}
