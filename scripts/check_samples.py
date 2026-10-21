"""Score the stored AI reads of the 10 demo invoices against the true data. 0 $, no network.

  python scripts/check_samples.py

For every demo invoice in data/pdf/ it looks up the cached answer in data/extracted/ for:
  - DeepSeek v4.1 Flash on the PDF text layer (the public reader),
  - Claude Haiku (claude -p) on the PDF,
  - Claude Haiku (claude -p) on the PNG image of the same invoice,
and compares every field with data/invoices/<name>.json. A missing cache entry is reported, never paid for.

Then it runs the image reads through the rules (in order, like the demo) WITHOUT the photo rule, to show
that the rules alone catch the misreads: no invoice may be approved that should not be.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from faktury import extract_cli, extract_openrouter as OR  # noqa: E402
from faktury.extract import to_invoice  # noqa: E402
from faktury.models import Invoice  # noqa: E402
from faktury.pipeline import Books, process  # noqa: E402
from faktury.store import Store  # noqa: E402
from faktury.service import pdf_text  # noqa: E402
from run_eval import FIELDS, norm  # noqa: E402

CACHE = ROOT / "data" / "extracted"


def cli_cache(path: Path) -> Path:
    # same key as faktury/extract_cli.read_file
    return CACHE / (hashlib.sha256(path.read_bytes() + extract_cli.PROMPT.encode()).hexdigest()[:24] + ".json")


def score(raw: dict, truth: Invoice) -> list[str]:
    got = to_invoice(raw)
    return [f"{f}: AI {getattr(got, f)} vs truth {getattr(truth, f)}"
            for f in FIELDS if norm(f, getattr(got, f)) != norm(f, getattr(truth, f))]


def main() -> int:
    expected = json.loads((ROOT / "data" / "expected.json").read_text(encoding="utf-8"))
    image_reads = {}
    readers = {"DeepSeek Flash, PDF": [], "Claude Haiku, PDF": [], "Claude Haiku, image": []}
    for pdf in sorted((ROOT / "data" / "pdf").glob("*.pdf")):
        truth = Invoice.from_dict(json.loads((ROOT / "data" / "invoices" / f"{pdf.stem}.json").read_text(encoding="utf-8")))
        sources = {
            "DeepSeek Flash, PDF": OR.cache_path(pdf_text(pdf), CACHE),
            "Claude Haiku, PDF": cli_cache(pdf),
            "Claude Haiku, image": cli_cache(pdf.with_suffix(".png")),
        }
        for name, c in sources.items():
            if not c.exists():
                readers[name].append((pdf.stem, None))
                continue
            d = json.loads(c.read_text(encoding="utf-8"))
            readers[name].append((pdf.stem, score(d.get("raw", d), truth)))
            if name == "Claude Haiku, image":
                image_reads[pdf.stem] = to_invoice(d.get("raw", d))

    for name, rows in readers.items():
        have = [r for r in rows if r[1] is not None]
        exact = sum(1 for _, wrong in have if not wrong)
        print(f"\n{name}: {exact} of {len(have)} with every field exact"
              + (f" ({len(rows) - len(have)} not in cache)" if len(have) < len(rows) else ""))
        for stem, wrong in rows:
            if wrong is None:
                print(f"  {stem}: not in cache")
            elif wrong:
                print(f"  {stem}: " + "; ".join(wrong))
    books, store, false_approvals = Books.load(ROOT / "data"), Store(), []
    print("\nDecisions on the image reads (rules only, photo rule off):")
    for stem in sorted(image_reads):
        got = process(image_reads[stem], books, store).decision.value
        want = expected[stem]["decision"]
        if got == "APPROVE" and want != "APPROVE":
            false_approvals.append(stem)
        print(f"  {stem}: expected {want:<8} got {got}")
    print(f"False approvals: {len(false_approvals)} {false_approvals or ''}")
    return 1 if false_approvals else 0


if __name__ == "__main__":
    sys.exit(main())
