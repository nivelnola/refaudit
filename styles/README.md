# Styles

A style is a file of rules that `refaudit format` applies to your bib: title case, venue names, authors, and which fields to show. This folder holds the built-in styles:

| Style | What it does |
|---|---|
| `default` | Changes nothing. Copy it to start your own |
| `ieee` | A starting point for IEEE venues (IEEEtran) |
| `acm` | A starting point for ACM venues (ACM-Reference-Format) |
| `wimnet` | The WiMNet group style, from the group's reference guidelines: braced acronyms, conference names as `Proc. ACM MobiCom'07`, pages and no month for journals, no DOIs |

`ieee` and `acm` are starting points, not official styles. Rules vary between venues, even within IEEE, so check them against your venue's author guide.

## A style changes the bib, not the printed bibliography

When LaTeX builds the bibliography, the venue's bibliography style (`IEEEtran.bst`, `ACM-Reference-Format.bst`) styles each entry again. It sets punctuation and order, decides which fields print, and may abbreviate names or change capitalization. A `.bst` can go further than any rule here, or undo one: many styles lowercase titles, whatever `title_case` says, unless words are braced. Always check the compiled PDF. `refaudit lint` warns about capitals a style may lowercase.

## Choosing a style

- In `refaudit_config/format.yaml`: `style: ieee`
- For one run: `refaudit format refs.bib --style ieee`
- A file anywhere: `--style path/to/my_style.yaml`

A name is looked up in `refaudit_config/styles/` first, then in this folder. So a file in your config folder named `ieee.yaml` replaces the built-in one. An unknown name stops the run and lists the styles there are.

A run only proposes changes: you get `format.md`, `changes.md` with every change before and after, and `<name>.bib.diff`. Add `--apply` to make them in your bib, or `--write` for a corrected copy.

## Making your own

1. Copy `default.yaml` to `refaudit_config/styles/my_venue.yaml`.
2. Change only what your venue needs. Everything left at `keep` is untouched.
3. Run `refaudit format refs.bib --style my_venue` and read `changes.md`. Your bib isn't changed yet.
4. Run it again with `--apply`, compile the paper, and compare the bibliography with your venue's examples.
5. Set `style: my_venue` in `format.yaml`.

## File layout

A style has an `all` section and optional type sections. `all` applies to every reference. A type section overrides `all` for that type, so list only what differs:

```yaml
all:
  title_case: sentence
  authors: initials

conference:
  venue_names: abbreviated
  pages: remove

journal:
  venue_names: abbreviated
  pages: add
```

Here a conference paper gets sentence case, initials, an abbreviated venue and hidden pages. A journal paper gets the same, but with pages shown or filled in.

### Reference types

| Section | BibTeX entries |
|---|---|
| `all` | Every reference, unless a section below overrides it |
| `preprint` | Any entry with an arXiv ID and no real journal or conference (checked first) |
| `conference` | `@inproceedings`, `@conference` |
| `journal` | `@article` |
| `book` | `@book`, `@inbook`, `@incollection` |
| `techreport` | `@techreport`, `@manual`, `@standard` |
| `other` | Everything else, such as `@misc` and `@phdthesis` |

## Rules

| Setting | Value | What it does |
|---|---|---|
| `title_case` | `keep` | Default. Leaves titles alone. With either setting below, braces around a whole title, as in `{{My Title}}`, are kept but don't stop the conversion |
| | `sentence` | "Attention is all you need" |
| | `title` | "Attention Is All You Need" |
| `protect_capitals` | `keep` | Default. Adds no braces |
| | `add` | Braces words a bibliography style would otherwise lowercase: `Massive MIMO for 5G` becomes `Massive {MIMO} for {5G}`. See [Protecting capitals](#protecting-capitals) |
| `venue_names` | `keep` | Default. Leaves venue names alone |
| | `abbreviated` | "NeurIPS" |
| | `full` | "Advances in Neural Information Processing Systems" |
| `venue_year` | `keep` | Default. Conferences only, like the two below. Leaves the year in the venue name alone, including one you wrote that shortening the name would drop |
| | `add` | `Proc. GRCon` becomes `Proc. GRCon'24`, using the entry's own year. If the name already has a different year, it's left alone and `lint` reports it |
| | `remove` | `Proc. GRCon'24` becomes `Proc. GRCon` |
| `venue_sponsor` | `keep` | Default. Leaves the sponsor alone, including one you wrote that shortening the name would drop |
| | `add` | `INFOCOM` becomes `IEEE INFOCOM`, only if the sponsor is in the venue lists or your entry already named it. Otherwise it's left alone and noted |
| | `remove` | `IEEE INFOCOM` becomes `INFOCOM` |
| `venue_prefix` | `keep` | Default. Leaves any "Proc." alone. If shortening the name would drop the one you wrote, it's kept as "Proc.", so "Proceedings of the" becomes "Proc." |
| | `add` | `GRCon` becomes `Proc. GRCon` |
| | `remove` | `Proc. GRCon` becomes `GRCon` |
| `doi` | `keep` | Default. Leaves DOIs as they are, including hidden ones |
| | `remove` | Hides the DOI (kept as `OPTdoi`) |
| | `add` | Shows a hidden DOI, or fills in a missing one from the verified record |
| `pages` | `keep` | Default. Leaves pages as they are |
| | `remove` | Hides the page range |
| | `add` | Shows hidden pages, or fills in missing ones from the verified record |
| `url` | `keep` | Default. Leaves URLs alone |
| | `remove` | Hides the URL |
| | `remove_if_doi` | Hides the URL only if the entry still shows a DOI |
| | `show` | Shows a hidden URL |
| `authors` | `keep` | Default. Leaves author names alone |
| | `initials` | "Smith, J. K." |
| `month` | `keep` | Default. Leaves the month alone |
| | `remove` | Hides the month |
| | `show` | Shows a hidden month |
| `remove_fields` | a list | Hides these fields, such as `[abstract, keywords, file]` |
| `show_fields` | a list | Shows these fields if they're hidden, such as `[note]` |

## The order rules run in

Each rule sees the result of the ones before it:

1. Hidden fields a rule asks to show (`add`, `show`, `show_fields`) are shown again
2. `title_case`
3. `protect_capitals`
4. `venue_names`
5. `venue_year`, `venue_sponsor`, `venue_prefix` (conferences only)
6. `doi`
7. `pages`
8. `url`
9. `authors`
10. `month`
11. `remove_fields`

Two things follow from the order:

- The `venue_` settings work on the name `venue_names` produced, so the result is the same whatever form the name started in.
- `url: remove_if_doi` looks at the DOI after the `doi` rule. With `doi: remove`, the DOI is already hidden, so the URL stays. That's deliberate: a reference keeps at least one way to find it.

## How the venue settings combine

`venue_names` picks the name. The three `venue_` settings then add, keep or remove its parts, on conference entries only. `keep` keeps what you wrote, even when the new name doesn't have it, so to get a bare abbreviation, use `remove`. Starting from `booktitle = {Proceedings of the 2013 IEEE Conference on Computer Communications}`, `year = {2013}`:

| Style | Result |
|---|---|
| `venue_names: abbreviated` | `Proc. IEEE INFOCOM 2013`: what you wrote is kept |
| the same, plus `venue_sponsor: remove` | `Proc. INFOCOM 2013` |
| the same, with all three on `remove` | `INFOCOM` |
| `venue_names: abbreviated`, plus `venue_year: add` | `Proc. IEEE INFOCOM'13` |
| `venue_names: full` | `IEEE International Conference on Computer Communications` |

On a name with none of the parts, like `INFOCOM`, all three on `add` give `Proc. IEEE INFOCOM'13`. The year comes from the entry's own `year`, or the one you wrote in the venue name. The sponsor comes from the venue lists, or from the name as you wrote it. `Proc. of the 30th Int. Conf. on Machine Learning` with all three on `add` gives `Proc. ICML'13`, with a note that no sponsor is listed for ICML.

With `add`, the `venue_` settings apply only to short names, because a long name usually already says who sponsors it.

## Hidden fields

When a style leaves a field out, it's renamed with `hidden_prefix` from `format.yaml`: `doi` becomes `OPTdoi`. BibTeX and biber ignore fields they don't know, so the bibliography looks the same as if the field were deleted, but nothing is lost:

```bibtex
  doi = {10.1145/1},        % before, with doi: remove
  OPTdoi = {10.1145/1},     % after
```

- `keep` leaves a hidden field hidden. That way fields you hid yourself, as Emacs and JabRef users often do, stay hidden.
- `add` (for `doi` and `pages`), `show` (for `url` and `month`) and `show_fields` show a hidden field again, with its original value. For `doi` and `pages`, your hidden value is preferred over the registry's.
- Hidden fields still identify the work. `verify` uses a hidden DOI or arXiv ID, and hiding a field never costs a new check.
- `hidden_prefix: null` deletes fields instead.

## Filling in details

`doi: add` and `pages: add` fill gaps only from a record found through the entry's own DOI or arXiv ID. That record comes from `verify`, in the same run or stored in the ledger from an earlier one, so `format` alone works offline. A title-only match is never used, since it may be another version of the work, such as the preprint.

When `verify` runs in the same command, entries it marks CHECK are left exactly as written, unless `skip_unverified: false` is set in `format.yaml`.

## Title case

`sentence` and `title` leave alone:

- `{braced}` words, and braces around the whole title
- ALLCAPS words (`MIMO`), and words with a capital after the first letter (`ResNet`, `mmWave`)
- lone capitals (`N-Path`, `Part I`) and capitals mixed with digits (`5G`, `3D`)
- `\commands` and `$math$`
- anything in `keep_capitalized` in `format.yaml`, such as `Zadoff-Chu` or `GNU Radio`

Proper nouns can't be detected automatically, so `Bayesian` stays capitalized only because it's in `keep_capitalized`. Check title changes in `changes.md`.

Sentence case still capitalizes the first word after a colon: "A study: The model".

## Protecting capitals

`title_case` changes the bib. But many bibliography styles (`plain`, ACM's, APA's) also recase titles when LaTeX builds the bibliography, and turn "MIMO" into "mimo" unless it's in braces. IEEEtran doesn't.

`protect_capitals: add` braces what a style would lowercase:

- words with a capital after their first character: `MIMO`, `LTE`, `mmWave`, `WiFi`, `5G`
- everything in `keep_capitalized` in `format.yaml`, as whole phrases: `{Bayesian}`, `{GNU Radio}`, `{Zadoff-Chu}`

It runs after `title_case`, and leaves alone a title that's braced as a whole, words already in braces, `\commands` and `$math$`. `Massive MIMO for 5G with Bayesian Priors` becomes `Massive {MIMO} for {5G} with {Bayesian} Priors`.

`refaudit lint` uses the same rules to warn about these words (the `unprotected_capitals` check), so a style with `protect_capitals: add` leaves it nothing to report. Proper nouns can't be detected automatically: add yours to `keep_capitalized`, and both will cover them.

## Venue lists

Venue names are looked up in the files in `../venues/`, chosen with `venue_lists` in `format.yaml`. Each line is `"Full or alternate name": Abbreviation`, and alternate names get their own line:

```yaml
sponsors: [IEEE, ACM, USENIX]

"Advances in Neural Information Processing Systems": NeurIPS
"Neural Information Processing Systems": NeurIPS
```

The first line for an abbreviation is the name `venue_names: full` writes.

- **Adding a field:** drop a new file in `venues/` and add its name to `venue_lists`.
- **Your own list:** use the same format and point `abbreviations_file` at it. Your entries supersede `venue_lists`, including every other built-in name for that venue. If you redefine ICML, "Proc. ICML" follows too. `format.md` lists each reference where your entry replaced a built-in one.
- **Missing venues:** when `venue_names` isn't `keep`, venues not on any list are left unchanged and listed in `format.md`, with a line ready to paste into your file.
- **Sponsors:** each venue file starts with a `sponsors:` line naming the organizations that sponsor venues in its field, such as `sponsors: [IEEE, ACM, USENIX]`. Joint forms of them, like `IEEE/ACM` or `ACM-SIAM`, are understood too. The sponsors of the files in `venue_lists` are combined with any in your `abbreviations_file`; if none declares any, IEEE, ACM and USENIX are used. A venue's own sponsor is the one that starts any of its listed names, so to teach the tool one, start a name with it; for example, if IEEE sponsors GNU Radio Conference, it will be written as `"IEEE GNU Radio Conference": GRCon`.

Matching ignores case, years, ordinals, parentheses and wording like "Proceedings of the", and understands common short forms ("Proc. of the 40th Int. Conf. on Machine Learning" is ICML). It doesn't guess at similar names.

The built-in lists were written by hand and haven't been checked against an official source. Journal abbreviations follow the ISO 4 pattern most publishers use, and conference acronyms follow common usage, which has no formal standard. Check them against your venue's style.
