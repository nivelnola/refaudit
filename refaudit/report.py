"""Shared pieces of the reports: headings, the table up top, entry lines and DOI links."""
from __future__ import annotations

import re
from urllib.parse import quote

from . import bib
from .changes import shown
from .sweeps.base import Row

_DOI = re.compile(r"(?:https?://(?:dx\.)?doi\.org/)?(10\.\d{4,9}/[^\s\"{}|]+)", re.I)

TITLE_MATCH = ("*(title match)* means the entry has no DOI or other identifier, so it was found by "
               "searching for its title. Nothing is changed from these matches.")
NOT_FOUND = ("*Couldn't confirm* doesn't mean a reference is fake: tech reports, standards and very "
             "recent papers often aren't in any registry.")


def _doi_link(match: re.Match) -> str:
    doi = match.group(1).replace("\\", "")
    tail = ""
    # Sentence punctuation after a DOI isn't part of it. A ")" is, if it closes a "(" inside.
    while doi and (doi[-1] in ".,;:]`" or (doi[-1] == ")" and doi.count(")") > doi.count("("))):
        tail = doi[-1] + tail
        doi = doi[:-1]
    shown = re.sub(r"([_*\[\]<>])", r"\\\1", doi)
    return f"[{shown}](https://doi.org/{quote(doi, safe='/')}){tail}"


def link_dois(text: str) -> str:
    """Make every DOI a clickable link, leaving ``` code blocks and `code` alone."""
    parts = text.split("```")
    for i in range(0, len(parts), 2):
        pieces = parts[i].split("`")
        pieces[::2] = [_DOI.sub(_doi_link, piece) for piece in pieces[::2]]
        parts[i] = "`".join(pieces)
    return "```".join(parts)


def anchor(heading: str) -> str:
    """The link target GitHub and VS Code give a heading: lowercase, punctuation dropped,
    spaces to hyphens."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def label(ref, title_match: bool = False) -> str:
    title = bib.plain(ref.get("title")) or ref.raw or "(no title)"
    suffix = " *(title match)*" if title_match else ""
    return f"**{ref.key}**: {title[:150]}{suffix}"


def change_lines(ref, changes: list) -> list[str]:
    """One line per changed field, for a sweep's own report. changes.md has the full table."""
    lines = []
    for c in changes:
        old, new = shown(c.old, c.new)
        if c.kind == "hide":
            lines.append(f"  - `{c.field}` hidden as `{c.rename}` (was: {old})")
        elif c.kind == "unhide":
            lines.append(f"  - `{c.field}` shown again: {new}")
        elif c.kind == "remove":
            lines.append(f"  - `{c.field}` removed (was: {old})")
        elif not c.old:
            lines.append(f"  - `{c.field}` added: {new}")
        else:
            lines.append(f"  - `{c.field}`: {old} → {new}")
    return lines


class Report:
    """A report's sections in reading order, each with a row for the table up top."""

    def __init__(self, title: str, intro: list[str]) -> None:
        self.title = title
        self.intro = intro
        self.lines: list[str] = []
        self.rows: list[Row] = []

    def section(self, heading: str, todo: str, count: int | None = None, label: str | None = None,
                in_table: bool = True) -> None:
        if in_table:
            self.rows.append(Row(label or heading, anchor(heading), count, todo))
        self.lines += [f"## {heading}", ""]

    def add(self, *lines: str) -> None:
        self.lines.extend(lines)

    def text(self, after_table: list[str] = ()) -> str:
        table = ["| Section | Count | What to do |", "|---|---:|---|"]
        for row in self.rows:
            shown = "" if row.count is None else str(row.count)
            table.append(f"| [{row.label}](#{row.anchor}) | {shown} | {row.todo} |")
        top = [f"# {self.title}", "", *self.intro]
        body = top + ((table + [""]) if self.rows else []) + list(after_table) + self.lines
        return link_dois("\n".join(body).rstrip() + "\n")


def intro(ctx, what: str) -> list[str]:
    return [f"{what} {', '.join(p.name for p in ctx.inputs)} on {ctx.today.isoformat()}.", ""]


def checked_bib(ctx) -> list[str]:
    """For reports on the bib itself: which version of it they checked."""
    if getattr(ctx, "applied", False):
        return [f"This checked `{ctx.bib_path.name}` after the changes were applied, so it's what's left "
                "to fix. `changes.md` lists what was changed.", ""]
    return []


def summary(ctx, done: list) -> str:
    """summary.md: one table for every sweep this run, linking into each report."""
    lines = ["# Reference audit", "", *intro(ctx, "Checked"),
             "| Report | Section | Count | What to do |", "|---|---|---:|---|"]
    for sweep, result in done:
        file = f"{sweep.name}.md"
        if not result.rows:
            lines.append(f"| [{sweep.name}]({file}) | Nothing found | | Nothing |")
        for row in result.rows:
            shown = "" if row.count is None else str(row.count)
            lines.append(f"| [{sweep.name}]({file}) | [{row.label}]({file}#{row.anchor}) | {shown} | {row.todo} |")
    lines.append("")
    return link_dois("\n".join(lines))
