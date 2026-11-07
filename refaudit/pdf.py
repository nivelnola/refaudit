"""Extract the reference list from a PDF."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import NamedTuple

import pymupdf

from .bib import Reference


class PdfError(Exception):
    """A problem with the PDF, with a message written for the user."""


def _spaced(word: str) -> str:
    """A word that may be letter-spaced, as in "R E F E R E N C E S"."""
    return r"\s?".join(word)


_HEADING = re.compile(rf"^\s*(?:[\dIVX]+\.?\s+)?({_spaced('references')}|{_spaced('bibliography')})\s*$", re.I)
_END = re.compile(r"^\s*(appendix\b|appendices\b|received \d|supplementary\b|biograph(?:y|ies)\b)", re.I)
_DOI = re.compile(r"10\.\d{4,9}/[^\s,;\"<>]+")
_ARXIV = re.compile(r"(?:arxiv[:\s]+|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5}|[a-z-]+/\d{7})", re.I)
_YEAR = re.compile(r"\b(19\d\d|20\d\d)[a-z]?\b")


class Line(NamedTuple):
    """One printed line: every piece of text at that height in one column, left to right."""
    page: int
    column: int
    x: float
    y0: float
    y1: float
    text: str
    band: int = 0  # a stretch of the page with one column layout, counted from the top


# --- Layout ------------------------------------------------------------------------
#
# A piece is (x0, y0, x1, y1, text): one stretch of text as the PDF stores it.

_GUTTER = 6        # points of clear space that separate two columns
_NARROWEST = 0.15  # share of the page width a column needs, so a strip of labels isn't one
_WIDE = 0.6        # pieces wider than this share of the page span columns, like a title


_SECTION_NUMBER = re.compile(r"^\s*[\dIVX]+\.?\s*$")


def _can_share_row(row: list, piece: tuple) -> bool:
    """A heading in a bigger font can overlap the line below it, as with "10. REFERENCES"
    above "[1] ...". It shares a row only with its own section number."""
    pieces = row + [piece]
    if not any(_HEADING.match(p[4]) for p in pieces):
        return True
    return all(_HEADING.match(p[4]) or _SECTION_NUMBER.match(p[4]) for p in pieces)


def _rows(pieces: list) -> list[list]:
    """Pieces grouped into printed lines: a piece whose vertical middle falls within a line
    belongs to it, since justified text is stored in pieces at slightly different heights."""
    rows: list[list] = []
    for piece in sorted(pieces, key=lambda p: ((p[1] + p[3]) / 2, p[0])):
        middle = (piece[1] + piece[3]) / 2
        if rows and min(p[1] for p in rows[-1]) <= middle <= max(p[3] for p in rows[-1]) \
                and _can_share_row(rows[-1], piece):
            rows[-1].append(piece)
        else:
            rows.append([piece])
    return rows


def _column_starts(pieces: list, width: float) -> list[float]:
    """Where each column starts, found from clear vertical gutters. Needs a few lines to go
    on, since one justified line has gaps that look like gutters."""
    if len(_rows(pieces)) < 3:
        return [float("-inf")]
    narrow = [p for p in pieces if p[2] - p[0] < width * _WIDE] or pieces
    size = int(width) + 2
    edges = [0] * (size + 1)
    for x0, _, x1, _, _ in narrow:
        edges[min(max(int(x0), 0), size)] += 1
        edges[min(max(int(x1) + 1, 0), size)] -= 1
    allowed = len(narrow) // 50  # a stray piece crossing a gutter, like a wide equation
    runs: list[list] = []
    covered = 0
    for x in range(size):
        covered += edges[x]
        if covered > allowed:
            if runs and x - runs[-1][1] <= _GUTTER:
                runs[-1][1] = x
            else:
                runs.append([x, x])
    # A narrow strip, such as reference labels set apart, joins the column beside it.
    i = 0
    while len(runs) > 1 and i < len(runs):
        if runs[i][1] - runs[i][0] < width * _NARROWEST:
            a = i if i + 1 < len(runs) else i - 1
            runs[a:a + 2] = [[runs[a][0], runs[a + 1][1]]]
            i = 0
        else:
            i += 1
    return [float("-inf")] + [r[0] - _GUTTER / 2 for r in runs[1:]]


def _bands(pieces: list, width: float) -> list[tuple[list, list[float]]]:
    """The page cut where its column layout changes, as with two columns of text above
    three columns of references. Returns (pieces, column starts) for each, top to bottom."""
    if not pieces:
        return []
    heights = sorted(p[3] - p[1] for p in pieces)
    gap = heights[len(heights) // 2] * 0.8
    bands: list[list] = []
    bottom = None
    for piece in sorted(pieces, key=lambda p: p[1]):
        if bands and piece[1] <= bottom + gap:
            bands[-1].append(piece)
            bottom = max(bottom, piece[3])
        else:
            bands.append([piece])
            bottom = piece[3]
    # A blank stretch across the whole page isn't always a change of layout. Space between
    # entries can line up across columns, so neighbours merge when no gutter is lost.
    merged: list[tuple[list, list[float]]] = []
    for band in bands:
        starts = _column_starts(band, width)
        if merged:
            union = merged[-1][0] + band
            union_starts = _column_starts(union, width)
            if len(union_starts) >= max(len(starts), len(merged[-1][1])):
                merged[-1] = (union, union_starts)
                continue
        merged.append((band, starts))
    return merged


def _glyph_text(name: str) -> str:
    """The text a glyph name stands for, by the Adobe naming rules: "f_f_i" is "ffi"."""
    text = ""
    for part in name.split(".")[0].split("_"):
        if re.fullmatch(r"uni(?:[0-9A-F]{4})+", part):
            text += "".join(chr(int(part[i:i + 4], 16)) for i in range(3, len(part), 4))
            continue
        if re.fullmatch(r"[A-Za-z]", part):  # a letter's glyph is named after it
            text += part
            continue
        code = pymupdf.glyph_name_to_unicode(part)
        if code <= 0 or code == 0xFFFD:
            return ""
        text += chr(code)
    return text


def _ligatures(page) -> dict:
    """Ligatures a font names in its encoding but doesn't map to text, as (text, width) by
    font name. MuPDF gives these as U+FFFF, so "traffic" comes out as "tra?c"."""
    doc, found = page.parent, {}
    for xref, _, _, basefont, *_ in page.get_fonts():
        try:
            kind, differences = doc.xref_get_key(xref, "Encoding/Differences")
            if kind != "array":
                continue
            first = int(doc.xref_get_key(xref, "FirstChar")[1])
            kind, widths = doc.xref_get_key(xref, "Widths")
            if kind == "xref":
                widths = doc.xref_object(int(widths.split()[0]))
            widths = [float(w) for w in re.findall(r"-?\d+(?:\.\d+)?", widths)]
        except (ValueError, IndexError, RuntimeError):
            continue
        code = None
        for token in re.findall(r"/[^\s/\[\]<>()]+|\d+", differences):
            if token[0] != "/":
                code = int(token)
                continue
            if code is None:
                continue
            text = _glyph_text(token[1:]) if "_" in token else ""
            if len(text) > 1 and 0 <= code - first < len(widths):
                found.setdefault(basefont.split("+")[-1], []).append((text, widths[code - first]))
            code += 1
    return found


_UNSURE = 0x100000  # private-use characters standing for a ligature only context can settle
_UNSURE_RUN = re.compile(r"[^\W\d_]*(?:[\U00100000-\U0010FFFD][^\W\d_]*)+")
_LIGATURE_CHARS = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi",
                                 "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"})


def _span_text(span: dict, ligatures: dict, unsure: list) -> str:
    """A rawdict span's text, with unmapped ligatures put back by their width.

    Measured widths are off by about 1%, and some fonts draw "ff" and "fi" equally wide.
    When more than one ligature fits, a placeholder goes in, and `unsure` keeps the
    choices, closest first, for _settle_ligatures to pick from by the words around it."""
    options = ligatures.get(span["font"])
    out = []
    for char in span["chars"]:
        c = char["c"]
        if c == "￿" and options and span["size"]:
            width = (char["bbox"][2] - char["bbox"][0]) * 1000 / span["size"]
            ranked = sorted(options, key=lambda option: abs(option[1] - width))
            best = abs(ranked[0][1] - width)
            close = [text for text, w in ranked if abs(w - width) <= best + 0.012 * width]
            if len(set(close)) == 1:
                c = close[0]
            else:
                unsure.append(list(dict.fromkeys(close)))
                c = chr(_UNSURE + len(unsure) - 1)
        out.append(c)
    return "".join(out)


def _settle_ligatures(pages: list, unsure: list) -> list:
    """Replace each placeholder with the choice that spells a word printed elsewhere in the
    document, or failing that, the closest width."""
    words = Counter(w.lower() for lines in pages for line, _ in lines
                    for w in re.findall(r"[^\W\d_]+", line.text))

    def settle(m):
        word = m.group()
        slots = [i for i, ch in enumerate(word) if ord(ch) >= _UNSURE]
        best, best_count = None, 0
        combos = [[]]
        for i in slots:
            combos = [c + [o] for c in combos for o in unsure[ord(word[i]) - _UNSURE]][:64]
        for combo in combos:
            spelled = "".join(combo[slots.index(i)] if i in slots else ch for i, ch in enumerate(word))
            if words[spelled.lower()] > best_count:
                best, best_count = spelled, words[spelled.lower()]
        return best or "".join(unsure[ord(ch) - _UNSURE][0] if ord(ch) >= _UNSURE else ch for ch in word)

    return [[(line._replace(text=_UNSURE_RUN.sub(settle, line.text)), edge) for line, edge in lines]
            for lines in pages]


def _visual_lines(page, number: int, unsure: list | None = None) -> list[tuple[Line, bool]]:
    """(line, near the page's top or bottom edge) in reading order: band by band down the
    page, each band column by column, and each column down.

    Pieces on one printed line are joined left to right, so the text comes out in the
    order it's printed. Ligatures come out as their letters.
    """
    unsure = [] if unsure is None else unsure
    width, height = page.rect.width, page.rect.height
    data = page.get_text("dict")
    unmapped = any("￿" in span["text"] for block in data["blocks"]
                   for line in block.get("lines", []) for span in line["spans"])
    ligatures = {}
    if unmapped:  # characters only rawdict can repair, one by one
        data = page.get_text("rawdict")
        ligatures = _ligatures(page)
    pieces = []
    for block in data["blocks"]:
        for line in block.get("lines", []):
            text = "".join(_span_text(span, ligatures, unsure) if unmapped else span["text"]
                           for span in line["spans"]).strip().translate(_LIGATURE_CHARS)
            x0, y0, x1, y1 = line["bbox"]
            if not text or re.fullmatch(r"\d{1,5}", text):
                continue
            pieces.append((x0, y0, x1, y1, text))
    lines = []
    for band_number, (band, starts) in enumerate(_bands(pieces, width)):
        by_column: dict = {}
        for piece in band:
            column = max(i for i, start in enumerate(starts) if piece[0] >= start)
            by_column.setdefault(column, []).append(piece)
        for column in sorted(by_column):
            for row in _rows(by_column[column]):
                row.sort(key=lambda p: p[0])
                y0, y1 = min(p[1] for p in row), max(p[3] for p in row)
                line = Line(number, column, row[0][0], y0, y1, " ".join(p[4] for p in row), band_number)
                lines.append((line, y0 < height * 0.13 or y1 > height * 0.87))
    return lines


def _header_key(text: str) -> str:
    return re.sub(r"[\d\s]+", "", text).lower()


def _clean_pages(doc) -> list[list[Line]]:
    """Reading-order lines per page, without headers and footers.

    Those are lines near a page edge whose text repeats across pages. Their position
    varies by template, so the repetition is what identifies them.
    """
    unsure: list = []
    raw = [_visual_lines(page, number, unsure) for number, page in enumerate(doc)]
    if unsure:
        raw = _settle_ligatures(raw, unsure)
    counts = Counter(key for lines in raw
                     for key in {_header_key(line.text) for line, edge in lines if edge})
    needed = max(3, len(raw) // 5)
    return [[line for line, edge in lines
             if not (edge and counts[_header_key(line.text)] >= needed)]
            for lines in raw]


def _reference_lines(doc) -> list[Line]:
    pages = _clean_pages(doc)
    if not any(pages):
        raise PdfError("This PDF has no text layer (it may be scanned), so its references "
                       "can't be read. OCR isn't supported.")
    start = None
    for p, lines in enumerate(pages):
        for i, line in enumerate(lines):
            if _HEADING.match(line.text):
                start = (p, i)  # keep the last heading in the document
    if start is None:
        raise PdfError("Couldn't find a References or Bibliography heading in this PDF.")
    p, i = start
    collected = list(pages[p][i + 1:])
    for lines in pages[p + 1:]:
        collected.extend(lines)
    for n, line in enumerate(collected):
        if _END.match(line.text):
            return collected[:n]
    return collected


def _hyphenated(lines: list[Line]) -> set:
    """Hyphenated word pairs printed whole somewhere, like ("full", "duplex")."""
    return {(a.lower(), b.lower()) for line in lines
            for a, b in re.findall(r"([^\W\d_]+)-([^\W\d_]+)", line.text)}


def _join(lines: list[str], hyphenated: set = frozenset()) -> str:
    """Join wrapped lines, repairing words and links split across them.

    A hyphen at a line's end is usually the typesetter's, but if the same pair of words is
    printed with a hyphen elsewhere, as "full-duplex" often is, the hyphen is kept.
    """
    text = ""
    for line in lines:
        before = re.search(r"([^\W\d_]+)-$", text)
        after = re.match(r"[^\W\d_]+", line)
        if not text:
            text = line
        elif re.search(r"(https?:|doi\.org/|10\.\d{4,9}/)\S*$", text):
            text += line  # links wrap with no space
        elif before and after and (before.group(1).lower(), after.group().lower()) in hyphenated:
            text += line
        elif text.endswith("-") and line[:1].islower():
            text = text[:-1] + line  # a word split across lines
        elif text.endswith("-"):
            text += line             # a hyphenated name: Full-Duplex
        else:
            text += " " + line
    return text.strip()


# --- Splitting into entries ---------------------------------------------------------

# Labels that start each entry, tried in this order. Numbers must run in sequence.
_NUMBERED = (r"^\[(\d+)\]\s*(.*)", r"^(\d+)\.\s+(.*)", r"^\((\d+)\)\s+(.*)")
_KEYED = r"^\[([A-Za-z][A-Za-z+\-.]{0,12}\d{2,4}[a-z]?)\]\s*(.*)"   # [BMK13], [Smi+20a]


def _trim_tail(tail: list[Line], body: list[Line]) -> list[Line]:
    """The last entry's lines, without what follows the list, such as author biographies or
    an appendix with no heading. Nothing ends the last entry, so it ends at the first break
    in the layout once its text is complete: a new column or page, extra space, or a line
    that doesn't start where the list's wrapped lines do."""
    continuation: dict = {}
    for line in body:
        continuation.setdefault(line.column, []).append(line.x)
    starts = {column: sorted(xs)[len(xs) // 2] for column, xs in continuation.items()}
    steps = sorted(b.y0 - a.y0 for a, b in zip(body, body[1:])
                   if (a.page, a.column) == (b.page, b.column) and b.y0 > a.y0)
    pitch = steps[len(steps) // 2] if steps else 0
    kept = tail[:1]
    for line in tail[1:]:
        prev = kept[-1]
        complete = re.search(r"[.)]$", prev.text.rstrip()) and _YEAR.search(" ".join(l.text for l in kept))
        if complete:
            same = (line.page, line.column) == (prev.page, prev.column)
            spaced = bool(pitch) and line.y0 - prev.y0 > pitch * 1.6
            aligned = line.column in starts and abs(line.x - starts[line.column]) <= 6
            if not same or spaced or not aligned:
                break
        kept.append(line)
    return kept


def _labeled_entries(lines: list[Line], labels: list) -> list[tuple[str, str]]:
    """(label, text) per entry, given (line index, label) for each line that starts one."""
    hyphenated = _hyphenated(lines)
    bounds = [i for i, _, _ in labels] + [len(lines)]
    groups = [lines[bounds[k]:bounds[k + 1]] for k in range(len(labels))]
    groups[-1] = _trim_tail(groups[-1], [l for g in groups[:-1] for l in g[1:]])
    entries = []
    for (_, label, rest), group in zip(labels, groups):
        entries.append((label, _join(([rest] if rest else []) + [l.text for l in group[1:]], hyphenated)))
    return entries


def _numbered(lines: list[Line]) -> list[tuple[str, str]] | None:
    """Numbers normally run in sequence, which keeps a stray "[7]" inside an entry from
    starting one. A label printed out of order, as when [12] sits above [8], is still taken
    if it appears only once and the list has nearly every number up to it."""
    for pattern in _NUMBERED:
        found = [(i, int(m.group(1)), m.group(2)) for i, line in enumerate(lines)
                 if (m := re.match(pattern, line.text))]
        if len(found) < 2:
            continue
        times = Counter(n for _, n, _ in found)
        present = sorted(times)
        dense = {n for k, n in enumerate(present, 1) if k >= 0.9 * n}
        labels, used, expected = [], set(), 1
        for i, n, rest in found:
            if n == expected or (n > expected and n not in used and n in dense and times[n] == 1):
                labels.append((i, n, rest))
                used.add(n)
                while expected in used:
                    expected += 1
        if len(labels) >= 2:
            entries = _labeled_entries(lines, [(i, f"[{n}]", rest) for i, n, rest in labels])
            order = {f"[{n}]": n for _, n, _ in labels}
            return sorted(entries, key=lambda entry: order[entry[0]])
    return None


def _keyed(lines: list[Line]) -> list[tuple[str, str]] | None:
    labels = [(i, f"[{m.group(1)}]", m.group(2)) for i, line in enumerate(lines)
              if (m := re.match(_KEYED, line.text))]
    if len(labels) < 2:
        return None
    return _labeled_entries(lines, labels)


def _layout_features(lines: list[Line]) -> list[dict]:
    """What the layout says about each line, for telling where an entry starts.

    Margins are measured per column on each page, since a two-column page has two. Gaps
    are measured against the usual spacing between lines, so they work at any font size.
    """
    lefts: dict = {}
    for line in lines:
        key = (line.page, line.band, line.column)
        lefts[key] = min(lefts.get(key, line.x), line.x)
    # A column holding only the end of one long entry has no line at the real margin,
    # so a margin well right of that column's usual one isn't trusted.
    by_column: dict = {}
    for (_, _, column), left in lefts.items():
        by_column.setdefault(column, []).append(left)
    usual = {column: sorted(values)[len(values) // 2] for column, values in by_column.items()}
    indented = {key for key in lefts
                if any(l.x > lefts[key] + 3 for l in lines if (l.page, l.band, l.column) == key)}

    steps = [b.y0 - a.y0 for a, b in zip(lines, lines[1:])
             if (a.page, a.band, a.column) == (b.page, b.band, b.column) and b.y0 > a.y0]
    # Most steps are within an entry, but in a list of one-line entries most are gaps, so
    # the usual spacing is taken from the low end.
    pitch = sorted(steps)[len(steps) // 4] if steps else 0
    features = []
    for i, line in enumerate(lines):
        key = (line.page, line.band, line.column)
        prev = lines[i - 1] if i else None
        same_column = prev is not None and (prev.page, prev.band, prev.column) == key
        left = lefts[key] if lefts[key] <= usual[line.column] + 4 else usual[line.column]
        features.append({
            "at_left": line.x <= left + 2,
            "indented": key in indented,
            # None at the top of a column, where there's no line above to measure from.
            "gap": (line.y0 - prev.y0 > pitch * 1.3) if same_column and pitch else None,
            "after_end": prev is None or bool(re.search(r"[.\])]$", prev.text.rstrip())),
        })
    return features


def _split_at(lines: list[Line], starts: list[bool]) -> list[str]:
    hyphenated = _hyphenated(lines)
    entries, current = [], []
    for line, start in zip(lines, starts):
        if start and current:
            entries.append(current)
            current = []
        current.append(line.text)
    if current:
        entries.append(current)
    return [_join(parts, hyphenated) for parts in entries]


def _plausibility(entries: list[str]) -> float:
    """How much a split looks like a reference list: entries of a sensible length, each
    with a year. More than two years usually means several entries run together, since an
    entry prints its year once, or twice in ACM style."""
    if not entries:
        return 0.0
    score = 0.0
    for text in entries:
        years = len(_YEAR.findall(text))
        score += 0.5 if years else 0.0
        score += 0.5 if 30 <= len(text) <= 900 else 0.0
        score -= 0.5 * max(0, years - 2)
    return score / len(entries)


def _unlabeled(lines: list[Line]) -> list[tuple[str, str]]:
    """Split an unnumbered list by its layout. Each common style is tried, and the split
    that looks most like a reference list wins. Earlier styles win ties.

    - Hanging indent: an entry starts at the column's margin, and later lines are indented.
    - Spacing: entries are separated by extra space.
    - Hanging indent, with no spacing to go by.
    - First-line indent: an entry's first line is indented, and later lines aren't.
    """
    if not lines:
        return []
    features = _layout_features(lines)
    strategies = [
        [f["at_left"] and f["indented"] and (bool(f["gap"]) or f["after_end"]) for f in features],
        [bool(f["gap"]) or (f["gap"] is None and f["at_left"] and f["after_end"]) for f in features],
        [f["at_left"] and f["after_end"] for f in features],
        [not f["at_left"] and f["after_end"] for f in features],
    ]
    best, best_score = [], -1.0
    for starts in strategies:
        entries = _split_at(lines, starts)
        score = _plausibility(entries)
        if score > best_score + 1e-9:
            best, best_score = entries, score
    return [(f"[{i}]", text) for i, text in enumerate(best, 1)]


def _split_entries(lines: list[Line]) -> list[tuple[str, str]]:
    """Split into (label, text): [1] and 1. lists, [Smi20] lists, or unnumbered ones by layout."""
    return _numbered(lines) or _keyed(lines) or _unlabeled(lines)


# --- Parsing one entry --------------------------------------------------------------

def _authors_field(text: str) -> str:
    """Turn 'A Bee, C Dee, and E Eff' into BibTeX form. Empty if these don't look like people."""
    text = text.strip().rstrip(".,")
    truncated = bool(re.search(r"\bet al\.?$", text))
    text = re.sub(r",?\s*et al\.?$", "", text)
    text = re.sub(r",\s*(Jr|Sr|II|III)\b\.?", r" \1", text)
    names = [n.strip() for n in re.split(r",\s*(?:and\s+)?|\s+and\s+|\s*&\s*", text) if n.strip()]
    for name in names:
        words = name.split()
        if not 1 <= len(words) <= 5 or re.search(r"[\d()]", name) or not name[:1].isupper():
            return ""
    return " and ".join(names + (["others"] if truncated else []))


_WORD = r"[^\W\d_][\w'’`´ˇ¨\-]*"
_PARTICLE = r"(?:van|von|der|den|de|del|della|di|da|du|le|la|dos|das|al|bin|ben)"
_FAMILY = re.compile(rf"((?:{_PARTICLE}\s+)*{_WORD}(?:\s+(?:{_PARTICLE}\s+)*{_WORD}){{0,2}}),\s+")
_GIVEN_WORD = re.compile(rf"([A-Z]\.(?:-?[A-Z]\.)*|{_WORD}\.?)")  # initials first: "J.-D." whole


def _last_first(text: str) -> tuple[str, str] | None:
    """Split 'Chen, Tingjun, Zhou, Jin, and Zussman, Gil. Title...' into BibTeX authors and
    the rest. None if the text doesn't start with names written family name first.

    Names alternate with their given names, so the list is read pair by pair. It ends at
    the first given name followed by a period and a space.
    """
    pos, names, truncated = 0, [], False
    while True:
        if names:
            etal = re.match(r",?\s+et\s+al\.?[:.,]?\s+", text[pos:])
            if etal:  # "Zappone, A., et al.: Title"
                pos += etal.end()
                truncated = True
                break
            joiner = re.match(r",?\s+and\s+|,\s+(?:and\s+)?", text[pos:])
            if not joiner:
                return None
            pos += joiner.end()
        family = _FAMILY.match(text, pos)
        if not family or family.group(1)[0].islower() and not re.match(_PARTICLE, family.group(1)):
            return None
        pos = family.end()
        given = []
        while True:
            word = _GIVEN_WORD.match(text, pos)
            if not word or len(given) == 4:
                return None
            given.append(word.group(1))
            pos = word.end()
            after = text[pos:pos + 6]
            if word.group(1).endswith(".") and re.match(r"\s+(?!and\b)\S", after) \
                    and not re.match(r"\s+[A-Z]\.", after):
                names.append((family.group(1), " ".join(given)))
                ended = True
                break
            if not word.group(1).endswith(".") and re.match(r"\.\s", after) or re.match(r":\s", after):
                pos += 1  # Copernicus ends the list with a colon: "Zhang, Q. S.: Title, ..."
                names.append((family.group(1), " ".join(given)))
                ended = True
                break
            if re.match(r",|\s+and\s", after):
                names.append((family.group(1), " ".join(given)))
                ended = False
                break
            if not re.match(r"\s+\S", after):
                return None
            pos += len(after) - len(after.lstrip())
        if ended:
            break
    # One name could just as well be two people written given name first, unless the
    # family name is a single word: "Jane Smith, John Doe" isn't read as one person.
    if len(names) == 1 and len(names[0][0].split()) > 1:
        return None
    if len(names) > 1 and not truncated and not re.search(r"\band\b", text[:pos]):
        return None
    # Family names don't hold initials, and are rarely several words: "Theodore S Rappaport,
    # Shu Sun, ..." is given names first, read wrongly in pairs.
    families = [family.split() for family, _ in names]
    if any(len(word.rstrip(".")) == 1 for words in families for word in words):
        return None
    if 2 * sum(1 for words in families if len([w for w in words if not re.fullmatch(_PARTICLE, w)]) > 1) \
            > len(names):
        return None
    # The list's closing period follows the last given name. An initial keeps its own.
    authors = " and ".join([f"{family}, {re.sub(r'(?<=[^\W\d_]{2})\.$', '', given)}"
                            for family, given in names] + (["others"] if truncated else []))
    return authors, text[pos:].strip()


_UP = r"[A-ZÀ-ÖØ-Þ]"
_INITIAL = rf"{_UP}[a-z]?\.(?:\s?-\s?{_UP}\.)?"
_SURNAME = rf"(?:(?i:{_PARTICLE})\s+)*{_UP}[\w'’`´ˇ¨\-]*"  # "Van Loan", "van der Berg"
# "J. K. Smith" or "Jane K Smith": initials first, or a full name of two to four words.
_INITIALS_NAME = re.compile(rf"(?:{_INITIAL}\s*)+{_SURNAME}(?:,?\s+Jr\.?)?")
_FULL_NAME = re.compile(rf"{_UP}[\w'’`´\-]+(?:\s+(?:{_UP}\.?(?=\s)|{_SURNAME})){{1,3}}")
_NAME_SEP = re.compile(r",\s+and\s+|,\s+|\s+and\s+|\s*&\s*")


def _given_first(text: str) -> tuple[str, str] | None:
    """Split 'J. Smith, K. Lee, and M. Chan, Title...' or 'Jane Smith and Kim Lee. Title...'
    into BibTeX authors and the rest. None if the text doesn't start with such a list.

    The list ends at the first comma or period not followed by another name. Every name must
    be written the same way, so a capitalized title isn't read as one more author."""
    pos, names, style, joined_by_and = 0, [], None, False
    while True:
        start = pos
        if names:
            sep = _NAME_SEP.match(text, pos)
            if not sep:
                break
            start = sep.end()
        m, kind = _INITIALS_NAME.match(text, start), "initials"
        if not m:
            m, kind = _FULL_NAME.match(text, start), "full"
        if not m or (style and kind != style) or not re.match(r"[\s,.&]", text[m.end():m.end() + 1]):
            break
        if names:
            joined_by_and |= bool(re.search(r"\band\b|&", sep.group()))
        names.append(m.group().strip().rstrip(","))
        style, pos = kind, m.end()
    if not names:
        return None
    etal = re.match(r",?\s+et\s+al\.?", text[pos:])
    if etal:
        pos += etal.end()
    end = re.match(r"[,.:]?\s+" if etal else r"[,.:]\s+", text[pos:])
    if not end:
        return None
    rest = text[pos + end.end():]
    if style == "full" and not (len(names) > 1 and joined_by_and or etal):
        return None
    if not rest or re.match(r"(?:19|20)\d\d\b", rest):
        return None
    return " and ".join(names + (["others"] if etal else [])), rest


def _clean_title(title: str) -> str:
    """Drop what follows a title when it's printed without quotes: a link, an arXiv ID, an
    edition, or ", in Proc. ..."."""
    title = re.sub(r"^\(?Eds?\.\)?,?\s+", "", title)  # "Ed., Handbook of ..."
    title = re.split(r"\s+(?:URL\s+)?https?:|[.,]?\s+(?:arXiv|doi|eprint)\b|,\s+in\s+(?=[A-Z])"
                     r"|[.,]\s+\d+(?:st|nd|rd|th)\s+ed(?:ition|\.)", title)[0]
    title = re.sub(r",?\s*\d+(?:st|nd|rd|th)\s+ed(?:ition|\.)?$", "", title.strip().rstrip("."))
    return re.sub(r",\s*(?:19|20)\d\d[a-z]?$", "", title.rstrip(".")).strip()


def _title_before_venue(rest: str) -> str:
    """Copernicus style, "Title, Journal, 14, 5411–5426, doi, 2022.": titles there end with a
    comma, not a period, so the title runs up to the venue, which is the part just before
    the first volume, page or article number. Titles with commas in them stay whole."""
    rest = re.split(r",\s+in:\s", rest, flags=re.I)[0]
    parts = re.split(r",\s+", rest)
    for i in range(1, len(parts) - 1):
        if re.fullmatch(r"[A-Za-z]?\d[\w–\-]*\.?|https?://\S+", parts[i + 1]):
            return _clean_title(", ".join(parts[:i]))
    return _title_from(rest)


def _title_from(rest: str) -> str:
    """The title at the start of what follows the authors: up to the sentence's end."""
    rest = re.split(r"\s+(?:URL\s+)?https?:", rest)[0]
    m = re.match(r"(.+?[.?!])(?:\s+(?=[A-Z(])|\s+(?=arXiv\b)|\s*$)", rest)
    return _clean_title(m.group(1) if m else rest)


_DITTO = re.compile(r"^\s*(?:[—–]+|-{2,})\s*[,.]?\s*")
# Links and access notes say where a reference is, not what it is.
_ONLINE = re.compile(r"\[Online\]\.?\s*|\bAvailable(?: online)?:?\s*(?:at:?\s*)?(?:https?://|www\.)\S*"
                     r"|\bRetrieved\b[^.]*?\bfrom\s+\S+|\bAccessed:?[^.]*\.?", re.I)


def _parse_entry(label: str, text: str) -> Reference:
    ref = Reference(key=label, source="pdf", raw=text)
    doi = _DOI.search(text)
    if doi:
        ref.fields["doi"] = doi.group().rstrip(".")
    arxiv = _ARXIV.search(text)
    if arxiv:
        ref.fields["eprint"] = arxiv.group(1)
        ref.fields["archiveprefix"] = "arXiv"

    # A ditto mark stands for the previous entry's authors: extract_references fills them in.
    text = _DITTO.sub("", re.sub(r"\s{2,}", " ", _ONLINE.sub(" ", text)).strip())
    title = authors = None
    bibtex_authors = None
    quoted = re.search(r"[“\"](.+?)[,.]?[”\"]", text)
    acm = re.match(r"(.+?)\.\s+(?:19|20)\d\d[a-z]?\.\s+(.+)", text)
    last_first = None if quoted or acm else _last_first(text)
    given_first = None if quoted or acm or last_first else _given_first(text)
    if quoted:  # IEEE: Authors, "Title," venue
        title, authors = quoted.group(1), text[:quoted.start()]
    elif acm:   # ACM: Authors. Year. Title. Venue
        authors = acm.group(1)
        title = _title_from(acm.group(2))
    elif last_first:  # Author-year: Family, Given, and Family, Given. Title. Venue, year
        bibtex_authors, rest = last_first
        colon = text[:len(text) - len(rest)].rstrip().endswith(":")
        title = _title_before_venue(rest) if colon else _title_from(rest)
    elif given_first:  # J. Smith and K. Lee, Title. Publisher, year  /  Jane Smith and Kim Lee. Title. Venue
        bibtex_authors, rest = given_first
        title = _title_from(rest)
    else:       # Authors. Title. Venue, year
        parts = re.split(r"(?<!\b[A-Z])(?<!et al)\.\s+(?=[A-Z])", text)
        if len(parts) >= 2:
            authors, title = parts[0], _clean_title(parts[1])

    years = _YEAR.findall(text)
    if years:
        ref.fields["year"] = years[0] if acm else years[-1]
    if title and len(title.split()) >= 2:
        ref.fields["title"] = title.strip()
        ref.fields["author"] = bibtex_authors if bibtex_authors is not None \
            else _authors_field(authors or "")
    else:
        ref.parse_ok = False
    return ref


def extract_references(path: Path) -> list[Reference]:
    try:
        doc = pymupdf.open(path)
    except Exception as err:
        raise PdfError(f"Couldn't open {path}: {err}") from err
    with doc:
        lines = _reference_lines(doc)
    refs: list[Reference] = []
    for label, text in _split_entries(lines):
        if not text:
            continue
        ref = _parse_entry(label, text)
        # "——, Title" repeats the authors of the entry before.
        if _DITTO.match(text) and ref.parse_ok and not ref.get("author") and refs and refs[-1].get("author"):
            ref.fields["author"] = refs[-1].fields["author"]
        refs.append(ref)
    return refs
