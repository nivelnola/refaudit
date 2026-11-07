"""format: apply a citation style to every bib entry."""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import bib, check, config, format as fmt, report
from ..changes import Change
from .base import BIB, Result, Sweep

CHANGED, UNCHANGED, SKIPPED = "CHANGED", "UNCHANGED", "SKIPPED"
_VENUE_RULES = ("venue_names", "venue_year", "venue_sponsor", "venue_prefix")


@dataclass
class Entry:
    ref: bib.Reference
    status: str = UNCHANGED
    changes: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    missing_venue: str | None = None
    venue_override: tuple | None = None
    unused: bool = False


def _shows(rules: dict, name: str) -> bool:
    """Whether the style asks for a hidden field to be shown again."""
    return (name in ("doi", "pages") and rules[name] == "add") \
        or (name in ("url", "month") and rules[name] == "show") or name in rules["show_fields"]


def _rule_for(name: str, rules: dict, ref: bib.Reference) -> str:
    if name == "title":
        return ", ".join(f"{r}: {rules[r]}" for r in ("title_case", "protect_capitals") if rules[r] != "keep")
    if name == bib.venue_field(ref):
        return ", ".join(f"{r}: {rules[r]}" for r in _VENUE_RULES if rules[r] != "keep") or "venue_names"
    if name in ("doi", "pages", "url", "month") and rules[name] != "keep":
        return f"{name}: {rules[name]}"
    if name == "author":
        return "authors: initials"
    if name in rules["remove_fields"]:
        return "remove_fields"
    if name in rules["show_fields"]:
        return "show_fields"
    return "style"


def restyle(ref: bib.Reference, base: dict, cfg: config.FormatConfig, venues: fmt.Venues,
            record: dict | None, corrected_why: dict) -> Entry:
    """Apply the style to `base` (the entry's fields plus any verified corrections).

    Changes are relative to the entry as written. `corrected_why` says why verify changed a
    field, so a value that came from the registry is credited to it.
    """
    rules = cfg.for_type(bib.type_of(ref))
    values, unhidden = dict(base), set()
    for name, value in ref.hidden.items():
        if value.strip() and name not in values and _shows(rules, name):
            values[name] = value
            unhidden.add(name)
    values, notes, missing, override = fmt.apply_rules(ref, values, rules, cfg.keep_capitalized, venues, record,
                                                       cfg.capitals_ignore, cfg.organization_words)
    entry = Entry(ref=ref, notes=notes, missing_venue=missing, venue_override=override)

    def why(name: str) -> list[str]:
        return [f"format: {_rule_for(name, rules, ref)} (style {cfg.style_name})"]

    for name in list(ref.fields) + [n for n in values if n not in ref.fields]:
        old, new = ref.fields.get(name), values.get(name)
        if name in unhidden:
            if new is not None:
                entry.changes.append(Change(name, None, ref.hidden[name], "unhide", why(name),
                                            rename=ref.hidden_names[name]))
            continue
        if new == old:
            continue
        if new is None:
            if cfg.hidden_prefix and name not in ref.hidden:
                entry.changes.append(Change(name, old, None, "hide", why(name), rename=cfg.hidden_prefix + name))
            else:
                entry.changes.append(Change(name, old, None, "remove", why(name)))
            continue
        reasons = why(name)
        if name in corrected_why:
            reasons = corrected_why[name] + ([] if new == base.get(name) else reasons)
        entry.changes.append(Change(name, old, new, "set" if old else "add", reasons))
    # Changes here may include verify's corrections, credited to it. Only style changes count.
    entry.status = CHANGED if any("format" in c.sweeps for c in entry.changes) else UNCHANGED
    return entry


def styled(entry: Entry) -> list:
    """The changes the style made, leaving out verify's corrections."""
    return [c for c in entry.changes if "format" in c.sweeps]


class Format(Sweep):
    name = "format"
    summary = "Apply a citation style: title case, venue names, authors, hidden fields"
    description = (
        "Applies the active style's rules to every bib entry: title case, venue names "
        "(abbreviated or full, with \"Proc.\", sponsor and year), author initials, and which "
        "fields to show. Fields a style leaves out are hidden, not deleted: doi becomes OPTdoi, "
        "which BibTeX and biber ignore, so another style can show it again.\n\n"
        "With results from an earlier verify in the ledger, \"doi: add\" and \"pages: add\" fill "
        "gaps from the verified record, at no cost. When verify runs in the same command, "
        "entries it marks CHECK are left as written, unless skip_unverified is false.\n\n"
        "Styles are files in styles/ (in your config folder or the tool's). Every change is "
        "listed in changes.md with its before and after, and in <name>.bib.diff. The guide to "
        "every rule, the order they run in and how to write a style is styles/README.md in the "
        "tool folder.")
    needs = BIB
    cost = "None. Offline: reads stored verify results from the ledger, if there are any."
    changes_bib = ("Only with --write (a corrected copy) or --apply (in place). Left-out fields are "
                   "hidden (OPTdoi), never deleted.")
    writes = "format.md and changes.md, plus <name>.bib.diff if anything would change"
    config_file = "format.yaml, and the style file it names"
    examples = [("refaudit format refs.bib", "propose changes for the style in format.yaml"),
                ("refaudit format refs.bib --style ieee", "propose IEEE style changes, change nothing"),
                ("refaudit format refs.bib --style ieee --apply", "make those changes in refs.bib"),
                ("refaudit format refs.bib --style my_style.yaml", "use a style file by path")]

    def add_arguments(self, parser) -> None:
        styles = ", ".join(config.available_styles()) or "none"
        parser.add_argument("--style", metavar="NAME",
                            help="the style to apply: a name from styles/ or a path to a .yaml file "
                                 f"(default: the style in format.yaml). Built in: {styles}. Your own "
                                 "go in refaudit_config/styles/")

    def run(self, ctx, prior: dict) -> Result:
        cfg = ctx.cfg.format
        verified = prior.get("verify")
        by_ref = {id(o.ref): o for o in verified.data} if verified else {}
        entries = []
        for ref in ctx.bib_refs:
            v = by_ref.get(id(ref))
            corrections, record, corrected_why = {}, None, {}
            if v is not None:
                if v.status == check.CHECK and cfg.skip_unverified:
                    entries.append(Entry(ref=ref, status=SKIPPED, unused=ctx.is_unused(ref)))
                    continue
                if v.status in (check.OK, check.CORRECTED):
                    corrections, record = v.corrections, v.record
                    corrected_why = {c.field: c.why for c in verified.changes.get(id(ref), [])}
            else:
                record = ctx.stored_record(ref)
            entry = restyle(ref, {**ref.fields, **corrections}, cfg, ctx.venues, record, corrected_why)
            entry.unused = ctx.is_unused(ref)
            entries.append(entry)
        result = Result(self.name, data=entries)
        for entry in entries:
            if entry.changes:
                result.changes[id(entry.ref)] = entry.changes
        count = {s: sum(1 for e in entries if e.status == s) for s in (CHANGED, SKIPPED, UNCHANGED)}
        result.console.append(f"Format (style {cfg.style_name}): {count[CHANGED]} changed, "
                              + (f"{count[SKIPPED]} left as written (not verified), " if count[SKIPPED] else "")
                              + f"{count[UNCHANGED]} unchanged.")
        return result

    def render(self, result: Result, ctx) -> str:
        cfg = ctx.cfg.format
        entries = result.data
        where = f" ({cfg.style_path})" if cfg.style_path else ""
        top = report.intro(ctx, "Formatted")
        top += [f"Style: **{cfg.style_name}**{where}. "
                + ("Left-out fields are hidden with the prefix "
                   f"`{cfg.hidden_prefix}`, so BibTeX ignores them and nothing is lost."
                   if cfg.hidden_prefix else "Left-out fields are deleted (hidden_prefix is null)."), ""]
        out = report.Report("Formatting", top)

        def suffix(e) -> str:
            return " (unused)" if e.unused else ""

        skipped = [e for e in entries if e.status == SKIPPED]
        if skipped:
            out.section("Left as written: not verified", "Fix them using verify.md, then run again",
                        len(skipped), label="SKIPPED")
            out.add("Verify marked these CHECK, so they weren't restyled. Set `skip_unverified: false` "
                    "in format.yaml to restyle them anyway.", "")
            out.add(*[f"- {report.label(e.ref)}{suffix(e)}" for e in skipped], "")

        changed = [e for e in entries if e.status == CHANGED]
        if changed:
            out.section("CHANGED: restyled in the corrected bib", "Skim the changes", len(changed), label=CHANGED)
            out.add("changes.md has each change with its before, after and reason.", "")
            for e in changed:
                out.add(f"- {report.label(e.ref)}{suffix(e)}", *report.change_lines(e.ref, styled(e)),
                        *[f"  - Note: {n}" for n in e.notes])
            out.add("")

        missing = sorted({e.missing_venue for e in entries if e.missing_venue})
        if missing:
            out.section("Venues with no abbreviation", "Add abbreviations if you want them", len(missing))
            out.add("These weren't changed. Fill in the abbreviation and paste the lines into your "
                    "`abbreviations_file`:", "", "```yaml")
            out.add(*['"{}": ABBREVIATION'.format(v.replace('"', '\\"')) for v in missing])
            out.add("```", "")

        overridden: dict = {}
        for e in entries:
            if e.venue_override:
                overridden.setdefault(e.venue_override, []).append(e.ref.key)
        if overridden:
            out.section("Your abbreviations file took priority", "Nothing, if that's what you wanted",
                        len(overridden))
            out.add("These venues are in both your `abbreviations_file` and `venue_lists`. Your entry was used.",
                    "", "| Venue in your bib | Your file | Built-in list | References |", "|---|---|---|---|")
            for (venue, builtin, yours), keys in sorted(overridden.items()):
                out.add(f"| {venue} | {yours} | {builtin} | {', '.join(keys)} |")
            out.add("")

        unchanged = [e for e in entries if e.status == UNCHANGED]
        if unchanged:
            out.section("Unchanged", "Nothing", len(unchanged), label=UNCHANGED)
            noted = [e for e in unchanged if e.notes]
            for e in noted:
                out.add(f"- {report.label(e.ref)}{suffix(e)}", *[f"  - Note: {n}" for n in e.notes])
            rest = [e.ref.key for e in unchanged if not e.notes]
            if rest:
                out.add(("- Also: " if noted else "") + ", ".join(rest))
            out.add("")
        result.rows = out.rows
        return out.text()
