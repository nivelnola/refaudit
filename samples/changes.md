> **This is a sample, not a real audit.** It's built from made-up entries so that it shows
> every kind of message this report can contain, which no real report would. Rebuild it with
> `python tests/make_sample_report.py`.

# Changes

paper.bib, paper.pdf on 2026-11-07. They're in `paper.corrected.bib`.

| Group | Entries | Look at |
|---|---:|---|
| [Corrected from a registry](#corrected-from-a-registry) | 1 | Check each: these change what the reference says |
| [Restyled](#restyled) | 5 | Skim: formatting only |

## Corrected from a registry

### fixed

An Old Title for This Paper · details in [verify](verify.md), [format](format.md)

| Field | Before | After | Why |
|---|---|---|---|
| `author` | Smith, J. and Dee, Cara | Smith, Jane and Dee, Cara and Roe, Rita | verify: Scholar Sidekick, high confidence |
| `title` | An Old Title for This Paper | The Right Title for This Paper | verify: Scholar Sidekick, high confidence |
| `journal` | IEEE Transactions on Wireless Communications | TWC | format: venue_names: abbreviated (style sample) |
| `year` | 2019 | 2020 | verify: Scholar Sidekick, high confidence |
| `volume` | 1 | 19 | verify: Scholar Sidekick, high confidence |
| `number` | 1 | 4 | verify: Scholar Sidekick, high confidence |
| `pages` | 1–5 | 2140–2152 | verify: Scholar Sidekick, high confidence |
| `url` | https://example.org/fixed | hidden as `OPTurl` | format: url: remove (style sample) |
| `doi` | *(none)* | [10.1000/fixed](https://doi.org/10.1000/fixed) | format: doi: add (style sample) |

## Restyled

### venue_no_year

A Conference Paper With No Year · details in [format](format.md)

| Field | Before | After | Why |
|---|---|---|---|
| `booktitle` | GRCon | Proc. GRCon | format: venue_year: add, venue_sponsor: add, venue_prefix: add (style sample) |

### your_abbreviation

A Paper Using Your Abbreviation · details in [format](format.md)

| Field | Before | After | Why |
|---|---|---|---|
| `journal` | IEEE Transactions on Wireless Communications | TWC | format: venue_names: abbreviated (style sample) |

### shown_again

A Paper Whose DOI Was Hidden · details in [format](format.md)

| Field | Before | After | Why |
|---|---|---|---|
| `doi` | hidden as `OPTdoi` | [10.1000/hidden](https://doi.org/10.1000/hidden) | format: doi: add (style sample) |

### printed_differently

A Paper Formatted Without Checking · details in [format](format.md)

| Field | Before | After | Why |
|---|---|---|---|
| `url` | https://example.org/x | hidden as `OPTurl` | format: url: remove (style sample) |

### unused_unchecked

A Paper the PDF Never Cites · details in [format](format.md)

| Field | Before | After | Why |
|---|---|---|---|
| `url` | https://example.org/unused | hidden as `OPTurl` | format: url: remove (style sample) |
