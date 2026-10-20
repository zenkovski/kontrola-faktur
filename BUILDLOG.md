# Buildlog

## 2026-10-20 — krok 1: deterministické jádro (bez AI)
- `faktury/models.py` — typy (Invoice, PurchaseOrder, Receipt, Supplier, Finding, Result), peníze v Decimal.
- `faktury/ico.py` — kontrolní číslice IČO (modulo 11).
- `faktury/checks.py` — IČO, aritmetika (řádky / DPH / celkem), dodavatel + bankovní účet,
  trojí párování (dodavatel, měna, položky, množství vs. objednáno a převzato, cena ±1 %).
- `faktury/store.py` — SQLite. `content_hash` UNIQUE = idempotence (stejný dokument = replay).
  Částečný UNIQUE index (ičo + číslo, jen nezastavené) = databáze sama nepustí druhou živou fakturu.
- `faktury/pipeline.py` — `process()` = replay → pravidla → duplicity → rozhodnutí → uložení.
  Rozhodnutí: jakýkoliv STOP → STOP, jinak REVIEW → REVIEW, jinak APPROVE.
- `scripts/make_samples.py` — 3 smyšlení dodavatelé, 3 objednávky, 3 příjemky, 10 scénářů + `data/expected.json`.
- `tests/test_core.py` — 32 testů, všechny zelené.

**Rozhodnutí:**
- Zastavená faktura neblokuje opravenou verzi se stejným číslem (dodavatel pošle opravu).
- Cizí bankovní účet u známého dodavatele = vždy STOP (nejčastější fakturační podvod).
- SQLite místo Postgresu: jde spustit bez instalace, pro ukázku stačí.


## 2026-10-20 — krok 2: AI extrakce + eval sada (bez klíče, zatím jen offline)
- `faktury/extract.py` — Claude Haiku 5.5 (`claude-haiku-5-5`), structured output přes `output_config.format`
  (JSON schema), effort `low`, žádné nástroje. Částky jako text → Decimal. Špatný výstup = `ExtractionError` → REVIEW.
  Cache podle hashe (model + verze promptu + text) v `eval_cache/` = stejný text se neplatí dvakrát.
- `faktury/guard.py` — druhá vrstva: hledá v původním dokumentu text, který dává pokyny AI → REVIEW.
  Hlavní obrana zůstává: pravidla text nečtou a AI nemá nástroje.
- `scripts/make_eval.py` — 40 faktur jako text ve 3 vzhledech (A daňový doklad, B anglický, C volný text),
  kategorie: clean 12, mismatch 6, fraud_account 4, math_error 4, unknown 4, injection 6, injection_attack 2, format 2.
  Očekávané rozhodnutí = jádro na pravdivých hodnotách.
- `scripts/run_eval.py` — skóre: shoda rozhodnutí, **falešná schválení** (musí být 0, jinak exit 1), přesnost polí,
  únos AI u injection, tokeny, cena, latence. Režimy `--oracle` (bez AI) a `--offline` (jen cache, 0 $).
- 55 testů zelených, `--oracle` 40/40.

**Nález:** hlídač minul „[instrukce pro model: …]“. Test „injection nikdy neočekává APPROVE“ to chytil. Opraveno.
**Známé omezení:** guard nechytí sociální inženýrství bez zmínky o AI („přepište IČO dodavatele…“).
Ten případ ale zastaví pravidla (cizí IČO u objednávky).

**Čeká na:** `ANTHROPIC_API_KEY` v `.env` → `python scripts/run_eval.py`. Odhad ceny 40 faktur < 0,05 $.

## 2026-10-20 — lokální web „Zkus to oklamat“ (předběžná verze kroku 6)
- `web/app.py` (FastAPI) + `web/index.html` (vanilla JS, tmavý styl, tyrkys). Port 8765.
- 10 faktur, úprava polí, 6 triků (cizí účet, zdražení, +5 ks, +1 000 Kč, vzkaz pro AI, rozbité IČO),
  přepočet součtů, výsledek krok po kroku, mazání paměti. Bez volání AI.
- Past: SQLite spojení nejde sdílet mezi vlákny FastAPI → `check_same_thread=False` + zámek v `app.py`.
- Ověřeno screenshotem na 1440 px a 390 px (mobil bez vodorovného posuvníku).

## 2026-10-20 — smaragdový vzhled + faktury jako PDF, čtení přes AI
- Vzhled převzatý z RAG-Dema: paleta „Bright Leaf + Emerald Green“, písma Bricolage Grotesque / Onest / JetBrains Mono,
  mřížka s auroru, pilulková navigace, sekce „AI čte, kód rozhoduje“.
- `scripts/render_invoices.py` — faktury jako skutečné PDF + PNG (styl českého daňového dokladu, QR platba SPD
  s IBAN, razítko, podpis). 3 dodavatelé = 3 barvy. `--eval` vykreslí i 40 eval faktur.
- `faktury/extract_cli.py` — lokální čtení fotky/PDF přes `claude -p --model haiku --allowedTools Read`
  (předplatné, bez API klíče). `normalize()` jen sjednocuje formáty (IBAN → účet, čárky, mezery, diakritika v kódu položky).
- Web: obrázek faktury, nahrání nebo vyfocení (JPG/PNG/WEBP/PDF, 10 MB), triky faktury překreslí (`/api/render`),
  odpovědi AI v cache `data/extracted/`.
- `tests/test_normalize.py` — 61 testů zelených.

**Nález:** Haiku z obrázku plete číslice (8↔6, 0↔8) u IČO, účtu i kódu položky: 5 z 10 faktur mělo chybu.
Z PDF (textová vrstva) 10 z 10 bez chyby. Žádná chyba čtení nevedla ke schválení: špatný účet = STOP,
špatná položka = REVIEW. Ukázky se proto čtou z PDF, fotky přes obrázek.
**Další nápad:** QR platbu číst kódem (cv2) a porovnat s účtem a částkou, které přečetla AI.

## 2026-10-21 — kontrola kódu jako senior: 3 cesty k falešnému schválení
Postup: podezření nejdřív ověřit sondou na skutečném kódu, opravit jen potvrzené, zamknout testem (`tests/test_regressions.py`).

**Potvrzeno a opraveno:**
- Stejné číslo faktury jinak zapsané + jiné datum → **APPROVE (dvojí platba)**. Teď `number_key()` (bez mezer, pomlček, velikosti písmen) + částečný UNIQUE index na `(supplier_ico, number_key)`.
- Položka rozdělená na 2 řádky (40 + 40, objednáno 40) → **APPROVE**. Teď se kusy sčítají podle položky.
- Stejná objednávka vyfakturovaná znovu pod novým číslem → **APPROVE**. Teď tabulka `invoice_lines`, sčítá se s dřívějšími nezastavenými fakturami.
- Faktura na 0 Kč → APPROVE. Teď `TOTAL_NOT_POSITIVE` / `LINE_NOT_POSITIVE` = REVIEW.
- Zaokrouhlení 0,125 → 0,12 (výchozí ROUND_HALF_EVEN). Teď ROUND_HALF_UP.
- Účet s nulami navíc → falešné STOP. Teď `account_key()`.
- MCP otevřel jakoukoliv cestu. Teď jen `data/`, `inbox/`, `.tmp/uploads/` nebo `KONTROLA_FAKTUR_FILES`.
- `/api/render` na špatná data spadl 500. Teď validace přes `InvoiceIn` → 422. Podezření na HTML injection se **nepotvrdilo** (datum a čísla padnou na parsování dřív).

**Přidáno:** text z PDF čte kód (PyMuPDF) a dostane ho hlídač; fotka = vždy aspoň REVIEW (`READ_FROM_PHOTO`); nepovedené čtení = REVIEW (`EXTRACTION_FAILED`), ne chyba; volitelný `X-API-Key` (`KONTROLA_FAKTUR_API_KEY`); limit 2 souběžná / 20 za hodinu čtení přes AI; audit `/v1/history` + MCP `invoice_history` (důvody, zdroj, verze pravidel, čas); migrace starých `history.db`.
Na stránce upozornění „Ukázkový projekt“, sekce „Z čeho je postavený“ a patička jako v RAG-Demu.

Testy: 108 zelených. Ověřeno i naživo: MCP přes skutečné stdio spojení (8 nástrojů), REST API na běžícím serveru, stránka na 1440 a 390 px bez chyb v konzoli.

**Zůstává:** uživatelské účty a fronta ke schválení (kdo pustil platbu), ARES a registr DPH naživo, QR platba kódem, ostré měření 40 faktur přes API, veřejné nasazení.
