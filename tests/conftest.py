"""Tests never touch the network or a paid API, locally or in CI.

The .env file is ignored and the OpenRouter key removed, so the reader falls back to cached answers
(data/extracted/) exactly as on a fresh clone. OpenRouter itself is tested with a fake HTTP layer.
"""
import os

os.environ["KONTROLA_FAKTUR_NO_DOTENV"] = "1"
os.environ.pop("OPENROUTER_API_KEY", None)
# The /v1 API is closed by default when no key is set; tests run it open on purpose.
os.environ["KONTROLA_FAKTUR_ALLOW_OPEN"] = "1"
os.environ["KONTROLA_FAKTUR_READER"] = "claude-cli"
os.environ["KONTROLA_FAKTUR_BUDGET_FILE"] = os.path.join(os.path.dirname(__file__), "..", ".tmp", "test-budget.json")
