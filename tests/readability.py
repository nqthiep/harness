"""Flesch–Kincaid grade level, for text a child has to read.

SC-1b cannot be run without children, but half of what it measures is mechanical: if a
message reads at grade 10, a ten-year-old cannot act on it, and no amount of goodwill in
the study will change that.  This is the part of SC-1b that can be measured today.
"""
from __future__ import annotations

import re

_VOWELS = "aeiouy"


def syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    count, prev_vowel = 0, False
    for ch in w:
        is_vowel = ch in _VOWELS
        if is_vowel and not prev_vowel:
            count += 1
        prev_vowel = is_vowel
    if w.endswith("e") and count > 1:
        count -= 1
    return max(1, count)


def strip_markup(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)      # code blocks
    text = re.sub(r"`[^`]*`", " ", text)                    # inline code
    text = re.sub(r"^\s*\|.*$", " ", text, flags=re.M)      # tables
    # Indented code inside a message is code, not prose.  Left in, a four-line example
    # with no full stop counts as one enormous sentence and inflates the grade.
    text = re.sub(r"^ {4,}\S.*$", " ", text, flags=re.M)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"^\s*->.*$", " ", text, flags=re.M)     # "-> docs/..." pointers
    text = re.sub(r"\S+\.md\S*", " ", text)                # file paths and anchors
    text = re.sub(r"[*_#>\[\]()—–…🎉←]", " ", text)
    return text


def grade(text: str, *, line_oriented: bool = False) -> tuple[float, int, int]:
    """(Flesch-Kincaid grade, sentences, words).

    `line_oriented` treats a newline as a sentence break, which is how someone reads a
    terminal message: a list row without a full stop is a unit, not a continuation of the
    line above it.  Without this, a four-row table inside a message counts as one enormous
    sentence and the grade is an artifact of formatting (Round 31).
    """
    clean = strip_markup(text)
    splitter = r"[.!?\n]+(?:\s|$)" if line_oriented else r"[.!?]+(?:\s|$)"
    sentences = [s for s in re.split(splitter, clean) if s.strip()]
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", clean)
    if not sentences or not words:
        return 0.0, 0, 0
    syl = sum(syllables(w) for w in words)
    g = 0.39 * (len(words) / len(sentences)) + 11.8 * (syl / len(words)) - 15.59
    return round(g, 1), len(sentences), len(words)


def hard_words(text: str, *, min_syllables: int = 4) -> list[str]:
    seen, out = set(), []
    for w in re.findall(r"[A-Za-z][A-Za-z'-]*", strip_markup(text)):
        lw = w.lower()
        if lw in seen or len(lw) < 5:
            continue
        seen.add(lw)
        if syllables(lw) >= min_syllables:
            out.append(lw)
    return sorted(out)
