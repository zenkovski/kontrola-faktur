"""Deterministic checks. No AI here: every rule is plain code with a test."""
from __future__ import annotations

from decimal import Decimal

from .ico import ico_valid
from .models import Finding, Invoice, PurchaseOrder, Receipt, Severity, Supplier, money

PRICE_TOLERANCE = Decimal("0.01")  # 1 % unit price difference is still OK
VAT_TOLERANCE = Decimal("1.00")  # rounding on the invoice, CZK


def check_ico(inv: Invoice) -> list[Finding]:
    if ico_valid(inv.supplier_ico):
        return []
    return [Finding("ICO_INVALID", Severity.STOP,
                    f"IČO {inv.supplier_ico} nemá platnou kontrolní číslici. Taková firma nemůže existovat.",
                    {"ico": inv.supplier_ico})]


def check_arithmetic(inv: Invoice) -> list[Finding]:
    out = []
    lines_sum = money(sum((l.amount for l in inv.lines), Decimal(0)))
    if lines_sum != inv.subtotal:
        out.append(Finding("SUBTOTAL_MISMATCH", Severity.STOP,
                           f"Součet řádků je {lines_sum}, ale faktura uvádí základ {inv.subtotal}.",
                           {"computed": str(lines_sum), "stated": str(inv.subtotal)}))
    vat = money(inv.subtotal * inv.vat_rate)
    if abs(vat - inv.vat_amount) > VAT_TOLERANCE:
        out.append(Finding("VAT_MISMATCH", Severity.STOP,
                           f"DPH {inv.vat_rate:%} ze základu je {vat}, faktura uvádí {inv.vat_amount}.",
                           {"computed": str(vat), "stated": str(inv.vat_amount)}))
    if inv.subtotal + inv.vat_amount != inv.total:
        out.append(Finding("TOTAL_MISMATCH", Severity.STOP,
                           f"Základ + DPH = {inv.subtotal + inv.vat_amount}, faktura uvádí celkem {inv.total}.",
                           {"computed": str(inv.subtotal + inv.vat_amount), "stated": str(inv.total)}))
    return out


def check_supplier(inv: Invoice, supplier: Supplier | None) -> list[Finding]:
    if supplier is None:
        return [Finding("SUPPLIER_UNKNOWN", Severity.REVIEW,
                        f"Dodavatel s IČO {inv.supplier_ico} není v seznamu dodavatelů.",
                        {"ico": inv.supplier_ico})]
    if supplier.bank_account != inv.bank_account:
        # Classic invoice fraud: real supplier, attacker's bank account.
        return [Finding("BANK_ACCOUNT_CHANGED", Severity.STOP,
                        f"Účet na faktuře ({inv.bank_account}) není účet, který máme u dodavatele "
                        f"{supplier.name} ({supplier.bank_account}). Ověřit telefonem, ne e-mailem.",
                        {"invoice": inv.bank_account, "known": supplier.bank_account})]
    return []


def check_three_way(inv: Invoice, po: PurchaseOrder | None, rcpt: Receipt | None) -> list[Finding]:
    if po is None:
        return [Finding("PO_MISSING", Severity.REVIEW,
                        f"K faktuře se nenašla objednávka {inv.po_number or '(neuvedena)'}.",
                        {"po_number": inv.po_number})]
    out = []
    if po.supplier_ico != inv.supplier_ico:
        out.append(Finding("PO_SUPPLIER_MISMATCH", Severity.STOP,
                           f"Objednávka {po.po_number} patří jinému dodavateli (IČO {po.supplier_ico}).",
                           {"po_ico": po.supplier_ico, "invoice_ico": inv.supplier_ico}))
    if po.currency != inv.currency:
        out.append(Finding("CURRENCY_MISMATCH", Severity.STOP,
                           f"Objednávka je v {po.currency}, faktura v {inv.currency}.",
                           {"po": po.currency, "invoice": inv.currency}))

    ordered = {l.sku: l for l in po.lines}
    received = rcpt.received if rcpt else {}
    if rcpt is None:
        out.append(Finding("RECEIPT_MISSING", Severity.REVIEW,
                           "Zboží zatím nikdo nepřevzal (chybí příjemka).", {"po_number": po.po_number}))

    for line in inv.lines:
        o = ordered.get(line.sku)
        if o is None:
            out.append(Finding("LINE_NOT_ORDERED", Severity.REVIEW,
                               f"Položka {line.sku} na objednávce není.", {"sku": line.sku}))
            continue
        if line.qty > o.qty:
            out.append(Finding("QTY_OVER_ORDERED", Severity.REVIEW,
                               f"{line.sku}: fakturováno {line.qty} ks, objednáno {o.qty} ks.",
                               {"sku": line.sku, "invoiced": str(line.qty), "ordered": str(o.qty)}))
        if rcpt is not None and line.qty > received.get(line.sku, Decimal(0)):
            out.append(Finding("QTY_OVER_RECEIVED", Severity.REVIEW,
                               f"{line.sku}: fakturováno {line.qty} ks, převzato "
                               f"{received.get(line.sku, Decimal(0))} ks.",
                               {"sku": line.sku, "invoiced": str(line.qty),
                                "received": str(received.get(line.sku, Decimal(0)))}))
        if abs(line.unit_price - o.unit_price) > o.unit_price * PRICE_TOLERANCE:
            out.append(Finding("PRICE_MISMATCH", Severity.REVIEW,
                               f"{line.sku}: cena {line.unit_price}, v objednávce {o.unit_price}.",
                               {"sku": line.sku, "invoice": str(line.unit_price), "po": str(o.unit_price)}))
    return out
