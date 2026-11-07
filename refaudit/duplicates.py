"""Find entries that may be the same work, cited twice under different keys."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from . import bib

_SMALL_WORDS = {"a", "an", "and", "the", "of", "for", "in", "on", "to", "with", "via", "by", "from",
                "at", "its", "is", "are", "using", "based", "toward", "towards"}


def _title_words(ref) -> set:
    words = (bib.fold(w) for w in re.split(r"[\s\-‐/:,.;()]+", bib.plain(ref.get("title"))))
    return {w for w in words if w not in _SMALL_WORDS and len(w) > 1}


def _first_author(ref) -> str:
    authors, _ = bib.authors_of(ref)
    return bib.fold(authors[0][0]) if authors else ""


def _record_doi(record: dict | None) -> str:
    return (record or {}).get("doi", "").lower()


@dataclass
class _Entry:
    """What the comparison needs from one entry, worked out once rather than for every pair."""
    ref: bib.Reference
    doi: str
    arxiv: str
    title: str
    first: str
    year: int | None
    words: set


def _entry(ref) -> _Entry:
    return _Entry(ref, bib.doi_of(ref), bib.arxiv_of(ref), bib.fold(bib.plain(ref.get("title"))),
                  _first_author(ref), bib.year_of(ref), _title_words(ref))


@dataclass
class Thresholds:
    """How alike two entries must be to be listed (duplicate_* in consistency.yaml)."""
    similarity: float = 0.88   # titles at least this alike count as "nearly the same"
    word_overlap: float = 0.6  # share of title words for a likely conference and journal version
    year_gap: int = 2          # ...and at most this many years apart


def _nearly_same(a: str, b: str, at_least: float) -> bool:
    """SequenceMatcher's ratio of at least `at_least`, ruling most pairs out with its cheap upper bounds."""
    matcher = difflib.SequenceMatcher(None, a, b)
    return matcher.real_quick_ratio() >= at_least and matcher.quick_ratio() >= at_least \
        and matcher.ratio() >= at_least


def _why(a: _Entry, b: _Entry, records: dict, limits: Thresholds) -> str | None:
    """Why these two entries may be the same work, or None. Strongest reason first."""
    if a.doi and a.doi.lower() == b.doi.lower():
        return f"same DOI ({a.doi})"
    if a.arxiv and a.arxiv.lower() == b.arxiv.lower():
        return f"same arXiv ID ({a.arxiv})"
    verified = _record_doi(records.get(a.ref.key))
    if verified and verified == _record_doi(records.get(b.ref.key)):
        return f"both verified as the same work ({records[a.ref.key]['doi']})"
    if not a.title or not b.title:
        return None
    if a.title == b.title:
        return "same title" if len(a.title) >= 12 else None
    if len(a.title) >= 20 and len(b.title) >= 20:
        if bib.same_title(a.ref.get("title"), b.ref.get("title")):
            return "same title, apart from a label or subtitle"
        if _nearly_same(a.title, b.title, limits.similarity):
            return "nearly the same title"
    # A conference paper and its journal version: same lead author, close years, and a
    # title that mostly overlaps.
    if a.first and a.first == b.first and a.year and b.year and abs(a.year - b.year) <= limits.year_gap \
            and min(len(a.words), len(b.words)) >= 3:
        overlap = len(a.words & b.words) / len(a.words | b.words)
        if overlap >= limits.word_overlap:
            return "same first author, close years and mostly the same title words"
    return None


def find(refs: list, records: dict | None = None,
         limits: Thresholds | None = None) -> list[tuple[list[str], list[str]]]:
    """Groups of entries that may be the same work: (cite keys, reasons), in file order.

    `records` maps a cite key to its verified record, if it has one, so two entries that
    verified as the same DOI are caught even when they're written differently. Entries
    with the same cite key are a different problem, reported by the sanity checks.
    """
    records = records or {}
    limits = limits or Thresholds()
    parent = list(range(len(refs)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    reasons: dict = {}
    entries = [_entry(ref) for ref in refs]
    for i, a in enumerate(refs):
        for j in range(i + 1, len(refs)):
            b = refs[j]
            if a.key.lower() == b.key.lower():
                continue
            why = _why(entries[i], entries[j], records, limits)
            if why:
                reasons[(i, j)] = why
                parent[root(j)] = root(i)
    groups: dict = {}
    for i in range(len(refs)):
        groups.setdefault(root(i), []).append(i)
    found = []
    for members in groups.values():
        if len(members) < 2:
            continue
        keys = [refs[i].key for i in members]
        pairs = [(i, j, text) for (i, j), text in reasons.items() if i in members]
        if len({text for _, _, text in pairs}) == 1:
            why = [pairs[0][2]]  # one reason covers the whole group
        else:
            why = [f"{refs[i].key} and {refs[j].key}: {text}" for i, j, text in pairs]
        found.append((keys, why))
    return found
