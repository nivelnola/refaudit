> **This is a sample, not a real audit.** It's built from made-up entries so that it shows
> every kind of message this report can contain, which no real report would. Rebuild it with
> `python tests/make_sample_report.py`.

# Bib and PDF compared

Compared paper.bib, paper.pdf on 2026-11-07.

| Section | Count | What to do |
|---|---:|---|
| [In the PDF but not the bib](#in-the-pdf-but-not-the-bib) | 1 | Add them to the bib, or check the PDF is current |
| [Printed differently](#printed-differently) | 2 | Recompile the PDF if it's out of date |
| [Unused references](#unused-references) | 2 | Remove any you don't need |

34 references appear in both. If the PDF was misread, fix the entry in `paper.references.bib` and run again.

## In the PDF but not the bib

These are printed in the paper, but no bib entry matches them. Verify still checks them, from the printed text.

- **[3]**: Jane Smith. 2019. A Paper Only in the PDF. Journal of Things. doi:[10.1000/pdf-only](https://doi.org/10.1000/pdf-only)

## Printed differently

These pairs differ, which can mean the PDF was compiled from an older bib:

- ok and [1]: the bib has year 2020, which isn't in the PDF's text: "Jane Smith. 2021. A Paper With Nothing to Change. Journal of Things."
- printed_differently and [2]: the titles differ. Bib: "A Paper Formatted Without Checking". PDF: "Jane Smith. 2020. A Paper Formatted Somehow Without Checking. Journal of Things."

## Unused references

These 2 bib entries aren't in the PDF's reference list. If many are listed, check the "Found N references in the PDF" line against the paper.

- **unused_checked**: An Uncited Paper Checked Earlier
- **unused_unchecked**: A Paper the PDF Never Cites
