"""compare: does the PDF's reference list match the bib?"""
from __future__ import annotations

from .. import bib, report
from .base import BIB_AND_PDF, Result, Sweep


class Compare(Sweep):
    name = "compare"
    summary = "Does the PDF's reference list match the bib?"
    description = (
        "Pairs each reference printed in the PDF with its bib entry, by identifier, title and "
        "year. Lists references printed in the PDF but missing from the bib, bib entries the "
        "paper doesn't cite (unused), and pairs whose year or title differ, which usually means "
        "the PDF was built from an older bib.\n\n"
        "In a full run, the unused list also tells verify which entries to skip, to save API calls.")
    needs = BIB_AND_PDF
    cost = "None. Offline."
    changes_bib = "No. Report only."
    writes = "compare.md"
    config_file = "none"
    examples = [("refaudit compare refs.bib paper.pdf", "check the PDF was built from this bib")]

    def run(self, ctx, prior: dict) -> Result:
        cross = ctx.cross
        result = Result(self.name, data=cross)
        result.console.append(f"Compare: {len(cross.pairs)} in both, {len(cross.pdf_only)} only in the "
                              f"PDF, {len(cross.bib_only)} only in the bib"
                              + (f", {len(cross.differences)} differ" if cross.differences else ""))
        return result

    def render(self, result: Result, ctx) -> str:
        cross = result.data
        out = report.Report("Bib and PDF compared", report.intro(ctx, "Compared"))
        out.add(f"{len(cross.pairs)} references appear in both. If the PDF was misread, fix the entry in "
                f"`{ctx.pdf_path.stem}.references.bib` and run again.", "")
        if cross.pdf_only:
            out.section("In the PDF but not the bib", "Add them to the bib, or check the PDF is current",
                        len(cross.pdf_only))
            out.add("These are printed in the paper, but no bib entry matches them. Verify still "
                    "checks them, from the printed text.", "")
            out.add(*[f"- **{r.key}**: {r.raw[:200]}" for r in cross.pdf_only], "")
        if cross.differences:
            out.section("Printed differently", "Recompile the PDF if it's out of date", len(cross.differences))
            out.add("These pairs differ, which can mean the PDF was compiled from an older bib:", "")
            out.add(*[f"- {d}" for d in cross.differences], "")
        if cross.bib_only:
            out.section("Unused references", "Remove any you don't need", len(cross.bib_only))
            out.add(f"These {len(cross.bib_only)} bib entries aren't in the PDF's reference list. If "
                    "many are listed, check the \"Found N references in the PDF\" line against the paper.", "")
            by_key = {r.key: r for r in ctx.bib_refs}
            out.add(*[f"- **{k}**: {bib.plain(by_key[k].get('title'))[:150]}" for k in cross.bib_only], "")
        result.rows = out.rows
        return out.text()
