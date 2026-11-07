"""Build samples/: one sample report per command, showing every kind of message the tool writes.

Run from the project folder:  python tests/make_sample_report.py

Nothing here is hand-written report text. Made-up entries and stored results go through the
same sweeps a real run uses, with no network, so the samples can't drift from what the tool
says. tests/test_refaudit.py fails if samples/ is out of date.

When you add a message to the tool, add an entry here that triggers it, then rerun this.
"""
from __future__ import annotations

import contextlib
import io
import sys
import tempfile
from argparse import Namespace
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from refaudit import bib, check, config, pdf, pipeline  # noqa: E402
from refaudit.context import Clients, Context  # noqa: E402
from refaudit.sweeps import REGISTRY  # noqa: E402

TODAY = date(2026, 11, 7)
OUTPUT = ROOT / "samples"
NAMES = ["summary", "compare", "verify", "format", "changes", "lint", "consistency"]

PREFACE = """> **This is a sample, not a real audit.** It's built from made-up entries so that it shows
> every kind of message this report can contain, which no real report would. Rebuild it with
> `python tests/make_sample_report.py`.

"""

STYLE = {
    "conference": {"venue_prefix": "add", "venue_sponsor": "add", "venue_year": "add"},
    "journal": {"venue_names": "abbreviated", "doi": "add", "pages": "add", "url": "remove"},
}

# Every entry that gets a result. Keys say what each one shows.
BIB = r"""
@article{retracted, author={Smith, Jane}, title={A Retracted Study of Things}, journal={Journal of Things}, year=2019, doi={10.1000/retracted}}
@article{wrong_doi, author={Lee, Kim}, title={What the Paper Is Really Called}, journal={Journal of Things}, year=2020, doi={10.1000/someone-else}}
@book{other_edition, author={Doe, John}, title={A Book About Things}, publisher={Things Press}, year=2015, isbn={9780000000001}}
@article{other_version, author={Wu, Max}, title={A Conference Paper Later in a Journal}, journal={Journal of Things}, year=2021, doi={10.1000/version}}
@article{ambiguous, author={Park, Ann}, title={Learning Things}, journal={Journal of Things}, year=2022, doi={10.1000/ambiguous}}
@misc{not_found_link_broken, author={Roe, Rita}, title={An Unindexed Technical Report}, year=2018, url={https://example.org/gone}}
@misc{not_found_anywhere, author={Roe, Rita}, title={A Workshop Paper Nobody Indexed}, year=2023}
@article{odd_verdict, author={Smith, Jane}, title={A Paper the API Was Unsure How to Label}, journal={Journal of Things}, year=2020}
@article{low_confidence, author={Smith, Jane}, title={A Paper Matched Only Loosely}, journal={Journal of Things}, year=2020}
@article{title_match_conflict, author={Smith, Jane}, title={A Paper Found by Its Title}, journal={Journal of Things}, year=2018, volume={7}}
@article{medium_confidence, author={Smith, Jane}, title={A Paper Matched With Medium Confidence}, journal={Journal of Things}, year=2019, doi={10.1000/medium}}
@article{quota_used_up, author={Smith, Jane}, title={Checked After the Call Limit}, journal={Journal of Things}, year=2020}
@article{api_failed, author={Smith, Jane}, title={Checked While the API Was Down}, journal={Journal of Things}, year=2020}
@misc{no_title, author={Smith, Jane}, year=2020}
@article{no_api_result, author={Smith, Jane}, title={An Entry the API Skipped}, journal={Journal of Things}, year=2020}
@article{fixed, author={Smith, J. and Dee, Cara}, title={An Old Title for This Paper}, journal={IEEE Transactions on Wireless Communications}, year=2019, volume={1}, number={1}, pages={1--5}, url={https://example.org/fixed}}
@article{longer_title, author={Smith, Jane}, title={A Short Title}, journal={Journal of Things}, year=2020, doi={10.1000/longer}, pages={3--9}}
@article{preprint_id, author={Smith, Jane}, title={A Published Paper With a Preprint DOI}, journal={Journal of Things}, year=2021, doi={10.48550/arXiv.2101.00002}, pages={1--2}}
@inproceedings{venue_conflicts, author={Smith, Jane}, title={A Conference Paper With Venue Notes}, booktitle={Proc. ACM INFOCOM'19}, year=2020, doi={10.1000/infocom}}
@inproceedings{venue_no_year, author={Smith, Jane}, title={A Conference Paper With No Year}, booktitle={GRCon}, doi={10.1000/grcon}}
@article{nothing_to_add, author={Smith, Jane}, title={A Journal Paper Found in Crossref}, journal={Journal of Things}, year=2020}
@article{venue_not_listed, author={Smith, Jane}, title={A Paper in an Unlisted Journal}, journal={Journal of Obscure Results}, year=2020, doi={10.1000/obscure}, pages={4--8}}
@article{your_abbreviation, author={Smith, Jane}, title={A Paper Using Your Abbreviation}, journal={IEEE Transactions on Wireless Communications}, year=2020, doi={10.1000/twc}, pages={4--8}}
@article{shown_again, author={Smith, Jane}, title={A Paper Whose DOI Was Hidden}, journal={Journal of Things}, year=2020, OPTdoi={10.1000/hidden}, pages={1--4}}
@misc{link_ok, author={{GNU Radio}}, title={GNU Radio Software}, year=2024, url={https://www.gnuradio.org/}}
@misc{link_broken, author={Smith, Jane}, title={A Verified Paper With a Dead Link}, year=2020, doi={10.1000/dead}, url={https://example.org/dead}}
@misc{link_moved, author={Smith, Jane}, title={A Verified Paper Whose Link Moved}, year=2020, doi={10.1000/moved}, url={https://old.example.org/paper}}
@misc{on_arxiv, author={Smith, Jane}, title={A Preprint Found on arXiv}, year=2021}
@misc{by_arxiv_id, author={Smith, Jane}, title={A Preprint Checked by Its arXiv ID}, year=2022, eprint={2201.00005}, archiveprefix={arXiv}}
@misc{preprint_match, author={Smith, Jane}, title={A Paper Matched to Its Preprint}, year=2021}
@article{ok, author={Smith, Jane}, title={A Paper With Nothing to Change}, journal={Journal of Things}, year=2020, doi={10.1000/ok}, pages={1--9}}
@article{printed_differently, author={Smith, Jane}, title={A Paper Formatted Without Checking}, journal={Journal of Things}, year=2020, url={https://example.org/x}}
@article{unused_unchecked, author={Smith, Jane}, title={A Paper the PDF Never Cites}, journal={Journal of Things}, year=2020, pages={5-9}, url={https://example.org/unused}}
@article{unused_checked, author={Smith, Jane}, title={An Uncited Paper Checked Earlier}, journal={Journal of Things}, year=2020, doi={10.1000/unused}}
"""

# One entry per lint check.
LINT_BIB = r"""
@book{missing_publisher, author={Doe, John}, title={A Book Without a Publisher}, year=2015}
@article{empty_number, author={Doe, John}, title={T}, journal={J}, year=2020, number={}}
@article{year_not_a_year, author={Doe, John}, title={T}, journal={J}, year={in press}}
@article{year_in_future, author={Doe, John}, title={T}, journal={J}, year=2031}
@inproceedings{venue_year, author={Doe, John}, title={T}, booktitle={Proc. IEEE NEWCAS'20}, year=2022}
@article{single_hyphen, author={Doe, John}, title={T}, journal={J}, year=2020, pages={1-9}}
@article{backward_pages, author={Doe, John}, title={T}, journal={J}, year=2020, pages={1333--64}}
@article{doi_prefix, author={Doe, John}, title={T}, journal={J}, year=2020, doi={https://doi.org/10.1000/x}}
@article{doi_invalid, author={Doe, John}, title={T}, journal={J}, year=2020, doi={not-a-doi}}
@article{et_al, author={Doe, John et al.}, title={T}, journal={J}, year=2020}
@article{comma_authors, author={Doe, John, Roe, Rita, Lee, Kim}, title={T}, journal={J}, year=2020}
@article{unescaped, author={Doe, John}, title={Pros & Cons of 100% Things}, journal={J}, year=2020}
@article{non_ascii, author={Müller, Jörg}, title={T}, journal={J}, year=2020}
@article{capitals, author={Doe, John}, title={Massive MIMO for LTE in the mmWave Band}, journal={J}, year=2020}
@article{misspelled, author={Doe, John}, title={T}, jounral={J}, year=2020}
@inproceedings{wrong_type, author={Doe, John}, title={T}, journal={Proc. Things}, year=2020}
@misc{unreadable, title={T}, = {x}}
@article{repeated_key, author={Doe, John}, title={One Paper}, journal={J}, year=2020}
@article{repeated_key, author={Roe, Rita}, title={A Different Paper Given the Same Key}, journal={J}, year=2021}
"""

# One group per kind of possible duplicate, then one per inconsistency.
CONSISTENCY_BIB = r"""
@misc{same_doi_a, title={First Way of Writing It}, doi={10.1000/same}}
@misc{same_doi_b, title={Second Way of Writing It}, doi={10.1000/SAME}}
@misc{same_arxiv_a, title={Third Way of Writing It}, eprint={2101.00009}, archiveprefix={arXiv}}
@misc{same_arxiv_b, title={Fourth Way of Writing It}, eprint={2101.00009}, archiveprefix={arXiv}}
@misc{verified_a, title={One Description of a Paper}}
@misc{verified_b, title={Quite a Different Description}}
@misc{same_title_a, title={Wideband Full-Duplex Radios in Practice}}
@misc{same_title_b, title={Wideband full-duplex radios in practice}}
@misc{label_a, title={Demo: Self-Interference Cancellation Across Environments}}
@misc{label_b, title={Self-Interference Cancellation Across Environments}}
@misc{nearly_a, title={Open-access full-duplex wireless in the ORBIT testbed}}
@misc{nearly_b, title={Open access full-duplex wireless in the ORBIT testbeds}}
@inproceedings{versions_a, author={Chen, Tingjun}, year=2019, title={Wideband Full-Duplex Wireless via Frequency-Domain Equalization}, booktitle={Proc. IEEE INFOCOM'19}, pages={1--9}}
@article{versions_b, author={Chen, Tingjun}, year=2020, title={Frequency-Domain Equalization for Wideband Full-Duplex Wireless Systems}, journal={J}}
@misc{three_a, title={A Title Cited Three Times Over}}
@misc{three_b, title={A title cited three times over}}
@misc{three_c, title={A Title Cited Three Times Over}, doi={10.1000/three}}
@inproceedings{venue_a, author={Smith, John}, title={Scheduling Radios in Practice}, booktitle={INFOCOM}, year=2018, pages={1--9}}
@inproceedings{venue_b, author={Smith, J.}, title={Routing Radios in Practice}, booktitle={IEEE INFOCOM}, year=2019, pages={1--9}}
@inproceedings{venue_c, author={Lee, Amy}, title={Caching radios in practice}, booktitle={IEEE INFOCOM}, year=2020, pages={1--9}}
@inproceedings{no_pages, author={Lee, Amy}, title={Sensing Radios in Practice}, booktitle={IEEE INFOCOM}, year=2021}
"""

ABBREVIATIONS = '"IEEE Transactions on Wireless Communications": TWC\n'


def record(title, authors=(("Smith", "Jane"),), year=2020, venue="Journal of Things", volume="",
           number="", pages="", doi="", kind="journal-article"):
    return {"type": kind, "title": title, "authors": [{"family": f, "given": g} for f, g in authors],
            "year": year, "venue": venue, "volume": volume, "number": number, "pages": pages, "doi": doi}


def result(verdict="matched", rec=None, confidence="high", by_search=False, mismatches=(),
           candidates=(), retracted=False, concern=False, corrections=False, **extra):
    return {"verdict": verdict, "confidence": confidence, "record": rec, "by_search": by_search,
            "mismatches": list(mismatches), "candidates": list(candidates), "caveats": [],
            "retraction": {"checked": True, "retracted": retracted, "concern": concern,
                           "corrections": corrections}, **extra}


def candidate(title, doi, score=0.96, year=2020):
    return {"score": score, "record": record(title, year=year, doi=doi)}


def results() -> dict:
    """What each entry's check came back with: a result, or (None, why it wasn't checked)."""
    same = lambda title, **kw: record(title, **kw)  # noqa: E731
    return {
        "retracted": result(rec=same("A Retracted Study of Things", year=2019, doi="10.1000/retracted"),
                            retracted=True, concern=True, arxiv={"withdrawn": True}, source="crossref"),
        "wrong_doi": result("mismatch", mismatches=[{"field": "title", "claimed": "What the Paper Is Really Called",
                                                     "resolved": "Something Else Entirely", "similarity": 0.2}]),
        "other_edition": result("mismatch", mismatches=[{"field": "year", "claimed": 2015, "resolved": 2009}]),
        "other_version": result("mismatch", mismatches=[{"field": "container", "claimed": "Journal of Things",
                                                         "resolved": "Proc. Things Conference", "similarity": 0.3}]),
        "ambiguous": result("ambiguous", candidates=[candidate("Learning Things, Part I", "10.1000/part1"),
                                                     candidate("Learning Things, Part II", "10.1000/part2")]),
        "not_found_link_broken": result("not_found", rec=None,
                                        candidates=[candidate("An Unindexed Technical Report (Draft)", "")],
                                        link={"url": "https://example.org/gone", "state": "broken",
                                              "detail": "HTTP 404"}),
        "not_found_anywhere": result("not_found", arxiv_checked=True),
        "odd_verdict": result("unclassifiable"),
        "low_confidence": result(rec=same("A Paper Matched Only Loosely"), confidence="low"),
        "title_match_conflict": result(rec=same("A Paper Found by Its Title", year=2019, volume="7", number="4",
                                                doi="10.1000/found"), by_search=True,
                                       candidates=[candidate("A Paper Found by Its Title", "10.1000/found")]),
        "medium_confidence": result(rec=same("A Paper Matched With Medium Confidence", year=2020,
                                             doi="10.1000/medium"), confidence="medium"),
        "quota_used_up": (None, "Not checked: max_calls_per_run reached."),
        "api_failed": (None, "Not checked: API returned HTTP 500."),
        "no_api_result": (None, "Not checked: the API returned no result."),
        "fixed": result(rec=record("The Right Title for This Paper",
                                   authors=(("Smith", "Jane"), ("Dee", "Cara"), ("Roe", "Rita")),
                                   year=2020, venue="IEEE Transactions on Wireless Communications", volume="19",
                                   number="4", pages="2140--2152", doi="10.1000/fixed")),
        "longer_title": result(rec=same("A Short Title: And Its Subtitle", doi="10.1000/longer", pages="3--9"),
                               corrections=True, source="crossref",
                               mismatches=[{"field": "container", "claimed": "Journal of Things",
                                            "resolved": "Annals of Stuff", "similarity": 0.1}]),
        "preprint_id": result(rec=record("A Published Paper With a Preprint DOI", year=2021, venue="arXiv",
                                         doi="10.48550/arXiv.2101.00002", kind="posted-content")),
        "venue_conflicts": result(rec=record("A Conference Paper With Venue Notes", doi="10.1000/infocom",
                                             venue="IEEE INFOCOM", kind="proceedings-article")),
        "venue_no_year": result(rec=record("A Conference Paper With No Year", year=None, doi="10.1000/grcon",
                                           venue="GNU Radio Conference", kind="proceedings-article")),
        "nothing_to_add": result(rec=same("A Journal Paper Found in Crossref", doi="10.1000/crossref"),
                                 confidence="medium", by_search=True, crossref_match=True, details_checked=True),
        "venue_not_listed": result(rec=record("A Paper in an Unlisted Journal", venue="Journal of Obscure Results",
                                              doi="10.1000/obscure", pages="4--8")),
        "your_abbreviation": result(rec=record("A Paper Using Your Abbreviation", doi="10.1000/twc", pages="4--8",
                                               venue="IEEE Transactions on Wireless Communications")),
        "shown_again": result(rec=same("A Paper Whose DOI Was Hidden", doi="10.1000/hidden", pages="1--4"),
                              source="crossref"),
        "link_ok": result("not_found", link={"url": "https://www.gnuradio.org/", "state": "ok", "detail": "HTTP 200"}),
        "link_broken": result(rec=record("A Verified Paper With a Dead Link", doi="10.1000/dead"), source="crossref"),
        "link_moved": result(rec=record("A Verified Paper Whose Link Moved", doi="10.1000/moved"), source="crossref"),
        "on_arxiv": result(rec=record("A Preprint Found on arXiv", year=2021, venue="arXiv", kind="preprint"),
                           confidence="medium", by_search=True,
                           arxiv={"id": "2101.00003", "journal_ref": "IEEE Trans. Wireless Commun. 21 (2022)",
                                  "doi": "10.1000/published", "withdrawn": False}),
        "by_arxiv_id": result(rec=record("A Preprint Checked by Its arXiv ID", year=2022, venue="arXiv",
                                         kind="preprint"), source="arxiv", arxiv_checked=True,
                              arxiv={"id": "2201.00005", "journal_ref": "Proc. Things Conference 2023",
                                     "doi": "", "withdrawn": False}),
        "preprint_match": result(rec=record("A Paper Matched to Its Preprint", year=2021, venue="arXiv",
                                            doi="10.48550/arXiv.2101.00004", kind="posted-content"),
                                 by_search=True,
                                 candidates=[candidate("A Paper Matched to Its Preprint", "10.1000/journal-version")]),
        "ok": result(rec=same("A Paper With Nothing to Change", doi="10.1000/ok", pages="1--9")),
        "printed_differently": result(rec=same("A Paper Formatted Without Checking")),
        "unused_checked": result(rec=same("An Uncited Paper Checked Earlier", doi="10.1000/unused")),
    }


LINKS = {"https://example.org/dead": ("broken", "HTTP 404"),
         "https://old.example.org/paper": ("moved", "https://new.example.org/paper")}

# The PDF's reference list: two match bib entries with differences, one isn't in the bib.
PDF_TEXT = {
    "[1]": "Jane Smith. 2021. A Paper With Nothing to Change. Journal of Things.",
    "[2]": "Jane Smith. 2020. A Paper Formatted Somehow Without Checking. Journal of Things.",
    "[3]": "Jane Smith. 2019. A Paper Only in the PDF. Journal of Things. doi:10.1000/pdf-only",
}
PDF_RESULT = result(rec=record("A Paper Only in the PDF", year=2020, doi="10.1000/pdf-only"))
UNUSED = ["unused_checked", "unused_unchecked"]


def _context(folder: Path, text: str) -> Context:
    abbreviations = folder / "my_venues.yaml"
    abbreviations.write_text(ABBREVIATIONS, encoding="utf-8")
    cfg = config.build({"format": {"style": "sample", "abbreviations_file": str(abbreviations)}},
                       folder, style_data=STYLE)
    args = Namespace(write=True, apply=False, recheck=False, dry_run=False, only=None, style=None)
    ctx = Context("full", args, cfg, Path("paper.bib"), Path("paper.pdf"), folder / "out", TODAY, Clients())
    ctx.bib_text = text
    ctx.bib_refs = bib.parse_bib(text, cfg.format.hidden_prefix, ctx.unreadable)
    return ctx


def build() -> dict:
    """Each sample's text, by report name."""
    with tempfile.TemporaryDirectory() as folder:
        folder = Path(folder)
        ctx = _context(folder, BIB)
        printed = [pdf._parse_entry(label, text) for label, text in PDF_TEXT.items()]
        ctx.pdf_refs = printed
        ctx.cross = check.cross_check([r for r in ctx.bib_refs if r.key in ("ok", "printed_differently")], printed)
        ctx.cross.pairs.update({f"[{i}]": r.key for i, r in enumerate(ctx.bib_refs, 10) if r.key not in UNUSED})
        ctx.cross.bib_only = list(UNUSED)

        # What earlier lookups stored, as a real run would find it in the ledger.
        failures = {}
        for ref in ctx.bib_refs:
            answer = results().get(ref.key)
            if isinstance(answer, tuple):
                failures[bib.fingerprint(ref)] = answer[1]
            elif answer is not None:
                ctx.ledger.put(bib.fingerprint(ref), answer)
        for ref in ctx.pdf_only:
            ctx.ledger.put(bib.fingerprint(ref), PDF_RESULT)
        for url, (state, detail) in LINKS.items():
            ctx.ledger.put_link(url, state, detail)
        fresh = {bib.fingerprint(r) for r in ctx.bib_refs if r.key not in ("ok", "fixed", "unused_checked")}
        stats = {"calls": 2, "free_calls": 14, "reused": 3, "saved": 1, "oldest": "2026-06-01",
                 "anonymous": True, "by_source": {"crossref": 5, "arxiv": 1, "sidekick": 21}}

        done = {"compare": REGISTRY["compare"].run(ctx, {})}
        done["verify"] = REGISTRY["verify"].evaluate(ctx, failures, fresh, stats)
        done["format"] = REGISTRY["format"].run(ctx, done)

        lint_ctx = _context(folder, LINT_BIB + BIB)
        lint_ctx.cross = ctx.cross
        done["lint"] = REGISTRY["lint"].run(lint_ctx, {})

        consistency_ctx = _context(folder, CONSISTENCY_BIB)
        for key in ("verified_a", "verified_b"):
            ref = next(r for r in consistency_ctx.bib_refs if r.key == key)
            consistency_ctx.ledger.put(bib.fingerprint(ref), result(rec=record("x", doi="10.1000/verified")))
        consistency_ctx.cross = ctx.cross
        done["consistency"] = REGISTRY["consistency"].run(consistency_ctx, {})

        sweeps = [REGISTRY[name] for name in REGISTRY]
        with contextlib.redirect_stdout(io.StringIO()):
            pipeline.write_outputs(ctx, sweeps, done)
        out = ctx.out_dir
        return {name: PREFACE + (out / f"{name}.md").read_text(encoding="utf-8") for name in NAMES}


if __name__ == "__main__":
    OUTPUT.mkdir(exist_ok=True)
    for name, text in build().items():
        (OUTPUT / f"{name}.md").write_text(text, encoding="utf-8")
    print(f"Wrote {len(NAMES)} samples to {OUTPUT}")
