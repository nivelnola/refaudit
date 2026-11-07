> **This is a sample, not a real audit.** It's built from made-up entries so that it shows
> every kind of message this report can contain, which no real report would. Rebuild it with
> `python tests/make_sample_report.py`.

# Verification

Verified paper.bib, paper.pdf on 2026-11-07.

**The paper uses 34 of the bib's 36 entries.** This report is about those. The other 2 are listed under [Unused references](#unused-references).

No API key was found in `.env`, so this ran anonymously at lower rate limits.

| Section | Count | What to do |
|---|---:|---|
| [CHECK](#check-look-at-these-yourself) | 16 | Look at each one |
| [Broken links](#broken-links) | 2 | Fix or remove each link |
| [CORRECTED](#corrected-details-fixed-from-the-registry) | 1 | Check each change in changes.md |
| [LINK OK](#link-ok-the-page-exists-but-the-reference-isnt-verified) | 1 | Confirm each page is the right one |
| [OK](#ok-verified-nothing-to-correct) | 15 | Nothing |
| [Unused references](#unused-references) | 2 | Remove any you don't need |
| [API use](#api-use) |  | Nothing |
| [What this doesn't check](#what-this-doesnt-check) |  | Read once |

*(title match)* means the entry has no DOI or other identifier, so it was found by searching for its title. Nothing is changed from these matches.

## CHECK: look at these yourself

Nothing in these was corrected. Format leaves them as written too, unless `skip_unverified` is false in format.yaml.

*Couldn't confirm* doesn't mean a reference is fake: tech reports, standards and very recent papers often aren't in any registry.

- **retracted**: A Retracted Study of Things
  - Retracted, according to Crossref and Retraction Watch.
  - Has an expression of concern.
  - arXiv's comment on this paper mentions that it was withdrawn.
- **wrong_doi**: What the Paper Is Really Called
  - Possibly wrong or fabricated: the identifier points to a work with a different title.
  - title: you have "What the Paper Is Really Called", the registry has "Something Else Entirely".
- **other_edition**: A Book About Things
  - The details differ from the registry's, so nothing was changed. For a book, this may just be a different edition or printing.
  - year: you have "2015", the registry has "2009".
- **other_version**: A Conference Paper Later in a Journal
  - The details differ from the registry's, so nothing was changed. This may be another version of the same work.
  - container: you have "Journal of Things", the registry has "Proc. Things Conference".
- **ambiguous**: Learning Things
  - Couldn't tell whether this is the right work. It may be another version, or the identifier may be wrong.
  - Note: Possible match: "Learning Things, Part I" (2020), [10.1000/part1](https://doi.org/10.1000/part1)
  - Note: Possible match: "Learning Things, Part II" (2020), [10.1000/part2](https://doi.org/10.1000/part2)
- **not_found_link_broken**: An Unindexed Technical Report
  - Couldn't confirm: not found in Crossref, PubMed or OpenAlex.
  - The link is broken (HTTP 404): https://example.org/gone
  - Note: Possible match: "An Unindexed Technical Report (Draft)" (2020), Journal of Things
- **not_found_anywhere**: A Workshop Paper Nobody Indexed
  - Couldn't confirm: not found in Crossref, PubMed, OpenAlex or arXiv.
- **odd_verdict**: A Paper the API Was Unsure How to Label
  - Unexpected API verdict: unclassifiable.
- **low_confidence**: A Paper Matched Only Loosely
  - Matched, but only with low confidence.
- **title_match_conflict**: A Paper Found by Its Title *(title match)*
  - The details differ from the registry's, so nothing was changed. This may be another version of the same work.
  - year: you have "2018", the registry has "2019".
  - Note: Possible DOI, unverified: [10.1000/found](https://doi.org/10.1000/found)
  - Note: The registry also has number 4.
- **medium_confidence**: A Paper Matched With Medium Confidence
  - The details differ from the registry's, so nothing was changed. The match's confidence (medium) is below what auto_correct (high) needs. A one-year gap is often the online versus print date.
  - year: you have "2019", the registry has "2020".
- **quota_used_up**: Checked After the Call Limit
  - Not checked: max_calls_per_run reached.
- **api_failed**: Checked While the API Was Down
  - Not checked: API returned HTTP 500.
- **no_title**: (no title)
  - Not checked: this entry has no title.
- **no_api_result**: An Entry the API Skipped
  - Not checked: the API returned no result.
- **[3]**: A Paper Only in the PDF
  - year: the PDF has "2019", the registry has "2020".

## Broken links

These references are verified, but their link no longer works or now goes somewhere else.

- **link_broken**: The link is broken (HTTP 404): https://example.org/dead
- **link_moved**: The link now goes to another site: https://old.example.org/paper → https://new.example.org/paper

## CORRECTED: details fixed from the registry

- **fixed**: An Old Title for This Paper
  - `author`: Smith, J. and Dee, Cara → Smith, Jane and Dee, Cara and Roe, Rita
  - `number`: 1 → 4
  - `pages`: 1–5 → 2140–2152
  - `title`: An Old Title for This Paper → The Right Title for This Paper
  - `volume`: 1 → 19
  - `year`: 2019 → 2020

## LINK OK: the page exists, but the reference isn't verified

- **link_ok**: GNU Radio Software
  - Link: https://www.gnuradio.org/

## OK: verified, nothing to correct

- **longer_title**: A Short Title
  - Note: A correction has been published for this work.
  - Note: The registry's title is longer or shorter than yours: "A Short Title: And Its Subtitle". Left as written.
  - Note: Venue differs: you have "Journal of Things", the registry has "Annals of Stuff".
- **preprint_id**: A Published Paper With a Preprint DOI
  - Note: The identifier points to a preprint, but this entry isn't cited as one. Year, volume and pages weren't compared.
- **venue_conflicts**: A Conference Paper With Venue Notes
- **venue_no_year**: A Conference Paper With No Year
- **nothing_to_add**: A Journal Paper Found in Crossref *(title match)*
  - Note: Found by searching Crossref directly: [10.1000/crossref](https://doi.org/10.1000/crossref). Scholar Sidekick's search missed it.
- **venue_not_listed**: A Paper in an Unlisted Journal
- **your_abbreviation**: A Paper Using Your Abbreviation
- **shown_again**: A Paper Whose DOI Was Hidden
- **link_broken**: A Verified Paper With a Dead Link
  - Note: The link is broken (HTTP 404): https://example.org/dead
- **link_moved**: A Verified Paper Whose Link Moved
  - Note: The link now goes to another site: https://old.example.org/paper → https://new.example.org/paper
- **on_arxiv**: A Preprint Found on arXiv *(title match)*
  - Note: Found on arXiv as 2101.00003, though not in Crossref, PubMed or OpenAlex. Its details weren't corrected from this match.
  - Note: arXiv lists a published version: IEEE Trans. Wireless Commun. 21 (2022), [10.1000/published](https://doi.org/10.1000/published). Consider citing it.
- **by_arxiv_id**: A Preprint Checked by Its arXiv ID
  - Note: arXiv lists a published version: Proc. Things Conference 2023. Consider citing it.
- **preprint_match**: A Paper Matched to Its Preprint *(title match)*
  - Note: The match is a preprint, so the venue you cite wasn't confirmed.
  - Note: Possible DOI, unverified: [10.1000/journal-version](https://doi.org/10.1000/journal-version)
- **ok**: A Paper With Nothing to Change
- **printed_differently**: A Paper Formatted Without Checking

## Unused references

These 2 bib entries aren't in the PDF's reference list.

*Not checked* means no API call was spent on it. Set `check_unused: true` in verify.yaml to verify these too.

- **unused_unchecked**: A Paper the PDF Never Cites (Not checked)
- **unused_checked**: An Uncited Paper Checked Earlier (OK)

## API use

- Scholar Sidekick calls this run: 2
- Free lookups this run (Crossref, arXiv and links): 14
- Checked this run by: arXiv, by ID 1, Crossref, by DOI 5, Scholar Sidekick 21
- Reused from earlier runs: 3 references, saving 1 call (one call checks up to 25)
- Oldest stored check: 2026-06-01. Run with `--recheck` before submitting to catch new retractions.

## What this doesn't check

This tool checks that each reference points to a real work with correct details. It doesn't check that a source supports what you cite it for. It's also only as good as the registries behind it, so skim the corrections.
