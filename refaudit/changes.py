"""Every change to the bib, with where it came from, and the before/after review files."""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from . import bib


@dataclass
class Change:
    field: str                  # the field, by its ordinary name ("doi", even when hidden)
    old: str | None             # value before; None if the field wasn't shown
    new: str | None             # value after; None if it's no longer shown
    kind: str                   # set | add | remove | hide | unhide
    why: list = field(default_factory=list)   # "verify: ...", "format: ..."
    rename: str | None = None   # hide: the field's new name. unhide: its old name

    @property
    def sweeps(self) -> list[str]:
        return list(dict.fromkeys(w.split(":", 1)[0] for w in self.why))


def edits_for(changes: list[Change]) -> dict:
    """The changes in the form bib.write_corrected takes, keyed by the field's name in the file."""
    edits = {}
    for c in changes:
        if c.kind == "hide":
            edits[c.field] = bib.Rename(c.rename)
        elif c.kind == "unhide":
            edits[c.rename.lower()] = bib.Rename(c.field)
        else:
            edits[c.field] = c.new
    return edits


def after(ref: bib.Reference, changes: list[Change]) -> bib.Reference:
    """The entry as it reads in the corrected bib, for fingerprinting."""
    fields, hidden = dict(ref.fields), dict(ref.hidden)
    for c in changes:
        if c.kind == "hide":
            hidden[c.field] = fields.pop(c.field, "")
        elif c.kind == "unhide":
            fields[c.field] = hidden.pop(c.field, "")
        elif c.new is None:
            fields.pop(c.field, None)
        else:
            fields[c.field] = c.new
    return bib.Reference(key=ref.key, etype=ref.etype, fields=fields, hidden=hidden)


# --- changes.md -------------------------------------------------------------------------

def shown(old: str | None, new: str | None) -> tuple[str | None, str | None]:
    """Old and new values as readable text, or as written when only the LaTeX differs, as
    when braces are added: "{SIC}" reads the same as "SIC", so plain text would hide it."""
    if old and new and bib.plain(old) == bib.plain(new):
        return f"`{old}`", f"`{new}`"
    return (bib.plain(old) if old else old), (bib.plain(new) if new else new)


def _cell(text: str | None) -> str:
    if text is None:
        return "*(none)*"
    return (text or "*(empty)*").replace("|", "\\|")[:300]


def _row(c: Change) -> str:
    old, new = shown(c.old, c.new)
    c = Change(c.field, old, new, c.kind, c.why, c.rename)
    if c.kind == "hide":
        before = _cell(c.old)
        after_text = f"hidden as `{c.rename}`"
    elif c.kind == "unhide":
        before = f"hidden as `{c.rename}`"
        after_text = _cell(c.new)
    elif c.kind == "remove":
        before, after_text = f"~~{_cell(c.old)}~~", "*(removed)*"
    else:
        before, after_text = _cell(c.old), _cell(c.new)
    why = "<br>".join(w.replace("|", "\\|") for w in c.why)
    return f"| `{c.field}` | {before} | {after_text} | {why} |"


def render(entries: list[tuple[bib.Reference, list[Change]]], inputs: list, today: str,
           corrected_name: str | None, links: dict, backup=None) -> str:
    """changes.md: every changed entry, data corrections first. `links` maps a sweep name to
    its report file, for the link back to the details. `backup` is set when --apply changed
    the bib itself."""
    corrected = [(r, cs) for r, cs in entries if any("verify" in c.sweeps for c in cs)]
    restyled = [(r, cs) for r, cs in entries if (r, cs) not in corrected]
    where = []
    if backup:
        where.append(f"These changes were applied to `{inputs[0]}`. The version before is in `{backup.name}`.")
    if corrected_name:
        where.append(f"They're in `{corrected_name}`.")
    if not where:
        where.append(f"Proposed only: `{inputs[0]}` wasn't changed. Run again with `--apply` to make "
                     "these changes to it, or `--write` for a corrected copy. The `.bib.diff` file "
                     "holds them as a patch.")
    if not entries:
        return "\n".join(["# Changes", "", f"{', '.join(inputs)} on {today}.", "", "Nothing to change.", ""])
    lines = ["# Changes", "", f"{', '.join(inputs)} on {today}. " + " ".join(where), ""]
    lines += ["| Group | Entries | Look at |", "|---|---:|---|"]
    if corrected:
        lines.append(f"| [Corrected from a registry](#corrected-from-a-registry) | {len(corrected)} | "
                     "Check each: these change what the reference says |")
    if restyled:
        lines.append(f"| [Restyled](#restyled) | {len(restyled)} | Skim: formatting only |")
    lines.append("")
    for heading, group in (("Corrected from a registry", corrected), ("Restyled", restyled)):
        if not group:
            continue
        lines += [f"## {heading}", ""]
        for ref, cs in group:
            title = bib.plain(ref.get("title"))[:120] or "(no title)"
            sweeps = list(dict.fromkeys(s for c in cs for s in c.sweeps))
            see = ", ".join(f"[{s}]({links[s]})" for s in sweeps if s in links)
            lines += [f"### {ref.key}", "", f"{title}" + (f" · details in {see}" if see else ""), "",
                      "| Field | Before | After | Why |", "|---|---|---|---|"]
            lines += [_row(c) for c in cs]
            lines.append("")
    return "\n".join(lines)


def unified_diff(original: str, corrected: str, name: str) -> str:
    """A patch from the original bib to the corrected one, to read or to apply."""
    a = original.splitlines(keepends=True)
    b = corrected.splitlines(keepends=True)
    if a and not a[-1].endswith("\n"):
        a[-1] += "\n"
    if b and not b[-1].endswith("\n"):
        b[-1] += "\n"
    return "".join(difflib.unified_diff(a, b, f"a/{name}", f"b/{name}", n=3))
