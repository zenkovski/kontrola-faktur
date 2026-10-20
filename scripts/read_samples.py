"""Let the AI read the 10 demo invoice PDFs once (via Claude Code CLI). Results are cached in data/extracted/."""
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from faktury.extract_cli import read_file  # noqa: E402


def one(p):
    try:
        inv, raw, s, cached = read_file(p, ROOT / "data/extracted")
        return f"{p.stem}: OK {'cache' if cached else f'{s:.0f} s'}"
    except Exception as e:
        return f"{p.stem}: CHYBA {e}"


if __name__ == "__main__":
    files = sorted((ROOT / "data/pdf").glob("*.pdf"))
    with ThreadPoolExecutor(3) as ex:
        for line in ex.map(one, files):
            print(line, flush=True)
