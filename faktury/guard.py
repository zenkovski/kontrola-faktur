"""Scan the raw document for text aimed at an AI.

This is a second, independent layer. The main defence is that the rules never read
free text and the AI has no tools. This scan only raises the case to a human (REVIEW),
because a real supplier never writes instructions for an AI on an invoice.
"""
from __future__ import annotations

import re
import unicodedata

from .models import Finding, Severity

PATTERNS = [
    r"\b(ai|a\.i\.|llm|gpt|claude|chatbot|asistent\w*|assistant|model)\b.{0,40}\b(schval\w*|approve\w*|ignor\w*|pokyn\w*|instrukc\w*)",
    r"\b(pokyn\w*|instrukc\w*|instructions?)\s+(pro|for)\s+(ai|llm|gpt|claude|model\w*|asistent\w*|assistant)\b",
    r"\bignor\w*\b.{0,40}\b(kontrol\w*|pravidl\w*|instrukc\w*|pokyn\w*|checks?|rules?|instructions?)",
    r"\b(system|systém)\s*:.{0,20}\b(schval\w*|approve\w*|ignor\w*)",
    r"\b(předschválen\w*|preapproved|pre-approved)\b",
    r"\bdecision\s*=",
]
_RX = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in PATTERNS]


def _normalize(text: str) -> str:
    # drop zero-width characters people use to hide words, collapse whitespace
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    return re.sub(r"\s+", " ", text)


def scan_document(text: str) -> list[Finding]:
    clean = _normalize(text)
    for rx in _RX:
        m = rx.search(clean)
        if m:
            return [Finding("TEXT_FOR_AI", Severity.REVIEW,
                            "Dokument obsahuje text, který dává pokyny AI. Skutečný dodavatel tohle "
                            "nepíše. Rozhodnutí to nemění, ale případ musí vidět člověk.",
                            {"match": m.group(0)[:120]})]
    return []
