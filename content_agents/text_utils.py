from __future__ import annotations

import re
from typing import List, Tuple

_FENCE = re.compile(r"```.*?```", re.DOTALL)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_URL = re.compile(r"https?://\S+")
_CUE = re.compile(r"^\s*[\[(](VISUAL|B-ROLL|SFX|MUSIC|pause)\b.*$", re.IGNORECASE | re.MULTILINE)


def word_count(md: str, include_code: bool = False) -> int:
    """Whitespace tokens containing a letter/digit. (Regex \\w splits Devanagari at vowel signs.)"""
    text = md if include_code else _FENCE.sub(" ", md)
    text = _IMAGE.sub(" ", text)
    text = _LINK.sub(r"\1", text)
    text = _URL.sub(" ", text)
    return sum(1 for tok in text.split() if any(c.isalnum() for c in tok))


def narration(script_md: str) -> str:
    """Script text without production cues ([VISUAL: ...], (pause)) and Markdown heading marks."""
    return re.sub(r"^#+\s*", "", _CUE.sub("", script_md), flags=re.MULTILINE)


def devanagari_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if "ऀ" <= c <= "ॿ") / len(letters)


def extract_links(md: str) -> List[Tuple[str, str]]:
    seen, out = set(), []
    for label, url in _LINK.findall(md):
        if url not in seen:
            seen.add(url)
            out.append((label, url))
    return out


def headings(md: str, level: int = 2) -> List[str]:
    prefix = "#" * level + " "
    return [ln[len(prefix):].strip() for ln in md.split("\n") if ln.startswith(prefix)]
