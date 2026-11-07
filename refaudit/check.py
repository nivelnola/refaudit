"""Turn a registry result into OK / CORRECTED / CHECK, and pair the bib with the PDF."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from . import api, bib, format as fmt

OK, CORRECTED, CHECK, LINK, NOT_CHECKED = "OK", "CORRECTED", "CHECK", "LINK OK", "NOT CHECKED"
_CONFIDENCE = {"low": 0, "medium": 1, "high": 2}
_REQUIRED = {"high": 2, "medium": 1}
_DIFFER = "The details differ from the registry's, so nothing was changed."
NO_RESULT = "Not checked: the API returned no result."


def _pretty(value) -> str:
    return "(empty)" if value in (None, "") else bib.plain(str(value))


def _record_authors(record: dict) -> str:
    names = []
    for author in record["authors"]:
        family, given = author["family"], author["given"]
        if given.endswith(" " + family):  # registries sometimes repeat it: "Henk Wymeersch"
            given = given[:-len(family)].strip()
        names.append(bib.to_latex(f"{family}, {given}" if given else family))
    return " and ".join(names)


def _words(text: str) -> set:
    return {w for w in (bib.fold(part) for part in re.split(r"[\s.\-‐]+", text)) if w}


def _same_person(family: str, given: str, other_family: str, other_given: str) -> bool:
    """Registries split compound names differently: 'Li, X. Rong' and 'Rong Li, X.' match."""
    ours, theirs = _words(family), _words(other_family)
    return ours == theirs or ours <= _words(other_family + " " + other_given) \
        or theirs <= _words(family + " " + given)


def _same_authors(authors: list, record_authors: list, truncated: bool) -> bool:
    compared = record_authors[:len(authors)] if truncated else record_authors
    return len(authors) == len(compared) and all(
        _same_person(family, given, other["family"], other["given"])
        for (family, given), other in zip(authors, compared))


def _is_article_number(number: str, record: dict) -> bool:
    """Registries can report an article number as the issue, as with Elsevier's 100682."""
    return number == record["pages"] or (number.isdigit() and len(number) >= 5)


def compare(ref: bib.Reference, record: dict) -> tuple[dict, list]:
    """Compare an entry with its verified record. Returns corrections and notes."""
    corrections, notes = {}, []
    is_journal = bib.type_of(ref) == "journal"

    title, record_title = bib.fold(bib.plain(ref.get("title"))), bib.fold(record["title"])
    if record_title and title != record_title:
        if title.startswith(record_title) or record_title.startswith(title):
            notes.append(f"The registry's title is longer or shorter than yours: "
                         f"\"{record['title']}\". Left as written.")
        else:
            corrections["title"] = bib.to_latex(record["title"])

    authors, truncated = bib.authors_of(ref)
    if record["authors"] and not _same_authors(authors, record["authors"], truncated):
        corrections["author"] = _record_authors(record)

    # A preprint's record doesn't describe a published version's venue, year or pages.
    if api.is_preprint_record(record) and bib.type_of(ref) != "preprint":
        notes.append("The identifier points to a preprint, but this entry isn't cited as one. "
                     "Year, volume and pages weren't compared.")
        return corrections, notes

    if record["year"] and bib.year_of(ref) and record["year"] != bib.year_of(ref):
        corrections["year"] = str(record["year"])
    if is_journal:
        for name in ("volume", "number"):
            if name == "number" and _is_article_number(record[name], record):
                continue
            if record[name] and bib.fold(bib.plain(ref.get(name))) != bib.fold(record[name]):
                corrections[name] = bib.to_latex(record[name])
    if record["pages"] and ref.get("pages"):
        ours = bib._pages_key(ref.get("pages"))
        theirs_pages = bib._pages_key(record["pages"])
        same_start = "--" not in record["pages"] and ours.split("-")[0] == theirs_pages
        if ours != theirs_pages and not same_start:
            corrections["pages"] = record["pages"]
    return corrections, notes


def _venue_note(result: dict, venues: fmt.Venues) -> str | None:
    for m in result["mismatches"]:
        if m["field"] == "container" and (m.get("similarity") or 0) < 0.5:
            claimed, resolved = str(m.get("claimed") or ""), str(m.get("resolved") or "")
            if not venues.same(claimed, resolved):
                return f"Venue differs: you have \"{claimed}\", the registry has \"{resolved}\"."
    return None


def _candidate_lines(result: dict, limit: int = 3) -> list[str]:
    """Only close title matches. Weaker ones are unrelated papers that share a few words."""
    lines = []
    close = [c for c in result["candidates"] if c["score"] >= 0.9]
    for candidate in close[:limit]:
        record = candidate["record"]
        where = record["doi"] or record["venue"] or "no identifier"
        lines.append(f"Possible match: \"{record['title']}\" ({record['year'] or 'n.d.'}), {where}")
    return lines


def _search_notes(result: dict) -> list[str]:
    """Notes for an entry that was confirmed by title search, not by its own identifier."""
    arxiv = result.get("arxiv")
    if arxiv:
        notes = [f"Found on arXiv as {arxiv['id']}, though not in Crossref, PubMed or OpenAlex. "
                 "Its details weren't corrected from this match."]
        if arxiv.get("journal_ref") or arxiv.get("doi"):
            published = ", ".join(v for v in (arxiv.get("journal_ref"), arxiv.get("doi")) if v)
            notes.append(f"arXiv lists a published version: {published}. Consider citing it.")
        return notes
    if result.get("crossref_match"):
        return [f"Found by searching Crossref directly: {result['record']['doi']}. "
                "Scholar Sidekick's search missed it."]
    notes = []
    if api.is_preprint_record(result["record"]):
        notes.append("The match is a preprint, so the venue you cite wasn't confirmed.")
    for candidate in result["candidates"]:
        record = candidate["record"]
        if record["doi"] and not api.is_preprint_record(record) and candidate["score"] >= 0.95:
            notes.append(f"Possible DOI, unverified: {record['doi']}")
            break
    return notes


def _title_match_differences(ref: bib.Reference, result: dict) -> tuple[list, list]:
    """Compare an entry with a record found by title. Nothing is changed from this.

    Only a clearly matching title counts, and a preprint record says nothing about a
    published version's details. Returns conflicts with your values, and values you lack.
    """
    record = result["record"]
    if not record or api.is_preprint_record(record) or not bib.same_title(ref.get("title"), record["title"]):
        return [], []
    corrections, _ = compare(ref, record)
    conflicts, extras = [], []
    for name, value in corrections.items():
        if name == "title":
            continue
        if ref.get(name):
            conflicts.append(f"{name}: you have \"{_pretty(ref.get(name))}\", "
                             f"the registry has \"{_pretty(value)}\".")
        else:
            extras.append(f"The registry also has {name} {_pretty(value)}.")
    return conflicts, extras


@dataclass
class VerifyOutcome:
    """What verification found for one reference, before any formatting."""
    ref: bib.Reference
    status: str = CHECK                              # OK | CORRECTED | LINK | CHECK
    reasons: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    corrections: dict = field(default_factory=dict)  # field -> registry value
    record: dict | None = None                       # the record reached by identifier, if any
    title_match: bool = False
    not_found: bool = False
    unused: bool = False                             # in the bib, but not in the PDF's references
    reused: bool = False                             # result came from the ledger
    checked_on: str | None = None
    source: str | None = None                        # crossref | arxiv | sidekick
    confidence: str | None = None
    link_problem: dict | None = None                 # a verified entry's link is broken or moved


def verify(ref: bib.Reference, result: dict | None, cfg, venues: fmt.Venues,
           not_checked: str | None = None) -> VerifyOutcome:
    """Decide whether a reference is verified. Anything uncertain is CHECK."""
    out = VerifyOutcome(ref=ref)
    if result is None:
        out.reasons.append(not_checked or NO_RESULT)
        return out

    retraction = result["retraction"]
    if retraction["retracted"]:
        out.reasons.append("Retracted, according to Crossref and Retraction Watch.")
    if retraction["concern"]:
        out.reasons.append("Has an expression of concern.")
    if retraction["corrections"]:
        out.notes.append("A correction has been published for this work.")
    if (result.get("arxiv") or {}).get("withdrawn"):
        out.reasons.append("arXiv's comment on this paper mentions that it was withdrawn.")

    verdict, record = result["verdict"], result["record"]
    link = result.get("link") or {}
    if verdict == "not_found" and link.get("state") == "ok":
        out.reasons.append(f"Link: {link['url']}")
        out.status = LINK
        return out
    if verdict == "mismatch":
        if any(m["field"] == "title" for m in result["mismatches"]) or not result["mismatches"]:
            out.reasons.append("Possibly wrong or fabricated: the identifier points to a work "
                               "with a different title.")
        elif bib.type_of(ref) == "book":
            out.reasons.append(f"{_DIFFER} For a book, this may just be a different edition or printing.")
        else:
            out.reasons.append(f"{_DIFFER} This may be another version of the same work.")
        for m in result["mismatches"]:
            out.reasons.append(f"{m['field']}: you have \"{m.get('claimed')}\", "
                               f"the registry has \"{m.get('resolved')}\".")
    elif verdict == "ambiguous":
        out.reasons.append("Couldn't tell whether this is the right work. It may be another "
                           "version, or the identifier may be wrong.")
        out.notes.extend(_candidate_lines(result))
    elif verdict == "not_found":
        where = "Crossref, PubMed, OpenAlex or arXiv" if result.get("arxiv_checked") \
            else "Crossref, PubMed or OpenAlex"
        out.reasons.append(f"Couldn't confirm: not found in {where}.")
        out.not_found = True
        if link.get("state") == "broken":
            out.reasons.append(f"The link is broken ({link['detail']}): {link['url']}")
        out.notes.extend(_candidate_lines(result))
    elif verdict != "matched":
        out.reasons.append(f"Unexpected API verdict: {verdict}.")
    elif result["confidence"] == "low":
        out.reasons.append("Matched, but only with low confidence.")

    if out.reasons:
        return out

    confidence = _CONFIDENCE.get(result["confidence"], 0)
    corrections = {}
    if result["by_search"]:
        out.title_match = True
        out.notes.extend(_search_notes(result))
        conflicts, extras = _title_match_differences(ref, result)
        out.notes.extend(extras)
        if conflicts:
            out.reasons.append(f"{_DIFFER} This may be another version of the same work.")
            out.reasons.extend(conflicts)
            return out
    else:
        out.record = record
        corrections, notes = compare(ref, record)
        out.notes.extend(notes)
        venue_note = _venue_note(result, venues)
        if venue_note:
            out.notes.append(venue_note)
        arxiv = result.get("arxiv") or {}
        if arxiv.get("journal_ref") or arxiv.get("doi"):
            published = ", ".join(v for v in (arxiv.get("journal_ref"), arxiv.get("doi")) if v)
            out.notes.append(f"arXiv lists a published version: {published}. Consider citing it.")

    if ref.source == "pdf":
        # There's no bib to fix, so a difference is something for you to look at.
        for name, value in corrections.items():
            out.reasons.append(f"{name}: the PDF has \"{_pretty(ref.get(name))}\", "
                               f"the registry has \"{_pretty(value)}\".")
        out.status = CHECK if out.reasons else OK
        return out

    required = _REQUIRED.get(cfg.auto_correct)
    if corrections and (required is None or confidence < required):
        out.reasons.append(f"{_DIFFER} The match's confidence ({result['confidence']}) is below what "
                           f"auto_correct ({cfg.auto_correct}) needs. A one-year gap is often the "
                           "online versus print date.")
        for name, value in corrections.items():
            out.reasons.append(f"{name}: you have \"{_pretty(ref.get(name))}\", "
                               f"the registry has \"{_pretty(value)}\".")
        return out

    out.corrections = corrections
    out.status = CORRECTED if corrections else OK
    return out


# --- Bib and PDF ------------------------------------------------------------------

@dataclass
class CrossCheck:
    pdf_only: list = field(default_factory=list)      # PDF references with no bib entry
    bib_only: list = field(default_factory=list)      # bib keys not in the PDF
    differences: list = field(default_factory=list)   # "key vs [n]: what differs"
    pairs: dict = field(default_factory=dict)         # PDF label -> bib key


def _title_key(ref: bib.Reference) -> str:
    return bib.fold(bib.plain(ref.get("title")))


def _same_id(ref, printed) -> bool:
    doi, arxiv = bib.doi_of(ref).lower(), bib.arxiv_of(ref).lower()
    return bool(doi and doi == bib.doi_of(printed).lower()) \
        or bool(arxiv and arxiv == bib.arxiv_of(printed).lower())


def _year_printed(ref, printed) -> bool:
    return bool(bib.year_of(ref)) and str(bib.year_of(ref)) in printed.raw


def _words_printed(ref, printed) -> bool:
    """Every title word is printed, for text with something stray inside the title."""
    words = re.findall(r"[a-z0-9]+", bib.plain(ref.get("title")).lower())
    printed_words = set(re.findall(r"[a-z0-9]+", printed.raw.lower()))
    return len(words) >= 3 and all(w in printed_words for w in words)


def _fuzzy(ref, printed) -> bool:
    ours, theirs = _title_key(ref), _title_key(printed)
    return bool(ours and theirs) and difflib.SequenceMatcher(None, ours, theirs).ratio() > 0.9


# Strictest first, across all entries, so a short title like "Full Duplex Radios" can't
# take the entry of a longer title that merely contains it.
_MATCH_STAGES = [
    _same_id,
    lambda r, p: bool(_title_key(r)) and _title_key(r) == _title_key(p),
    lambda r, p: len(_title_key(r)) >= 6 and _title_key(r) in bib.fold(p.raw) and _year_printed(r, p),
    lambda r, p: len(_title_key(r)) >= 12 and _title_key(r) in bib.fold(p.raw),
    lambda r, p: _words_printed(r, p) and _year_printed(r, p),
    _fuzzy,
]


def cross_check(bib_refs: list, pdf_refs: list) -> CrossCheck:
    result, used, matches = CrossCheck(), set(), {}
    for stage in _MATCH_STAGES:
        for ref in bib_refs:
            if ref.key in matches:
                continue
            for printed in pdf_refs:
                if printed.key not in used and stage(ref, printed):
                    matches[ref.key] = printed
                    used.add(printed.key)
                    break
    for ref in bib_refs:
        match = matches.get(ref.key)
        if match is None:
            result.bib_only.append(ref.key)
            continue
        result.pairs[match.key] = ref.key
        year = bib.year_of(ref)
        if year and str(year) not in match.raw:
            result.differences.append(f"{ref.key} and {match.key}: the bib has year {year}, "
                                      f"which isn't in the PDF's text: \"{match.raw}\"")
        title = _title_key(ref)
        if title and title not in bib.fold(match.raw):
            result.differences.append(f"{ref.key} and {match.key}: the titles differ. Bib: "
                                      f"\"{bib.plain(ref.get('title'))}\". PDF: \"{match.raw}\"")
    result.pdf_only = [r for r in pdf_refs if r.key not in used]
    return result
