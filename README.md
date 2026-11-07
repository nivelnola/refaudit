# refaudit

Audits a paper's references. Give it a `.bib`, a PDF, or both:

```
refaudit refs.bib paper.pdf
```

That runs every check. Each check is also its own command, so you can run just the one you need:

```
refaudit lint refs.bib                    # quick offline check while writing
refaudit verify refs.bib paper.pdf        # are the references real, with the right details?
refaudit format refs.bib --style ieee     # restyle for a venue
```

If Windows says `refaudit` isn't recognized, use `python -m refaudit`, which always works.

## Install

You need Python 3.10 or newer and git.

1. Get the code and go into its folder:
   ```
   git clone <repository URL> refaudit
   cd refaudit
   ```
2. Install it, ideally in a virtual environment so it doesn't touch your other Python packages:
   ```
   python -m venv .venv
   .venv\Scripts\activate          # Windows
   source .venv/bin/activate       # macOS and Linux
   pip install -e .
   ```
   `-e` installs it from this folder, so a `git pull` updates it without reinstalling.
3. Copy `.env.example` to `.env` and paste in your Scholar Sidekick key. Get a free key at [scholar-sidekick.com/account](https://scholar-sidekick.com/account). `.env` must stay in this folder. It's git-ignored, and it's the only place the key is read from. Only `verify` uses it. Without a key, `verify` still runs, at lower rate limits.
4. Check it works: `refaudit --help`. Optional: edit the files in `refaudit_config/`.

To update: `git pull` in this folder.

To uninstall: `pip uninstall refaudit`, then delete this folder (and `.venv` if you made one). Nothing is installed anywhere else. Your output folders, named `refaudit/` next to your papers, are yours to keep or delete.

## Commands

| Command | What it checks | Needs | Network and cost | Changes your bib? |
|---|---|---|---|---|
| `full` | Everything below that the inputs allow. What runs when you don't name a command | a bib, a PDF, or both | as `verify` | as `verify` and `format` |
| `verify` | Is each reference real, with correct details and working links? | a bib, a PDF, or both | free for most entries with a DOI or arXiv ID; Scholar Sidekick API calls for the rest | only details a registry confirms |
| `format` | Does each entry follow your citation style? | a bib | none | yes, restyled |
| `lint` | Is each entry sound on its own? | a bib | none | no |
| `consistency` | Do the entries hold together? Duplicates, naming, missing fields | a bib, a PDF, or both | none | no |
| `compare` | Does the PDF's reference list match the bib? | a bib and a PDF | none | no |

`refaudit --help` lists them. `refaudit verify --help` (or any command's `--help`) says exactly what that command checks, what it costs, what it writes, which config file it reads, and gives examples.

A typical paper: `lint` and `consistency` while writing, since they're free and instant. `verify` before submitting. `format --style ieee` to see what a venue needs, then `format --style ieee --apply` for the camera-ready.

In a `full` run, a check the inputs don't allow is skipped with a note: with only a bib, `compare` is skipped. Run on its own, a command without its inputs stops and says what it needs.

`lint` and `consistency` check your bib as it is on disk. In a `full` run with `--apply`, that's after the changes, so their reports list what's left to fix.

### Flags

| Flag | Commands | What it does |
|---|---|---|
| `--config` DIR | all | Uses a specific config folder |
| `--out` FOLDER | all | Writes the output somewhere else |
| `--help` | all | Explains the command |
| `--dry-run` | `verify`, `full` | Shows how many API calls a run would use, without spending any or writing anything |
| `--recheck` | `verify`, `full` | Ignores stored results and checks everything again. Worth running once before submitting |
| `--style` NAME | `format`, `full` | Uses this style instead of the one in `format.yaml`: a name from `styles/`, or a path to a `.yaml` file |
| `--write` | `verify`, `format`, `full` | Also writes `<name>.corrected.bib`, a corrected copy, in the output folder |
| `--apply` | `verify`, `format`, `full` | Makes the changes in your bib itself, keeping the version before as `<name>.bib.bak` next to it |
| `--only` SECTIONS | `consistency`, `full` | Runs only these consistency sections, comma-separated, like `--only duplicates` |

## What you get

A `refaudit/` folder next to your input (or wherever `--out` says). Each command writes its own report, so a `lint` run doesn't overwrite your last `verify` report.

**Your bib isn't changed unless you ask.** `verify` and `format` propose their changes in `changes.md` and `<name>.bib.diff`. Add `--write` for a corrected copy, or `--apply` to make the changes in your bib itself. `--apply` keeps the version before as `<name>.bib.bak`, and writes the bib back in its own encoding and line endings.

| File | What it is |
|---|---|
| `summary.md` | When several checks ran: one table of every section, with its count, what to do, and a link to it |
| `verify.md`, `format.md`, `lint.md`, `consistency.md`, `compare.md` | Each command's report, with a table at the top linking each section to its count and what to do |
| `changes.md` | Every change to your bib, as a table of **Before**, **After** and **Why** for each entry. Corrections from a registry come first, then restyling |
| `<name>.corrected.bib` | With `--write`: a corrected copy of your bib |
| `<name>.bib.diff` | The same changes as a patch. Open it in VS Code to see them side by side, or apply it yourself with `git apply refs.bib.diff`, run in the bib's folder |
| `<pdf name>.references.bib` | The references read from the PDF, so the PDF is read only once. If one was misread, fix it here. See [Reading the PDF](#reading-the-pdf) |
| `ledger.json` | What's been verified, for the tool to read. Keep it, and share it with co-authors, so nobody pays to check the same reference twice |

[samples/](samples/) has one sample of each report, showing every kind of message it can contain.

Every *Why* names the check and the rule behind the change, such as `verify: Crossref, by DOI, high confidence` or `format: title_case: sentence (style ieee)`. When `verify` corrects a value and `format` then restyles it, both are listed.

## verify

Every reference gets one result:

| Result | Meaning | What you do |
|---|---|---|
| **OK** | Verified, nothing to correct | Nothing |
| **CORRECTED** | Verified, and the registry's details are proposed in `changes.md` | Check each change |
| **CHECK** | Possibly fake, retracted, or not found, or the check failed. The report says why | Look at it yourself |
| **LINK OK** | Not in any index, but its URL works | Confirm it's the right page |

LINK OK means the page exists, not that the reference is accurate. CHECK entries are left exactly as written, including their formatting, unless you set `skip_unverified: false`. Unused entries that weren't verified show **Not checked**.

While it runs, the terminal shows each step, with a progress bar and count, and a message if it's waiting on a rate limit. When the output goes to a file, it prints plain lines instead of a bar.

### Results can change between runs

Scholar Sidekick's answer for the same reference can differ from one day to the next: a reference it matched last week may come back "not found" today, or the other way round. That's the service, not your bib. Three things limit it:

- **Stored results are reused.** Once a reference is checked, the ledger keeps the answer for `recheck_after_days`, so its verdict doesn't flicker between runs.
- **Free sources back it up.** An entry with a DOI or arXiv ID is checked against Crossref or arXiv directly, without Scholar Sidekick. Anything it can't find gets a Crossref and an arXiv title search too.
- **Add identifiers.** An entry with a DOI or arXiv ID gets a stable, exact answer. One found only by title search depends on search results, which change.

### How references are looked up

Cheapest and most certain first:

1. **Free, by identifier.** An entry with a DOI is looked up in Crossref, which also reports retractions, expressions of concern and corrections (from Retraction Watch). An entry with an arXiv ID is looked up on arXiv, up to 50 IDs per request. A match by identifier is as certain as a lookup gets, and costs nothing.
2. **Scholar Sidekick**, for whatever step 1 couldn't settle: entries with no identifier, DOIs Crossref doesn't hold (DataCite DOIs, such as Zenodo's), and identifiers that point to a different work, since that deserves a second opinion before it's called fabricated. References are sent in batches of 25, with the ledger saved after each batch, so an interrupted run keeps what it paid for.
3. **Free second checks** for anything still not found: a Crossref title search, an arXiv title search, and the entry's link. See below.
4. **Links.** Every other entry's link is checked too, for free.

Set `sidekick_confirms_all: true` to send everything to Scholar Sidekick anyway. It costs more, and adds PubMed and OpenAlex retraction data, which matters most for biomedical papers.

`verify.md` says which source settled how many references.

### Second checks: Crossref and arXiv

Scholar Sidekick's title search can miss real papers, such as a title with a `Demo:` label or a book cited with its subtitle. When it finds nothing, the tool searches Crossref directly. A match needs the same title (a label or subtitle may differ), the same first author and a year within one, and it becomes OK with a note giving the DOI.

If that finds nothing, the tool asks arXiv's free API, by title. arXiv allows one request every 3 seconds, so this adds about 3 seconds per not-found reference, once. Results go in the ledger.

- **Found, with a closely matching title and the same first author:** OK, with a note giving the arXiv ID and any published version arXiv lists.
- **The entry's arXiv ID points to a different title:** CHECK, possibly wrong or fabricated.
- **Not on arXiv:** CHECK, couldn't confirm.
- **arXiv can't be reached:** the result stays as it was, and it's tried again next run.

A Crossref or arXiv title match never changes your entry, since it may be a different version from the one you cite. Papers in none of these, such as many workshop and community conference papers, stay CHECK. That's a gap in the indexes, not a verdict on the paper.

### Links

With `url_check: all`, the default, every entry's link is checked:

- **Not found anywhere, but the link works:** LINK OK.
- **Not found, and the link is broken (404 or 410):** "the link is broken" is added to its CHECK entry.
- **Verified, but the link is broken or now goes to another site:** it stays OK, and it's listed under **Broken links** so you can fix it.
- **Any other answer**, like 403 or a timeout: treated as no answer, since many sites block scripts. It's asked again next run.

Link results are stored in the ledger and expire like other results. `url_check: not_found` checks only entries no registry has, and `off` checks none.

### Entries with no identifier

If an entry has no DOI or arXiv ID, it's confirmed by title search and marked *(title match)*. Those search records usually lack pages and volume, so the tool fetches the full record from Crossref, for free and only once. It then compares the details but changes nothing, since the match may be another version:

- **Your value conflicts with the registry's:** CHECK, showing both values.
- **The registry has something you left out:** OK, with a note.

Add DOIs to your master bib if you want details corrected automatically. You can still hide them with `doi: remove`.

### Used and unused references

When you give both a bib and a PDF, the reports focus on the references the paper actually uses. Bib entries that aren't in the PDF's reference list are listed under **Unused references** in each report, and don't count in the tables.

Unused entries don't spend API calls unless you set `check_unused: true`. If a result is already stored in the ledger, it's still shown. Formatting still applies to them, and they're never removed.

With only a bib, there's no way to tell what's used, so everything is checked.

### Keeping API costs down

`ledger.json` stores a fingerprint of each reference and its result, built from the title, authors, year, volume, number, pages, DOI and arXiv ID. It ignores capitalization, LaTeX, cite keys, venue naming and hidden fields.

- **Unchanged references make no API calls**, even if you rename a key, reorder the file, change your style, or apply the changes.
- **An edited reference is re-checked**, and only that one.
- **CHECK results are reused too**, so a tech report that's never found doesn't cost you every run.
- **Results expire** after `recheck_after_days`, because a paper can be retracted after it was verified.

The ledger lives in the output folder, so if you use `--out`, use the same folder each time. `format` and `consistency` read it too, for free.

## format

`format` applies a **style**: a file of rules for title case, venue names, authors, and which fields to show. This folder ships `default` (changes nothing), `ieee` and `acm` as starting points, and `wimnet`, the WiMNet group style. Pick one with `style:` in `format.yaml`, or `--style` for one run.

```yaml
all:
  title_case: sentence
  authors: initials

conference:
  venue_names: abbreviated
  venue_prefix: add       # Proc. IEEE INFOCOM'13, with the next two
  venue_sponsor: add
  venue_year: add
  pages: remove
```

**[styles/README.md](styles/README.md) is the full guide:** every rule and its values, reference types, the order rules run in, how the venue settings combine, title case, venue lists, and how to make a style for your venue.

Three things to know:

- **Fields are hidden, never deleted.** When a style leaves a field out, `doi` becomes `OPTdoi`. BibTeX ignores it, so the bibliography looks the same, but nothing is lost, and `doi: add` shows it again. `hidden_prefix: null` deletes instead.
- **It builds on `verify`.** Entries verify marks CHECK are left as written, unless `skip_unverified: false`. `doi: add` and `pages: add` fill gaps only from a record found through the entry's own DOI or arXiv ID, stored in the ledger, so `format` alone stays offline.
- **Your venue's `.bst` file styles entries again** when LaTeX builds the bibliography, and can go further than any rule here. Check the compiled PDF.

## lint

Checks each bib entry by itself, for free and with no network calls. Nothing is changed. Each check can be turned off in `lint.yaml`:

| Check | What it finds |
|---|---|
| `repeated_keys` | The same cite key used for two entries |
| `required_fields` | Anything BibTeX requires for that entry type, such as `publisher` on a `@book` |
| `empty_fields` | `number = {}` |
| `years` | A year that isn't one, or is more than a year ahead |
| `venue_year` | A venue year that disagrees with the entry, like `Proc. IEEE NEWCAS'20` with `year = {2022}` |
| `pages` | A single hyphen instead of `--`, or a range that runs backward or is abbreviated, like `1333--64` |
| `doi` | Not a valid DOI, or with a prefix like `https://doi.org/` that belongs outside the field |
| `authors` | "et al." written out, or several names separated by commas |
| `latex_specials` | Unescaped `&`, `%`, `#` or `_` in a title, venue, publisher or series |
| `non_ascii` | Accented letters that classic BibTeX can sort or capitalize wrongly. Off with `backend: biber` |
| `unprotected_capitals` | Words like `LTE`, `MIMO`, `mmWave` or `5G`, and your `keep_capitalized` words, that a BibTeX style may lowercase unless they're in braces. Words in `capitals_ignore`, such as units like `dB`, are left out. The style rule `protect_capitals: add` fixes them |
| `unknown_fields` | A field name that looks like a misspelling, like `jounral` |
| `wrong_type_fields` | `journal` on an `@inproceedings`, or `booktitle` on an `@article`, so the venue may not be printed |

An entry so malformed it can't be read is listed too, with its line number, and every other entry is still checked. A missing closing brace is different: nothing after it can be read reliably, so the run stops and says where it is.

## consistency

Compares the entries with each other, for free. Nothing is changed. Each section can be turned off in `consistency.yaml`, or picked with `--only`:

- `duplicates`: entries that may be the same work cited twice under different keys, such as a conference paper and its journal version. It flags the same DOI or arXiv ID, verified results pointing to the same DOI (from the ledger), the same title or the same title apart from a label like `Demo:` or a subtitle, nearly the same title, and the same first author with years within two and mostly the same title words. The `duplicate_` settings set how alike is alike enough. With a PDF, its references that aren't in the bib are included. On a PDF alone, it's a quick duplicate check of someone else's paper.
- `venues`: the same venue written differently, like `Proc. IEEE INFOCOM'18` and `INFOCOM`.
- `authors`: the same person written differently, like `J. Smith` and `John Smith`. Names are grouped by family name and first initial, and a group is flagged only if it holds a single full first name, so John and Jane Smith are never taken for one person.
- `fields`: a field most entries of a type have, missing from a few. At least `min_share` of them must have it. Fields your style hides aren't compared.
- `conventions`: title case or venue naming that varies within a type.

Possible duplicates come first, and are counted separately in `summary.md`, since they're errors rather than style.

## compare

Pairs each reference printed in the PDF with its bib entry, by identifier, title and year. It lists references printed but missing from the bib, bib entries the paper doesn't cite, and pairs whose year or title differ, which usually means the PDF was compiled from an older bib.

### Reading the PDF

The tool finds the References or Bibliography heading, even letter-spaced as "R E F E R E N C E S", and splits what follows into entries. It reads one, two or three columns, and finds them per page, so a page with two columns of text above three columns of references reads in the right order. It handles:

- numbered lists: `[1]`, `1.` and `(1)`
- labeled lists: `[BMK13]`, `[Jai+11]`
- unnumbered lists, as in author-year styles, split by the layout. It tries a hanging indent, extra space between entries and a first-line indent, measured per column, and keeps whichever split looks most like a reference list

Names written family name first, as in "Chen, Tingjun, Zhou, Jin, and Zussman, Gil", are read as people, not as a list of single words. If a reference still can't be split into fields, its printed text is sent to Scholar Sidekick as is.

If the counts look wrong, such as many bib entries listed as unused, check the "Found N references in the PDF" line against the paper.

### Fixing a misread reference

What the tool reads is saved as `<pdf name>.references.bib` in the output folder, one entry per printed reference, with its label and printed text. Later runs read that file instead of the PDF, which is much faster. It's read again when the PDF changes, or when a newer version of the tool reads PDFs differently.

If a reference was misread, fix its entry in that file and run again. Your edits are used from then on, and the file is never overwritten once you've changed it. If the PDF changes after you've edited the file, the new PDF is read for that run and your file is left as it is: delete it to save the new reading. An entry with no title is one the tool couldn't split into fields.

## Config reference

Settings live in a config folder, one file per command: `verify.yaml`, `format.yaml`, `lint.yaml` and `consistency.yaml`. The tool uses `--config` if you give it, then a `refaudit_config/` folder next to your input, then the one in this folder. A missing file means that command's defaults. A typo, an invalid value, or a setting in the wrong file stops the run with a message saying where it belongs.

### verify.yaml

| Setting | Default | What it does |
|---|---|---|
| `auto_correct` | `high` | How confident the match must be before wrong details are fixed: `high`, `medium` or `off`. Below that, or at `off`, the reference becomes CHECK with the registry's values |
| `crossref_lookup` | `true` | Uses Crossref directly: by DOI first, and by title for anything Scholar Sidekick can't find, fetching full records for title matches so their pages and volume get compared. Free, and doesn't use your quota. `false` turns it off |
| `arxiv_lookup` | `true` | Uses arXiv directly: by ID first, and by title for anything still not found. Free. `false` turns it off |
| `url_check` | `all` | Which links to check: every entry's (`all`), only entries no registry has (`not_found`), or none (`off`). Free |
| `sidekick_confirms_all` | `false` | `true` sends every reference to Scholar Sidekick, even ones a DOI or arXiv ID settles for free |
| `check_unused` | `false` | With a PDF, bib entries it doesn't cite aren't verified, to save API calls. `true` checks them too |
| `recheck_after_days` | `180` | How long a stored result stays valid. `null` means forever |
| `max_calls_per_run` | `null` | Stops after this many API calls. References past the limit are CHECK, never OK. `null` means no limit |

No setting can make the tool rewrite a reference flagged as mismatched, ambiguous, not found or retracted.

### format.yaml

| Setting | Default | What it does |
|---|---|---|
| `style` | `default` | The style to apply: a name from `styles/` (in the config folder, then this folder) or a path to a `.yaml` file. `--style` overrides it |
| `skip_unverified` | `true` | When `verify` runs in the same command, leaves entries it marks CHECK exactly as written. `false` restyles them anyway |
| `hidden_prefix` | `OPT` | The prefix for hidden fields: `doi` becomes `OPTdoi`. `x-` works too. `null` deletes fields instead of hiding them |
| `capitals_ignore` | units: `[dB, dBm, Hz, GHz, …]` | Capitalized words to leave alone: `protect_capitals` doesn't brace them, and lint doesn't warn about them |
| `organization_words` | `[university, institute, research, …]` | Words that mark an author as an organization, as in "Columbia University". `authors: initials` leaves those names alone, and lint asks for braces around them, since BibTeX would read them as people |
| `keep_capitalized` | `[Bayesian, Gaussian, Markov]` | Proper nouns, such as `Zadoff-Chu` or `GNU Radio`, that keep exactly this capitalization. `title_case` won't lowercase them, `protect_capitals: add` braces them, and lint's `unprotected_capitals` check warns when they're unbraced. ALLCAPS, ResNet-style words, lone capitals (N-Path) and `{braced}` words are already handled, but proper nouns can't be detected automatically |
| `venue_lists` | `[computer_science, electrical_computer_engineering]` | Which files from `venues/` to use. Each also lists the organizations that sponsor venues in its field, for the `venue_sponsor` rule |
| `abbreviations_file` | `null` | Path to your own venue file. Its entries supersede `venue_lists`, and `format.md` notes where that changed a reference. It can add sponsors with a `sponsors: [Springer]` line. A relative path is relative to the config folder |

### lint.yaml

| Setting | Default | What it does |
|---|---|---|
| `backend` | `bibtex` | Which program builds your bibliography: `bibtex` or `biber`. `biber` handles UTF-8, so `non_ascii` doesn't apply |
| each check above | `true` | `false` turns that check off, as in `unprotected_capitals: false` |

### consistency.yaml

| Setting | Default | What it does |
|---|---|---|
| `min_share` | `0.75` | How many entries of a type must have a field before the rest are listed as missing it |
| `duplicate_similarity` | `0.88` | How alike two titles must be, from 0 to 1, to count as nearly the same. 0.88 catches "testbed" and "testbeds" |
| `duplicate_word_overlap` | `0.6` | For a likely conference and journal version: the share of title words two entries by the same first author must have in common |
| `duplicate_year_gap` | `2` | ...and the most years apart they can be |
| each section above | `true` | `false` turns that section off, as in `conventions: false` |

## What it can't do

- It checks that a reference points to a real work with correct details. **It doesn't check that a source supports what you cite it for.**
- `CHECK: couldn't confirm` doesn't mean fake. Standards, tech reports, software and very recent papers often aren't in Crossref, PubMed or OpenAlex.
- The verifier can miss small changes, such as one altered word in an otherwise correct title. The tool compares the full author list itself, since the API only compares the first author.
- Registries sometimes contain errors, so check the corrections in `changes.md`.
- Scanned PDFs with no text can't be read, and a PDF alone gets reports but no bib changes.

## Privacy

Only reference details (title, authors, year, venue, identifiers) are sent to Scholar Sidekick, only the DOI or title to Crossref, and only the arXiv ID or title to arXiv, never your paper's text. If the tool can't split a PDF reference into fields, it sends that reference's printed text to Scholar Sidekick. Link checks request the link itself and nothing else.

## Tests

```
pip install -e ".[test]"
pytest
```

The tests use no network and make no API calls.
