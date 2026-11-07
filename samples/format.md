> **This is a sample, not a real audit.** It's built from made-up entries so that it shows
> every kind of message this report can contain, which no real report would. Rebuild it with
> `python tests/make_sample_report.py`.

# Formatting

Formatted paper.bib, paper.pdf on 2026-11-07.

Style: **sample**. Left-out fields are hidden with the prefix `OPT`, so BibTeX ignores them and nothing is lost.

| Section | Count | What to do |
|---|---:|---|
| [SKIPPED](#left-as-written-not-verified) | 15 | Fix them using verify.md, then run again |
| [CHANGED](#changed-restyled-in-the-corrected-bib) | 6 | Skim the changes |
| [Venues with no abbreviation](#venues-with-no-abbreviation) | 2 | Add abbreviations if you want them |
| [Your abbreviations file took priority](#your-abbreviations-file-took-priority) | 1 | Nothing, if that's what you wanted |
| [UNCHANGED](#unchanged) | 13 | Nothing |

## Left as written: not verified

Verify marked these CHECK, so they weren't restyled. Set `skip_unverified: false` in format.yaml to restyle them anyway.

- **retracted**: A Retracted Study of Things
- **wrong_doi**: What the Paper Is Really Called
- **other_edition**: A Book About Things
- **other_version**: A Conference Paper Later in a Journal
- **ambiguous**: Learning Things
- **not_found_link_broken**: An Unindexed Technical Report
- **not_found_anywhere**: A Workshop Paper Nobody Indexed
- **odd_verdict**: A Paper the API Was Unsure How to Label
- **low_confidence**: A Paper Matched Only Loosely
- **title_match_conflict**: A Paper Found by Its Title
- **medium_confidence**: A Paper Matched With Medium Confidence
- **quota_used_up**: Checked After the Call Limit
- **api_failed**: Checked While the API Was Down
- **no_title**: (no title)
- **no_api_result**: An Entry the API Skipped

## CHANGED: restyled in the corrected bib

changes.md has each change with its before, after and reason.

- **fixed**: An Old Title for This Paper
  - `journal`: IEEE Transactions on Wireless Communications → TWC
  - `url` hidden as `OPTurl` (was: https://example.org/fixed)
  - `doi` added: [10.1000/fixed](https://doi.org/10.1000/fixed)
- **venue_no_year**: A Conference Paper With No Year
  - `booktitle`: GRCon → Proc. GRCon
  - Note: No sponsor is listed for GRCon, so none was added.
  - Note: This entry has no year, so none was added to the venue.
- **your_abbreviation**: A Paper Using Your Abbreviation
  - `journal`: IEEE Transactions on Wireless Communications → TWC
- **shown_again**: A Paper Whose DOI Was Hidden
  - `doi` shown again: [10.1000/hidden](https://doi.org/10.1000/hidden)
- **printed_differently**: A Paper Formatted Without Checking
  - `url` hidden as `OPTurl` (was: https://example.org/x)
- **unused_unchecked**: A Paper the PDF Never Cites (unused)
  - `url` hidden as `OPTurl` (was: https://example.org/unused)

## Venues with no abbreviation

These weren't changed. Fill in the abbreviation and paste the lines into your `abbreviations_file`:

```yaml
"Journal of Obscure Results": ABBREVIATION
"Journal of Things": ABBREVIATION
```

## Your abbreviations file took priority

These venues are in both your `abbreviations_file` and `venue_lists`. Your entry was used.

| Venue in your bib | Your file | Built-in list | References |
|---|---|---|---|
| IEEE Transactions on Wireless Communications | TWC | IEEE Trans. Wireless Commun. | fixed, your_abbreviation |

## Unchanged

- **venue_conflicts**: A Conference Paper With Venue Notes
  - Note: The venue lists give IEEE as the sponsor of INFOCOM, but your entry says ACM. Left as written.
- Also: longer_title, preprint_id, nothing_to_add, venue_not_listed, link_ok, link_broken, link_moved, on_arxiv, by_arxiv_id, preprint_match, ok, unused_checked
