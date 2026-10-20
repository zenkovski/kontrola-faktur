# Eval: deepseek-v4.1-flash (prompt or-v1, 2026-10-20)

- Cases: **40**
- Decision matches expected: **40/40**
- **False approvals (expected REVIEW/STOP, got APPROVE): 0** 
- Missed STOPs: 0 
- Too strict (expected APPROVE): 0 
- Extraction fully exact: 40/40
- Injection cases where a key value was changed: 0 
- Injection text copied into `note`: 8/8
- Output errors (→ REVIEW): 0 
- Tokens: 22855 in / 6356 out, cost $0.0047 (this run $0)
- Latency p50 None s, max None s

## By category

| category | correct |
|---|---|
| clean | 12/12 |
| format | 2/2 |
| fraud_account | 4/4 |
| injection | 6/6 |
| injection_attack | 2/2 |
| math_error | 4/4 |
| mismatch | 6/6 |
| unknown | 4/4 |

## Field accuracy

| field | accuracy |
|---|---|
| supplier_ico | 100% |
| invoice_number | 100% |
| issue_date | 100% |
| currency | 100% |
| bank_account | 100% |
| po_number | 100% |
| vat_rate | 100% |
| subtotal | 100% |
| vat_amount | 100% |
| total | 100% |
| lines | 100% |

## Failures and differences

None.
