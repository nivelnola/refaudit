"""Read BibTeX, normalize references, and write a corrected copy with minimal edits."""
from __future__ import annotations

import functools
import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from pylatexenc.latex2text import LatexNodes2Text

_LATEX = LatexNodes2Text(math_mode="verbatim", keep_comments=False)
_UNACCENTED = str.maketrans({"ı": "i", "ø": "o", "ł": "l", "đ": "d", "ð": "d", "þ": "th",
                              "æ": "ae", "œ": "oe"})
_ARXIV_ID = r"(\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})"

# Fields BibTeX and biblatex know. Used to tell a hidden field (OPTdoi) from a real one
# that merely starts the same way (options), and to spot misspelled field names.
KNOWN_FIELDS = {
    "address", "annote", "author", "booktitle", "chapter", "crossref", "edition", "editor",
    "howpublished", "institution", "journal", "key", "month", "note", "number", "organization",
    "pages", "publisher", "school", "series", "title", "type", "volume", "year",
    # Common extensions and biblatex.
    "abstract", "addendum", "annotation", "archiveprefix", "arxiv", "articleno", "bookauthor",
    "booksubtitle", "date", "doi", "eid", "eprint", "eprintclass", "eprinttype", "eventdate",
    "eventtitle", "file", "isbn", "issn", "issue", "issuetitle", "journaltitle", "keywords",
    "language", "location", "mendeley-groups", "mrnumber", "numpages", "origdate", "pagetotal",
    "primaryclass", "pubstate", "shortjournal", "shorttitle", "subtitle", "timestamp",
    "titleaddon", "url", "urldate", "venue", "version", "biburl", "bibsource", "langid",
    "options", "ids", "related", "relatedtype", "entrysubtype", "nationality", "pmid", "pmcid",
    "copyright", "publisher-place", "acmid", "lccn", "zmnumber", "day", "maintitle", "part",
}


class BibError(Exception):
    """A problem in the bib file, with a message written for the user."""


@dataclass
class FieldSpan:
    start: int       # start of the field name
    val_start: int
    val_end: int
    end: int         # after the trailing comma, if any
    has_comma: bool


@dataclass
class Reference:
    key: str
    etype: str = "misc"
    fields: dict = field(default_factory=dict)  # lowercase name -> LaTeX value
    source: str = "bib"                         # bib | pdf
    raw: str = ""                               # the reference as printed (PDF only)
    spans: dict = field(default_factory=dict)
    close: int = 0                              # offset of the entry's closing brace
    parse_ok: bool = True                       # False: PDF text we couldn't split up
    hidden: dict = field(default_factory=dict)  # field -> value, for fields renamed so BibTeX skips them
    hidden_names: dict = field(default_factory=dict)  # field -> its name as written in the file

    def get(self, name: str) -> str:
        return self.fields.get(name, "")

    def any(self, name: str) -> str:
        """The field's value, even if it's hidden. Hidden fields still identify the work."""
        return self.fields.get(name) or self.hidden.get(name, "")


@dataclass(frozen=True)
class Rename:
    """A change that renames a field and leaves its value as it is."""
    to: str


def hidden_base(name: str, prefix: str | None) -> str | None:
    """The field a hidden name stands for: "optdoi" -> "doi". None if it isn't hidden."""
    if not prefix:
        return None
    prefix = prefix.lower()
    if name.startswith(prefix) and name[len(prefix):] in KNOWN_FIELDS and name not in KNOWN_FIELDS:
        return name[len(prefix):]
    return None


def read_text(path: Path) -> str:
    """Read a file without translating line endings."""
    return read_with_encoding(path)[0]


def read_with_encoding(path: Path) -> tuple[str, str]:
    """The text, and the encoding to write it back in: utf-8, utf-8-sig (with a BOM) or latin-1."""
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig"), "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1"


# --- Parsing ----------------------------------------------------------------------

def _match_close(text: str, start: int, opener: str) -> int:
    """Index of the delimiter closing the one at `start`, or -1."""
    closer = "}" if opener == "{" else ")"
    depth, i = 0, start
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == opener:
            depth += 1
        elif c == closer:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _skip_blank(text: str, i: int, end: int) -> int:
    """Skip whitespace and % comment lines."""
    while i < end:
        if text[i].isspace():
            i += 1
        elif text[i] == "%":
            newline = text.find("\n", i, end)
            i = end if newline == -1 else newline + 1
        else:
            break
    return i


def _read_value(text: str, i: int, end: int, macros: dict) -> tuple[str, int]:
    """Read a value expression (pieces joined by #). Returns its text and end offset."""
    parts, last = [], i
    while i < end:
        c = text[i]
        if c == "{":
            close = _match_close(text, i, "{")
            if close == -1 or close >= end:
                raise ValueError("unbalanced braces")
            parts.append(text[i + 1:close])
            i = close + 1
        elif c == '"':
            j, depth = i + 1, 0
            while j < end and not (text[j] == '"' and depth == 0 and text[j - 1] != "\\"):
                depth += {"{": 1, "}": -1}.get(text[j], 0)
                j += 1
            if j >= end:
                raise ValueError("unbalanced quotes")
            parts.append(text[i + 1:j])
            i = j + 1
        else:
            m = re.compile(r"[^\s,#{}\"]+").match(text, i, end)
            if not m:
                raise ValueError("missing value")
            word = m.group()
            parts.append(macros.get(word.lower(), word))
            i = m.end()
        last = i
        i = _skip_blank(text, i, end)
        if i < end and text[i] == "#":
            i = _skip_blank(text, i + 1, end)
        else:
            break
    return "".join(parts), last


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def parse_bib(text: str, hidden_prefix: str | None = "OPT", unreadable: list | None = None) -> list[Reference]:
    """Parse entries, remembering each field's position so edits can be minimal.

    Fields named with `hidden_prefix`, like OPTdoi, are kept apart in `hidden`. If
    `unreadable` is a list, an entry that can't be read is skipped and added to it as
    (key, line, why), instead of stopping. A missing closing brace always stops, since
    nothing after it can be read reliably.
    """
    refs, macros, pos = [], {}, 0
    entry_start = re.compile(r"@\s*(\w+)\s*([{(])")
    while True:
        m = entry_start.search(text, pos)
        if not m:
            return refs
        line_start = text.rfind("\n", 0, m.start()) + 1
        if "%" in text[line_start:m.start()]:  # commented out
            pos = m.end()
            continue
        close = _match_close(text, m.end() - 1, m.group(2))
        if close == -1:
            raise BibError(f"Line {_line_number(text, m.start())}: an entry is missing its closing "
                           "brace, so the bib file can't be read. Fix it and run again.")
        pos = close + 1
        kind = m.group(1).lower()
        if kind in ("comment", "preamble"):
            continue
        body_start, body_end = m.end(), close
        try:
            if kind == "string":
                name, sep, _ = text[body_start:body_end].partition("=")
                if sep:
                    start = _skip_blank(text, body_start + len(name) + 1, body_end)
                    macros[name.strip().lower()] = _read_value(text, start, body_end, macros)[0]
                continue
            ref = _parse_entry(text, kind, body_start, body_end, macros)
            for name in list(ref.fields):
                base = hidden_base(name, hidden_prefix)
                if base and base not in ref.hidden:
                    ref.hidden[base] = ref.fields.pop(name)
                    start = ref.spans[name].start
                    ref.hidden_names[base] = text[start:start + len(name)]  # as written: OPTdoi
            refs.append(ref)
        except ValueError as err:
            if unreadable is None:
                raise BibError(f"Line {_line_number(text, m.start())}: couldn't read this entry "
                               f"({err}). Fix it and run again.") from err
            # The entry's end is known, so the rest of the file can still be read.
            key = text[body_start:body_end].split(",", 1)[0].strip()[:60]
            unreadable.append((key, _line_number(text, m.start()), str(err)))


def _parse_entry(text: str, kind: str, start: int, end: int, macros: dict) -> Reference:
    comma = text.find(",", start, end)
    key_end = end if comma == -1 else comma
    ref = Reference(key=text[start:key_end].strip(), etype=kind, close=end)
    i = key_end + 1
    name_re = re.compile(r"([^\s=,{}\"#%]+)\s*=\s*")
    while True:
        i = _skip_blank(text, i, end)
        if i >= end:
            return ref
        m = name_re.match(text, i, end)
        if not m:
            near = " ".join(text[i:min(i + 20, end)].split())
            raise ValueError(f"unexpected text after a field, near \"{near}\"")
        value, val_end = _read_value(text, m.end(), end, macros)
        after = _skip_blank(text, val_end, end)
        has_comma = after < end and text[after] == ","
        name = m.group(1).lower()
        ref.fields[name] = value
        ref.spans[name] = FieldSpan(i, m.end(), val_end, after + 1 if has_comma else val_end, has_comma)
        i = after + 1 if has_comma else after


# --- Normalizing ------------------------------------------------------------------

@functools.lru_cache(maxsize=65536)
def plain(latex: str) -> str:
    """LaTeX to readable text. Cached: converting is slow, and comparing every pair of
    entries for duplicates asks for the same titles and names over and over."""
    if not latex:
        return ""
    escaped = latex.replace("%", r"\%")
    try:
        text = _LATEX.latex_to_text(escaped)
    except Exception:  # odd LaTeX shouldn't stop an audit
        text = latex.replace("{", "").replace("}", "")
    return re.sub(r"\s+", " ", text).strip()


def fold(text: str) -> str:
    """Lowercase letters and digits only, with accents removed. Used for comparing."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.casefold().translate(_UNACCENTED)  # letters with no separable accent
    return re.sub(r"[^a-z0-9]", "", text)


def same_title(ours: str, theirs: str) -> bool:
    """Same work's title, allowing a 'Demo:' label or a subtitle only one side has."""
    ours = re.sub(r"^\s*(demo|poster|invited|abstract|short paper)\s*:\s*", "", plain(ours), flags=re.I)
    theirs = re.sub(r"^\s*(demo|poster|invited|abstract|short paper)\s*(abstract)?\s*:\s*", "",
                    plain(theirs), flags=re.I)
    a, b = fold(ours), fold(theirs)
    if not a or not b:
        return False
    if a == b:
        return True
    return (":" in ours and fold(ours.split(":")[0]) == b and len(b) >= 12) \
        or (":" in theirs and fold(theirs.split(":")[0]) == a and len(a) >= 12)


def split_authors(value: str) -> list[str]:
    """Split on ' and ' at brace depth zero. Returns raw LaTeX names."""
    names, depth, start, i = [], 0, 0, 0
    while i < len(value):
        c = value[i]
        depth += {"{": 1, "}": -1}.get(c, 0)
        if depth == 0 and value[i:i + 5].lower() == " and ":
            names.append(value[start:i])
            start = i = i + 5
            continue
        if depth == 0 and c.isspace():
            m = re.compile(r"\s+and\s+", re.I).match(value, i)
            if m:
                names.append(value[start:i])
                start = i = m.end()
                continue
        i += 1
    names.append(value[start:])
    return [n.strip() for n in names if n.strip()]


def _split_words(name: str) -> list[str]:
    words, depth, cur = [], 0, ""
    for c in name:
        depth += {"{": 1, "}": -1}.get(c, 0)
        if c.isspace() and depth == 0:
            if cur:
                words.append(cur)
            cur = ""
        else:
            cur += c
    return words + ([cur] if cur else [])


def split_name(raw: str) -> tuple[str, str]:
    """(family, given) as raw LaTeX. A fully braced name is an organization: all family."""
    raw = raw.strip()
    if raw.startswith("{") and _match_close(raw, 0, "{") == len(raw) - 1:
        return raw, ""
    depth, parts, cur = 0, [], ""
    for c in raw:
        depth += {"{": 1, "}": -1}.get(c, 0)
        if c == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += c
    parts.append(cur)
    if len(parts) > 1:
        return parts[0].strip(), parts[-1].strip()
    words = _split_words(raw)
    if len(words) < 2:
        return raw, ""
    if len(words) > 2 and words[-1].rstrip(".") in ("Jr", "Sr", "II", "III"):
        words = words[:-1]
    i = len(words) - 1  # keep particles (van der, de) with the family name
    while i > 0 and words[i - 1].islower():
        i -= 1
    return " ".join(words[i:]), " ".join(words[:i])


# The default for organization_words in format.yaml.
ORG_WORDS = {
    "university", "universität", "université", "universidad", "institute", "institut", "college",
    "school", "laboratory", "laboratories", "lab", "labs", "inc", "corp", "corporation", "company",
    "ltd", "llc", "gmbh", "project", "consortium", "group", "alliance", "foundation", "association",
    "society", "committee", "agency", "center", "centre", "council", "organization", "organisation",
    "forum", "team", "department", "ministry", "office", "initiative", "research", "technologies",
    "instruments", "systems", "radio", "partnership", "commission", "administration", "authority",
}


def looks_like_organization(raw_name: str, words: set | list | None = None) -> bool:
    """An unbraced author that's an organization: "Columbia University", "Ettus Research".
    BibTeX reads it as a person ("C. University"), so it belongs in braces. `words` are the
    words that mark one (organization_words in format.yaml)."""
    marks = {w.lower() for w in (ORG_WORDS if words is None else words)}
    name = raw_name.strip()
    if not name or "," in name or (name.startswith("{") and _match_close(name, 0, "{") == len(name) - 1):
        return False
    found = [w.strip(".").lower() for w in plain(name).split()]
    return len(found) > 1 and any(w in marks for w in found)


def authors_of(ref: Reference) -> tuple[list[tuple[str, str]], bool]:
    """Plain (family, given) pairs, and whether the list ends in 'and others'."""
    names = split_authors(ref.get("author"))
    truncated = bool(names) and plain(names[-1]).lower() in ("others", "et al.", "et al")
    if truncated:
        names = names[:-1]
    pairs = []
    for name in names:
        family, given = split_name(name)
        pairs.append((plain(family), plain(given)))
    return pairs, truncated


def clean_doi(value: str) -> str:
    value = plain(value).strip().rstrip(".,;")
    value = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", value, flags=re.I)
    return re.sub(r"^doi:\s*", "", value, flags=re.I)


def doi_of(ref: Reference) -> str:
    doi = clean_doi(ref.any("doi"))
    if doi:
        return doi
    m = re.search(r"doi\.org/(10\.\d{4,9}/\S+)", ref.any("url"), re.I)
    return clean_doi(m.group(1)) if m else ""


def arxiv_of(ref: Reference) -> str:
    """arXiv ID from eprint fields, a journal note like 'arXiv:2101.00001', or a URL."""
    prefix = ref.any("archiveprefix").lower()
    if ref.any("arxiv"):
        return ref.any("arxiv").strip()
    eprint = ref.any("eprint").strip()
    if eprint and (prefix == "arxiv" or re.fullmatch(_ARXIV_ID + r"(v\d+)?", eprint)):
        return re.sub(r"^arxiv:", "", eprint, flags=re.I)
    for name in ("journal", "note", "howpublished", "url", "publisher"):
        m = re.search(r"arxiv(?:\.org/(?:abs|pdf)/|[:\s]\s*(?:preprint\s+)?(?:arxiv:)?)" + _ARXIV_ID,
                      ref.any(name), re.I)
        if m:
            return m.group(1)
    return ""


def url_of(ref: Reference, hidden: bool = True) -> str:
    """A web link from the url field, howpublished or note, or the printed text of a PDF entry.

    With `hidden` false, only links the bibliography will actually print count.
    """
    for name in ("url", "howpublished", "note"):
        m = re.search(r"https?://[^\s{}]+", ref.any(name) if hidden else ref.get(name))
        if m:
            return _clean_url(m.group().replace("\\", ""))
    if ref.source == "pdf":
        m = re.search(r"https?://[^\s{}]+", ref.raw)
        if m:
            return _clean_url(m.group())
    return ""


def _clean_url(url: str) -> str:
    """Drop punctuation after a link, and what a PDF printed right after it with no space:
    a year ("…8888884,2019") or an identifier ("…/abs/2103.00020arXiv:2103.00020")."""
    url = re.sub(r"(?i)(?<=[\w/])(arxiv|doi):.*$", "", url)
    return re.sub(r",(?:19|20)\d\d$", "", url.rstrip(".,;")).rstrip(".,;")


def is_doi_link(url: str) -> bool:
    """A link that only resolves a DOI. Checking it says nothing the DOI check doesn't."""
    return bool(re.match(r"https?://(dx\.)?doi\.org/", url, re.I))


def year_of(ref: Reference) -> int | None:
    m = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", plain(ref.get("year")) or plain(ref.get("date")))
    return int(m.group(1)) if m else None


def venue_field(ref: Reference) -> str | None:
    """The field holding this reference's venue, if it has one."""
    for name in ("booktitle", "journal", "journaltitle"):
        if ref.get(name):
            return name
    return None


def is_arxiv_venue(value: str) -> bool:
    return bool(re.search(r"arxiv|^corr$", plain(value), re.I))


def type_of(ref: Reference) -> str:
    etype = ref.etype
    real_venue = any(ref.get(n) and not is_arxiv_venue(ref.get(n))
                     for n in ("booktitle", "journal", "journaltitle"))
    if arxiv_of(ref) and not real_venue:
        return "preprint"
    if etype in ("inproceedings", "conference"):
        return "conference"
    if etype == "article":
        return "journal"
    if etype in ("book", "inbook", "incollection"):
        return "book"
    if etype in ("techreport", "manual", "standard"):
        return "techreport"
    return "other"


def _pages_key(value: str) -> str:
    return re.sub(r"[\s–—-]+", "-", plain(value)).strip("-").lower()


def fingerprint(ref: Reference) -> str:
    """Identity hash. Ignores capitalization, LaTeX, cite key, field order and venue naming.

    Hidden fields count as if they were shown, so hiding a field never costs a re-check.
    """
    if ref.hidden:
        ref = Reference(key=ref.key, etype=ref.etype, fields={**ref.hidden, **ref.fields},
                        source=ref.source, raw=ref.raw, parse_ok=ref.parse_ok)
    authors, _ = authors_of(ref)
    parts = [
        fold(plain(ref.get("title"))),
        "|".join(fold(family) for family, _ in authors),
        str(year_of(ref) or ""),
        fold(plain(ref.get("volume"))),
        fold(plain(ref.get("number"))),
        _pages_key(ref.get("pages")),
        doi_of(ref).lower(),
        arxiv_of(ref).lower(),
        "" if ref.parse_ok else fold(ref.raw),
    ]
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]


# --- Writing -----------------------------------------------------------------------

def to_latex(text: str) -> str:
    """Escape characters that break LaTeX in a value taken from a registry."""
    return re.sub(r"(?<!\\)([&%#_])", r"\\\1", text)


def _rename_edit(text: str, span: FieldSpan, new_name: str) -> tuple[int, int, str]:
    """Rename a field, keeping the column its "=" sits in when there's room."""
    equals = text.index("=", span.start, span.val_start)
    label = text[span.start:equals]
    name = label.rstrip()
    if len(label) - len(name) <= 1:  # "doi =" or "doi=": not aligned, so keep the same spacing
        return span.start, span.start + len(name), new_name
    # Aligned "doi     =": keep the "=" in its column if the new name fits.
    padded = new_name.ljust(len(label)) if len(new_name) < len(label) else new_name + " "
    return span.start, equals, padded


def _field_edits(text: str, ref: Reference, changes: dict) -> list[tuple[int, int, str]]:
    """Text edits for one entry. `changes` maps field -> new value, None to remove, or a
    Rename. A field is named as it is in the file, so a hidden one is "optdoi"."""
    edits = []
    additions = {}
    for name, value in changes.items():
        span = ref.spans.get(name)
        if isinstance(value, Rename):
            if span is not None:
                edits.append(_rename_edit(text, span, value.to))
            continue
        if span is None:
            if value is not None:
                additions[name] = value
        elif value is None:
            line_start = text.rfind("\n", 0, span.start) + 1
            start = line_start if not text[line_start:span.start].strip() else span.start
            end = span.end
            m = re.compile(r"[ \t]*\r?\n").match(text, end)
            if m and start == line_start:
                end = m.end()
            edits.append((start, end, ""))
        else:
            edits.append((span.val_start, span.val_end, "{" + value + "}"))

    if additions:
        kept = [s for n, s in ref.spans.items() if changes.get(n, "keep") is not None
                and n not in {h.lower() for h in ref.hidden_names.values()}]
        newline = "\r\n" if "\r\n" in text else "\n"
        if kept:
            anchor = max(kept, key=lambda s: s.end)
            line_start = text.rfind("\n", 0, anchor.start) + 1
            indent = text[line_start:anchor.start] if not text[line_start:anchor.start].strip() else "  "
            width = anchor.val_start - anchor.start
            has_comma = anchor.has_comma
            pos = anchor.end
        else:
            indent, width, has_comma = "  ", 0, True
            comma = text.find(",", 0, ref.close)
            head = text.rfind("@", 0, ref.close)
            comma = text.find(",", head, ref.close)
            pos = comma + 1 if comma != -1 else ref.close
        inserted = "" if has_comma else ","
        for name, value in additions.items():
            label = f"{name} = " if not width else f"{name} ".ljust(max(width - 2, len(name) + 1)) + "= "
            inserted += f"{newline}{indent}{label}{{{value}}},"
        edits.append((pos, pos, inserted))
    return edits


def write_corrected(text: str, changes_by_ref: list[tuple[Reference, dict]]) -> str:
    """Apply changes to the original text. Everything else stays byte-identical."""
    edits = []
    for ref, changes in changes_by_ref:
        if changes:
            edits.extend(_field_edits(text, ref, changes))
    for start, end, new in sorted(edits, key=lambda e: e[0], reverse=True):
        text = text[:start] + new + text[end:]
    return text
