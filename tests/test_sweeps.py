"""The sweep structure: commands, help, staged verification, hidden fields, review files,
consistency and the lint additions. None of these use the network."""
import subprocess
from datetime import date

import pytest

from conftest import ExplodingClient, FakeClient, matched_entry, record
from refaudit import api, bib, cli, config, sanity
from refaudit.sweeps import REGISTRY
from refaudit.sweeps.consistency import author_variants
from test_refaudit import (AFTER, BEFORE, GOOD, ONE, STYLE, FakeArxiv, FakeCrossref, FakeLinks, corrected,
                           crossref_record, entry, make_pdf, only_first, paper, parsed, read, run_tool,
                           write_config)


# --- Commands and help ------------------------------------------------------------------

@pytest.mark.parametrize("argv", [[], ["-h"], ["--help"], ["help"]])
def test_main_help_lists_every_command(argv, capsys):
    assert cli.run(argv) == 0
    text = capsys.readouterr().out
    assert text.startswith("usage: refaudit [COMMAND] FILE... [options]")
    for name in ["full", *REGISTRY]:
        assert f"\n  {name} " in text
    assert "--config DIR" in text and "examples:" in text


@pytest.mark.parametrize("name", ["full", *REGISTRY])
def test_each_command_has_its_own_help(name, capsys):
    with pytest.raises(SystemExit) as stop:
        cli.run([name, "--help"])
    assert stop.value.code == 0
    text = capsys.readouterr().out
    assert text.startswith(f"usage: refaudit {name} FILE... [options]")
    assert "global options" in text and "examples:" in text
    if name != "full":
        sweep = REGISTRY[name]
        for label in ("Inputs:", "Network & cost:", "Changes your bib:", "Writes:", "Reads config from:"):
            assert label in text
        assert sweep.summary.split(":")[0] in text.replace("\n", " ")


def test_help_topic_gives_that_commands_help(capsys):
    assert cli.run(["help", "verify"]) == 0
    assert "Free, by identifier" in capsys.readouterr().out


def test_command_help_shows_its_own_options(capsys):
    with pytest.raises(SystemExit):
        cli.run(["format", "--help"])
    text = capsys.readouterr().out
    flat = " ".join(text.split())
    assert "--style NAME" in text and "--apply" in text and "Built in: acm, default, ieee, wimnet" in flat
    assert "--dry-run" not in text


def test_full_help_shows_every_commands_options(capsys):
    with pytest.raises(SystemExit):
        cli.run(["full", "--help"])
    text = capsys.readouterr().out
    assert all(flag in text for flag in ("--dry-run", "--recheck", "--style", "--write", "--apply", "--only"))


@pytest.mark.parametrize("name", ["lint", "consistency", "compare"])
def test_commands_that_never_change_a_bib_have_no_output_options(name, capsys):
    with pytest.raises(SystemExit):
        cli.run([name, "--help"])
    text = capsys.readouterr().out
    assert "--write" not in text and "--apply" not in text


def test_version(capsys):
    assert cli.run(["--version"]) == 0
    assert capsys.readouterr().out.strip().startswith("refaudit ")


@pytest.mark.parametrize("argv,message", [
    (["verfy", "refs.bib"], 'there\'s no command "verfy"'),
    (["help", "verfy"], 'there\'s no command "verfy"'),
    (["lint", "refs.bib", "--bogus"], "Run `refaudit lint --help` for usage"),
])
def test_mistakes_give_a_one_line_error(argv, message, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "refs.bib").write_text(ONE, encoding="utf-8")
    with pytest.raises(SystemExit, match=message):
        cli.run(argv)


def test_a_command_without_its_inputs_says_what_it_needs(tmp_path):
    with pytest.raises(SystemExit, match="refaudit compare needs both a .bib file and a PDF"):
        run_tool(tmp_path, command="compare")


def test_full_skips_what_the_inputs_dont_allow(tmp_path, capsys):
    out = run_tool(tmp_path, client=FakeClient(only_first))
    assert "Skipping compare: it needs both a .bib file and a PDF." in capsys.readouterr().out
    assert not (out / "compare.md").exists()
    assert all((out / f"{name}.md").exists() for name in ("summary", "verify", "format", "lint", "consistency"))


def test_a_bare_file_means_full(tmp_path):
    path = tmp_path / "refs.bib"
    path.write_text(GOOD, encoding="utf-8")
    cli.run([str(path), "--out", str(tmp_path / "out")], client=FakeClient(only_first),
            env_file=tmp_path / "x", arxiv_client=FakeArxiv(), crossref_client=FakeCrossref(),
            link_client=FakeLinks())
    assert (tmp_path / "out" / "summary.md").exists()


@pytest.mark.parametrize("command", ["lint", "consistency", "format"])
def test_offline_commands_make_no_calls(tmp_path, command):
    class Exploding:
        def __getattr__(self, name):
            raise AssertionError("no network")
    path = tmp_path / "refs.bib"
    path.write_text(GOOD, encoding="utf-8")
    cli.run([command, str(path), "--out", str(tmp_path / "out")], client=ExplodingClient(),
            env_file=tmp_path / "x", arxiv_client=Exploding(), crossref_client=Exploding(),
            link_client=Exploding())
    assert (tmp_path / "out" / f"{command}.md").exists()
    assert not (tmp_path / "out" / "ledger.json").exists()


def test_only_one_report_for_one_command(tmp_path):
    out = run_tool(tmp_path, client=ExplodingClient(), command="lint")
    assert sorted(p.name for p in out.iterdir()) == ["lint.md"]


# --- Verify: free lookups by identifier first -----------------------------------------

def test_a_doi_is_settled_by_crossref_for_free(tmp_path):
    crossref = FakeCrossref(full=crossref_record())
    out = run_tool(tmp_path, ONE.replace("}\n}", "},\n  doi = {10.1000/real}\n}").replace("2023", "2020")
                   .replace("Smith, Jane", "Smith, Jane and Doe, John"),
                   client=ExplodingClient(), crossref=crossref, cfg={"url_check": "off"})
    verify = read(out, "verify")
    assert "## OK" in verify and "Crossref, by DOI 1" in verify
    assert crossref.calls == 1


def test_crossref_records_corrections_with_their_source(tmp_path):
    text = GOOD.replace("10--20", "10--25")
    out = run_tool(tmp_path, text, client=FakeClient(only_first), crossref=FakeCrossref(full=crossref_record()))
    assert parsed(out, "smith2020").get("pages") == "10--20"
    assert "verify: Crossref, by DOI, high confidence" in read(out, "changes")


def test_a_year_off_by_one_from_crossref_is_shown_not_corrected(tmp_path):
    # From a real paper: the entry says 2020, Crossref's print date is 2021.
    text = GOOD.replace("{2020}", "{2021}")
    client = FakeClient(only_first)
    out = run_tool(tmp_path, text, client=client, crossref=FakeCrossref(full=crossref_record()),
                   cfg={"url_check": "off"})
    assert [c["title"] for c in client.bodies[0]["claims"]] == ["Second Paper on Things"]  # DOI settled free
    assert parsed(out, "smith2020").get("year") == "2021"
    verify = read(out, "verify")
    assert "confidence (medium) is below what auto_correct (high) needs" in verify
    assert 'year: you have "2021", the registry has "2020"' in verify


def test_a_doi_pointing_elsewhere_gets_a_second_opinion(tmp_path):
    client = FakeClient(only_first)
    run_tool(tmp_path, client=client, crossref=FakeCrossref(full=crossref_record(title="Another Paper")))
    assert [c["title"] for c in client.bodies[0]["claims"]] == ["A Real Paper", "Second Paper on Things"]


def test_a_doi_mismatch_stands_if_the_paid_check_finds_nothing(tmp_path):
    out = run_tool(tmp_path, client=FakeClient(lambda c: entry("not_found")),
                   crossref=FakeCrossref(full=crossref_record(title="Another Paper")))
    assert "Possibly wrong or fabricated" in read(out, "verify")


def test_crossref_retraction_notices_are_read(tmp_path):
    crossref = FakeCrossref(full=crossref_record() | {"updates": ["retraction"]})
    out = run_tool(tmp_path, client=FakeClient(only_first), crossref=crossref)
    assert "Retracted, according to Crossref" in read(out, "verify")


def test_a_title_match_picks_up_a_retraction_from_the_full_record(tmp_path):
    from test_refaudit import sparse_match
    text = GOOD.replace(",\n  doi     = {10.1000/real}", "")
    crossref = FakeCrossref(full=crossref_record() | {"updates": ["retraction"]})
    out = run_tool(tmp_path, text, client=FakeClient(sparse_match), crossref=crossref)
    assert "Retracted, according to Crossref" in read(out, "verify")


def test_crossref_parses_retraction_notices():
    item = {"title": ["RETRACTED: A Real Paper"], "DOI": "10.1/x", "type": "journal-article",
            "updated-by": [{"type": "retraction", "DOI": "10.1/n"}, {"type": "correction", "DOI": "10.1/m"}]}
    record_ = api.CrossrefClient._record(item)
    assert record_["title"] == "A Real Paper" and record_["updates"] == ["correction", "retraction"]
    result = api.crossref_by_doi(bib.parse_bib("@article{a, title={A Real Paper}, doi={10.1/x}}")[0],
                                 FakeCrossref(full=record_))
    assert result["retraction"]["retracted"] and result["retraction"]["corrections"]


def test_entries_without_an_identifier_go_straight_to_the_paid_check(tmp_path):
    crossref = FakeCrossref()
    client = FakeClient(lambda c: entry("not_found"))
    run_tool(tmp_path, ONE, client=client, crossref=crossref, cfg={"url_check": "off"})
    assert len(client.bodies) == 1 and crossref.calls == 1  # the title search after "not found"


def test_sidekick_confirms_all_skips_the_free_stage(tmp_path):
    crossref = FakeCrossref(full=crossref_record())
    client = FakeClient(only_first)
    run_tool(tmp_path, client=client, crossref=crossref, cfg={"sidekick_confirms_all": True})
    assert len(client.bodies[0]["claims"]) == 2 and crossref.calls == 0


ARXIV_ONE = ONE.replace("}\n}", "},\n  eprint = {2305.02822},\n  archiveprefix = {arXiv}\n}")


def test_arxiv_ids_are_checked_together_for_free(tmp_path):
    arxiv = FakeArxiv([paper(authors=["Jane Smith"])])
    out = run_tool(tmp_path, ARXIV_ONE, client=ExplodingClient(), arxiv=arxiv, cfg={"url_check": "off"})
    assert arxiv.calls == 1 and "arXiv, by ID 1" in read(out, "verify")


def test_an_arxiv_id_for_another_paper_is_flagged(tmp_path):
    arxiv = FakeArxiv([paper(title="Something Else Entirely")])
    out = run_tool(tmp_path, ARXIV_ONE, client=FakeClient(lambda c: entry("not_found")), arxiv=arxiv)
    assert "Possibly wrong or fabricated" in read(out, "verify")


def test_an_unknown_arxiv_id_isnt_asked_about_twice(tmp_path):
    arxiv = FakeArxiv([])
    out = run_tool(tmp_path, ARXIV_ONE, client=FakeClient(lambda c: entry("not_found")), arxiv=arxiv)
    assert arxiv.calls == 1 and "Crossref, PubMed, OpenAlex or arXiv" in read(out, "verify")


def test_arxiv_by_ids_asks_in_chunks(monkeypatch):
    asked = []
    client = api.ArxivClient(sleep=lambda s: None)
    monkeypatch.setattr(client, "_get", lambda params: asked.append(params["id_list"]) or [])
    assert client.by_ids([f"2101.{i:05d}" for i in range(120)]) == {}
    assert [len(a.split(",")) for a in asked] == [50, 50, 20]


def test_dry_run_with_no_identifiers_gives_one_number(tmp_path, capsys):
    run_tool(tmp_path, ONE, client=ExplodingClient(), command="verify", flags=["--dry-run"])
    assert "This run would use 1 API call(s)." in capsys.readouterr().out


def test_a_doi_link_isnt_checked_or_called_moved(tmp_path, monkeypatch):
    # From a real paper: every doi.org link redirects to the publisher, which isn't a move.
    text = GOOD.replace("  doi     = {10.1000/real}", "  url     = {https://doi.org/10.1000/real}")
    links = FakeLinks(("moved", "https://publisher.example/x"))
    run_tool(tmp_path, text, client=FakeClient(only_first), links=links)
    assert "https://doi.org/10.1000/real" not in links.urls

    class Response:
        status_code = 200
        url = "https://ieeexplore.ieee.org/document/1/"

        def close(self):
            pass
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: Response())
    assert api.LinkChecker().check("https://doi.org/10.1/x")[0] == "ok"


def test_crossref_is_paced_to_its_published_limits(monkeypatch):
    # Crossref without registering: one request at a time, 5/s by DOI, 1/s for searches.
    now, waits = [0.0], []

    class Response:
        status_code = 200
        headers = {"x-rate-limit-limit": "1", "x-rate-limit-interval": "1s"}

        @staticmethod
        def json():
            return {"message": {"items": []}}
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: Response())
    client = api.CrossrefClient(sleep=lambda s: (waits.append(round(s, 2)), now.__setitem__(0, now[0] + s)),
                                clock=lambda: now[0])
    client.by_title("a")
    client.by_title("b")
    assert waits == [1.0]
    Response.headers = {"x-rate-limit-limit": "50", "x-rate-limit-interval": "1s"}  # never faster than 5/s
    client.by_doi("10.1/x")
    client.by_doi("10.1/y")
    assert waits[-1] == 0.2


def test_a_year_printed_after_a_link_isnt_part_of_it():
    ref = bib.Reference(key="[3]", source="pdf", raw="Smith. A title. https://doi.org/10.1109/X.2019.8888884,2019")
    assert bib.url_of(ref) == "https://doi.org/10.1109/X.2019.8888884"
    glued = bib.Reference(key="[32]", source="pdf", raw="A. Radford. CLIP. http://arxiv.org/abs/2103.00020arXiv:2103.00020")
    assert bib.url_of(glued) == "http://arxiv.org/abs/2103.00020"


def test_free_lookups_give_the_same_results_in_parallel(tmp_path, monkeypatch):
    from refaudit.sweeps import verify
    text = "".join(f"@article{{p{i},\n  author = {{Smith, Jane}},\n  title = {{A Real Paper}},\n"
                   f"  journal = {{J}},\n  year = {{2020}},\n  doi = {{10.1000/r{i}}},\n}}\n" for i in range(12))
    runs = []
    for workers in (1, 6):
        monkeypatch.setattr(verify, "WORKERS", workers)
        folder = tmp_path / str(workers)
        folder.mkdir()
        out = run_tool(folder, text, client=ExplodingClient(), crossref=FakeCrossref(full=crossref_record()),
                       cfg={"url_check": "off"})
        runs.append(read(out, "verify").replace(str(folder), ""))
    assert runs[0] == runs[1] and "Crossref, by DOI 12" in runs[0]


def test_link_checker_reports_a_move_to_another_site(monkeypatch):
    class Response:
        status_code = 200
        url = "https://new.example.org/page"

        def close(self):
            pass
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: Response())
    assert api.LinkChecker().check("http://old.example.org/page") == ("moved", "https://new.example.org/page")
    Response.url = "https://www.old.example.org/page"
    assert api.LinkChecker().check("http://old.example.org/page")[0] == "ok"


# --- Format: hidden fields, the gate, the ledger ----------------------------------------

def test_a_hidden_field_is_shown_again_by_add(tmp_path):
    text = "@article{a,\n  title = {T},\n  OPTdoi = {10.1/x},\n  year = {2020},\n}\n"
    out = run_tool(tmp_path, text, client=ExplodingClient(), command="format", cfg={"all": {"doi": "add"}})
    assert corrected(out) == text.replace("OPTdoi", "doi")
    assert "hidden as `OPTdoi`" in read(out, "changes")


def test_keep_leaves_a_hidden_field_hidden(tmp_path):
    text = "@article{a,\n  title = {T},\n  OPTdoi = {10.1/x},\n  year = {2020},\n}\n"
    assert corrected(run_tool(tmp_path, text, client=ExplodingClient(), command="format")) == text


def test_show_fields_and_url_show(tmp_path):
    text = "@misc{a,\n  title = {T},\n  OPTurl = {http://x},\n  OPTnote = {n},\n  OPTkeywords = {},\n}\n"
    out = run_tool(tmp_path, text, client=ExplodingClient(), command="format",
                   cfg={"all": {"url": "show", "show_fields": ["note", "keywords"]}})
    assert corrected(out) == "@misc{a,\n  title = {T},\n  url = {http://x},\n  note = {n},\n  OPTkeywords = {},\n}\n"


def test_a_real_field_that_starts_like_the_prefix_isnt_hidden():
    ref = bib.parse_bib("@book{a, title={T}, options={x}, OPTdoi={10.1/x}}")[0]
    assert ref.fields == {"title": "T", "options": "x"} and ref.hidden == {"doi": "10.1/x"}


def test_hidden_identifiers_still_identify_the_work():
    shown = bib.parse_bib("@article{a, title={T}, doi={10.1/x}, pages={1--2}}")[0]
    hidden = bib.parse_bib("@article{a, title={T}, OPTdoi={10.1/x}, OPTpages={1--2}}")[0]
    assert bib.doi_of(hidden) == "10.1/x" and bib.fingerprint(hidden) == bib.fingerprint(shown)


def test_hiding_a_field_costs_no_new_check(tmp_path):
    out = run_tool(tmp_path, client=FakeClient(only_first), cfg={"all": {"doi": "remove", "pages": "remove"}})
    again = tmp_path / "next.bib"
    again.write_text(corrected(out), encoding="utf-8")
    cli.run([str(again), "--out", str(out), "--write"], client=ExplodingClient(), env_file=tmp_path / "x",
            arxiv_client=FakeArxiv(), crossref_client=FakeCrossref(), link_client=FakeLinks())


def test_format_alone_uses_a_stored_record_to_add_a_doi(tmp_path):
    text = GOOD.replace("  doi     = {10.1000/real}\n", "").replace("{10--20},", "{10--20}")
    run_tool(tmp_path, text, client=FakeClient(only_first), command="verify")
    out = run_tool(tmp_path, text, client=ExplodingClient(), command="format", cfg={"all": {"doi": "add"}})
    assert parsed(out, "smith2020").get("doi") == "10.1000/real"


def test_check_entries_are_left_as_written_by_default(tmp_path):
    client = FakeClient(lambda c: entry("mismatch", matched=record(title="Some Other Paper")))
    out = run_tool(tmp_path, client=client, cfg={"all": {"title_case": "sentence"}})
    assert corrected(out) == GOOD
    assert "## Left as written: not verified" in read(out, "format")


def test_the_gate_can_be_opened(tmp_path):
    client = FakeClient(lambda c: entry("mismatch", matched=record(title="Some Other Paper")))
    out = run_tool(tmp_path, client=client, cfg={"all": {"title_case": "sentence"}, "skip_unverified": False})
    assert parsed(out, "smith2020").get("title") == "A real paper"


def test_by_default_changes_are_only_proposed(tmp_path, capsys):
    out = run_tool(tmp_path, BEFORE, client=ExplodingClient(), command="format", cfg=STYLE, write=False)
    assert not (out / "refs.corrected.bib").exists()
    assert (tmp_path / "refs.bib").read_text(encoding="utf-8") == BEFORE
    assert "Proposed only: `refs.bib` wasn't changed" in read(out, "changes") and (out / "refs.bib.diff").exists()
    assert "Add --write for a corrected copy, or --apply" in capsys.readouterr().out


def test_apply_changes_the_bib_and_keeps_a_backup(tmp_path, capsys):
    out = run_tool(tmp_path, BEFORE, client=ExplodingClient(), command="format", cfg=STYLE, write=False,
                   flags=["--apply"])
    assert (tmp_path / "refs.bib").read_text(encoding="utf-8") == AFTER
    assert (tmp_path / "refs.bib.bak").read_text(encoding="utf-8") == BEFORE
    assert not (out / "refs.corrected.bib").exists()
    assert "applied to `refs.bib`. The version before is in `refs.bib.bak`" in read(out, "changes")
    assert "Applied to" in capsys.readouterr().out


def test_apply_and_write_together(tmp_path):
    out = run_tool(tmp_path, BEFORE, client=ExplodingClient(), command="format", cfg=STYLE, flags=["--apply"])
    assert corrected(out) == AFTER == (tmp_path / "refs.bib").read_text(encoding="utf-8")


def test_apply_with_nothing_to_change_touches_nothing(tmp_path):
    run_tool(tmp_path, client=ExplodingClient(), command="format", write=False, flags=["--apply"])
    assert not (tmp_path / "refs.bib.bak").exists()


def test_apply_keeps_the_bibs_encoding_and_line_endings(tmp_path):
    text = "@article{a,\r\n  author = {M\u00fcller, J\u00fcrgen},\r\n  title = {T},\r\n  year = {2020},\r\n}\r\n"
    (tmp_path / "refs.bib").write_bytes(text.encode("latin-1"))
    write_config(tmp_path, {"all": {"authors": "initials"}})
    cli.run(["format", str(tmp_path / "refs.bib"), "--out", str(tmp_path / "out"), "--apply"])
    assert (tmp_path / "refs.bib").read_bytes() == text.replace("J\u00fcrgen", "J.").encode("latin-1")


def test_apply_refuses_what_the_encoding_cant_hold(tmp_path):
    text = "@article{a,\n  author = {M\u00fcller, J.},\n  title = {Old},\n  year = {2020},\n}\n"
    (tmp_path / "refs.bib").write_bytes(text.encode("latin-1"))
    client = FakeClient(lambda c: matched_entry(rec=record(title="Caf\u00e9 \u2013 Ny\u00e5", authors=(("M\u00fcller", "J."),),
                                                          year=2020, first=None, volume="", issue="", doi="")))
    with pytest.raises(SystemExit, match="can't store. Nothing was changed"):
        cli.run(["verify", str(tmp_path / "refs.bib"), "--out", str(tmp_path / "out"), "--apply"], client=client,
                env_file=tmp_path / "x", arxiv_client=FakeArxiv(), crossref_client=FakeCrossref(),
                link_client=FakeLinks())
    assert (tmp_path / "refs.bib").read_bytes() == text.encode("latin-1")


# --- changes.md and the diff --------------------------------------------------------------

def test_changes_credit_each_sweep(tmp_path):
    text = GOOD.replace("A Real Paper", "A Wrong Paper Title")
    out = run_tool(tmp_path, text, client=FakeClient(only_first), cfg={"all": {"title_case": "sentence"}})
    changes = read(out, "changes")
    assert changes.index("## Corrected from a registry") < changes.index("## Restyled")
    row = next(line for line in changes.splitlines() if line.startswith("| `title` | A Wrong Paper Title"))
    assert "| A real paper |" in row and "verify: Scholar Sidekick" in row and "format: title_case: sentence" in row


def test_the_diff_applies_to_the_original(tmp_path):
    out = run_tool(tmp_path, BEFORE, client=ExplodingClient(), command="format", cfg=STYLE)
    diff = (out / "refs.bib.diff").read_text(encoding="utf-8")
    assert diff.startswith("--- a/refs.bib\n+++ b/refs.bib\n")
    check = subprocess.run(["git", "apply", "--check", str(out / "refs.bib.diff")], cwd=tmp_path,
                           capture_output=True, text=True)
    assert check.returncode == 0, check.stderr
    subprocess.run(["git", "apply", str(out / "refs.bib.diff")], cwd=tmp_path, check=True)
    assert (tmp_path / "refs.bib").read_text(encoding="utf-8") == corrected(out)


def test_nothing_to_change_says_so_and_leaves_no_diff(tmp_path):
    out = run_tool(tmp_path, client=ExplodingClient(), command="format")
    assert "Nothing to change." in read(out, "changes") and not (out / "refs.bib.diff").exists()


# --- Consistency ---------------------------------------------------------------------------

CONSISTENCY_BIB = r"""
@inproceedings{v1, author={Smith, John}, title={Scheduling in Practice}, booktitle={Proc. IEEE INFOCOM'18}, year=2018, pages={1--9}}
@inproceedings{v2, author={Smith, J.}, title={Routing in Practice}, booktitle={INFOCOM}, year=2019, pages={1--9}}
@inproceedings{v3, author={Smith, Jane}, title={Caching in Practice}, booktitle={IEEE INFOCOM}, year=2020, pages={1--9}}
@inproceedings{v4, author={Lee, Amy}, title={Sensing in Practice}, booktitle={IEEE INFOCOM}, year=2021, pages={1--9}}
@inproceedings{v5, author={Lee, Amy}, title={Coding in Practice}, booktitle={IEEE INFOCOM}, year=2022}
@article{d1, author={Roe, Rita}, title={A Much Longer Duplicated Title}, journal={J}, year=2020}
@article{d2, author={Roe, Rita}, title={A much longer duplicated title}, journal={J}, year=2020}
"""


def test_consistency_finds_each_kind(tmp_path):
    out = run_tool(tmp_path, CONSISTENCY_BIB, client=ExplodingClient(), command="consistency")
    report = read(out, "consistency")
    assert report.index("## Possible duplicates") < report.index("## Venues written differently")
    assert "**d1**, **d2**: same title" in report
    assert '"Proc. IEEE INFOCOM": v1' in report and '"INFOCOM": v2' in report
    assert "**pages** in 4 of 5 conference entries. Missing from: v5" in report


def test_different_years_of_one_conference_are_not_inconsistent(tmp_path):
    # From a real bib: Proc. MobiCom'19, '10 and '20 are three papers, written the same way.
    text = "".join(f"@inproceedings{{m{y},\n  title = {{Paper {y}}},\n  booktitle = {{Proc. MobiCom'{y}}},\n"
                   f"  year = {{20{y}}},\n}}\n" for y in ("10", "19", "20"))
    out = run_tool(tmp_path, text, client=ExplodingClient(), command="consistency")
    assert "Venues written differently" not in read(out, "consistency")


def test_crossref_title_search_includes_the_first_author_and_year(tmp_path):
    # From a real paper: "Full Duplex Radios" alone ranks four other papers above Bharadia's.
    crossref = FakeCrossref()
    run_tool(tmp_path, ONE, client=FakeClient(lambda c: entry("not_found")), crossref=crossref,
             cfg={"url_check": "off"})
    assert crossref.asked == ("A Real Paper", "Smith", 2023)


def test_john_and_jane_are_not_merged():
    refs = bib.parse_bib(CONSISTENCY_BIB)
    assert author_variants(refs) == []  # J. Smith could be either, so nothing is claimed
    refs = [r for r in refs if r.key != "v3"]
    groups = author_variants(refs)
    assert len(groups) == 1 and set(groups[0]["forms"]) == {"John Smith", "J. Smith"}


def test_consistency_only_runs_the_sections_asked_for(tmp_path, capsys):
    out = run_tool(tmp_path, CONSISTENCY_BIB, client=ExplodingClient(), command="consistency",
                   flags=["--only", "duplicates"])
    report = read(out, "consistency")
    assert "## Possible duplicates" in report and "## Venues" not in report
    with pytest.raises(SystemExit, match="--only can't include dupes"):
        run_tool(tmp_path, CONSISTENCY_BIB, client=ExplodingClient(), command="consistency",
                 flags=["--only", "dupes"])


def test_fields_the_style_hides_arent_called_missing(tmp_path):
    out = run_tool(tmp_path, CONSISTENCY_BIB, client=ExplodingClient(), command="consistency",
                   cfg={"conference": {"pages": "remove"}})
    assert "**pages**" not in read(out, "consistency")


def test_consistency_works_on_a_pdf_alone(tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    make_pdf(pdf_path, [[(100, "References"),
                         (130, "[1] Jane Smith. 2020. A Much Longer Duplicated Title. Journal of Things."),
                         (150, "[2] Jane Smith. 2020. A much longer duplicated title. Journal of Things.")]])
    cli.run(["consistency", str(pdf_path), "--out", str(tmp_path / "out")])
    assert "**[1]**, **[2]**: same title" in read(tmp_path / "out", "consistency")


# --- Lint additions --------------------------------------------------------------------

def lint(text, backend="bibtex"):
    return [p for _, p in sanity.check_entries(bib.parse_bib(text), date(2026, 11, 6), backend=backend)]


@pytest.mark.parametrize("text,expected", [
    ("@misc{a, title={Massive MIMO for LTE}}", "capitals a BibTeX style may lowercase: MIMO, LTE"),
    ("@misc{a, title={The mmWave Band}}", "mmWave"),
    ("@article{a, title={T}, jounral={J}}", '"jounral" looks like a misspelling of "journal"'),
    ("@article{a, title={T}, booktitle={B}}", "@article entries take journal, not booktitle"),
    ("@inproceedings{a, title={T}, journal={J}}", "@inproceedings entries take booktitle, not journal"),
    ("@misc{a, title={T}, author={Müller, J.}}", "non-ASCII characters (ü)"),
])
def test_lint_compatibility_checks(text, expected):
    assert any(expected in p for p in lint(text)), lint(text)


@pytest.mark.parametrize("text", [
    "@misc{a, title={Massive {MIMO} for {LTE}}}", "@misc{a, title={{Massive MIMO for LTE}}}",
    "@misc{a, title={A \\LaTeX{} Guide}}", "@misc{a, title={T}, mycustomfield={x}}",
    "@misc{a, title={T}, pages={1–9}}", "@misc{a, title={T}, OPTjounral={x}}",
])
def test_lint_compatibility_stays_quiet(text):
    assert lint(text) == []


def test_an_unreadable_entry_is_reported_and_the_rest_still_checked(tmp_path):
    text = "@misc{broken, title={T}, = {X}}\n@misc{fine, title={Massive MIMO}}\n"
    out = run_tool(tmp_path, text, client=ExplodingClient(), command="lint")
    report = read(out, "lint")
    assert "**broken**: Line 1: this entry couldn't be read" in report and "**fine**" in report


def test_a_missing_closing_brace_still_stops_the_run(tmp_path):
    with pytest.raises(bib.BibError, match="missing its closing brace"):
        run_tool(tmp_path, "@misc{a, title={T}\n", client=ExplodingClient(), command="lint")


@pytest.mark.parametrize("title,expected", [
    ("Massive MIMO for 5G with Bayesian Priors", "Massive {MIMO} for {5G} with {Bayesian} Priors"),
    ("Sounding in GNU Radio", "Sounding in {GNU Radio}"),
    ("Using {MIMO} and \\LaTeX{} with $O(N)$ WiFi", "Using {MIMO} and \\LaTeX{} with $O(N)$ {WiFi}"),
    ("{{A Whole Braced MIMO Title}}", "{{A Whole Braced MIMO Title}}"),
    ("\\emph{GNU Radio} in Practice", "\\emph{GNU Radio} in Practice"),
    ("Plain words only", "Plain words only"),
])
def test_protect_capitals(title, expected):
    from refaudit import format as fmt
    assert fmt.protect_capitals(title, ["Bayesian", "GNU Radio"]) == expected


def test_protect_capitals_in_a_style_runs_after_title_case(tmp_path):
    text = "@misc{a,\n  title = {Massive MIMO Systems for Bayesian Inference},\n}\n"
    out = run_tool(tmp_path, text, client=ExplodingClient(), command="format",
                   cfg={"all": {"title_case": "sentence", "protect_capitals": "add"}, "keep_capitalized": ["Bayesian"]})
    assert parsed(out, "a").get("title") == "Massive {MIMO} systems for {Bayesian} inference"
    assert "format: title_case: sentence, protect_capitals: add" in read(out, "changes")


def test_lint_warns_about_keep_capitalized_words_and_format_fixes_them(tmp_path):
    text = "@misc{a,\n  title = {A Bayesian Take on MIMO},\n}\n"
    cfg = {"all": {"protect_capitals": "add"}, "keep_capitalized": ["Bayesian"]}
    out = run_tool(tmp_path, text, client=ExplodingClient(), command="lint", cfg=cfg)
    assert "may lowercase: Bayesian, MIMO" in read(out, "lint") and "protect_capitals: add" in read(out, "lint")
    fixed = run_tool(tmp_path, text, client=ExplodingClient(), command="format", cfg=cfg)
    (tmp_path / "again").mkdir()
    again = run_tool(tmp_path / "again", corrected(fixed), client=ExplodingClient(), command="lint", cfg=cfg)
    assert "No problems found." in read(again, "lint")


# --- The PDF's references, saved as a .bib ----------------------------------------------

PRINTED = [[(100, "References"),
            (130, "[1] Jane Smith and John Doe. 2020. A Real Paper. Journal of Things. doi:10.1000/real"),
            (150, "[2] Odd {braced} text C:\\path that won't split"),
            (170, "[3] Kim Lee. 2021. Second Paper on Things. arXiv:2101.00001")]]


def test_the_saved_reading_matches_the_pdf(tmp_path):
    from refaudit import pdf, pdfcache
    make_pdf(tmp_path / "paper.pdf", PRINTED)
    fresh = pdf.extract_references(tmp_path / "paper.pdf")
    first, note = pdfcache.load(tmp_path / "paper.pdf", tmp_path / "out")
    assert note.startswith("Found 3 references in the PDF")
    again, note = pdfcache.load(tmp_path / "paper.pdf", tmp_path / "out")
    assert note == "Read 3 references from paper.references.bib (the copy saved last time of paper.pdf)"
    for a, b in zip(fresh, again):
        assert (a.key, a.fields, a.raw, a.parse_ok, a.source) == (b.key, b.fields, b.raw, b.parse_ok, b.source)
        assert bib.fingerprint(a) == bib.fingerprint(b)


def test_the_pdf_is_read_once(tmp_path, monkeypatch):
    from refaudit import pdf, pdfcache
    make_pdf(tmp_path / "paper.pdf", PRINTED)
    pdfcache.load(tmp_path / "paper.pdf", tmp_path / "out")
    monkeypatch.setattr(pdf, "extract_references", lambda path: pytest.fail("read the PDF again"))
    assert len(pdfcache.load(tmp_path / "paper.pdf", tmp_path / "out")[0]) == 3


def test_your_edits_to_the_saved_reading_are_used_and_kept(tmp_path, monkeypatch):
    from refaudit import pdf, pdfcache
    make_pdf(tmp_path / "paper.pdf", PRINTED)
    pdfcache.load(tmp_path / "paper.pdf", tmp_path / "out")
    saved = tmp_path / "out" / "paper.references.bib"
    saved.write_text(saved.read_text(encoding="utf-8").replace("Second Paper on Things", "Second Paper, Fixed"),
                     encoding="utf-8")
    monkeypatch.setattr(pdfcache, "EXTRACTOR_VERSION", "2")  # even a newer reader keeps your edits
    refs, note = pdfcache.load(tmp_path / "paper.pdf", tmp_path / "out")
    assert "your edited copy" in note and refs[2].get("title") == "Second Paper, Fixed"


def test_a_changed_pdf_is_read_again_but_your_edits_are_not_overwritten(tmp_path):
    from refaudit import pdfcache
    make_pdf(tmp_path / "paper.pdf", PRINTED)
    pdfcache.load(tmp_path / "paper.pdf", tmp_path / "out")
    saved = tmp_path / "out" / "paper.references.bib"
    edited = saved.read_text(encoding="utf-8").replace("A Real Paper", "A Real Paper, Edited")
    saved.write_text(edited, encoding="utf-8")
    make_pdf(tmp_path / "paper.pdf", PRINTED[:1] + [[(100, "References"), (130, "[1] New. 2022. Only One.")]])
    refs, note = pdfcache.load(tmp_path / "paper.pdf", tmp_path / "out")
    assert "has changed since you edited" in note and saved.read_text(encoding="utf-8") == edited
    assert refs[0].get("title") != "A Real Paper, Edited"


def test_a_newer_reader_redoes_an_unedited_reading(tmp_path, monkeypatch):
    from refaudit import pdf, pdfcache
    make_pdf(tmp_path / "paper.pdf", PRINTED)
    pdfcache.load(tmp_path / "paper.pdf", tmp_path / "out")
    monkeypatch.setattr(pdfcache, "EXTRACTOR_VERSION", "2")
    calls = []
    real = pdf.extract_references
    monkeypatch.setattr(pdf, "extract_references", lambda path: calls.append(path) or real(path))
    pdfcache.load(tmp_path / "paper.pdf", tmp_path / "out")
    assert calls and "extractor=2" in (tmp_path / "out" / "paper.references.bib").read_text(encoding="utf-8")


def test_a_dry_run_doesnt_save_the_reading(tmp_path):
    make_pdf(tmp_path / "paper.pdf", PRINTED)
    cli.run(["verify", str(tmp_path / "paper.pdf"), "--out", str(tmp_path / "out"), "--dry-run"])
    assert not (tmp_path / "out").exists()


# --- After --apply, lint and consistency check the changed bib -------------------------

def test_lint_after_apply_checks_the_changed_bib(tmp_path):
    text = "@misc{a,\n  title = {Massive MIMO Systems},\n  year = {2020},\n}\n"
    cfg = {"all": {"protect_capitals": "add"}, "skip_unverified": False}  # not found, but restyled anyway
    out = run_tool(tmp_path, text, client=FakeClient(lambda c: entry("not_found")), cfg=cfg, write=False,
                   flags=["--apply"])
    assert "{MIMO}" in (tmp_path / "refs.bib").read_text(encoding="utf-8")
    assert "No problems found." in read(out, "lint")
    assert "after the changes were applied" in read(out, "lint")


def test_without_apply_lint_checks_the_bib_as_it_is(tmp_path):
    text = "@misc{a,\n  title = {Massive MIMO Systems},\n  year = {2020},\n}\n"
    out = run_tool(tmp_path, text, client=FakeClient(lambda c: entry("not_found")),
                   cfg={"all": {"protect_capitals": "add"}}, flags=["--write"])
    assert "may lowercase: MIMO" in read(out, "lint")


# --- From the wacky-style experiment on the 2024 MobiCom demo ---------------------------

@pytest.mark.parametrize("given,expected", [
    ("C.-B.", "C.-B."), ("Chan-Byoung", "C.-B."), ("Jean-Luc", "J.-L."), ("A.M.", "A. M."),
    ("Min Keun", "M. K."), ("J. K.", "J. K."),
])
def test_initials_are_stable(given, expected):
    from refaudit import format as fmt
    assert fmt._initials(given) == expected and fmt._initials(expected) == expected


def test_an_organization_keeps_its_name_under_initials():
    from refaudit import format as fmt
    assert fmt.authors_to_initials("Columbia University and Chae, Chan-Byoung and {GNU Radio} and Ettus Research") \
        == "Columbia University and Chae, C.-B. and {GNU Radio} and Ettus Research"


@pytest.mark.parametrize("name,flagged", [
    ("Columbia University", True), ("Ettus Research", True), ("GNU Radio", True),
    ("{Columbia University}", False), ("Smith, Jane", False), ("Jane Smith", False), ("3GPP", False),
])
def test_lint_asks_for_braces_around_an_organization(name, flagged):
    found = lint(f"@misc{{a, title={{T}}, author={{{name}}}}}")
    assert any("looks like an organization" in p for p in found) == flagged, found


@pytest.mark.parametrize("venue,expected", [
    ("Proc.~SRIF'13", "Proc.~SRIF'13"),             # was "Proc. Proc.~SRIF'13"
    ("Proc.~IEEE ICT'18", "Proc.~IEEE ICT'18"),
    ("Proc. IEEE/MTT-S~IMS'20", "Proc. IEEE/MTT-S~IMS'20"),
    ("GRCon", "Proc. GRCon'18"),
])
def test_a_non_breaking_space_in_a_venue_is_a_space(venue, expected):
    from refaudit import format as fmt
    venues = fmt.load_venues(["computer_science", "electrical_computer_engineering"], None)
    rules = {"venue_prefix": "add", "venue_sponsor": "add", "venue_year": "add"}
    assert fmt.style_venue(venue, 2018, rules, venues, venue)[0] == expected


def test_added_braces_show_in_the_reports(tmp_path):
    text = "@misc{a,\n  title = {Achieving 54dB SIC},\n}\n"
    out = run_tool(tmp_path, text, client=ExplodingClient(), command="format",
                   cfg={"all": {"protect_capitals": "add"}})
    assert "| `Achieving 54dB SIC` | `Achieving 54dB {SIC}` |" in read(out, "changes")
    assert "`title`: `Achieving 54dB SIC` → `Achieving 54dB {SIC}`" in read(out, "format")


def test_a_style_given_as_a_path_is_named_once(tmp_path):
    style = tmp_path / "wacky.yaml"
    style.write_text("all: {authors: initials}\n", encoding="utf-8")
    out = run_tool(tmp_path, ONE, client=ExplodingClient(), command="format", flags=["--style", str(style)])
    assert f"Style: **wacky** ({style})." in read(out, "format")
    assert "(style wacky)" in read(out, "changes")


def test_the_wacky_style_is_stable_on_a_second_run(tmp_path):
    text = ("@article{Chung,\n  author = {Chung, Min Keun and Chae, Chan-Byoung and Columbia University},\n"
            "  title = {Prototyping Real-Time Full Duplex Radios at 54dB},\n  journal = {IEEE Commun. Mag.},\n"
            "  year = {2015},\n}\n@inproceedings{s,\n  title = {Sensing},\n  booktitle = {Proc.~SRIF'13},\n"
            "  year = {2013},\n}\n")
    cfg = {"all": {"title_case": "title", "protect_capitals": "add", "authors": "initials"},
           "conference": {"venue_names": "abbreviated", "venue_prefix": "add", "venue_sponsor": "add",
                          "venue_year": "add"}, "journal": {"venue_names": "full"}}
    first = corrected(run_tool(tmp_path, text, client=ExplodingClient(), command="format", cfg=cfg))
    (tmp_path / "again").mkdir()
    second = corrected(run_tool(tmp_path / "again", first, client=ExplodingClient(), command="format", cfg=cfg))
    assert second == first
    assert "Chae, C.-B. and Columbia University" in first and "Proc.~SRIF'13" in first


# --- Heuristics in the config ------------------------------------------------------------

def test_units_are_left_alone_by_default_and_the_list_is_yours(tmp_path):
    text = "@misc{a,\n  title = {A 54dB MIMO Canceller at 2.4GHz},\n}\n"
    out = run_tool(tmp_path, text, client=ExplodingClient(), command="lint")
    assert "may lowercase: MIMO." in read(out, "lint")  # not dB or GHz
    (tmp_path / "mine").mkdir()
    out = run_tool(tmp_path / "mine", text, client=ExplodingClient(), command="lint", cfg={"capitals_ignore": []})
    assert "may lowercase: 54dB, MIMO, 4GHz." in read(out, "lint")
    from refaudit import format as fmt
    assert fmt.at_risk("5G and 80MHz BW at 15dBm", [], ["MHz", "dBm"]) == ["5G", "BW"]


def test_protect_capitals_skips_capitals_ignore(tmp_path):
    text = "@misc{a,\n  title = {MIMO at 28 GHz},\n}\n"
    out = run_tool(tmp_path, text, client=ExplodingClient(), command="format",
                   cfg={"all": {"protect_capitals": "add"}})
    assert parsed(out, "a").get("title") == "{MIMO} at 28 GHz"


def test_organization_words_are_configurable(tmp_path):
    text = "@misc{a,\n  title = {T},\n  author = {Acme Widgets and Smith, Jane},\n}\n"
    cfg = {"all": {"authors": "initials"}, "organization_words": ["widgets"]}
    out = run_tool(tmp_path, text, client=ExplodingClient(), command="format", cfg=cfg)
    assert parsed(out, "a").get("author") == "Acme Widgets and Smith, J."
    (tmp_path / "lint").mkdir()
    out = run_tool(tmp_path / "lint", text, client=ExplodingClient(), command="lint", cfg=cfg)
    assert '"Acme Widgets" looks like an organization' in read(out, "lint")


def test_duplicate_thresholds_are_configurable(tmp_path):
    text = ("@misc{a, title={Open access full-duplex wireless in the ORBIT testbed}}\n"
            "@misc{b, title={Open access full-duplex wireless in the ORBIT testbeds}}\n")
    out = run_tool(tmp_path, text, client=ExplodingClient(), command="consistency")
    assert "nearly the same title" in read(out, "consistency")
    (tmp_path / "strict").mkdir()
    out = run_tool(tmp_path / "strict", text, client=ExplodingClient(), command="consistency",
                   cfg={"duplicate_similarity": 0.999})
    assert "Possible duplicates" not in read(out, "consistency")


@pytest.mark.parametrize("data", [{"duplicate_similarity": 2}, {"duplicate_year_gap": -1},
                                  {"duplicate_word_overlap": "half"}])
def test_invalid_duplicate_thresholds_stop(data):
    with pytest.raises(config.ConfigError):
        config.build({"consistency": data}, style_data={})


# --- Venue sponsors ----------------------------------------------------------------------

@pytest.mark.parametrize("original,expected", [
    ("Proc. ACM SIGCOMM'23", "Proc. ACM SIGCOMM'23"),              # was "SIGCOMM": keep dropped all three
    ("Proceedings of the 2013 IEEE Conference on Computer Communications", "Proc. IEEE INFOCOM 2013"),
    ("Proc. IEEE/ACM IPSN'20", "Proc. IEEE/ACM IPSN'20"),
    ("Proc.~SRIF'13", "Proc.~SRIF'13"),
    ("USENIX Security Symposium", "USENIX Security"),               # not "USENIX USENIX Security"
    ("International Conference on Machine Learning", "ICML"),       # nothing written, nothing added
])
def test_keep_holds_on_to_what_you_wrote(tmp_path, original, expected):
    text = f"@inproceedings{{a,\n  title = {{T}},\n  booktitle = {{{original}}},\n  year = {{2023}},\n}}\n"
    out = run_tool(tmp_path, text, client=ExplodingClient(), command="format",
                   cfg={"conference": {"venue_names": "abbreviated"}})
    assert parsed(out, "a").get("booktitle") == expected


def test_each_venue_list_brings_its_fields_sponsors():
    from refaudit import format as fmt
    assert "ACM" in fmt.load_venues(["computer_science"], None).sponsors
    ece = fmt.load_venues(["electrical_computer_engineering"], None).sponsors
    assert "IET" in ece and "ACM" not in ece
    assert fmt.load_venues(["computer_science"], None).sponsor_re.match("ACM-SIAM SODA").group(1) == "ACM-SIAM"
    assert fmt.load_venues([], None).sponsors == ["IEEE", "ACM", "USENIX"]  # nothing declared


def test_your_venue_file_can_add_sponsors(tmp_path):
    from refaudit import format as fmt
    mine = tmp_path / "my_venues.yaml"
    mine.write_text('sponsors: [Springer]\n"International Conference on Communications": ICC\n', encoding="utf-8")
    venues = fmt.load_venues(["computer_science"], mine)
    assert venues.sponsors[0] == "Springer" and "ACM" in venues.sponsors
    rules = {"venue_prefix": "keep", "venue_sponsor": "remove", "venue_year": "keep"}
    assert fmt.style_venue("Springer NETWORKING", 2020, rules, venues, "Springer NETWORKING")[0] == "NETWORKING"
    plain = fmt.load_venues(["computer_science"], None)
    assert fmt.style_venue("Springer NETWORKING", 2020, rules, plain, "Springer NETWORKING")[0] == "Springer NETWORKING"


@pytest.mark.parametrize("text", ["sponsors: IEEE\n", "sponsors: [IEEE, 3]\n", '"Some Venue": [A, B]\n'])
def test_a_malformed_venue_file_stops(tmp_path, text):
    from refaudit import format as fmt
    bad = tmp_path / "bad.yaml"
    bad.write_text(text, encoding="utf-8")
    with pytest.raises(config.ConfigError):
        fmt.load_venues([], bad)


def test_biber_allows_utf8():
    assert lint("@misc{a, title={T}, author={Müller, J.}}", backend="biber") == []


def test_lint_checks_can_be_turned_off(tmp_path):
    out = run_tool(tmp_path, "@misc{a, title={Massive MIMO}}\n", client=ExplodingClient(), command="lint",
                   cfg={"unprotected_capitals": False})
    assert "No problems found." in read(out, "lint")


def test_known_fields_include_what_the_parser_hides_by():
    assert config.FormatConfig().hidden_prefix == "OPT" and "doi" in bib.KNOWN_FIELDS
