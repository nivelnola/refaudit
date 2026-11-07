"""consistency: do the entries hold together as a set? Duplicates first, then naming."""
from __future__ import annotations

import re
from collections import Counter

from .. import bib, config, duplicates, format as fmt, report
from .base import ANY, Result, Sweep

_COMPARED_FIELDS = ("author", "title", "year", "doi", "pages", "volume", "number", "publisher",
                    "address", "month", "url", "editor", "isbn", "issn", "series", "location")
_SMALL = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to",
          "via", "with", "vs", "into", "over", "per", "but", "nor"}


def _initial(word: str) -> bool:
    return bool(re.fullmatch(r"[^\W\d_](\.|-[^\W\d_]\.)?", word)) or word.endswith(".") and len(word) <= 3


def author_variants(refs: list) -> list[dict]:
    """The same person written differently: "J. Smith" in one entry, "John Smith" in another.

    Names are grouped by family name and first initial. A group is flagged only when it holds
    one full first name, so John and Jane Smith are never taken for one person.
    """
    groups: dict = {}
    for ref in refs:
        authors, _ = bib.authors_of(ref)
        for family, given in authors:
            words = given.replace(".", ". ").split()
            if not family or not words:
                continue
            key = (bib.fold(family), bib.fold(words[0])[:1])
            form = f"{given} {family}".strip()
            groups.setdefault(key, {}).setdefault(form, []).append(ref.key)
    found = []
    for forms in groups.values():
        if len(forms) < 2:
            continue
        firsts = {bib.fold(f.replace(".", ". ").split()[0]) for f in forms
                  if not _initial(f.replace(".", ". ").split()[0])}
        if len(firsts) > 1:
            continue  # different people who share an initial
        found.append({"forms": {f: list(dict.fromkeys(keys)) for f, keys in forms.items()},
                      "suggest": max(forms, key=lambda f: (len(forms[f]), len(f)))})
    return found


_VENUE_YEAR = re.compile(r"(?<!\d)(?:19|20)\d\d(?!\d)|['’]\d\d(?!\d)")


def venue_variants(refs: list, venues: fmt.Venues) -> list[dict]:
    """Entries whose venues are the same, written differently: "Proc. IEEE INFOCOM", "INFOCOM'18"."""
    groups: dict = {}
    for ref in refs:
        name = bib.venue_field(ref)
        if not name or bib.is_arxiv_venue(ref.get(name)):
            continue
        written = bib.plain(ref.get(name))
        found = venues.find(written)
        if found:
            # Different years of one conference are different venues, written the same way.
            form = " ".join(_VENUE_YEAR.sub(" ", written).split()) or written
            groups.setdefault(found[1], {}).setdefault(form, []).append(ref.key)
    return [{"venue": abbrev, "forms": forms, "suggest": max(forms, key=lambda f: len(forms[f]))}
            for abbrev, forms in sorted(groups.items()) if len(forms) > 1]


def missing_fields(refs: list, min_share: float, fcfg: config.FormatConfig) -> list[dict]:
    """Fields most entries of a type have, missing from a few. Hidden fields count as present,
    and fields the style leaves out aren't compared."""
    by_type: dict = {}
    for ref in refs:
        by_type.setdefault(bib.type_of(ref), []).append(ref)
    found = []
    for ref_type, group in sorted(by_type.items()):
        if len(group) < 4:
            continue
        rules = fcfg.for_type(ref_type)
        for name in _COMPARED_FIELDS:
            if rules.get(name) == "remove" or name in rules["remove_fields"]:
                continue
            missing = [r.key for r in group if not r.any(name).strip()]
            have = len(group) - len(missing)
            if missing and have >= min_share * len(group):
                found.append({"type": ref_type, "field": name, "have": have, "total": len(group),
                              "missing": missing})
    return found


def _title_style(title: str) -> str | None:
    """'title' or 'sentence' case, or None if the title can't tell."""
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]*", fmt_strip(title))[1:]
             if w.lower() not in _SMALL and len(w) > 3 and not any(c.isupper() for c in w[1:])]
    if len(words) < 2:
        return None
    upper = sum(w[0].isupper() for w in words)
    if upper == len(words):
        return "title"
    if upper == 0:
        return "sentence"
    return None


def fmt_strip(title: str) -> str:
    text = bib.plain(re.sub(r"\{[^{}]*\}", " ", title))
    return re.split(r"[:?!]", text)[0] if ":" in text else text


def _venue_form(value: str, venues: fmt.Venues) -> str | None:
    found = venues.find(value)
    if not found:
        return None
    written = bib.fold(fmt._venue_key(value))
    return "abbreviated" if written != bib.fold(fmt._venue_key(found[0])) else "full"


def mixed_conventions(refs: list, venues: fmt.Venues) -> list[dict]:
    """Within a type, some entries one way and some the other. The minority is listed."""
    by_type: dict = {}
    for ref in refs:
        by_type.setdefault(bib.type_of(ref), []).append(ref)
    found = []
    for ref_type, group in sorted(by_type.items()):
        checks = {
            "title case": {r.key: _title_style(r.get("title")) for r in group},
            "venue names": {r.key: _venue_form(r.get(bib.venue_field(r) or ""), venues)
                            for r in group if bib.venue_field(r)},
        }
        for what, styles in checks.items():
            counts = Counter(s for s in styles.values() if s)
            if len(counts) < 2:
                continue
            (major, n_major), (minor, n_minor) = counts.most_common(2)
            if n_major == n_minor:
                continue
            found.append({"type": ref_type, "what": what, "major": major, "n_major": n_major,
                          "minor": minor, "keys": [k for k, s in styles.items() if s == minor]})
    return found


class Consistency(Sweep):
    name = "consistency"
    summary = "Do the entries hold together? Duplicates, naming, missing fields"
    description = (
        "Compares the entries with each other, with no network. Nothing is changed. Most serious first:\n"
        "  - Possible duplicates: the same work under two keys (same DOI or arXiv ID, the same or\n"
        "    nearly the same title, a conference paper and its journal version). With a PDF, its\n"
        "    references that aren't in the bib are included. Uses verified DOIs from the ledger.\n"
        "  - Venues written differently: \"Proc. IEEE INFOCOM\" in one entry, \"INFOCOM'18\" in another\n"
        "  - Authors written differently: \"J. Smith\" and \"John Smith\"\n"
        "  - Fields most entries of a type have, missing from a few\n"
        "  - Mixed conventions: title case or venue naming that varies within a type")
    needs = ANY
    cost = "None. Offline: reads stored verify results from the ledger, if there are any."
    changes_bib = "No. Report only."
    writes = "consistency.md"
    config_file = "consistency.yaml"
    examples = [("refaudit consistency refs.bib", "all consistency checks"),
                ("refaudit consistency refs.bib --only duplicates", "just possible duplicates"),
                ("refaudit consistency paper.pdf", "duplicates in someone else's paper")]

    def add_arguments(self, parser) -> None:
        parser.add_argument("--only", metavar="SECTIONS",
                            help="comma-separated sections to run: " + ", ".join(config.CONSISTENCY_SECTIONS))

    def run(self, ctx, prior: dict) -> Result:
        cfg = ctx.cfg.consistency
        on = dict(cfg.sections)
        only = getattr(ctx.args, "only", None)
        if only:
            chosen = [s.strip() for s in only.split(",") if s.strip()]
            unknown = [s for s in chosen if s not in config.CONSISTENCY_SECTIONS]
            if unknown:
                raise SystemExit(f"Error: --only can't include {', '.join(unknown)}. "
                                 f"Use {', '.join(config.CONSISTENCY_SECTIONS)}.")
            on = {s: s in chosen for s in config.CONSISTENCY_SECTIONS}
        refs = ctx.bib_refs
        data = {
            "duplicates": duplicates.find(ctx.all_refs, ctx.matched_records(), duplicates.Thresholds(
                cfg.duplicate_similarity, cfg.duplicate_word_overlap, cfg.duplicate_year_gap))
            if on["duplicates"] else [],
            "venues": venue_variants(refs, ctx.venues) if on["venues"] else [],
            "authors": author_variants(refs) if on["authors"] else [],
            "fields": missing_fields(refs, cfg.min_share, ctx.cfg.format) if on["fields"] else [],
            "conventions": mixed_conventions(refs, ctx.venues) if on["conventions"] else [],
        }
        result = Result(self.name, data=data)
        others = sum(len(v) for k, v in data.items() if k != "duplicates")
        result.console.append(f"Consistency: {len(data['duplicates'])} possible duplicate(s), "
                              f"{others} inconsistenc{'y' if others == 1 else 'ies'}.")
        return result

    def render(self, result: Result, ctx) -> str:
        data = result.data
        out = report.Report("Consistency", report.intro(ctx, "Compared the entries of") + report.checked_bib(ctx))
        if not any(data.values()):
            out.add("Nothing found.", "")
        if data["duplicates"]:
            out.section("Possible duplicates", "Keep one entry for each work", len(data["duplicates"]))
            out.add("These entries may be the same work cited more than once. If they are, keep one "
                    "and point every citation to it.", "")
            for keys, reasons in data["duplicates"]:
                shown = ", ".join(f"**{k}**" + (" (unused)" if k in ctx.unused_keys else "") for k in keys)
                if len(reasons) == 1:
                    out.add(f"- {shown}: {reasons[0]}")
                else:
                    out.add(f"- {shown}", *[f"  - {reason}" for reason in reasons])
            out.add("")

        def forms(group: dict) -> list[str]:
            return [f"  - \"{form}\": {', '.join(keys)}" for form, keys in group["forms"].items()]

        if data["venues"]:
            out.section("Venues written differently", "Pick one form for each venue", len(data["venues"]))
            out.add("Each group is one venue. The `venue_names` style setting can make them all the same.", "")
            for g in data["venues"]:
                counts = sorted((len(keys) for keys in g["forms"].values()), reverse=True)
                lead = f", most often \"{g['suggest']}\"" if counts[0] > counts[1] else \
                    f", written {len(g['forms'])} ways"
                out.add(f"- **{g['venue']}**{lead}:", *forms(g))
            out.add("")
        if data["authors"]:
            out.section("Authors written differently", "Write each name the same way", len(data["authors"]))
            out.add("Each group looks like one person. If it's really two, ignore it.", "")
            for g in data["authors"]:
                out.add(f"- **{g['suggest']}**:", *forms(g))
            out.add("")
        if data["fields"]:
            out.section("Fields missing from a few entries", "Add the field, or ignore it", len(data["fields"]))
            for g in data["fields"]:
                out.add(f"- **{g['field']}** in {g['have']} of {g['total']} {g['type']} entries. "
                        f"Missing from: {', '.join(g['missing'])}")
            out.add("")
        if data["conventions"]:
            out.section("Mixed conventions", "Make them match; a format style can do it", len(data["conventions"]))
            for g in data["conventions"]:
                out.add(f"- **{g['what']}** in {g['type']} entries: {g['n_major']} {g['major']}, but "
                        f"{len(g['keys'])} {g['minor']}: {', '.join(g['keys'])}")
            out.add("")
        result.rows = out.rows
        return out.text()
