# How to verify the numbers in the README

Every number in the README table "What I measured" comes from one of two scripts. Both replay stored AI answers,
so they cost **0 $**, need **no API key** and run in about **2 seconds**. CI runs both on every push.

```bash
pip install -e ".[dev]"
python scripts/run_eval.py --reader openrouter --offline   # 40-invoice eval
python scripts/check_samples.py                             # the 10 demo invoices: PDF vs image
```

Both exit with code `1` if any invoice is approved that should not be.

## Claim → script → data

| README claim | Command | Data it reads | Where to look in the output |
|---|---|---|---|
| 40 / 40 decisions correct, 0 false approvals | `run_eval.py --reader openrouter --offline` | `data/eval/001.txt`–`040.txt` (invoice text), `data/eval/truth.json` (true values and expected decision), `eval_cache/openrouter/*.json` (the stored model answers) | "Decision matches expected", "False approvals" |
| every field exact 40 / 40 | same | same | "Extraction fully exact", table "Field accuracy" |
| all 8 injections copied into `note`, none obeyed | same | cases with category `injection` and `injection_attack` | "Injection text copied into `note`", "Injection cases where a key value was changed" |
| 1.4 s per invoice | same | field `seconds` in each cache file, measured on the original paid call (20. 10. 2026) | "Latency p50" |
| $0.0047 for all 40 | same | field `usage.cost` in each cache file, as billed by OpenRouter | "Tokens … cost" |
| PDF: 10 of 10 every field exact (DeepSeek Flash and Claude Haiku) | `check_samples.py` | `data/pdf/*.pdf`, `data/invoices/*.json` (true values), `data/extracted/*.json` (stored answers) | "DeepSeek Flash, PDF", "Claude Haiku, PDF" |
| Image: 5 of 10 had a misread digit | same | `data/pdf/*.png` and the same files | "Claude Haiku, image", with each wrong field listed |
| 0 false approvals after a misread | same | `data/expected.json` (expected decisions) | "Decisions on the image reads", "False approvals" |

The full output of the last run is in [eval-deepseek-v4.1-flash.md](eval-deepseek-v4.1-flash.md) (per category,
per field) and [eval-deepseek-v4.1-flash.json](eval-deepseek-v4.1-flash.json) (per invoice).

## Is the harness itself right?

```bash
python scripts/run_eval.py --oracle
```

Puts the true values in place of the AI answer. It must give 40 / 40. If it did not, the expected decisions or the
rules would be wrong, not the model.

## How to check that the cache is not made up

- Each cache file name is a hash of the model name, the prompt version, the system prompt and the invoice text
  (`faktury/extract_openrouter.cache_path`). Change any of them and the replay fails with "missing answer" instead of
  paying or guessing.
- Each file stores the raw model output (`raw`), the usage OpenRouter returned (`usage`) and the time (`seconds`).
- To repeat the run live, delete `eval_cache/openrouter/`, put `OPENROUTER_API_KEY` in `.env` and run
  `python scripts/run_eval.py --reader openrouter`. It costs about $0.005. Results can differ by a field or two
  between runs, the decisions should not.

## What these numbers do not show

- 40 invoices made by me, in 3 layouts. Real invoices from many suppliers will be harder.
- The image result (5 of 10) is from one run of Claude Haiku on clean rendered images, not phone photos.
- "0 false approvals" depends on the rules, not on the model. That is the design: the model may be wrong,
  the rules decide.
