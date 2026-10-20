# Kontrola faktur — pravidla projektu

Portfolio ukázka na GitHub. Pravidla pro AI parťáka (Claude Code), který na projektu pracuje.

## Hlavní pravidlo
**AI čte, kód rozhoduje.** Výpočty, limity, párování a duplicity jsou v `faktury/checks.py` a
`faktury/pipeline.py`. AI (krok 2) smí jen vytáhnout data z dokumentu do typu `Invoice`
a lidsky vysvětlit nálezy. AI nikdy nemění `Decision`.

## Pevné zásady
- Peníze jen `Decimal`, nikdy `float`.
- Pole `note` je nedůvěryhodný text. Jádro ho nečte. Test `test_note_is_ignored_by_rules` to hlídá.
- Každé pravidlo = test v `tests/`. Nové pravidlo bez testu nepřidávat.
- Data jsou smyšlená (`scripts/make_samples.py`). Žádné skutečné faktury do repa.
- Žádné závislosti navíc bez důvodu. Jádro = jen stdlib.
- Hlášky pro návštěvníka česky, kód a názvy anglicky.

## Příkazy
```
python scripts/make_samples.py   # přegeneruje data/
python -m pytest -q              # testy
python scripts/make_eval.py      # přegeneruje 40 eval faktur
python scripts/run_eval.py --oracle    # měření bez AI (kontrola skriptu)
python scripts/run_eval.py --offline   # jen uložené odpovědi, 0 $
python scripts/run_eval.py             # ostré měření (potřebuje ANTHROPIC_API_KEY v .env)
python -m uvicorn web.app:app --port 8765   # lokální web http://127.0.0.1:8765
```

## Stav
Viz `BUILDLOG.md`.
