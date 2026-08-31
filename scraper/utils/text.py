"""Small text helpers (shared by parsers and scoring)."""
from __future__ import annotations

import re

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "of", "to", "in", "on", "for",
    "with", "at", "by", "from", "is", "are", "was", "were", "be", "been",
    "this", "that", "these", "those", "it", "its", "as", "so", "we", "you",
    "they", "he", "she", "i", "my", "their", "our", "your", "his", "her",
    "not", "no", "has", "have", "had", "do", "does", "did", "will", "would",
    "can", "could", "should", "about", "over", "into", "me", "us", "them",
    "am", "very", "really", "just", "also", "out", "up", "down", "there",
    "here", "when", "what", "which", "who", "whom", "why", "how", "all",
    "any", "each", "more", "most", "some", "such", "only", "own", "same",
    "than", "too", "then", "now", "s", "t", "don", "ve", "ll", "re",
}


def to_int(value, default: int = 0) -> int:
    """Parse a string to int robustly (strips commas, signs, stray text)."""
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    s = re.sub(r"[^\d-]", "", str(value))
    if not s or s in ("-", ""):
        return default
    try:
        return int(s)
    except ValueError:
        return default


def to_float(value, default: float | None = None) -> float | None:
    """Parse a string to float, tolerating commas; None on failure."""
    if value is None:
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = re.sub(r"[^\d.\-]", "", str(value))
    if not s or s in (".", "-", "-.", ""):
        return default
    try:
        return float(s)
    except ValueError:
        return default


def tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, and split into word tokens."""
    if not text:
        return []
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if len(w) > 1]
