"""Deterministic checks. No AI here: every rule is plain code with a test."""
from __future__ import annotations

from decimal import Decimal

from .ico import ico_valid
from .models import Finding, Invoice, PurchaseOrder, Receipt, Severity, Supplier, account_key, money

PRICE_TOLERANCE = Decimal("0.01")  # 1 % unit price difference is still OK
VAT_RATES = (Decimal("0"), Decimal("0.12"), Decimal("0.21"))  # legal Czech rates; one rate per invoice
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
    if inv.vat_rate not in VAT_RATES:  # RT-KF-01: a made-up rate must not pass just because the sums fit
        out.append(Finding("VAT_RATE_INVALID", Severity.REVIEW,
                           f"Sazba DPH {inv.vat_rate:%} není platná česká sazba (0 %, 12 %, 21 %).",
                           {"stated": str(inv.vat_rate)}))
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


def check_amounts(inv: Invoice) -> list[Finding]:
    """A zero or negative invoice, or a negative line, is not a normal purchase (credit note, error, trick)."""
    out = []
    if inv.total <= 0:
        out.append(Finding("TOTAL_NOT_POSITIVE", Severity.REVIEW,
                           f"Celková částka je {inv.total}. Nulová nebo záporná faktura není běžný nákup.",
                           {"total": str(inv.total)}))
    bad = [l.sku for l in inv.lines if l.qty <= 0 or l.unit_price < 0]
    if bad:
        out.append(Finding("LINE_NOT_POSITIVE", Severity.REVIEW,
                           f"Položky s nulovým nebo záporným množstvím či cenou: {', '.join(bad)}.",
                           {"skus": bad}))
    return out


def check_supplier(inv: Invoice, supplier: Supplier | None) -> list[Finding]:
    if supplier is None:
        return [Finding("SUPPLIER_UNKNOWN", Severity.REVIEW,
                        f"Dodavatel s IČO {inv.supplier_ico} není v seznamu dodavatelů.",
                        {"ico": inv.supplier_ico})]
    if account_key(supplier.bank_account) != account_key(inv.bank_account):
        # Classic invoice fraud: real supplier, attacker's bank account.
        return [Finding("BANK_ACCOUNT_CHANGED", Severity.STOP,
                        f"Účet na faktuře ({inv.bank_account}) není účet, který máme u dodavatele "
                        f"{supplier.name} ({supplier.bank_account}). Ověřit telefonem, ne e-mailem.",
                        {"invoice": inv.bank_account, "known": supplier.bank_account})]
    return []


def check_three_way(inv: Invoice, po: PurchaseOrder | None, rcpt: Receipt | None,
                    already_invoiced: dict[str, Decimal] | None = None) -> list[Finding]:
    """Invoice vs purchase order vs goods receipt.

    Quantities are summed per product across the lines of this invoice AND across earlier invoices
    for the same order, so neither splitting a line nor a second invoice can bill more than arrived.
    """
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
    earlier = already_invoiced or {}
    if rcpt is None:
        out.append(Finding("RECEIPT_MISSING", Severity.REVIEW,
                           "Zboží zatím nikdo nepřevzal (chybí příjemka).", {"po_number": po.po_number}))

    this_invoice: dict[str, Decimal] = {}
    for line in inv.lines:
        this_invoice[line.sku] = this_invoice.get(line.sku, Decimal(0)) + line.qty
        o = ordered.get(line.sku)
        if o is not None and abs(line.unit_price - o.unit_price) > o.unit_price * PRICE_TOLERANCE:
            out.append(Finding("PRICE_MISMATCH", Severity.REVIEW,
                               f"{line.sku}: cena {line.unit_price}, v objednávce {o.unit_price}.",
                               {"sku": line.sku, "invoice": str(line.unit_price), "po": str(o.unit_price)}))

    for sku, qty in this_invoice.items():
        o = ordered.get(sku)
        if o is None:
            out.append(Finding("LINE_NOT_ORDERED", Severity.REVIEW,
                               f"Položka {sku} na objednávce není.", {"sku": sku}))
            continue
        before = earlier.get(sku, Decimal(0))
        total = before + qty
        note = f" (z toho {before} ks už na dřívějších fakturách)" if before else ""
        if total > o.qty:
            out.append(Finding("QTY_OVER_ORDERED", Severity.REVIEW,
                               f"{sku}: fakturováno celkem {total} ks{note}, objednáno {o.qty} ks.",
                               {"sku": sku, "invoiced_total": str(total), "earlier": str(before),
                                "ordered": str(o.qty)}))
        got = received.get(sku, Decimal(0))
        if rcpt is not None and total > got:
            out.append(Finding("QTY_OVER_RECEIVED", Severity.REVIEW,
                               f"{sku}: fakturováno celkem {total} ks{note}, převzato {got} ks.",
                               {"sku": sku, "invoiced_total": str(total), "earlier": str(before),
                                "received": str(got)}))
    return out
