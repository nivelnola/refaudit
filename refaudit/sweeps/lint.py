"""lint: is each bib entry sound on its own?"""
from __future__ import annotations

from .. import report, sanity
from .base import BIB, Result, Sweep


class Lint(Sweep):
    name = "lint"
    summary = "Is each entry sound on its own? Missing fields, bad values, BibTeX pitfalls"
    description = (
        "Checks each bib entry by itself, with no network. Nothing is changed.\n"
        "  - Repeated cite keys, missing required fields, empty fields\n"
        "  - Years that aren't years or are in the future, or disagree with the venue (NEWCAS'20)\n"
        "  - Pages with one hyphen or running backward, malformed DOIs, \"et al.\" written out\n"
        "  - Unescaped & % # _, and entries so malformed they can't be read at all\n"
        "  - Non-ASCII characters classic BibTeX mishandles (off with backend: biber)\n"
        "  - Capitals a style will lowercase unless braced: LTE, MIMO, WiFi\n"
        "  - Misspelled field names (jounral), and fields wrong for the entry type")
    needs = BIB
    cost = "None. Offline."
    changes_bib = "No. Report only."
    writes = "lint.md"
    config_file = "lint.yaml"
    examples = [("refaudit lint refs.bib", "quick check while writing")]

    def run(self, ctx, prior: dict) -> Result:
        cfg = ctx.cfg.lint
        fcfg = ctx.cfg.format
        issues = sanity.check_entries(ctx.bib_refs, ctx.today, cfg.checks, cfg.backend, fcfg.keep_capitalized,
                                      fcfg.capitals_ignore, fcfg.organization_words)
        issues = [(key, f"Line {line}: this entry couldn't be read ({why}), so nothing else checked it. "
                        "BibTeX will likely skip it too.") for key, line, why in ctx.unreadable] + issues
        result = Result(self.name, data=issues)
        used = [i for i in issues if i[0] not in ctx.unused_keys]
        result.console.append(f"Lint: {len(used)} problem(s) to fix" +
                              (f", plus {len(issues) - len(used)} in unused entries" if len(used) < len(issues) else "")
                              + ".")
        return result

    def render(self, result: Result, ctx) -> str:
        issues = result.data
        used = [i for i in issues if i[0] not in ctx.unused_keys]
        unused = [i for i in issues if i[0] in ctx.unused_keys]
        out = report.Report("Lint", report.intro(ctx, "Linted") + report.checked_bib(ctx))
        if not issues:
            out.add("No problems found.", "")
        if used:
            out.section("Problems", "Fix these in your bib", len(used))
            out.add("Problems in the bib itself. These aren't changed for you.", "")
            out.add(*[f"- **{key}**: {problem}" for key, problem in used], "")
        if unused:
            out.section("Unused references", "Fix these, or remove the entries", len(unused))
            out.add("Problems in entries the paper doesn't cite.", "")
            out.add(*[f"- **{key}**: {problem}" for key, problem in unused], "")
        result.rows = out.rows
        return out.text()
