"""Free checks on the bib itself. These run on every audit and make no network calls."""
from __future__ import annotations

import difflib
import re
from datetime import date

from . import bib, format as fmt

# BibTeX's required fields. A tuple means any one of those fields will do.
_REQUIRED = {
    "article": ["author", "title", ("journal", "journaltitle"), ("year", "date")],
    "inproceedings": ["author", "title", "booktitle", ("year", "date")],
    "conference": ["author", "title", "booktitle", ("year", "date")],
    "book": [("author", "editor"), "title", "publisher", ("year", "date")],
    "inbook": [("author", "editor"), "title", ("chapter", "pages"), "publisher", ("year", "date")],
    "incollection": ["author", "title", "booktitle", "publisher", ("year", "date")],
    "techreport": ["author", "title", "institution", ("year", "date")],
    "phdthesis": ["author", "title", "school", ("year", "date")],
    "mastersthesis": ["author", "title", "school", ("year", "date")],
    "manual": ["title"],
    "misc": ["title"],
}
_LATEX_TEXT_FIELDS = ("title", "booktitle", "journal", "journaltitle", "publisher", "series")
_VENUE_YEAR = re.compile(r"(?<!\d)((?:19|20)\d\d)(?!\d)|['’](\d\d)(?!\d)")


def _missing_fields(ref) -> list[str]:
    issues = []
    for need in _REQUIRED.get(ref.etype, []):
        options = need if isinstance(need, tuple) else (need,)
        if not any(ref.get(name).strip() for name in options):
            issues.append(f"Missing required field for @{ref.etype}: {' or '.join(options)}.")
    return issues


def _empty_fields(ref) -> list[str]:
    return [f"The {name} field is empty." for name, value in ref.fields.items() if not value.strip()]


def _venue_year(ref) -> list[str]:
    year = bib.year_of(ref)
    venue = ref.get("booktitle")
    if not year or not venue:
        return []
    written = [m.group(1) or m.group(2) for m in _VENUE_YEAR.finditer(venue)]
    if written and not any(w == str(year) or (len(w) == 2 and int(w) == year % 100) for w in written):
        return [f"The venue says \"{bib.plain(venue)}\", but the entry's year is {year}."]
    return []


def _year(ref, today: date) -> list[str]:
    text = ref.get("year").strip()
    if not text:
        return []
    year = bib.year_of(ref)
    if year is None:
        return [f"The year \"{text}\" doesn't look like a year."]
    if year > today.year + 1:
        return [f"The year {year} is in the future."]
    return []


def _pages(ref) -> list[str]:
    pages = ref.get("pages").strip()
    if re.fullmatch(r"\d+\s*-\s*\d+", pages):
        return [f"The pages \"{pages}\" use a single hyphen. BibTeX expects \"--\"."]
    m = re.fullmatch(r"(\d+)\s*(?:--|–)\s*(\d+)", pages)
    if m and int(m.group(2)) < int(m.group(1)):
        return [f"The pages \"{pages}\" run backward or are abbreviated."]
    return []


def _doi(ref) -> list[str]:
    raw = bib.plain(ref.get("doi")).strip()
    if not raw:
        return []
    if re.match(r"(https?://|doi:)", raw, re.I):
        return [f"The doi field has a prefix. It should hold only the DOI: {bib.clean_doi(raw)}"]
    if not re.fullmatch(r"10\.\d{4,9}/\S+", raw):
        return [f"The doi \"{raw}\" isn't a valid DOI."]
    return []


def _authors(ref, org_words=None) -> list[str]:
    value = ref.get("author")
    issues = []
    if re.search(r"\bet\.?\s+al\b", value, re.I):
        issues.append("The author list has \"et al.\" written out. BibTeX expects \"and others\".")
    for name in bib.split_authors(value):
        depth = commas = 0
        for c in name:
            depth += {"{": 1, "}": -1}.get(c, 0)
            commas += c == "," and depth == 0
        if commas > 2:
            issues.append(f"\"{bib.plain(name)}\" looks like several names. Authors should be "
                          "separated by \"and\", not commas.")
            break
    for name in bib.split_authors(value):
        if bib.looks_like_organization(name, org_words):
            shown = bib.plain(name)
            issues.append(f"\"{shown}\" looks like an organization. Write it in braces, {{{shown}}}, or "
                          "BibTeX reads it as a person and may print it as initials and a surname.")
    return issues


def _unescaped(ref) -> list[str]:
    issues = []
    for name in _LATEX_TEXT_FIELDS:
        text = re.sub(r"\$[^$]*\$", "", ref.get(name))
        found = sorted(set(re.findall(r"(?<!\\)[&%#_]", text)))
        if found:
            issues.append(f"The {name} field has unescaped {' '.join(found)}, which can break LaTeX.")
    return issues


def _duplicate_keys(refs: list) -> list[tuple[str, str]]:
    """The same cite key twice. Different keys for the same work are in duplicates.py."""
    counts: dict[str, list] = {}
    for ref in refs:
        counts.setdefault(ref.key.lower(), []).append(ref)
    return [(group[0].key, f"This cite key is used by {len(group)} entries. BibTeX keeps only the first, "
                           "so citations of the others point to the wrong work.")
            for group in counts.values() if len(group) > 1]


_NO_ASCII_NEEDED = ("url", "doi", "file", "eprint", "urldate", "timestamp", "biburl", "bibsource")
_WRONG_FOR_TYPE = {"journal": ("inproceedings", "conference", "incollection"),
                   "booktitle": ("article",)}


def _non_ascii(ref) -> list[str]:
    issues = []
    for name, value in ref.fields.items():
        if name in _NO_ASCII_NEEDED:
            continue
        # Accented letters are what classic BibTeX mis-sorts and mis-cases. Dashes and quotes print fine.
        found = sorted({c for c in value if ord(c) > 127 and c.isalpha()})
        if found:
            issues.append(f"The {name} field has non-ASCII characters ({' '.join(found[:6])}), which "
                          "classic BibTeX can sort or capitalize wrongly. Write them as LaTeX, like "
                          "{\\\"u} for ü, or set backend: biber in lint.yaml if you use biber.")
    return issues


def _unprotected_capitals(ref, keep_words: list = (), ignore=()) -> list[str]:
    """Shares its word list with format's protect_capitals, so the two always agree."""
    risky = fmt.at_risk(ref.get("title"), list(keep_words), ignore)
    if not risky:
        return []
    shown = ", ".join(risky[:8])
    return [f"The title has capitals a BibTeX style may lowercase: {shown}. Wrap each in braces, "
            f"like {{{risky[0]}}}, or set protect_capitals: add in your style."]


def _unknown_fields(ref) -> list[str]:
    issues = []
    for name in ref.fields:
        if name in bib.KNOWN_FIELDS:
            continue
        close = difflib.get_close_matches(name, bib.KNOWN_FIELDS, n=1, cutoff=0.8)
        if close:
            issues.append(f"The field \"{name}\" looks like a misspelling of \"{close[0]}\". "
                          "BibTeX ignores fields it doesn't know.")
    return issues


def _wrong_type_fields(ref) -> list[str]:
    issues = []
    for name, types in _WRONG_FOR_TYPE.items():
        if ref.etype in types and ref.get(name).strip():
            wanted = "booktitle" if name == "journal" else "journal"
            if not ref.get(wanted).strip():
                issues.append(f"@{ref.etype} entries take {wanted}, not {name}, so the venue may not be printed.")
    return issues


CHECKS = {
    "required_fields": _missing_fields, "empty_fields": _empty_fields, "venue_year": _venue_year,
    "pages": _pages, "doi": _doi, "authors": _authors, "latex_specials": _unescaped,
    "non_ascii": _non_ascii, "unprotected_capitals": _unprotected_capitals,
    "unknown_fields": _unknown_fields, "wrong_type_fields": _wrong_type_fields,
}


def check_entries(refs: list, today: date | None = None, enabled: dict | None = None,
                  backend: str = "bibtex", keep_capitalized: list = (), capitals_ignore=(),
                  org_words=None) -> list[tuple[str, str]]:
    """(cite key, problem) for everything found, in file order, with repeated keys last.

    `enabled` turns checks off by name; `backend` biber allows UTF-8. `keep_capitalized`
    (from format.yaml) adds proper nouns to the capitals a style may lowercase."""
    today = today or date.today()
    enabled = enabled or {}
    on = {name: enabled.get(name, True) for name in [*CHECKS, "years", "repeated_keys"]}
    if backend == "biber":
        on["non_ascii"] = False
    issues = []
    for ref in refs:
        found = []
        for name, check in CHECKS.items():
            if not on[name]:
                continue
            if name == "unprotected_capitals":
                found.extend(check(ref, keep_capitalized, capitals_ignore))
            elif name == "authors":
                found.extend(check(ref, org_words))
            else:
                found.extend(check(ref))
            if name == "empty_fields" and on["years"]:
                found.extend(_year(ref, today))
        issues.extend((ref.key, problem) for problem in found)
    return issues + (_duplicate_keys(refs) if on["repeated_keys"] else [])
