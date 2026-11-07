> **This is a sample, not a real audit.** It's built from made-up entries so that it shows
> every kind of message this report can contain, which no real report would. Rebuild it with
> `python tests/make_sample_report.py`.

# Lint

Linted paper.bib, paper.pdf on 2026-11-07.

| Section | Count | What to do |
|---|---:|---|
| [Problems](#problems) | 31 | Fix these in your bib |
| [Unused references](#unused-references) | 2 | Fix these, or remove the entries |

## Problems

Problems in the bib itself. These aren't changed for you.

- **unreadable**: Line 18: this entry couldn't be read (unexpected text after a field, near "= {x}"), so nothing else checked it. BibTeX will likely skip it too.
- **missing_publisher**: Missing required field for @book: publisher.
- **empty_number**: The number field is empty.
- **year_not_a_year**: The year "in press" doesn't look like a year.
- **year_in_future**: The year 2031 is in the future.
- **venue_year**: The venue says "Proc. IEEE NEWCAS'20", but the entry's year is 2022.
- **single_hyphen**: The pages "1-9" use a single hyphen. BibTeX expects "--".
- **backward_pages**: The pages "1333--64" run backward or are abbreviated.
- **doi_prefix**: The doi field has a prefix. It should hold only the DOI: [10.1000/x](https://doi.org/10.1000/x)
- **doi_invalid**: The doi "not-a-doi" isn't a valid DOI.
- **et_al**: The author list has "et al." written out. BibTeX expects "and others".
- **comma_authors**: "Doe, John, Roe, Rita, Lee, Kim" looks like several names. Authors should be separated by "and", not commas.
- **unescaped**: The title field has unescaped % &, which can break LaTeX.
- **non_ascii**: The author field has non-ASCII characters (ö ü), which classic BibTeX can sort or capitalize wrongly. Write them as LaTeX, like {\"u} for ü, or set backend: biber in lint.yaml if you use biber.
- **capitals**: The title has capitals a BibTeX style may lowercase: MIMO, LTE, mmWave. Wrap each in braces, like {MIMO}, or set protect_capitals: add in your style.
- **misspelled**: Missing required field for @article: journal or journaltitle.
- **misspelled**: The field "jounral" looks like a misspelling of "journal". BibTeX ignores fields it doesn't know.
- **wrong_type**: Missing required field for @inproceedings: booktitle.
- **wrong_type**: @inproceedings entries take booktitle, not journal, so the venue may not be printed.
- **odd_verdict**: The title has capitals a BibTeX style may lowercase: API. Wrap each in braces, like {API}, or set protect_capitals: add in your style.
- **api_failed**: The title has capitals a BibTeX style may lowercase: API. Wrap each in braces, like {API}, or set protect_capitals: add in your style.
- **no_title**: Missing required field for @misc: title.
- **no_api_result**: The title has capitals a BibTeX style may lowercase: API. Wrap each in braces, like {API}, or set protect_capitals: add in your style.
- **preprint_id**: The title has capitals a BibTeX style may lowercase: DOI. Wrap each in braces, like {DOI}, or set protect_capitals: add in your style.
- **venue_conflicts**: The venue says "Proc. ACM INFOCOM'19", but the entry's year is 2020.
- **venue_no_year**: Missing required field for @inproceedings: year or date.
- **shown_again**: The title has capitals a BibTeX style may lowercase: DOI. Wrap each in braces, like {DOI}, or set protect_capitals: add in your style.
- **link_ok**: The title has capitals a BibTeX style may lowercase: GNU. Wrap each in braces, like {GNU}, or set protect_capitals: add in your style.
- **on_arxiv**: The title has capitals a BibTeX style may lowercase: arXiv. Wrap each in braces, like {arXiv}, or set protect_capitals: add in your style.
- **by_arxiv_id**: The title has capitals a BibTeX style may lowercase: arXiv, ID. Wrap each in braces, like {arXiv}, or set protect_capitals: add in your style.
- **repeated_key**: This cite key is used by 2 entries. BibTeX keeps only the first, so citations of the others point to the wrong work.

## Unused references

Problems in entries the paper doesn't cite.

- **unused_unchecked**: The pages "5-9" use a single hyphen. BibTeX expects "--".
- **unused_unchecked**: The title has capitals a BibTeX style may lowercase: PDF. Wrap each in braces, like {PDF}, or set protect_capitals: add in your style.
