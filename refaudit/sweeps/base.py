"""What every sweep provides: its help, its flags, a run, and a report."""
from __future__ import annotations

from dataclasses import dataclass, field

BIB, BIB_AND_PDF, ANY = "bib", "bib+pdf", "bib|pdf"


@dataclass
class Row:
    """One line of summary.md's table."""
    label: str
    anchor: str      # heading anchor inside the sweep's report
    count: int | None
    todo: str


@dataclass
class Result:
    name: str
    data: object = None                      # sweep-specific: outcomes, issues, groups
    changes: dict = field(default_factory=dict)  # cite key -> list[Change] (verify and format)
    console: list = field(default_factory=list)  # lines for the terminal summary
    rows: list = field(default_factory=list)     # Row, filled in by render
    stats: dict = field(default_factory=dict)


class Sweep:
    name = ""
    summary = ""          # one line, for refaudit --help
    description = ""      # what it checks, for refaudit <name> --help
    needs = BIB
    cost = ""
    changes_bib = ""
    writes = ""
    config_file = ""
    examples: list = []   # (command, what it does)

    def add_arguments(self, parser) -> None:
        """This sweep's own flags."""

    def run(self, ctx, prior: dict) -> Result:
        raise NotImplementedError

    def render(self, result: Result, ctx) -> str:
        """The markdown for <name>.md. Also fills result.rows for summary.md."""
        raise NotImplementedError

    def missing_input(self, ctx) -> str | None:
        """Why this sweep can't run on these inputs, or None."""
        if self.needs == BIB and not ctx.bib_path:
            return "needs a .bib file"
        if self.needs == BIB_AND_PDF and not (ctx.bib_path and ctx.pdf_path):
            return "needs both a .bib file and a PDF"
        return None

    @property
    def inputs_text(self) -> str:
        return {BIB: "a .bib file", BIB_AND_PDF: "a .bib file and a PDF",
                ANY: "a .bib file, a PDF, or both"}[self.needs]
