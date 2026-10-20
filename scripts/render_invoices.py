"""Render invoices as realistic Czech PDF + PNG documents (the thing a person photographs or uploads).

  python scripts/render_invoices.py              # 10 demo invoices -> data/pdf/
  python scripts/render_invoices.py --eval       # 40 eval invoices -> data/eval/pdf/

Every company is made up. The QR code is a real Czech "QR Platba" (SPD) code with the same
account and amount as printed, unless the invoice JSON sets "qr_account" to something else
(a realistic attack: the text says one account, the QR pays another).
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import sys
from datetime import date, timedelta
from decimal import Decimal
from html import escape
from pathlib import Path

import qrcode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import make_samples as S  # noqa: E402

US = {"name": "Zenn Výroba s.r.o.", "street": "Průmyslová 12", "city": "284 01 Kutná Hora",
      "ico": S.make_ico("0745821")}
BRAND = {
    S.NOVAK: {"name": "Kancelářské potřeby Novák s.r.o.", "street": "Husova 8", "city": "586 01 Jihlava",
              "color": "#1f5fbf", "mark": "N", "web": "www.novak-kancelar.cz",
              "reg": "Krajský soud v Brně, oddíl C, vložka 81234", "person": "Petr Novák"},
    S.VLTAVA: {"name": "Strojírna Vltava a.s.", "street": "Nádražní 1520", "city": "370 01 České Budějovice",
               "color": "#3d4a55", "mark": "SV", "web": "www.strojirna-vltava.cz",
               "reg": "Krajský soud v Českých Budějovicích, oddíl B, vložka 2210", "person": "Ing. Jana Dvořáková"},
    S.MORAVA: {"name": "Kávový servis Morava s.r.o.", "street": "Masarykova 44", "city": "602 00 Brno",
               "color": "#7a4a2a", "mark": "KM", "web": "www.kavamorava.cz",
               "reg": "Krajský soud v Brně, oddíl C, vložka 99871", "person": "Tomáš Kučera"},
    S.CIZI: {"name": "Poradenství Hájek s.r.o.", "street": "Vinohradská 90", "city": "120 00 Praha 2",
             "color": "#2f7d5b", "mark": "H", "web": "www.hajek-poradenstvi.cz",
             "reg": "Městský soud v Praze, oddíl C, vložka 310445", "person": "Martin Hájek"},
}
DESC = {"PAPIR-A4": "Papír A4 80 g, 500 listů", "TONER-HP26": "Toner HP 26A černý",
        "LOZISKO-6204": "Ložisko kuličkové 6204-2RS", "HRIDEL-H25": "Hřídel H25 kalená",
        "KAVA-ZRNO-1KG": "Káva zrnková Brazil 1 kg", "SERVIS-KAVOVAR": "Servis kávovaru (paušál)",
        "KONZULTACE": "Konzultace, hodina"}


def cz(x) -> str:
    return f"{Decimal(str(x)):,.2f}".replace(",", " ").replace(".", ",")


def d_cz(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{int(d)}.{int(m)}.{y}"


def iban(account: str) -> str:
    """Czech account 'prefix-number/bank' -> IBAN (mod 97)."""
    acc, bank = account.split("/")
    prefix, number = acc.split("-") if "-" in acc else ("0", acc)
    bban = bank.zfill(4) + prefix.zfill(6) + number.zfill(10)
    check = 98 - int(bban + "123500") % 97  # C=12, Z=35
    return f"CZ{check:02d}{bban}"


def qr_png(account: str, amount, vs: str) -> str:
    spd = f"SPD*1.0*ACC:{iban(account)}*AM:{Decimal(str(amount)):.2f}*CC:CZK*X-VS:{vs}*MSG:FAKTURA {vs}"
    img = qrcode.make(spd, box_size=6, border=1)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def html(inv: dict) -> str:
    b = BRAND.get(inv["supplier_ico"], BRAND[S.NOVAK])
    vs = "".join(ch for ch in inv["invoice_number"] if ch.isdigit())[-10:]
    issued = inv["issue_date"]
    due = (date.fromisoformat(issued) + timedelta(days=14)).isoformat()
    rows = ""
    for l in inv["lines"]:
        net = Decimal(str(l["qty"])) * Decimal(l["unit_price"])
        vat = (net * Decimal(inv["vat_rate"])).quantize(Decimal("0.01"))
        rows += (f"<tr><td>{escape(DESC.get(l['sku'], l['sku']) + l.get('extra', ''))}<br><small>{escape(l['sku'])}</small></td>"
                 f"<td class=r>{str(l['qty']).replace('.', ',')}</td><td>ks</td><td class=r>{cz(l['unit_price'])}</td>"
                 f"<td class=r>{cz(net)}</td><td class=r>21%</td><td class=r>{cz(net + vat)}</td></tr>")
    qr = qr_png(inv.get("qr_account") or inv["bank_account"], inv["total"], vs)
    note = escape(inv.get("note", "")).replace("\n", "<br>")
    po = escape(inv["po_number"]) if inv.get("po_number") else "&nbsp;"
    c = b["color"]
    return f"""<!doctype html><html lang=cs><head><meta charset=utf-8><style>
@page{{size:A4;margin:0}}
*{{box-sizing:border-box}}
body{{margin:0;font:10.5px/1.35 Arial,Helvetica,sans-serif;color:#111;background:#fff}}
.page{{width:210mm;height:297mm;padding:12mm 13mm;position:relative}}
.box{{border:1px solid #222}}
.top{{display:grid;grid-template-columns:1fr 1fr;border:1px solid #222}}
.title{{background:{c};color:#fff;display:flex;justify-content:space-between;align-items:center;padding:6px 10px;font-weight:700;font-size:15px;grid-column:1/-1}}
.cell{{padding:8px 10px}}
.sup{{border-right:1px solid #222}}
.sup .nm{{font-size:15px;font-weight:700}}
.logo{{float:right;width:46px;height:46px;border-radius:50%;background:{c};color:#fff;display:grid;place-items:center;font-weight:700;font-size:16px}}
.kv{{display:grid;grid-template-columns:62px 1fr;gap:1px 6px;margin-top:8px}}
.kv b{{font-weight:700}}
.small{{font-size:9px;color:#333}}
.pay{{display:grid;grid-template-columns:1fr 1fr;gap:2px 8px;border-bottom:1px solid #222;padding-bottom:6px;margin:-8px -10px 8px;padding:6px 10px}}
.pay span{{color:#555}}
.cust .nm{{font-size:14px;font-weight:700;margin-top:4px}}
.dates{{display:grid;grid-template-columns:repeat(3,1fr);border:1px solid #222;border-top:0}}
.dates div{{padding:5px 10px;border-right:1px solid #222}}
.dates div:last-child{{border-right:0}}
.dates span{{display:block;font-size:9px;color:#555}}
.dates b{{display:block;text-align:center;font-size:12px;margin-top:2px}}
table{{width:100%;border-collapse:collapse;margin-top:10px;font-size:10px}}
th{{background:#eee;font-weight:400;text-align:left;padding:4px 5px;border:1px solid #999}}
td{{padding:4px 5px;border:1px solid #bbb;vertical-align:top}}
td small{{color:#666}}
.r{{text-align:right;white-space:nowrap}}
.sum{{margin-top:6px;margin-left:auto;width:58%}}
.sum td{{border:0;padding:2px 5px}}
.mid{{display:grid;grid-template-columns:150px 1fr;gap:14px;margin-top:22px;align-items:start}}
.qr img{{width:130px;height:130px;image-rendering:pixelated;display:block}}
.qr div{{font-weight:700;margin-top:4px;text-align:center;width:130px}}
.note{{border:1px dashed #999;padding:6px 8px;min-height:40px;font-size:10px}}
.foot{{position:absolute;left:13mm;right:13mm;bottom:12mm;display:grid;grid-template-columns:1fr 1fr 1.2fr;border:1px solid #222}}
.foot > div{{padding:6px 10px;border-right:1px solid #222;min-height:120px}}
.foot > div:last-child{{border-right:0}}
.foot span{{font-size:9px;color:#555;display:block}}
.stamp{{margin-top:16px;border:2px solid {c};color:{c};padding:4px 6px;font-size:9px;transform:rotate(-4deg);display:inline-block;opacity:.85}}
.stamp b{{font-size:11px;display:block}}
.sign{{font-family:"Segoe Script","Brush Script MT",cursive;font-size:20px;color:#1d3a8a;margin:-14px 0 0 50px;transform:rotate(-6deg)}}
.tot div{{display:flex;justify-content:space-between;padding:1px 0}}
.tot .big{{font-size:20px;font-weight:700;justify-content:flex-end;margin-top:10px}}
</style></head><body><div class=page>
<div class=top>
  <div class=title><span>DAŇOVÝ DOKLAD FAKTURA</span><span>{escape(inv['invoice_number'])}</span></div>
  <div class="cell sup">
    <div class=logo>{b['mark']}</div>
    <div class=small>Dodavatel</div>
    <div class=nm>{escape(b['name'])}</div>
    <div>{escape(b['street'])}<br>{escape(b['city'])}<br>Česká republika</div>
    <div class=small style="margin-top:6px">Firma zapsána u {escape(b['reg'])}</div>
    <div class=kv><b>IČO:</b><span>{escape(inv['supplier_ico'])}</span><b>DIČ:</b><span>CZ{escape(inv['supplier_ico'])}</span>
      <span></span><span>Plátce DPH</span><b>WEB:</b><span>{b['web']}</span>
      <b>ÚČET:</b><b>{escape(inv['bank_account'])}</b><b>IBAN:</b><span>{iban(inv['bank_account'])}</span></div>
  </div>
  <div class="cell cust">
    <div class=pay><span>Forma úhrady:</span><b>převodním příkazem</b><span>Variabilní symbol:</span><b>{vs}</b>
      <span>Číslo objednávky:</span><b>{po}</b><span>Konstantní symbol:</span><b>0308</b></div>
    <div class=small>Odběratel</div>
    <div class=nm>{US['name']}</div>
    <div>{US['street']}<br>{US['city']}</div>
    <div class=kv><b>IČO:</b><span>{US['ico']}</span><b>DIČ:</b><span>CZ{US['ico']}</span></div>
  </div>
</div>
<div class=dates><div><span>Datum vystavení</span><b>{d_cz(issued)}</b></div>
  <div><span>Datum zdanitelného plnění</span><b>{d_cz(issued)}</b></div>
  <div><span>Datum splatnosti</span><b>{d_cz(due)}</b></div></div>
<table><tr><th>Popis položky</th><th class=r>Množství</th><th>MJ</th><th class=r>Cena za MJ</th><th class=r>Celkem bez DPH</th><th class=r>DPH</th><th class=r>Celkem s DPH</th></tr>
{rows}</table>
<table class=sum><tr><td>Základ daně 21 %</td><td class=r>{cz(inv['subtotal'])} Kč</td></tr>
<tr><td>DPH 21 %</td><td class=r>{cz(inv['vat_amount'])} Kč</td></tr></table>
<div class=mid>
  <div class=qr><img src="data:image/png;base64,{qr}"><div>QR platba</div></div>
  <div><div class=small>Poznámka</div><div class=note>{note or 'Děkujeme za spolupráci.'}</div></div>
</div>
<div class=foot>
  <div><span>Vystavil</span>{escape(b['person'])}
    <div class=stamp><b>{escape(b['name'])}</b>{escape(b['street'])}, {escape(b['city'])}<br>IČO {escape(inv['supplier_ico'])}</div>
    <div class=sign>{escape(b['person'].split()[-1])}</div></div>
  <div><span>Převzal</span></div>
  <div class=tot><div><span>Celková suma bez DPH:</span>{cz(inv['subtotal'])} Kč</div>
    <div><span>DPH:</span>{cz(inv['vat_amount'])} Kč</div>
    <div><span>Celková suma s DPH:</span>{cz(inv['total'])} Kč</div>
    <div><span>Uhrazeno zálohami:</span>0,00 Kč</div>
    <span style="margin-top:8px">K úhradě:</span>
    <div class=big>{cz(inv['total'])} Kč</div></div>
</div>
</div></body></html>"""


def render(items: list[tuple[str, dict]], out: Path) -> None:
    from playwright.sync_api import sync_playwright

    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 794, "height": 1123}, device_scale_factor=1.6)
        for name, inv in items:
            page.set_content(html(inv), wait_until="load")
            page.pdf(path=str(out / f"{name}.pdf"), format="A4", print_background=True)
            page.screenshot(path=str(out / f"{name}.png"), full_page=False)
        browser.close()
    print(f"OK: {len(items)} faktur do {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", action="store_true")
    args = ap.parse_args()
    if args.eval:
        truth = json.loads((ROOT / "data/eval/truth.json").read_text(encoding="utf-8"))
        render([(k, v["invoice"]) for k, v in truth.items()], ROOT / "data/eval/pdf")
    else:
        items = [(p.stem, json.loads(p.read_text(encoding="utf-8")))
                 for p in sorted((ROOT / "data/invoices").glob("*.json"))]
        render(items, ROOT / "data/pdf")
