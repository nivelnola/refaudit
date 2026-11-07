> **This is a sample, not a real audit.** It's built from made-up entries so that it shows
> every kind of message this report can contain, which no real report would. Rebuild it with
> `python tests/make_sample_report.py`.

# Consistency

Compared the entries of paper.bib, paper.pdf on 2026-11-07.

| Section | Count | What to do |
|---|---:|---|
| [Possible duplicates](#possible-duplicates) | 8 | Keep one entry for each work |
| [Venues written differently](#venues-written-differently) | 1 | Pick one form for each venue |
| [Authors written differently](#authors-written-differently) | 1 | Write each name the same way |
| [Fields missing from a few entries](#fields-missing-from-a-few-entries) | 1 | Add the field, or ignore it |
| [Mixed conventions](#mixed-conventions) | 2 | Make them match; a format style can do it |

## Possible duplicates

These entries may be the same work cited more than once. If they are, keep one and point every citation to it.

- **same_doi_a**, **same_doi_b**: same DOI ([10.1000/same](https://doi.org/10.1000/same))
- **same_arxiv_a**, **same_arxiv_b**: same arXiv ID (2101.00009)
- **verified_a**, **verified_b**: both verified as the same work ([10.1000/verified](https://doi.org/10.1000/verified))
- **same_title_a**, **same_title_b**: same title
- **label_a**, **label_b**: same title, apart from a label or subtitle
- **nearly_a**, **nearly_b**: nearly the same title
- **versions_a**, **versions_b**: same first author, close years and mostly the same title words
- **three_a**, **three_b**, **three_c**: same title

## Venues written differently

Each group is one venue. The `venue_names` style setting can make them all the same.

- **INFOCOM**, written 2 ways:
  - "Proc. IEEE INFOCOM": versions_a
  - "INFOCOM": venue_a

## Authors written differently

Each group looks like one person. If it's really two, ignore it.

- **John Smith**:
  - "John Smith": venue_a
  - "J. Smith": venue_b

## Fields missing from a few entries

- **pages** in 4 of 5 conference entries. Missing from: no_pages

## Mixed conventions

- **title case** in conference entries: 4 title, but 1 sentence: venue_c
- **title case** in other entries: 6 title, but 4 sentence: same_title_b, nearly_a, nearly_b, three_b
