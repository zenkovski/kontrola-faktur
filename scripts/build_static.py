"""Build the static web version into site/ (for Cloudflare Pages, no Python server).

  python scripts/build_static.py

The 10 demo invoices are read from the AI cache (a budget of 0 makes sure nothing paid is called).
Checking runs in the browser: web/static-api.js loads Pyodide and the project's own Python code.
Upload is off on the web: reading a new invoice costs money, the local version does it.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from faktury.service import DailyBudget, Service  # noqa: E402

OUT = ROOT / "site"
PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.27.2/full/pyodide.js"
PY_FILES = ["faktury/*.py", "scripts/render_invoices.py", "scripts/make_samples.py", "web/static_api.py",
            "data/suppliers.json", "data/orders.json", "data/receipts.json", "data/expected.json",
            "data/invoices/*.json"]

PAGE_EDITS = [
    ('<script>\nconst $ = s', f'<script src="{PYODIDE}"></script>\n<script src="static-api.js"></script>\n<script>\nconst $ = s'),
    ('$("#hint").textContent = `${photos}. Vlastních faktur dnes zbývá ${u.left} z ${u.limit}. Ukázky vlevo fungují vždy.`;',
     '$("#hint").textContent = "Na webu jen ukázky vlevo. Kontrola běží přímo v prohlížeči (Python přes Pyodide).";'),
    ('<a href="/docs">API dokumentace</a>', '<a href="https://github.com/zenkovski/kontrola-faktur#rest-api">API (lokální verze)</a>'),
    ('<span>Nahrané faktury zůstávají na tomhle počítači ve složce .tmp/uploads.</span>',
     '<span>Webová verze: kontrola běží v tvém prohlížeči, nic se nikam neposílá.</span>'),
]


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "api").mkdir(parents=True)
    (OUT / "py").mkdir()
    shutil.copytree(ROOT / "data" / "pdf", OUT / "doc")

    service = Service(":memory:", budget=DailyBudget(0, None))
    samples = [{**d, "image": f"/doc/{d['id']}.png", "pdf": f"/doc/{d['id']}.pdf"} for d in service.demo_list()]
    reads = {s["id"]: service.read(ROOT / "data" / "pdf" / f"{s['id']}.pdf") for s in samples}
    assert all(r["cached"] for r in reads.values()), "a demo invoice is not in the AI cache"
    write(OUT / "api" / "samples.json", samples)
    write(OUT / "api" / "books.json", service.reference())
    write(OUT / "api" / "reads.json", reads)

    bundle = {}
    for pattern in PY_FILES:
        for p in sorted(ROOT.glob(pattern)):
            bundle[p.relative_to(ROOT).as_posix()] = p.read_text(encoding="utf-8")
    write(OUT / "py" / "bundle.json", bundle)

    page = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    for old, new in PAGE_EDITS:
        assert page.count(old) == 1, f"page changed, edit not found: {old[:50]}"
        page = page.replace(old, new)
    (OUT / "index.html").write_text(page, encoding="utf-8")
    shutil.copy(ROOT / "web" / "static-api.js", OUT / "static-api.js")
    print(f"site/ ready: {len(samples)} samples, {len(bundle)} Python/data files")


def write(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
