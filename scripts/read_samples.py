"""Let the AI read the 10 demo invoice PDFs once. Results are cached in data/extracted/,
so the demo page never spends the daily budget on them.

  python scripts/read_samples.py                 # reader from .env (DeepSeek via OpenRouter, or Claude CLI)
  KONTROLA_FAKTUR_READER=claude-cli python scripts/read_samples.py
"""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from faktury.service import DailyBudget, Service, reader_name  # noqa: E402

svc = Service(budget=DailyBudget(1000, None))  # own budget: preparing the demo is not visitor traffic


def one(p):
    try:
        r = svc.read(p)
        return f"{p.stem}: OK {r['reader']} {'cache' if r['cached'] else f'{r['seconds']} s'}"
    except Exception as e:
        return f"{p.stem}: CHYBA {e}"


if __name__ == "__main__":
    print("čtečka:", reader_name())
    files = sorted((ROOT / "data/pdf").glob("*.pdf"))
    with ThreadPoolExecutor(3) as ex:
        for line in ex.map(one, files):
            print(line, flush=True)
