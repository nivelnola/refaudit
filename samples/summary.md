> **This is a sample, not a real audit.** It's built from made-up entries so that it shows
> every kind of message this report can contain, which no real report would. Rebuild it with
> `python tests/make_sample_report.py`.

# Reference audit

Checked paper.bib, paper.pdf on 2026-11-07.

| Report | Section | Count | What to do |
|---|---|---:|---|
| [compare](compare.md) | [In the PDF but not the bib](compare.md#in-the-pdf-but-not-the-bib) | 1 | Add them to the bib, or check the PDF is current |
| [compare](compare.md) | [Printed differently](compare.md#printed-differently) | 2 | Recompile the PDF if it's out of date |
| [compare](compare.md) | [Unused references](compare.md#unused-references) | 2 | Remove any you don't need |
| [verify](verify.md) | [CHECK](verify.md#check-look-at-these-yourself) | 16 | Look at each one |
| [verify](verify.md) | [Broken links](verify.md#broken-links) | 2 | Fix or remove each link |
| [verify](verify.md) | [CORRECTED](verify.md#corrected-details-fixed-from-the-registry) | 1 | Check each change in changes.md |
| [verify](verify.md) | [LINK OK](verify.md#link-ok-the-page-exists-but-the-reference-isnt-verified) | 1 | Confirm each page is the right one |
| [verify](verify.md) | [OK](verify.md#ok-verified-nothing-to-correct) | 15 | Nothing |
| [verify](verify.md) | [Unused references](verify.md#unused-references) | 2 | Remove any you don't need |
| [verify](verify.md) | [API use](verify.md#api-use) |  | Nothing |
| [verify](verify.md) | [What this doesn't check](verify.md#what-this-doesnt-check) |  | Read once |
| [format](format.md) | [SKIPPED](format.md#left-as-written-not-verified) | 15 | Fix them using verify.md, then run again |
| [format](format.md) | [CHANGED](format.md#changed-restyled-in-the-corrected-bib) | 6 | Skim the changes |
| [format](format.md) | [Venues with no abbreviation](format.md#venues-with-no-abbreviation) | 2 | Add abbreviations if you want them |
| [format](format.md) | [Your abbreviations file took priority](format.md#your-abbreviations-file-took-priority) | 1 | Nothing, if that's what you wanted |
| [format](format.md) | [UNCHANGED](format.md#unchanged) | 13 | Nothing |
| [lint](lint.md) | [Problems](lint.md#problems) | 31 | Fix these in your bib |
| [lint](lint.md) | [Unused references](lint.md#unused-references) | 2 | Fix these, or remove the entries |
| [consistency](consistency.md) | [Possible duplicates](consistency.md#possible-duplicates) | 8 | Keep one entry for each work |
| [consistency](consistency.md) | [Venues written differently](consistency.md#venues-written-differently) | 1 | Pick one form for each venue |
| [consistency](consistency.md) | [Authors written differently](consistency.md#authors-written-differently) | 1 | Write each name the same way |
| [consistency](consistency.md) | [Fields missing from a few entries](consistency.md#fields-missing-from-a-few-entries) | 1 | Add the field, or ignore it |
| [consistency](consistency.md) | [Mixed conventions](consistency.md#mixed-conventions) | 2 | Make them match; a format style can do it |
