"""Formatting rules: capitalization, venue names, authors, and field removal."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from . import bib
from .config import TOOL_DIR, ConfigError

_SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "en", "for", "from", "if", "in", "into",
                "nor", "of", "off", "on", "onto", "or", "over", "per", "the", "to", "up", "via",
                "vs", "with"}
_EXPANSIONS = {"proc": "proceedings", "int": "international", "intl": "international",
               "conf": "conference", "symp": "symposium", "trans": "transactions",
               "annu": "annual", "assoc": "association", "comput": "computer"}


# --- Capitalization ----------------------------------------------------------------

def _tokens(title: str) -> list[str]:
    """Split into words and separators, keeping {braced groups} and \\commands whole."""
    tokens, cur, depth = [], "", 0
    for c in title:
        depth += {"{": 1, "}": -1}.get(c, 0)
        if depth == 0 and c.isspace():
            if cur:
                tokens.append(cur)
                cur = ""
            tokens.append(c)
        else:
            cur += c
    return tokens + ([cur] if cur else [])


def _is_protected(word: str) -> bool:
    if "{" in word or "\\" in word or "$" in word:
        return True
    letters = re.sub(r"[^A-Za-z]", "", word)
    if not letters:
        return True
    # ALLCAPS, ResNet, mmWave: any capital after the first letter.
    if any(c.isupper() for c in letters[1:]):
        return True
    # A lone capital is a name or label, as in N-Path or Part I. "A" is just the article.
    if letters.isupper() and letters != "A":
        return True
    # 5G, 3D: a capital mixed with a digit.
    return bool(re.search(r"\d", word) and any(c.isupper() for c in letters))


def _case_word(word: str, capitalize: bool) -> str:
    """Set one word's first letter, leaving protected words alone."""
    if _is_protected(word):
        return word
    m = re.search(r"[^\W\d_]", word)
    if not m:
        return word
    letter = m.group().upper() if capitalize else m.group().lower()
    return word[:m.start()] + letter + word[m.end():]


def _split_hyphens(token: str) -> list[str]:
    """Split on hyphens outside braces."""
    parts, cur, depth = [], "", 0
    for c in token:
        depth += {"{": 1, "}": -1}.get(c, 0)
        if c == "-" and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += c
    return parts + [cur]


def _starts_clause(previous: str) -> bool:
    return previous.rstrip().endswith((":", "?", "!", ".", "—"))


def _unwrap(title: str) -> tuple[str, int]:
    """Remove braces around a whole title, as in {{My Title}}. Returns the title and how many."""
    count = 0
    while title.startswith("{") and bib._match_close(title, 0, "{") == len(title) - 1:
        title = title[1:-1]
        count += 1
    return title, count


def _restore_words(title: str, keep_words: list) -> str:
    """Put keep_capitalized words and phrases back in their given form."""
    for phrase in sorted(keep_words, key=len, reverse=True):
        pattern = r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"
        title = re.sub(pattern, lambda _, p=phrase: p, title, flags=re.I)
    return title


# --- Capitals a bibliography style may lowercase ----------------------------------

# A word with a capital after its first character: MIMO, mmWave, 5G. "Bayesian" isn't one.
_MIXED_CASE = re.compile(r"(?<![A-Za-z0-9\\])[A-Za-z0-9][A-Za-z0-9]*?[A-Z][A-Za-z0-9]*(?![A-Za-z0-9])")


def _plain_runs(text: str) -> list[tuple[int, int]]:
    """Stretches a style can recase: outside braces, $math$ and \\command names."""
    runs, depth, math, start, i = [], 0, False, 0, 0
    while i < len(text):
        c = text[i]
        free = depth == 0 and not math
        if c == "\\" and free:
            runs.append((start, i))
            m = re.compile(r"\\([A-Za-z]+|.)").match(text, i)
            i = start = m.end() if m else i + 1
            continue
        if c == "$" and depth == 0:
            if free:
                runs.append((start, i))
            math = not math
            start = i + 1
        elif c == "{" and not math:
            if depth == 0:
                runs.append((start, i))
            depth += 1
        elif c == "}" and not math and depth > 0:
            depth -= 1
            if depth == 0:
                start = i + 1
        i += 1
    if depth == 0 and not math:
        runs.append((start, len(text)))
    return [(a, b) for a, b in runs if b > a]


def _phrase_pattern(phrase: str) -> re.Pattern:
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(phrase) + r"(?![A-Za-z0-9])")


def _whole_title_braced(title: str) -> bool:
    title = title.strip()
    return title.startswith("{") and bib._match_close(title, 0, "{") == len(title) - 1


def _ignored(word: str, ignore) -> bool:
    """In capitals_ignore, alone or after a number: "dB", "54dB", "2.4GHz" (seen as "4GHz")."""
    return word in ignore or re.sub(r"^\d+", "", word) in ignore


def at_risk(title: str, keep_words: list, ignore=()) -> list[str]:
    """Words and phrases in a title that a style which recases titles would lowercase:
    unbraced ALLCAPS or mixed-case words (MIMO, mmWave), and keep_capitalized phrases.
    Words in `ignore` (capitals_ignore in format.yaml, such as units like dB) are left out."""
    if not title or _whole_title_braced(title):
        return []
    found = []
    for a, b in _plain_runs(title):
        segment = title[a:b]
        for phrase in sorted(keep_words, key=len, reverse=True):
            pattern = _phrase_pattern(phrase)
            if pattern.search(segment):
                found.append(phrase)
                segment = pattern.sub(lambda m: " " * len(m.group()), segment)
        found.extend(m.group() for m in _MIXED_CASE.finditer(segment) if not _ignored(m.group(), ignore))
    return list(dict.fromkeys(found))


def protect_capitals(title: str, keep_words: list, ignore=()) -> str:
    """Brace everything at_risk finds, so the style prints it as written: {MIMO}, {GNU Radio}."""
    if not title or _whole_title_braced(title):
        return title

    def each_run(text: str, change) -> str:
        out, last = [], 0
        for a, b in _plain_runs(text):
            out += [text[last:a], change(text[a:b])]
            last = b
        return "".join(out + [text[last:]])

    for phrase in sorted(keep_words, key=len, reverse=True):
        pattern = _phrase_pattern(phrase)
        title = each_run(title, lambda s, p=pattern: p.sub(lambda m: "{" + m.group() + "}", s))
    return each_run(title, lambda s: _MIXED_CASE.sub(
        lambda m: m.group() if _ignored(m.group(), ignore) else "{" + m.group() + "}", s))


def to_sentence_case(title: str, keep_words: list) -> str:
    inner, wraps = _unwrap(title)
    out, first, previous = [], True, ""
    for token in _tokens(inner):
        if token.isspace():
            out.append(token)
            continue
        capitalize = first or _starts_clause(previous)
        parts = _split_hyphens(token)
        cased = [_case_word(parts[0], capitalize)] + [_case_word(p, False) for p in parts[1:]]
        out.append("-".join(cased))
        first, previous = False, token
    return "{" * wraps + _restore_words("".join(out), keep_words) + "}" * wraps


def to_title_case(title: str, keep_words: list) -> str:
    inner, wraps = _unwrap(title)
    tokens = _tokens(inner)
    word_positions = [i for i, t in enumerate(tokens) if not t.isspace()]
    out, previous = [], ""
    for i, token in enumerate(tokens):
        if token.isspace():
            out.append(token)
            continue
        edge = i in (word_positions[0], word_positions[-1]) or _starts_clause(previous)
        small = re.sub(r"[^\w]", "", token).lower() in _SMALL_WORDS
        cased = []
        for j, part in enumerate(_split_hyphens(token)):
            cased.append(_case_word(part, edge or not small or j > 0))
        out.append("-".join(cased))
        previous = token
    return "{" * wraps + _restore_words("".join(out), keep_words) + "}" * wraps


# --- Authors -----------------------------------------------------------------------

def _initials(given: str) -> str:
    """'Jean-Luc' and 'J.-L.' both give 'J.-L.'; 'A.M.' gives 'A. M.'. Running it twice changes nothing."""
    words = []
    for word in bib._split_words(given):
        initials = ""
        for m in re.finditer(r"(-?)([^.\-\s]+)", bib.plain(word)):
            hyphen, part = m.groups()
            initials += ("-" if hyphen and initials else (" " if initials else "")) + part[0].upper() + "."
        if initials:
            words.append(initials)
    return " ".join(words)


def authors_to_initials(value: str, org_words=None) -> str:
    names = []
    for raw in bib.split_authors(value):
        family, given = bib.split_name(raw)
        if bib.plain(raw).lower() in ("others", "et al.", "et al") or bib.looks_like_organization(raw, org_words):
            names.append(raw)  # an organization has no initials; lint suggests braces for it
        elif given:
            names.append(f"{family}, {_initials(given)}")
        else:
            names.append(raw)
    return " and ".join(names)


# --- Venues ------------------------------------------------------------------------

def _venue_key(name: str) -> str:
    """Normalize a venue name: no case, years, ordinals, parentheses or 'Proceedings of the'."""
    text = bib.plain(name).lower()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"\b(19|20)\d\d\b|['\u2019]\d\d\b", " ", text)
    text = re.sub(r"\b\d+(st|nd|rd|th)\b", " ", text)
    text = text.replace("&", " and ")
    words = [_EXPANSIONS.get(w, w) for w in re.findall(r"[a-z0-9+]+", text)]
    while words and words[0] in ("proceedings", "of", "the", "in"):
        words.pop(0)
    while len(words) > 1 and words[-1] == "proceedings":
        words.pop()
    return " ".join(words)


def _load_venue_file(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as err:
        raise ConfigError(f"Couldn't read venue file {path}: {err}") from err
    if not isinstance(data, dict):
        raise ConfigError(f'Venue file {path} must have lines like "Full Name": Abbreviation.')
    return {str(k): str(v) for k, v in data.items()}


class _VenueTable:
    """Names to (full name, abbreviation), from one or more files. Earlier files win."""

    def __init__(self, files: list[Path]):
        self.names: dict[str, list[str]] = {}  # abbreviation -> every name listed for it
        self.by_key: dict[str, tuple[str, str]] = {}
        self.full_of: dict[str, str] = {}
        self.abbreviation_keys: set[str] = set()
        for path in files:
            for full, abbrev in _load_venue_file(path).items():
                self.full_of.setdefault(abbrev, full)
                self.names.setdefault(abbrev, []).append(full)
                entry = (self.full_of[abbrev], abbrev)
                key, abbrev_key = _venue_key(full), _venue_key(abbrev)
                if key:
                    self.by_key.setdefault(key, entry)
                if abbrev_key:
                    self.by_key.setdefault(abbrev_key, entry)
                    self.abbreviation_keys.add(abbrev_key)

    def find(self, name: str) -> tuple[str, str] | None:
        text = bib.plain(name)
        key = _venue_key(text)
        found = self.by_key.get(key)
        if found:
            return found
        # "Proc. ACM MobiCom'12": a publisher followed by an abbreviation.
        if re.match(r"proc", text, re.I):
            short = re.sub(r"^((ieee|acm|usenix|acm/ieee|ieee/acm|ieee/cvf|sigcomm)\s+)+", "", key)
            if short in self.abbreviation_keys:
                return self.by_key[short]
        # "Some Long Name (CVPR)": fall back on the abbreviation in parentheses.
        for inner in re.findall(r"\(([^)]+)\)", text):
            found = self.by_key.get(_venue_key(inner))
            if found:
                return found
        return None


class Venues:
    """Venue lookups. Everything in the user's abbreviations_file beats the built-in lists."""

    def __init__(self, builtin_files: list[Path], custom_file: Path | None = None):
        self.custom = _VenueTable([custom_file] if custom_file else [])
        self.builtin = _VenueTable(builtin_files)
        # If the user redefines one name of a venue, its other built-in names follow.
        self.replaced: dict[str, tuple[str, str]] = {}
        for key, (_, abbrev) in self.builtin.by_key.items():
            if key in self.custom.by_key:
                self.replaced.setdefault(abbrev, self.custom.by_key[key])

    def find(self, name: str) -> tuple[str, str] | None:
        found = self.custom.find(name)
        if found:
            return found
        found = self.builtin.find(name)
        return self.replaced.get(found[1], found) if found else None

    def override(self, name: str, mode: str) -> tuple[str, str] | None:
        """(built-in value, your value) if abbreviations_file changed the result for this venue."""
        ours, theirs = self.find(name), self.builtin.find(name)
        if not ours or not theirs:
            return None
        index = 1 if mode == "abbreviated" else 0
        return (theirs[index], ours[index]) if ours[index] != theirs[index] else None

    def sponsor_of(self, name: str) -> str:
        """The sponsor that starts any listed name of this venue, or "" if none does."""
        names = []
        found = self.custom.find(name)
        if found:
            names += [found[1], *self.custom.names.get(found[1], [])]
        found = self.builtin.find(name)
        if found:
            names += self.builtin.names.get(found[1], [])
        for listed in names:
            m = _SPONSOR.match(re.sub(r"^The\s+", "", listed))
            if m:
                return m.group(1)
        return ""

    def same(self, a: str, b: str) -> bool:
        found_a, found_b = self.find(a), self.find(b)
        return bool(found_a and found_b and found_a[1] == found_b[1])


# "~" is LaTeX's non-breaking space, so it separates words like a space: "Proc.~IEEE ICT".
_PROC = re.compile(r"^(proc\.?|proceedings)([\s~]+of)?([\s~]+the)?[\s~]+", re.I)
_SPONSOR = re.compile(r"^((?:IEEE|ACM|USENIX)(?:[/-](?:IEEE|ACM|CVF|RSJ|SIAM|ION))*)[\s~]+")
_YEAR_END = re.compile(r"[\s~]*(['’](\d\d)|\b((?:19|20)\d\d))$")
_YEAR_START = re.compile(r"^((?:19|20)\d\d)[\s~]+")


def _separator(matched: str, word: str) -> str:
    """The space that followed a word as written: "~" if it was LaTeX's non-breaking one."""
    return "~" if "~" in matched[len(word):] else " "


def style_venue(value: str, year: int | None, rules: dict, venues: Venues,
                original: str) -> tuple[str, list[str]]:
    """Apply venue_year, venue_sponsor and venue_prefix to a conference's name.

    Splits the name into prefix, sponsor, name and year, changes the requested parts, and
    joins them again. Nothing is added unless it can be backed up: the year comes from the
    entry, and the sponsor from the venue lists or the name as you wrote it.
    """
    notes = []
    prefix, rest, prefix_sep, sponsor_sep = "", value.strip(), " ", " "
    m = _PROC.match(rest)
    if m:
        prefix, rest = m.group().strip(" ~\t\n"), rest[m.end():]
        prefix_sep = _separator(m.group(), prefix)
    year_text, m = "", _YEAR_END.search(rest)
    if m:
        year_text, rest = m.group(1), rest[:m.start()]
    m = _YEAR_START.match(rest)
    if m and not year_text:
        year_text, rest = m.group(1), rest[m.end():]
    sponsor, core, m = "", rest, _SPONSOR.match(rest)
    # "USENIX Security" is the name itself, since "Security" alone would mean nothing.
    if m and (venues.find(rest[m.end():]) or not venues.find(rest)):
        sponsor, core = m.group(1), rest[m.end():]
        sponsor_sep = _separator(m.group(), sponsor)

    known = venues.find(core)
    short = not re.search(r"[\s~]", core) or bool(known and bib.fold(known[1]) == bib.fold(core))

    if rules["venue_prefix"] == "remove":
        prefix = ""
    elif rules["venue_prefix"] == "add" and short and not prefix:
        prefix = "Proc."

    if rules["venue_sponsor"] == "remove":
        sponsor = ""
    elif rules["venue_sponsor"] == "add" and short:
        listed = venues.sponsor_of(core) or venues.sponsor_of(original)
        given = _SPONSOR.match(_PROC.sub("", original.strip()) + " ")
        if sponsor and listed and sponsor not in listed and listed not in sponsor:
            notes.append(f"The venue lists give {listed} as the sponsor of {core}, but your entry "
                         f"says {sponsor}. Left as written.")
        elif not sponsor and not _SPONSOR.match(core):  # "USENIX Security" already has one
            sponsor = listed or (given.group(1) if given else "")
            if not sponsor:
                notes.append(f"No sponsor is listed for {core}, so none was added.")

    if rules["venue_year"] == "remove":
        year_text = ""
    elif rules["venue_year"] == "add" and short:
        if not year_text:
            # Abbreviating can drop a year you wrote. It still counts, right or wrong.
            written = _PROC.sub("", original.strip())
            m = _YEAR_END.search(written) or _YEAR_START.match(written)
            if m:
                year_text = "'" + re.sub(r"\D", "", m.group(1))[-2:]
        # A written year that disagrees with the entry is left alone. The sanity checks report it.
        if not year:
            notes.append("This entry has no year, so none was added to the venue.")
        elif not year_text:
            year_text = f"'{year % 100:02d}"

    name = ""
    for part, sep in ((prefix, prefix_sep), (sponsor, sponsor_sep), (core, "")):
        if part:
            name += part + sep
    name = name.rstrip(" ~")
    if year_text and not year_text.startswith(("'", "’")):
        return f"{name} {year_text}", notes
    return name + year_text, notes


def load_venues(venue_lists: list, abbreviations_file: Path | None) -> Venues:
    folder = TOOL_DIR / "venues"
    files = []
    for name in venue_lists:
        path = folder / f"{name}.yaml"
        if not path.is_file():
            available = ", ".join(sorted(p.stem for p in folder.glob("*.yaml"))) or "none"
            raise ConfigError(f'venue_lists has "{name}", but there\'s no venues/{name}.yaml. '
                              f"Available: {available}.")
        files.append(path)
    return Venues(files, abbreviations_file)


# --- One reference ------------------------------------------------------------------

def apply_rules(ref: bib.Reference, values: dict, rules: dict, keep_capitalized: list,
                venues: Venues, record: dict | None, capitals_ignore=(),
                org_words=None) -> tuple[dict, list, str | None, tuple | None]:
    """Apply format rules on top of `values` (the entry's fields after any corrections).

    `record` is the verified record, used by "add". Returns the updated values, notes for
    the report, a venue name missing from the venue lists (or None), and how
    abbreviations_file changed the venue (original name, built-in value, your value, or None).
    """
    values, notes, missing, override = dict(values), [], None, None
    original_venue = values.get(bib.venue_field(ref) or "", "")

    title = values.get("title")
    if title and rules["title_case"] == "sentence":
        values["title"] = to_sentence_case(title, keep_capitalized)
    elif title and rules["title_case"] == "title":
        values["title"] = to_title_case(title, keep_capitalized)
    if values.get("title") and rules["protect_capitals"] == "add":
        values["title"] = protect_capitals(values["title"], keep_capitalized, capitals_ignore)

    venue = bib.venue_field(ref)
    ref_type = bib.type_of(ref)
    if venue and venue in values and rules["venue_names"] != "keep" \
            and ref_type not in ("preprint",) and not bib.is_arxiv_venue(values[venue]):
        found = venues.find(values[venue])
        if found:
            changed = venues.override(values[venue], rules["venue_names"])
            if changed:
                override = (bib.plain(values[venue]), *changed)
            values[venue] = bib.to_latex(found[1] if rules["venue_names"] == "abbreviated" else found[0])
        else:
            missing = bib.plain(values[venue])

    if venue and values.get(venue) and ref_type == "conference" \
            and any(rules[name] != "keep" for name in ("venue_year", "venue_sponsor", "venue_prefix")):
        match = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", bib.plain(values.get("year", "")))
        values[venue], venue_notes = style_venue(values[venue], int(match.group(1)) if match else None,
                                                 rules, venues, original_venue)
        notes.extend(venue_notes)

    if rules["doi"] == "remove":
        values.pop("doi", None)
    elif rules["doi"] == "add" and not values.get("doi"):
        if record and record.get("doi"):
            values["doi"] = record["doi"]

    if rules["pages"] == "remove":
        values.pop("pages", None)
    elif rules["pages"] == "add" and not values.get("pages"):
        if record and record.get("pages"):
            values["pages"] = record["pages"]

    if rules["url"] == "remove" or (rules["url"] == "remove_if_doi" and values.get("doi")):
        values.pop("url", None)
    if rules["authors"] == "initials" and values.get("author"):
        values["author"] = authors_to_initials(values["author"], org_words)
    if rules["month"] == "remove":
        values.pop("month", None)
    for name in rules["remove_fields"]:
        values.pop(name, None)
    return values, notes, missing, override
