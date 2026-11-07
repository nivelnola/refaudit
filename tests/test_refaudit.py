import hashlib
import io
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
import yaml

from conftest import ExplodingClient, FakeClient, matched_entry, record
from refaudit import api, bib, check, cli, config, duplicates, format as fmt, ledger, pdf, progress, report, sanity
from refaudit import report as report_module

ROOT = Path(__file__).resolve().parent.parent

GOOD = """% A comment that must survive.
@article{smith2020,
  author  = {Smith, Jane and Doe, John},
  title   = {A Real Paper},
  journal = {Journal of Things},
  year    = {2020},
  volume  = {12},
  number  = {3},
  pages   = {10--20},
  doi     = {10.1000/real}
}

% Another comment.
@misc{other, title = {Second Paper on Things}, year = 2021, note = "x"}
"""


ONE = "@misc{a,\n  author = {Smith, Jane},\n  title = {A Real Paper},\n  year = {2023}\n}\n"
BOTH = ONE.replace("Smith, Jane", "Smith, Jane and Doe, John").replace("2023", "2020")


class FakeArxiv:
    """Stands in for arXiv. Finds nothing unless given papers; None means unreachable."""

    def __init__(self, papers=()):
        self.papers = papers
        self.calls = 0

    def by_id(self, arxiv_id):
        self.calls += 1
        return self.papers if self.papers is None else list(self.papers)

    by_title = by_id

    def by_ids(self, ids):
        self.calls += 1
        return None if self.papers is None else {p["id"].lower(): p for p in self.papers}


def paper(title="A Real Paper", authors=("Jane Smith", "John Doe"), **extra):
    return {"id": "2305.02822", "title": title, "authors": list(authors), "year": 2023, "doi": "",
            "journal_ref": "", "withdrawn": False, **extra}


class FakeCrossref:
    def __init__(self, records=(), full=None):
        self.records = records
        self.full = full if full is not None else {}
        self.calls = 0

    def by_doi(self, doi):
        self.calls += 1
        return self.full

    def by_title(self, title, author="", year=None):
        self.calls += 1
        self.asked = (title, author, year)
        return self.records if self.records is None else list(self.records)


class FakeLinks:
    def __init__(self, answer=("unknown", "HTTP 403")):
        self.answer = answer
        self.calls = 0
        self.urls = []

    def check(self, url):
        self.calls += 1
        self.urls.append(url)
        return self.answer


REPORTS = ("summary", "compare", "verify", "format", "lint", "consistency", "changes")


def read(out, name):
    """One sweep's report."""
    return (out / f"{name}.md").read_text(encoding="utf-8")


def report_text(out):
    """Every report a run wrote, in reading order."""
    return "\n".join((out / f"{name}.md").read_text(encoding="utf-8") for name in REPORTS
                     if (out / f"{name}.md").exists())


def write_config(folder, cfg):
    """Write a flat test config as the config folder: one file per sweep, rules in a style."""
    home = {key: name for name, keys in config.FILES.items() for key in keys}
    files, style = {}, {}
    for key, value in cfg.items():
        if key in ("all", *config.TYPES):
            style[key] = value
        else:
            files.setdefault(home.get(key, "verify"), {})[key] = value
    target = folder / "refaudit_config"
    (target / "styles").mkdir(parents=True, exist_ok=True)
    (target / "styles" / "test.yaml").write_text(yaml.safe_dump(style), encoding="utf-8")
    files.setdefault("format", {})["style"] = "test"
    for name, data in files.items():
        (target / f"{name}.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def run_tool(tmp_path, text=GOOD, client=None, flags=(), cfg=None, name="refs.bib", today=None,
             arxiv=None, crossref=None, links=None, command="full", write=True):
    """Run a command. Commands that can change a bib get --write, so corrected() can read it."""
    path = tmp_path / name
    if write and command in cli.WRITERS:
        flags = [*flags, "--write"]
    path.write_text(text, encoding="utf-8", newline="")
    if cfg is not None:
        write_config(tmp_path, cfg)
    code = cli.run([command, str(path), "--out", str(tmp_path / "out"), *flags], client=client or FakeClient(),
                   today=today, env_file=tmp_path / "missing.env", arxiv_client=arxiv or FakeArxiv(),
                   crossref_client=crossref or FakeCrossref(), link_client=links or FakeLinks())
    assert code == 0
    return tmp_path / "out"


def corrected(out, stem="refs"):
    return (out / f"{stem}.corrected.bib").read_text(encoding="utf-8")


def parsed(out, key, stem="refs"):
    return next(r for r in bib.parse_bib(corrected(out, stem)) if r.key == key)


def only_first(claim):
    """The first entry's record for it, and a matching record for the second."""
    if "Second" in claim["title"]:
        return matched_entry(rec=record(title="Second Paper on Things", doi="10.1000/two", year=2021,
                                        authors=(), first=None))
    return matched_entry()


def entry(verdict, **extra):
    return {"status": "ok", "verdict": verdict, "confidence": "high", "matched": None, "mismatches": [],
            "candidates": [], "retraction": {}, "_provenance": {"stages_run": ["compare"],
                                                                "resolved_via": "crossref"}, **extra}


# --- Safety ------------------------------------------------------------------------

def test_mismatch_is_never_rewritten(tmp_path):
    client = FakeClient(lambda c: entry("mismatch", matched=record(title="Some Other Paper")))
    out = run_tool(tmp_path, client=client, cfg={"all": {"title_case": "sentence", "doi": "remove"}})
    assert corrected(out) == GOOD
    assert "CHECK" in report_text(out)


@pytest.mark.parametrize("verdict", ["ambiguous", "not_found"])
def test_unresolved_is_never_rewritten(tmp_path, verdict):
    out = run_tool(tmp_path, client=FakeClient(lambda c: entry(verdict)), cfg={"all": {"doi": "remove"}})
    assert corrected(out) == GOOD


def test_retracted_is_check_even_when_matched(tmp_path):
    def answer(claim):
        result = matched_entry()
        result["retraction"]["isRetracted"] = True
        return result
    out = run_tool(tmp_path, client=FakeClient(answer))
    assert corrected(out) == GOOD
    assert "Retracted" in report_text(out)


def test_api_failure_is_never_ok(tmp_path):
    out = run_tool(tmp_path, client=FakeClient(fail=api.ApiError("network problem")))
    report = report_text(out)
    assert corrected(out) == GOOD
    assert "Not checked: network problem" in report
    assert "## OK" not in report


def test_failed_results_are_not_stored(tmp_path):
    run_tool(tmp_path, client=FakeClient(fail=api.ApiError("x")))
    client = FakeClient(only_first)
    run_tool(tmp_path, client=client)
    assert client.calls == 1


def test_not_found_says_not_necessarily_fake(tmp_path):
    out = run_tool(tmp_path, client=FakeClient(lambda c: entry("not_found")))
    assert "doesn't mean a reference is fake" in report_text(out)


def test_title_search_match_corrects_nothing(tmp_path):
    def answer(claim):
        rec = record(volume="99", first="1", last="2", year=2017, kind="preprint",
                     venue="arXiv (Cornell University)")
        return matched_entry(rec=rec, via="openalex-search")
    out = run_tool(tmp_path, client=FakeClient(answer), cfg={"all": {"doi": "add", "pages": "add"}})
    assert parsed(out, "smith2020").get("volume") == "12"
    assert parsed(out, "smith2020").get("year") == "2020"
    assert not parsed(out, "other").get("doi")
    assert "(title match)" in report_text(out)


def test_key_never_appears_in_output(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SCHOLAR_API_KEY=ssk_secret123\n", encoding="utf-8")
    assert config.read_api_key(env) == "ssk_secret123"
    out = run_tool(tmp_path, client=FakeClient(only_first))
    for path in out.iterdir():
        assert "ssk_secret123" not in path.read_text(encoding="utf-8")


def test_system_environment_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("SCHOLAR_API_KEY", "ssk_from_system")
    assert config.read_api_key(tmp_path / "missing.env") is None


def test_original_file_is_untouched(tmp_path):
    path = tmp_path / "refs.bib"
    run_tool(tmp_path, client=FakeClient(only_first), cfg={"all": {"title_case": "sentence"}})
    assert path.read_text(encoding="utf-8") == GOOD


# --- Corrections -------------------------------------------------------------------

def test_wrong_details_are_fixed(tmp_path):
    text = GOOD.replace("{12}", "{13}").replace("10--20", "10--25").replace("Doe, John", "Dough, John")
    out = run_tool(tmp_path, text, client=FakeClient(only_first))
    ref = parsed(out, "smith2020")
    assert ref.get("volume") == "12"
    assert ref.get("pages") == "10--20"
    assert "Doe, John" in ref.get("author")
    assert "% A comment that must survive." in corrected(out)
    assert "## CORRECTED" in (out / "verify.md").read_text(encoding="utf-8")


def test_medium_confidence_flags_instead_of_fixing(tmp_path):
    text = GOOD.replace("{12}", "{13}")
    client = FakeClient(lambda c: matched_entry(confidence="medium"))
    out = run_tool(tmp_path, text, client=client)
    assert parsed(out, "smith2020").get("volume") == "13"
    assert "below what auto_correct (high) needs" in report_text(out)


def test_auto_correct_medium_fixes(tmp_path):
    text = GOOD.replace("{12}", "{13}")
    client = FakeClient(lambda c: matched_entry(confidence="medium"))
    out = run_tool(tmp_path, text, client=client, cfg={"auto_correct": "medium"})
    assert parsed(out, "smith2020").get("volume") == "12"


def test_auto_correct_off_never_fixes(tmp_path):
    text = GOOD.replace("{12}", "{13}")
    out = run_tool(tmp_path, text, client=FakeClient(only_first), cfg={"auto_correct": "off"})
    assert parsed(out, "smith2020").get("volume") == "13"


def test_and_others_is_not_an_author_error(tmp_path):
    text = GOOD.replace("Smith, Jane and Doe, John", "Smith, Jane and others")
    out = run_tool(tmp_path, text, client=FakeClient(only_first))
    assert "others" in parsed(out, "smith2020").get("author")


def test_accents_and_latex_are_not_author_errors():
    ref = bib.parse_bib('@article{a, author={M{\\"u}ller, Hans}, title={T}}')[0]
    assert bib.authors_of(ref)[0][0][0] == "Müller"
    rec = api.trim_record(record(authors=(("Muller", "Hans"),)))
    assert "author" not in check.compare(ref, rec)[0]


# These came from a real audit, where registry data would have made good entries worse.
@pytest.mark.parametrize("ours,theirs", [
    ("Li, X. Rong and Jilkov, Vesselin P.", (("Rong Li", "X."), ("Jilkov", "V.P."))),
    ("L{\\'o}pez Escoriza, Adri{\\`a}", (("Escoriza", "Adria Lopez"),)),
    ("Carneiro Viana, Aline", (("Viana", "Aline Carneiro"),)),
    ("Rezende, J. F. D.", (("de Rezende", "José F."),)),
])
def test_compound_names_split_differently_are_the_same_people(ours, theirs):
    ref = bib.parse_bib(f"@article{{a, author={{{ours}}}, title={{A Real Paper}}}}")[0]
    assert "author" not in check.compare(ref, api.trim_record(record(authors=theirs)))[0]


@pytest.mark.parametrize("a,b", [("Danış", "Daniş"), ("Sørensen", "Sorensen"), ("Łukasz", "Lukasz"),
                                 ("Weiß", "Weiss"), ("Đặng", "Dang")])
def test_letters_without_separable_accents_compare_equal(a, b):
    assert bib.fold(a) == bib.fold(b)


def test_truncated_author_list_is_corrected():
    ref = bib.parse_bib("@article{a, author={Peng, B. and Seco-Granados, G.}, title={A Real Paper}}")[0]
    rec = api.trim_record(record(authors=(("Peng", "Bile"), ("Seco-Granados", "G"), ("Wymeersch", "Henk Wymeersch"))))
    assert check.compare(ref, rec)[0]["author"] == "Peng, Bile and Seco-Granados, G and Wymeersch, Henk"


def test_different_person_is_corrected():
    ref = bib.parse_bib("@article{a, author={Jones, Amy}, title={A Real Paper}}")[0]
    assert "author" in check.compare(ref, api.trim_record(record(authors=(("Smith", "Jane"),))))[0]


@pytest.mark.parametrize("issue,first,last", [("103121", "103121", None), ("100682", "1", "9")])
def test_article_number_is_not_an_issue(issue, first, last):
    ref = bib.parse_bib("@article{a, title={A Real Paper}}")[0]
    rec = api.trim_record(record(issue=issue, first=first, last=last, authors=()))
    assert "number" not in check.compare(ref, rec)[0]


def test_weak_candidates_are_hidden(tmp_path):
    def answer(claim):
        weak = {"item": record(title="Unrelated Thing", doi="10.1/weak"), "score": 0.8}
        strong = {"item": record(title="Close Thing", doi="10.1/strong"), "score": 0.95}
        return entry("not_found", candidates=[strong, weak])
    report = report_text(run_tool(tmp_path, client=FakeClient(answer)))
    assert "Close Thing" in report and "Unrelated Thing" not in report


def test_book_edition_difference_is_not_called_fabricated(tmp_path):
    text = "@book{a, title={Signal Processing}, year=2010}"
    mismatch = [{"field": "year", "claimed": 2010, "resolved": 1989, "similarity": 0}]
    client = FakeClient(lambda c: entry("mismatch", mismatches=mismatch))
    report = report_text(run_tool(tmp_path, text, client=client))
    assert "different edition" in report and "fabricated" not in report


def test_title_mismatch_is_called_possibly_fabricated(tmp_path):
    mismatch = [{"field": "title", "claimed": "A", "resolved": "B", "similarity": 0.1}]
    client = FakeClient(lambda c: entry("mismatch", mismatches=mismatch))
    report = report_text(run_tool(tmp_path, client=client))
    assert "fabricated" in report


def test_conference_venue_year_not_taken_from_preprint_record():
    ref = bib.parse_bib("@inproceedings{a, title={A Real Paper}, booktitle={NeurIPS}, year=2017, "
                        "doi={10.48550/arxiv.1}}")[0]
    rec = api.trim_record(record(year=2016, doi="10.48550/arxiv.1", kind="preprint"))
    corrections, notes = check.compare(ref, rec)
    assert "year" not in corrections
    assert notes


# --- Bib round trip ----------------------------------------------------------------

def test_no_changes_leaves_file_byte_identical():
    text = GOOD.replace("\n", "\r\n")
    assert bib.write_corrected(text, [(r, {}) for r in bib.parse_bib(text)]) == text


def test_edits_touch_only_the_changed_field():
    ref = bib.parse_bib(GOOD)[0]
    new = bib.write_corrected(GOOD, [(ref, {"volume": "99"})])
    assert new == GOOD.replace("{12}", "{99}")


def test_remove_and_add_fields():
    ref = bib.parse_bib(GOOD)[0]
    new = bib.write_corrected(GOOD, [(ref, {"doi": None, "note": "hello"})])
    again = bib.parse_bib(new)[0]
    assert not again.get("doi")
    assert again.get("note") == "hello"
    assert again.get("pages") == "10--20"
    assert "% A comment" in new


def test_add_field_when_last_field_has_no_comma_or_is_removed():
    text = "@article{k,\n  title = {T},\n  doi = {D}\n}\n"
    ref = bib.parse_bib(text)[0]
    for changes in ({"note": "n"}, {"doi": None, "note": "n"}):
        again = bib.parse_bib(bib.write_corrected(text, [(ref, changes)]))[0]
        assert again.get("note") == "n" and again.get("title") == "T"


def test_macros_and_concatenation():
    text = '@string{jt = "Journal of Things"}\n@article{a, title = "A " # "B", journal = jt, month = jan}'
    ref = bib.parse_bib(text)[0]
    assert ref.get("title") == "A B"
    assert ref.get("journal") == "Journal of Things"
    assert ref.get("month") == "jan"


def test_commented_out_entry_is_ignored():
    assert not bib.parse_bib("% @article{gone, title={x}}\n")


def test_unbalanced_entry_gives_clear_error():
    with pytest.raises(bib.BibError, match="Line 2"):
        bib.parse_bib("\n@article{a, title={x}\n")


# --- Config ------------------------------------------------------------------------

def test_defaults_change_nothing(tmp_path):
    out = run_tool(tmp_path, client=FakeClient(only_first))
    assert corrected(out) == GOOD


def test_shipped_config_changes_nothing():
    cfg = config.load(ROOT / "refaudit_config")
    assert cfg.format.style == "default"
    rules = cfg.format.rules
    assert all(value in ("keep", []) for t in config.TYPES for value in rules[t].values())


@pytest.mark.parametrize("name", ["default", "ieee", "acm", "wimnet"])
def test_shipped_styles_load(name):
    path = config.find_style(name, None)
    assert path.parent == ROOT / "styles"
    assert config.build_style(yaml.safe_load(path.read_text(encoding="utf-8")))


def test_shipped_config_files_hold_only_their_own_settings():
    for name in config.FILES:
        data = yaml.safe_load((ROOT / "refaudit_config" / f"{name}.yaml").read_text(encoding="utf-8")) or {}
        assert set(data) <= set(config.FILES[name])


def test_type_sections_override_all():
    cfg = config.build(style_data={"all": {"pages": "add"}, "conference": {"pages": "remove"}}).format
    assert cfg.for_type("conference")["pages"] == "remove"
    assert cfg.for_type("journal")["pages"] == "add"


def test_typo_gives_clear_message():
    with pytest.raises(config.ConfigError, match='title_case can\'t be "sentance". Use keep, sentence or title.'):
        config.build_style({"all": {"title_case": "sentance"}})


@pytest.mark.parametrize("files,style", [
    ({"verify": {"titel_case": "keep"}}, None), ({}, {"all": {"pagez": "keep"}}),
    ({"verify": {"recheck_after_days": "soon"}}, None), ({"verify": {"auto_correct": "maybe"}}, None),
    ({"format": {"abbreviations_file": "nope.yaml"}}, None), ({"styles": {}}, None),
    ({"lint": {"backend": "latex"}}, None), ({"consistency": {"min_share": 2}}, None),
    ({"format": {"hidden_prefix": "has space"}}, None), ({}, {"journl": {}}),
])
def test_invalid_config_stops(files, style):
    with pytest.raises(config.ConfigError):
        config.build(files, style_data=style or {})


@pytest.mark.parametrize("files,message", [
    ({"format": {"auto_correct": "high"}}, "auto_correct belongs in verify.yaml, not format.yaml"),
    ({"verify": {"keep_capitalized": []}}, "keep_capitalized belongs in format.yaml"),
    ({"format": {"all": {"doi": "remove"}}}, "all is a style rule. Put it in a style file under styles/"),
])
def test_a_setting_in_the_wrong_file_says_where_it_goes(files, message):
    with pytest.raises(config.ConfigError, match=re.escape(message)):
        config.build(files, style_data={})


def test_yaml_off_is_accepted():
    files = {"verify": yaml.safe_load("auto_correct: off\nurl_check: off")}
    cfg = config.build(files, style_data={}).verify
    assert cfg.auto_correct == "off" and cfg.url_check == "off"


def test_unknown_style_lists_the_ones_there_are():
    with pytest.raises(config.ConfigError, match="No style named \"nope\". Available: acm, default, ieee, wimnet"):
        config.find_style("nope", None)


def test_a_style_in_the_config_folder_beats_the_tools(tmp_path):
    (tmp_path / "styles").mkdir()
    (tmp_path / "styles" / "ieee.yaml").write_text("all: {doi: remove}\n", encoding="utf-8")
    cfg = config.load(tmp_path, style="ieee").format
    assert cfg.style_path == tmp_path / "styles" / "ieee.yaml" and cfg.for_type("journal")["doi"] == "remove"


def test_style_flag_overrides_format_yaml(tmp_path):
    out = run_tool(tmp_path, BEFORE, client=ExplodingClient(), command="format", cfg=STYLE,
                   flags=["--style", "ieee"])
    assert "Style: **ieee**" in (out / "format.md").read_text(encoding="utf-8")
    assert "booktitle = {Proc. ICML}" in corrected(out) and "Bharadia, D." in corrected(out)


def test_a_stray_yaml_in_the_config_folder_stops(tmp_path):
    (tmp_path / "old.yaml").write_text("x: 1\n", encoding="utf-8")
    with pytest.raises(config.ConfigError, match="old.yaml, which refaudit doesn't read"):
        config.load(tmp_path)


@pytest.mark.parametrize("text,expected", [
    ("@inproceedings{a, title={T}}", "conference"),
    ("@conference{a, title={T}}", "conference"),
    ("@article{a, title={T}, journal={J}}", "journal"),
    ("@article{a, title={T}, journal={arXiv preprint arXiv:2101.00001}}", "preprint"),
    ("@misc{a, title={T}, eprint={2101.00001}, archivePrefix={arXiv}}", "preprint"),
    ("@book{a, title={T}}", "book"), ("@inbook{a, title={T}}", "book"), ("@incollection{a, title={T}}", "book"),
    ("@techreport{a, title={T}}", "techreport"), ("@manual{a, title={T}}", "techreport"),
    ("@standard{a, title={T}}", "techreport"), ("@misc{a, title={T}}", "other"),
    ("@phdthesis{a, title={T}}", "other"),
])
def test_entry_types(text, expected):
    assert bib.type_of(bib.parse_bib(text)[0]) == expected


def test_readme_documents_every_option():
    readme = "\n".join((path).read_text(encoding="utf-8") for path in (ROOT / "README.md", ROOT / "styles" / "README.md"))
    keys = [key for keys in config.FILES.values() for key in keys]
    keys += [*config.RULES, *config.LIST_RULES, *config.AUTO_CORRECT, *config.URL_CHECK, *config.BACKENDS]
    keys += [value for values in config.RULES.values() for value in values]
    keys += [f"{name}.yaml" for name in config.FILES]
    keys += ["full", "verify", "format", "lint", "consistency", "compare"]
    keys += ["--dry-run", "--recheck", "--config", "--out", "--style", "--write", "--apply", "--only", "--help"]
    missing = [k for k in keys if f"`{k}`" not in readme]
    assert not missing, f"README is missing: {missing}"


# --- Formatting --------------------------------------------------------------------

@pytest.mark.parametrize("title,expected", [
    ("Attention Is All You Need", "Attention is all you need"),
    ("{BERT}: Pre-Training of Deep Networks", "{BERT}: Pre-training of deep networks"),
    ("Deep ResNet Models for BERT", "Deep ResNet models for BERT"),
    ("A Bayesian Method: The Next Step", "A Bayesian method: The next step"),
    ("Localization in 5G Networks", "Localization in 5G networks"),
])
def test_sentence_case(title, expected):
    assert fmt.to_sentence_case(title, ["Bayesian"]) == expected


@pytest.mark.parametrize("title,expected", [
    ("attention is all you need", "Attention Is All You Need"),
    ("a method for the {BERT} model", "A Method for the {BERT} Model"),
    ("deep ResNet of things: a study", "Deep ResNet of Things: A Study"),
])
def test_title_case(title, expected):
    assert fmt.to_title_case(title, []) == expected


def test_case_changes_applied_and_listed(tmp_path):
    out = run_tool(tmp_path, client=FakeClient(only_first), cfg={"all": {"title_case": "sentence"}})
    assert parsed(out, "smith2020").get("title") == "A real paper"
    assert "`title`: A Real Paper → A real paper" in report_text(out)


def test_per_type_rules_in_output(tmp_path):
    text = ("@inproceedings{c, title={Second Paper on Things}, booktitle={Proceedings of the 40th "
            "International Conference on Machine Learning}, pages={1--2}, doi={10.1234/c}, year=2021}\n"
            + GOOD)
    client = FakeClient(lambda c: only_first(c) if "Real" in c["title"] else matched_entry(
        rec=record(title="Second Paper on Things", doi="10.1234/c", year=2021, authors=(), first="1", last="2")))
    out = run_tool(tmp_path, text, client=client, cfg={
        "conference": {"pages": "remove", "doi": "remove", "venue_names": "abbreviated"}})
    conf, journal = parsed(out, "c"), parsed(out, "smith2020")
    assert conf.get("booktitle") == "Proc. ICML"  # the "Proceedings of" you wrote is kept
    assert not conf.get("pages") and not conf.get("doi")
    assert journal.get("pages") == "10--20" and journal.get("doi") == "10.1000/real"


def test_add_uses_verified_record(tmp_path):
    text = "@article{a, title={A Real Paper}, author={Smith, Jane and Doe, John}, year=2020, journal={J}, doi={10.1000/real}}"
    out = run_tool(tmp_path, text, client=FakeClient(only_first), cfg={"all": {"pages": "add"}})
    assert parsed(out, "a").get("pages") == "10--20"


def test_other_rules(tmp_path):
    text = GOOD.replace("  doi ", "  url = {http://x},\n  month = jan,\n  abstract = {long},\n  doi ")
    cfg = {"all": {"url": "remove_if_doi", "month": "remove", "authors": "initials",
                   "remove_fields": ["abstract"]}}
    out = run_tool(tmp_path, text, client=FakeClient(only_first), cfg=cfg)
    ref = parsed(out, "smith2020")
    assert not ref.get("url") and not ref.get("month") and not ref.get("abstract")
    assert ref.get("author") == "Smith, J. and Doe, J."


def test_initials_written_together_are_all_kept():
    assert fmt.authors_to_initials("Klumperink, Eric A.M.") == "Klumperink, E. A. M."


def test_initials_handle_hyphens_and_organizations():
    assert fmt.authors_to_initials("Dupont, Jean-Luc Marie") == "Dupont, J.-L. M."
    assert fmt.authors_to_initials("{3GPP} and Smith, Jane") == "{3GPP} and Smith, J."


# --- Venues ------------------------------------------------------------------------

def built_in():
    return fmt.load_venues(["computer_science", "electrical_computer_engineering"], None)


@pytest.mark.parametrize("name,abbrev", [
    ("Advances in Neural Information Processing Systems", "NeurIPS"),
    ("NIPS", "NeurIPS"),
    ("Proc. of the 40th Int. Conf. on Machine Learning", "ICML"),
    ("Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition (CVPR)", "CVPR"),
    ("Proceedings of the IEEE International Symposium on Dynamic Spectrum Access Networks (DySPAN)", "DySPAN"),
    ("2023 IEEE International Conference on Communications", "ICC"),
    ("IEEE Transactions on Wireless Communications", "IEEE Trans. Wireless Commun."),
    ("Some Workshop Nobody Lists (CVPR)", "CVPR"),
])
def test_venue_matching(name, abbrev):
    assert built_in().find(name)[1] == abbrev


def test_full_name_for_any_alias():
    assert built_in().find("NIPS")[0] == "Advances in Neural Information Processing Systems"


def test_unknown_venue_does_not_match_a_similar_one():
    assert built_in().find("International Conference on Machine Learning Applications") is None


@pytest.mark.parametrize("name", ["computer_science", "electrical_computer_engineering"])
def test_venue_files_have_no_duplicate_lines(name):
    lines = (ROOT / "venues" / f"{name}.yaml").read_text(encoding="utf-8").splitlines()
    keys = [l.split('":')[0] for l in lines if l.startswith('"')]
    assert len(keys) == len(set(keys))
    data = yaml.safe_load("\n".join(lines))
    assert isinstance(data.pop("sponsors"), list)  # every built-in list says who sponsors its field
    assert len(data) == len(keys)


def test_venue_files_do_not_conflict():
    seen = {}
    for name in ("computer_science", "electrical_computer_engineering"):
        data = yaml.safe_load((ROOT / "venues" / f"{name}.yaml").read_text(encoding="utf-8"))
        data.pop("sponsors", None)
        for full, abbrev in data.items():
            key = fmt._venue_key(full)
            assert seen.setdefault(key, abbrev) == abbrev, f"{full} conflicts"


def test_venue_lists_picks_files():
    assert fmt.load_venues(["computer_science"], None).find("IEEE Transactions on Communications") is None
    assert fmt.load_venues(["electrical_computer_engineering"], None).find("NeurIPS") is None


def test_custom_file_wins(tmp_path):
    path = tmp_path / "mine.yaml"
    path.write_text('"International Conference on Machine Learning": MyICML\n"Tiny Workshop": TW\n', encoding="utf-8")
    venues = fmt.load_venues(["computer_science"], path)
    assert venues.find("International Conference on Machine Learning")[1] == "MyICML"
    assert venues.find("Tiny Workshop")[1] == "TW"


def test_unknown_list_gives_clear_error():
    with pytest.raises(config.ConfigError, match="no venues/biology.yaml"):
        fmt.load_venues(["biology"], None)


def test_missing_venue_is_flagged_with_paste_line(tmp_path):
    out = run_tool(tmp_path, client=FakeClient(only_first), cfg={"all": {"venue_names": "abbreviated"}})
    report = report_text(out)
    assert '"Journal of Things": ABBREVIATION' in report
    assert parsed(out, "smith2020").get("journal") == "Journal of Things"


# --- Ledger ------------------------------------------------------------------------

def test_second_run_makes_no_calls(tmp_path):
    run_tool(tmp_path, client=FakeClient(only_first))
    run_tool(tmp_path, client=ExplodingClient())


@pytest.mark.parametrize("edit", [
    lambda t: t.replace("smith2020", "renamed"),
    lambda t: t.replace("A Real Paper", "A real paper"),
    lambda t: "\n".join(reversed(t.split("\n\n"))),
    lambda t: t.replace("Smith, Jane", "Smith, J."),
    lambda t: t.replace("Journal of Things", "J. Things"),
])
def test_cosmetic_edits_make_no_calls(tmp_path, edit):
    run_tool(tmp_path, client=FakeClient(only_first))
    run_tool(tmp_path, edit(GOOD), client=ExplodingClient())


def test_corrected_bib_makes_no_calls_and_stays_stable(tmp_path):
    cfg = {"all": {"title_case": "sentence", "doi": "remove", "pages": "remove", "authors": "initials"}}
    text = GOOD.replace("{12}", "{13}")
    out = run_tool(tmp_path, text, client=FakeClient(only_first), cfg=cfg)
    fixed = corrected(out)
    again = tmp_path / "next.bib"
    again.write_text(fixed, encoding="utf-8", newline="")
    cli.run([str(again), "--out", str(out), "--write"], client=ExplodingClient(), env_file=tmp_path / "x")
    assert (out / "next.corrected.bib").read_text(encoding="utf-8") == fixed


def test_new_format_settings_reuse_stored_results(tmp_path):
    run_tool(tmp_path, client=FakeClient(only_first))
    out = run_tool(tmp_path, client=ExplodingClient(), cfg={"all": {"doi": "remove"}})
    assert not parsed(out, "smith2020").get("doi")


def test_editing_one_entry_rechecks_only_that_entry(tmp_path):
    run_tool(tmp_path, client=FakeClient(only_first))
    client = FakeClient(only_first)
    run_tool(tmp_path, GOOD.replace("2020", "2019"), client=client)
    assert client.calls == 1
    assert len(client.bodies[0]["claims"]) == 1


def test_check_results_are_reused(tmp_path):
    run_tool(tmp_path, client=FakeClient(lambda c: entry("not_found")))
    out = run_tool(tmp_path, client=ExplodingClient())
    assert "Couldn't confirm" in report_text(out)


def test_results_expire(tmp_path):
    day = date(2026, 1, 1)
    run_tool(tmp_path, client=FakeClient(only_first), today=day)
    run_tool(tmp_path, client=ExplodingClient(), today=day + timedelta(days=180))
    client = FakeClient(only_first)
    run_tool(tmp_path, client=client, today=day + timedelta(days=181))
    assert client.calls == 1


def test_recheck_forces_calls(tmp_path):
    run_tool(tmp_path, client=FakeClient(only_first))
    client = FakeClient(only_first)
    run_tool(tmp_path, client=client, flags=["--recheck"])
    assert client.calls == 1


def test_interrupted_run_keeps_finished_batches(tmp_path):
    text = "".join(f"@misc{{k{i}, title={{Paper number {i} about things}}}}\n" for i in range(30))

    class DiesOnSecond(FakeClient):
        def audit(self, body):
            if self.calls == 1:
                raise KeyboardInterrupt
            return super().audit(body)

    with pytest.raises(KeyboardInterrupt):
        run_tool(tmp_path, text, client=DiesOnSecond())
    client = FakeClient()
    run_tool(tmp_path, text, client=client)
    assert client.calls == 1
    assert len(client.bodies[0]["claims"]) == 5


def test_damaged_ledger_starts_over(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text("{not json", encoding="utf-8")
    assert ledger.Ledger(path, 180).get("anything") is None


# --- Flags and batching -------------------------------------------------------------

def test_batches_of_25(tmp_path):
    text = "".join(f"@misc{{k{i}, title={{Paper number {i} about things}}}}\n" for i in range(26))
    client = FakeClient()
    run_tool(tmp_path, text, client=client)
    assert [len(b["claims"]) for b in client.bodies] == [25, 1]


def test_format_only_makes_no_calls(tmp_path):
    out = run_tool(tmp_path, client=ExplodingClient(), command="format",
                   cfg={"all": {"doi": "remove"}})
    assert not parsed(out, "smith2020").get("doi")
    assert not (out / "ledger.json").exists() and not (out / "verify.md").exists()
    assert "## CHANGED" in (out / "format.md").read_text(encoding="utf-8")


def test_dry_run_makes_no_calls_and_writes_nothing(tmp_path, capsys):
    out = run_tool(tmp_path, client=ExplodingClient(), flags=["--dry-run"])
    assert not out.exists()
    assert "would use 1 API call" in capsys.readouterr().out


def test_call_limit_marks_the_rest_not_checked(tmp_path):
    text = "".join(f"@misc{{k{i}, title={{Paper number {i} about things}}}}\n" for i in range(30))
    client = FakeClient(limit=1)
    out = run_tool(tmp_path, text, client=client)
    assert client.calls == 1
    assert report_text(out).count("max_calls_per_run reached") == 5


def test_real_client_stops_at_limit_and_retries_rate_limits():
    class Response:
        def __init__(self, code):
            self.status_code, self.headers = code, {"Retry-After": "0"}

        def json(self):
            return {"entries": []}

    client = api.Client(None, limit=3, sleep=lambda s: None)
    answers = iter([Response(429), Response(200)])
    client.session.post = lambda *a, **k: next(answers)
    assert client.audit({}) == {"entries": []}
    assert client.calls == 2
    client.session.post = lambda *a, **k: Response(429)
    with pytest.raises(api.LimitReached):
        client.audit({})
    assert client.calls == 3


def test_rejected_key_stops_with_a_clear_message(tmp_path):
    client = FakeClient(fail=api.AuthError("the API rejected the key in .env"))
    with pytest.raises(SystemExit, match="rejected"):
        run_tool(tmp_path, client=client)


def test_claim_sends_details_only():
    ref = bib.parse_bib(GOOD)[0]
    claim = api.claim_for(ref)
    assert claim == {"title": "A Real Paper", "authors": [{"family": "Smith", "given": "Jane"},
                     {"family": "Doe", "given": "John"}], "year": 2020,
                     "container": "Journal of Things", "doi": "10.1000/real"}


def test_trim_handles_live_record_shape():
    trimmed = api.trim_record(record())
    assert trimmed["pages"] == "10--20" and trimmed["doi"] == "10.1000/real"
    assert trimmed["volume"] == "12" and trimmed["number"] == "3" and trimmed["year"] == 2020


# --- From a real audit of a camera-ready paper -------------------------------------

def make_pdf(path, pages):
    import pymupdf
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page()
        for y, text in lines:
            page.insert_text((60, y), text, fontsize=9)
    doc.save(path)


def test_repeating_headers_are_removed_wherever_they_sit(tmp_path):
    path = tmp_path / "p.pdf"
    header = (62, "MobiCom '26, October 26-30, 2026, Austin, TX, USA")  # below the old cutoff
    body = [[header, (300, f"Body text on page {i}.")] for i in range(4)]
    refs = [header, (100, "References"),
            (120, "[1] Jane Smith. 2017. A CMOS Passive Circulator and Its Application in"),
            (132, "a Full-Duplex Receiver. IEEE JSSC.")]
    next_page = [header, (100, "[2] John Doe. 2018. Second Paper Title Here. Nature.")]
    make_pdf(path, [*body, refs, next_page])
    found = pdf.extract_references(path)
    assert len(found) == 2
    assert "MobiCom" not in found[0].raw and "MobiCom" not in found[1].raw
    assert found[0].get("title") == "A CMOS Passive Circulator and Its Application in a Full-Duplex Receiver"


def test_url_is_not_part_of_the_title():
    ref = pdf._parse_entry("[28]", "GNU Radio. 2019. GNU Radio 3.8. http://gnuradio.org/.")
    assert ref.get("title") == "GNU Radio 3.8"


def test_short_title_connects_to_its_pdf_entry():
    refs = bib.parse_bib("@misc{g, author={{GNU Radio}}, title={{GNU Radio 3.8}}, year=2019}")
    printed = [pdf._parse_entry("[1]", "GNU Radio. 2019. GNU Radio 3.8. http://gnuradio.org/.")]
    assert check.cross_check(refs, printed).pairs == {"[1]": "g"}


def test_short_title_does_not_take_a_longer_titles_entry():
    refs = bib.parse_bib("@article{short, title={Full Duplex Radios}, year=2013}\n"
                         "@article{long, title={Prototyping Real-Time Full Duplex Radios}, year=2015}")
    printed = [pdf._parse_entry("[1]", "A Bee. 2015. Prototyping Real-Time Full Duplex Radios. Mag."),
               pdf._parse_entry("[2]", "C Dee. 2013. Full Duplex Radios. SIGCOMM.")]
    assert check.cross_check(refs, printed).pairs == {"[1]": "long", "[2]": "short"}


def test_stray_text_inside_a_title_still_connects():
    refs = bib.parse_bib("@article{a, title={Canceller Tuning Using Adaptive Learning Rate Functions}, year=2020}")
    printed = [pdf._parse_entry("[1]", "A Bee. 2020. Canceller Tuning Using Adaptive Learning "
                                       "Stray Header Words Rate Functions. RWS.")]
    assert check.cross_check(refs, printed).pairs == {"[1]": "a"}


def test_weak_guesses_are_not_stored_and_old_ledgers_are_cleaned(tmp_path):
    weak = {"item": record(title="Ultrasonic Characterization of Nuclear Fuel"), "score": 0.7}
    out = run_tool(tmp_path, ONE, client=FakeClient(lambda c: entry("not_found", candidates=[weak])))
    assert "Ultrasonic" not in (out / "ledger.json").read_text(encoding="utf-8")
    path = tmp_path / "old.json"
    path.write_text('{"results": {"f": {"checked": "2026-01-01", "result": {"verdict": "not_found", '
                    '"candidates": [{"score": 0.7, "record": {"title": "Nuclear"}}]}}}}', encoding="utf-8")
    assert ledger.Ledger(path, None).get("f")["candidates"] == []


@pytest.mark.parametrize("ours,theirs,same", [
    ("Demo: Full-Duplex Wireless Based on a Canceller", "Full-duplex wireless based on a canceller", True),
    ("Sparse and Redundant Representations: From Theory to Applications", "Sparse and Redundant Representations", True),
    ("{An Exact Title}", "An exact title", True),
    ("Enabling Practical Full-Duplex Wireless", "Enabling Full-Duplex Wireless in GNU Radio", False),
    ("MIDU: Enabling MIMO", "MIDU", False),
])
def test_same_title(ours, theirs, same):
    assert bib.same_title(ours, theirs) is same




def crossref_record(**extra):
    return api.trim_record(record()) | extra


def test_direct_crossref_search_finds_what_the_api_missed(tmp_path):
    text = BOTH.replace("A Real Paper", "Demo: A Real Paper")
    out = run_tool(tmp_path, text, client=not_found_client(), crossref=FakeCrossref([crossref_record()]))
    report = report_text(out)
    assert "## OK" in report and "Found by searching Crossref directly: [10.1000/real](https://doi.org/10.1000/real)" in report
    assert corrected(out) == text


@pytest.mark.parametrize("extra", [{"title": "Other Paper Entirely"}, {"year": 2010},
                                   {"authors": [{"family": "Jones", "given": "Amy"}]}])
def test_crossref_needs_title_author_and_year(tmp_path, extra):
    text = BOTH
    out = run_tool(tmp_path, text, client=not_found_client(), crossref=FakeCrossref([crossref_record(**extra)]))
    assert "## CHECK" in report_text(out)


def test_crossref_down_retries_next_run_without_paying(tmp_path):
    text = BOTH
    run_tool(tmp_path, text, client=not_found_client(), crossref=FakeCrossref(None))
    retry = FakeCrossref([crossref_record()])
    out = run_tool(tmp_path, text, client=ExplodingClient(), crossref=retry)
    assert retry.calls == 1 and "Found by searching" in report_text(out)


def test_crossref_lookup_can_be_turned_off(tmp_path):
    crossref = FakeCrossref([crossref_record()])
    run_tool(tmp_path, ONE, client=not_found_client(), crossref=crossref, cfg={"crossref_lookup": False})
    assert crossref.calls == 0


def test_crossref_client_parses_records(monkeypatch):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"message": {"items": [{
                "title": ["A <i>Real</i> Paper"], "DOI": "10.1234/x", "type": "journal-article", "volume": "5",
                "issue": "2", "page": "1-9", "container-title": ["Nature"],
                "issued": {"date-parts": [[2020, 1]]}, "author": [{"family": "Smith", "given": "J"}, {"name": "Org"}],
            }]}}
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: Response())
    found = api.CrossrefClient().by_title("x")[0]
    assert found["title"] == "A Real Paper" and found["pages"] == "1--9" and found["year"] == 2020
    assert [a["family"] for a in found["authors"]] == ["Smith"]


def sparse_match(claim):
    """What title search really returns: authors, but no pages or volume."""
    if "Second" in claim["title"]:
        return only_first(claim)
    return matched_entry(rec=record(volume="", issue="", first=None), via="openalex-search")


def test_title_match_details_come_from_a_free_crossref_lookup(tmp_path):
    # No DOI, so the entry is only found by title, and the full record comes from Crossref.
    text = GOOD.replace("10--20", "1--17").replace(",\n  doi     = {10.1000/real}", "")
    assert "doi" not in text.split("@misc")[0]
    crossref = FakeCrossref(full=crossref_record())
    out = run_tool(tmp_path, text, client=FakeClient(sparse_match), crossref=crossref)
    report = report_text(out)
    assert 'pages: you have "1\u201317", the registry has "10\u201320"' in report
    assert corrected(out) == text
    assert crossref.calls == 1


def test_details_lookup_is_stored_and_not_repeated(tmp_path):
    crossref = FakeCrossref(full=crossref_record())
    run_tool(tmp_path, client=FakeClient(sparse_match), crossref=crossref)
    again = FakeCrossref(full=crossref_record())
    run_tool(tmp_path, client=ExplodingClient(), crossref=again)
    assert again.calls == 0


def test_details_lookup_ignores_a_different_title(tmp_path):
    text = GOOD.replace("10--20", "1--17")
    crossref = FakeCrossref(full=crossref_record(title="Another Paper Entirely"))
    out = run_tool(tmp_path, text, client=FakeClient(sparse_match), crossref=crossref)
    assert "you have" not in report_text(out)


def test_doi_unknown_to_crossref_leaves_the_result_alone(tmp_path):
    out = run_tool(tmp_path, client=FakeClient(sparse_match), crossref=FakeCrossref(full={}))
    assert "## OK" in report_text(out)


def test_details_lookup_retries_when_crossref_is_down(tmp_path, monkeypatch):
    def fail(*a, **k):
        raise api.requests.ConnectionError()
    monkeypatch.setattr(api.requests, "get", fail)
    assert api.CrossrefClient(sleep=lambda s: None).by_doi("10.1234/x") is None


def test_details_lookup_skipped_when_turned_off(tmp_path):
    crossref = FakeCrossref(full=crossref_record())
    run_tool(tmp_path, client=FakeClient(sparse_match), crossref=crossref, cfg={"crossref_lookup": False})
    assert crossref.calls == 0


def title_match(**kwargs):
    return lambda claim: matched_entry(rec=record(**kwargs), via="crossref-search")


def test_title_match_conflict_is_check_and_changes_nothing(tmp_path):
    text = GOOD.replace("10--20", "10--99")
    out = run_tool(tmp_path, text, client=FakeClient(title_match()))
    report = report_text(out)
    assert 'pages: you have "10–99", the registry has "10–20"' in report
    assert corrected(out) == text


def test_title_match_missing_value_is_a_note(tmp_path):
    text = GOOD.replace("  volume  = {12},\n", "")
    out = run_tool(tmp_path, text, client=FakeClient(title_match()))
    assert "The registry also has volume 12." in report_text(out)
    assert corrected(out) == text


def test_title_match_of_a_preprint_or_other_title_is_not_compared(tmp_path):
    text = GOOD.replace("10--20", "10--99")
    for rec in ({"kind": "preprint"}, {"title": "A Real Paper With More Words"}):
        result = api.trim_entry(title_match(**rec)(None))
        assert check._title_match_differences(bib.parse_bib(text)[0], result) == ([], [])


@pytest.mark.parametrize("text,expected", [
    ("Possible DOI: 10.1145/1859995.1859997", "Possible DOI: [10.1145/1859995.1859997](https://doi.org/10.1145/1859995.1859997)"),
    ("Found: 10.1234/x. Next sentence", "Found: [10.1234/x](https://doi.org/10.1234/x). Next sentence"),
    ("see (10.1234/x), and more", "see ([10.1234/x](https://doi.org/10.1234/x)), and more"),
    ("10.1016/S0140-6736(26)00603-3", "[10.1016/S0140-6736(26)00603-3](https://doi.org/10.1016/S0140-6736%2826%2900603-3)"),
    ("https://doi.org/10.1234/x", "[10.1234/x](https://doi.org/10.1234/x)"),
    ("10.1234/a_b", r"[10.1234/a\_b](https://doi.org/10.1234/a_b)"),
    (r"10.1234/a\_b", r"[10.1234/a\_b](https://doi.org/10.1234/a_b)"),
    ("10.1002/1:1<1::AID>3.0.CO;2-1",
     r"[10.1002/1:1\<1::AID\>3.0.CO;2-1](https://doi.org/10.1002/1%3A1%3C1%3A%3AAID%3E3.0.CO%3B2-1)"),
    ("no doi 10.5 here, or volume 10.1", "no doi 10.5 here, or volume 10.1"),
])
def test_dois_become_links(text, expected):
    assert report.link_dois(text) == expected


def test_code_blocks_are_not_linked():
    text = '```yaml\n"10.1/x": ABBREVIATION\n```'
    assert report.link_dois(text) == text


def test_every_doi_in_the_report_is_a_link(tmp_path):
    client = FakeClient(lambda c: entry("not_found", candidates=[
        {"item": record(title="Close Thing", doi="10.1234/close"), "score": 0.95}]))
    text = report_text(run_tool(tmp_path, ONE, client=client))
    assert "[10.1234/close](https://doi.org/10.1234/close)" in text
    assert not re.findall(r"(?<![\[/])10\.\d{4,9}/", text)


# --- Formatting, end to end: the exact text written to the corrected bib ------------

BEFORE = r"""% My conference papers.
@inproceedings{conf,
  author = {Bharadia, Dinesh and McMilin, Emily and Katti, Sachin},
  title = {{Full Duplex Radios for N-Path Zadoff-Chu Sounding in GNU Radio}},
  booktitle = {Proceedings of the 40th International Conference on Machine Learning},
  pages = {375--386},
  month = jun,
  year = {2013},
  doi = {10.1145/2486001.2486033},
  url = {https://example.org/conf},
  abstract = {Long text.},
}

@article{jour,
  author = {Chung, Min Keun},
  title = {Prototyping Real-Time In-Band Full-Duplex {MIMO} Radios},
  journal = {IEEE Commun. Mag.},
  volume = {53},
  pages = {56--63},
  year = {2015},
  doi = {10.1109/MCOM.2015.7263346},
  url = {https://example.org/jour},
}
"""

STYLE = {
    "all": {"title_case": "sentence", "authors": "initials", "month": "remove",
            "remove_fields": ["abstract"], "url": "remove_if_doi"},
    "conference": {"venue_names": "abbreviated", "pages": "remove", "doi": "remove"},
    "journal": {"venue_names": "full"},
    "keep_capitalized": ["Zadoff-Chu", "GNU Radio"],
}

AFTER = r"""% My conference papers.
@inproceedings{conf,
  author = {Bharadia, D. and McMilin, E. and Katti, S.},
  title = {{Full duplex radios for N-path Zadoff-Chu sounding in GNU Radio}},
  booktitle = {Proc. ICML},
  OPTpages = {375--386},
  OPTmonth = jun,
  year = {2013},
  OPTdoi = {10.1145/2486001.2486033},
  url = {https://example.org/conf},
  OPTabstract = {Long text.},
}

@article{jour,
  author = {Chung, M. K.},
  title = {Prototyping real-time in-band full-duplex {MIMO} radios},
  journal = {IEEE Communications Magazine},
  volume = {53},
  pages = {56--63},
  year = {2015},
  doi = {10.1109/MCOM.2015.7263346},
  OPTurl = {https://example.org/jour},
}
"""

# The same, with hidden_prefix: null, which deletes left-out fields instead of hiding them.
AFTER_DELETED = r"""% My conference papers.
@inproceedings{conf,
  author = {Bharadia, D. and McMilin, E. and Katti, S.},
  title = {{Full duplex radios for N-path Zadoff-Chu sounding in GNU Radio}},
  booktitle = {Proc. ICML},
  year = {2013},
  url = {https://example.org/conf},
}

@article{jour,
  author = {Chung, M. K.},
  title = {Prototyping real-time in-band full-duplex {MIMO} radios},
  journal = {IEEE Communications Magazine},
  volume = {53},
  pages = {56--63},
  year = {2015},
  doi = {10.1109/MCOM.2015.7263346},
}
"""


def test_formatting_writes_exactly_the_expected_bib(tmp_path):
    out = run_tool(tmp_path, BEFORE, client=ExplodingClient(), command="format", cfg=STYLE)
    assert corrected(out) == AFTER


def test_hidden_prefix_null_deletes_instead(tmp_path):
    out = run_tool(tmp_path, BEFORE, client=ExplodingClient(), command="format",
                   cfg={**STYLE, "hidden_prefix": None})
    assert corrected(out) == AFTER_DELETED


def test_original_bib_is_unchanged_by_formatting(tmp_path):
    run_tool(tmp_path, BEFORE, client=ExplodingClient(), command="format", cfg=STYLE)
    assert (tmp_path / "refs.bib").read_text(encoding="utf-8") == BEFORE


def test_formatting_twice_changes_nothing_more(tmp_path):
    out = run_tool(tmp_path, AFTER, client=ExplodingClient(), command="format", cfg=STYLE)
    assert corrected(out) == AFTER


@pytest.mark.parametrize("setting,before,after", [
    ({"title_case": "title"}, "title = {{a study of the {BERT} model}}", "title = {{A Study of the {BERT} Model}}"),
    ({"title_case": "sentence"}, "title = {{A Study: The {BERT} Model}}", "title = {{A study: The {BERT} model}}"),
    ({"venue_names": "full"}, "journal = {NIPS}", "journal = {Advances in Neural Information Processing Systems}"),
    ({"doi": "remove"}, "doi = {10.1234/x},", "OPTdoi = {10.1234/x},"),
    ({"pages": "remove"}, "pages = {1--2},", "OPTpages = {1--2},"),
    ({"url": "remove"}, "url = {http://x},", "OPTurl = {http://x},"),
    ({"authors": "initials"}, "author = {Levin, Alon S.},", "author = {Levin, A. S.},"),
    ({"month": "remove"}, "month = jan,", "OPTmonth = jan,"),
    ({"remove_fields": ["note"]}, "note = {x},", "OPTnote = {x},"),
])
def test_each_setting_changes_the_bib_text(tmp_path, setting, before, after):
    field = "  " + before.rstrip(",") + ",\n"
    text = "@article{a,\n" + field + "  year = {2020},\n}\n"
    expected = "@article{a,\n" + ("  " + after.rstrip(",") + ",\n" if after else "") + "  year = {2020},\n}\n"
    out = run_tool(tmp_path, text, client=ExplodingClient(), command="format", cfg={"all": setting})
    assert corrected(out) == expected


@pytest.mark.parametrize("title,expected", [
    ("{{PAFD: Phased Array Full-Duplex}}", "{{PAFD: Phased array full-duplex}}"),
    ("{A Small-Form-Factor Canceller}", "{A small-form-factor canceller}"),
    ("{{N-Path Filters and Mixer-First Receivers: A Review}}", "{{N-path filters and mixer-first receivers: A review}}"),
    ("{{Survey of Target Tracking. Part I: Dynamic Models}}", "{{Survey of target tracking. Part I: Dynamic models}}"),
    ("{{1 Chip, 2x the Bandwidth}}", "{{1 chip, 2x the bandwidth}}"),
])
def test_sentence_case_on_braced_titles(title, expected):
    assert fmt.to_sentence_case(title, []) == expected


def test_keep_capitalized_handles_phrases_and_hyphens():
    assert fmt.to_sentence_case("Using Zadoff-Chu Sequences in GNU Radio", ["Zadoff-Chu", "GNU Radio"]) \
        == "Using Zadoff-Chu sequences in GNU Radio"
    assert fmt.to_sentence_case("A Markov-Chain Model", ["Markov"]) == "A Markov-chain model"


def test_title_case_keeps_hyphens_in_braced_titles():
    assert fmt.to_title_case("{{wideband full-duplex radios of the future}}", []) \
        == "{{Wideband Full-Duplex Radios of the Future}}"


@pytest.mark.parametrize("name,abbrev", [
    ("Proc. ACM MobiCom'12", "MobiCom"), ("Proc. IEEE ISSCC'21", "ISSCC"), ("Proc. ACM SIGCOMM’13", "SIGCOMM"),
])
def test_proceedings_shorthand_is_recognized(name, abbrev):
    assert built_in().find(name)[1] == abbrev


@pytest.mark.parametrize("name,abbrev", [
    ("Proc. ACM MobiHoc'19", "MobiHoc"), ("Proc. IEEE RFIC'23", "RFIC"), ("Proc. IEEE CICC'17", "CICC"),
    ("IEEE Spectrum", "IEEE Spectr."), ("SIAM Review", "SIAM Rev."), ("Physical Review X", "Phys. Rev. X"),
])
def test_venues_added_after_a_real_audit(name, abbrev):
    assert built_in().find(name)[1] == abbrev


def test_a_publisher_name_alone_does_not_match_a_journal():
    assert built_in().find("IEEE Machine Learning") is None


# --- abbreviations_file supersedes venue_lists --------------------------------------

def my_venues(tmp_path, text='"International Conference on Machine Learning": MyICML\n'):
    path = tmp_path / "mine.yaml"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("name", ["International Conference on Machine Learning", "ICML",
                                  "Proc. ICML'23", "Proc. of the 40th Int. Conf. on Machine Learning"])
def test_custom_entry_beats_every_built_in_name(tmp_path, name):
    assert fmt.load_venues(["computer_science"], my_venues(tmp_path)).find(name)[1] == "MyICML"


def test_custom_entry_supersedes_in_full_mode_too(tmp_path):
    venues = fmt.load_venues(["computer_science"], my_venues(tmp_path, '"Intl. Machine Learning Meeting": ICML\n'))
    assert venues.find("ICML")[0] == "Intl. Machine Learning Meeting"


def test_other_venues_still_use_the_built_in_list(tmp_path):
    assert fmt.load_venues(["computer_science"], my_venues(tmp_path)).find("NeurIPS")[1] == "NeurIPS"


def override_run(tmp_path, mine):
    path = my_venues(tmp_path, mine)
    text = "@inproceedings{a,\n  title = {T},\n  booktitle = {International Conference on Machine Learning},\n}\n" \
           "@inproceedings{b,\n  title = {U},\n  booktitle = {ICML},\n}\n"
    out = run_tool(tmp_path, text, client=ExplodingClient(), command="format",
                   cfg={"conference": {"venue_names": "abbreviated"}, "abbreviations_file": str(path)})
    return out, report_text(out)


def test_report_notes_when_custom_supersedes(tmp_path):
    out, report = override_run(tmp_path, '"International Conference on Machine Learning": MyICML\n')
    assert parsed(out, "a").get("booktitle") == "MyICML" and parsed(out, "b").get("booktitle") == "MyICML"
    assert "## Your abbreviations file took priority" in report
    assert "| International Conference on Machine Learning | MyICML | ICML | a |" in report
    assert "| ICML | MyICML | ICML | b |" in report


def test_report_stays_quiet_when_nothing_was_superseded(tmp_path):
    _, report = override_run(tmp_path, '"Tiny Workshop": TW\n')
    assert "took priority" not in report


# --- Venue year, sponsor and prefix -------------------------------------------------

def styled(value, year=2024, original=None, **rules):
    full = {"venue_year": "keep", "venue_sponsor": "keep", "venue_prefix": "keep", **rules}
    return fmt.style_venue(value, year, full, built_in(), original or value)


@pytest.mark.parametrize("value,year,rules,expected", [
    ("Proc. GRCon", 2024, {"venue_year": "add"}, "Proc. GRCon'24"),
    ("INFOCOM", 2018, {"venue_sponsor": "add"}, "IEEE INFOCOM"),
    ("GRCon", 2024, {"venue_prefix": "add"}, "Proc. GRCon"),
    ("INFOCOM", 2018, {"venue_year": "add", "venue_sponsor": "add", "venue_prefix": "add"}, "Proc. IEEE INFOCOM'18"),
    ("CVPR", 2023, {"venue_sponsor": "add"}, "IEEE/CVF CVPR"),
    ("MobiCom", 2023, {"venue_sponsor": "add"}, "ACM MobiCom"),
    ("IPSN", 2023, {"venue_sponsor": "add"}, "ACM/IEEE IPSN"),
    ("Proc. IEEE INFOCOM'18", 2018, {"venue_year": "remove"}, "Proc. IEEE INFOCOM"),
    ("Proc. IEEE INFOCOM'18", 2018, {"venue_sponsor": "remove"}, "Proc. INFOCOM'18"),
    ("Proc. IEEE INFOCOM'18", 2018, {"venue_prefix": "remove"}, "IEEE INFOCOM'18"),
    ("Proc. IEEE INFOCOM’18", 2018, {"venue_year": "remove"}, "Proc. IEEE INFOCOM"),
    ("2023 IEEE ICC", 2023, {"venue_year": "remove"}, "IEEE ICC"),
    ("Proceedings of the IEEE ICC 2023", 2023, {"venue_year": "remove", "venue_prefix": "remove"}, "IEEE ICC"),
    ("INFOCOM", 2005, {"venue_year": "add"}, "INFOCOM'05"),
    ("USENIX Security", 2023, {"venue_sponsor": "add", "venue_year": "add"}, "USENIX Security'23"),
    ("USENIX ATC", 2023, {"venue_sponsor": "remove"}, "USENIX ATC"),
    ("IEEE S&P", 2023, {"venue_sponsor": "add"}, "IEEE S&P"),
])
def test_venue_parts(value, year, rules, expected):
    assert styled(value, year, **rules)[0] == expected


def test_adding_what_is_already_there_changes_nothing():
    rules = {"venue_year": "add", "venue_sponsor": "add", "venue_prefix": "add"}
    assert styled("Proc. IEEE INFOCOM'18", 2018, **rules) == ("Proc. IEEE INFOCOM'18", [])


def test_no_sponsor_is_invented():
    value, notes = styled("ICML", 2023, venue_sponsor="add")
    assert value == "ICML" and "No sponsor is listed for ICML" in notes[0]
    value, notes = styled("Unknownfest", 2023, venue_sponsor="add")
    assert value == "Unknownfest" and notes


def test_a_sponsor_you_wrote_survives_abbreviating():
    assert styled("SRIF", 2013, original="Proc. ACM SRIF'13", venue_sponsor="add")[0] == "Proc. ACM SRIF'13"


def test_a_conflicting_sponsor_is_flagged_and_left_alone():
    value, notes = styled("ACM INFOCOM", 2018, venue_sponsor="add")
    assert value == "ACM INFOCOM" and "IEEE" in notes[0]


def test_a_conflicting_year_is_left_alone():
    # The sanity checks report it, so formatting adds no second note.
    assert styled("Proc. ACM MobiHoc'19", 2021, venue_year="add") == ("Proc. ACM MobiHoc'19", [])


def test_a_year_you_wrote_survives_abbreviating():
    assert styled("CICC", 2017, original="Proc. IEEE CICC'17", venue_year="add") == ("Proc. IEEE CICC'17", [])
    assert styled("ICC", 2023, original="2023 IEEE ICC", venue_year="add") == ("IEEE ICC'23", [])


def test_a_wrong_year_you_wrote_survives_abbreviating():
    assert styled("NEWCAS", 2022, original="Proc. IEEE NEWCAS'20", venue_year="add") == ("Proc. IEEE NEWCAS'20", [])


def test_a_wrong_venue_year_is_reported_once(tmp_path):
    text = GOOD_ENTRY.replace("Proc. IEEE ICC'20", "Proc. IEEE ICC'19")
    out = run_tool(tmp_path, text, cfg={"all": {"venue_year": "add"}})
    report = report_text(out)
    assert "## Left as written" not in read(out, "format")  # formatted, so the formatter saw the year too
    assert report.count("the entry's year is 2020") == 1


def test_no_year_in_the_entry_is_noted():
    value, notes = styled("GRCon", None, venue_year="add")
    assert value == "GRCon" and "no year" in notes[0]


def test_long_names_are_not_decorated():
    long = "International Conference on Machine Learning"
    assert styled(long, 2023, venue_year="add", venue_sponsor="add", venue_prefix="add")[0] == long


def test_sponsor_can_be_taught_with_a_venue_file(tmp_path):
    venues = fmt.load_venues([], my_venues(tmp_path, '"IEEE GNU Radio Conference": GRCon\n'))
    assert venues.sponsor_of("GRCon") == "IEEE"


VENUE_BEFORE = """@inproceedings{a,
  title = {T},
  booktitle = {INFOCOM},
  year = {2018},
}

@inproceedings{b,
  title = {U},
  booktitle = {Proceedings of the IEEE/CVF Conference on Computer Vision and Pattern Recognition (CVPR)},
  year = {2023},
}

@inproceedings{c,
  title = {V},
  booktitle = {Proc. GRCon},
  year = {2024},
}

@article{d,
  title = {W},
  journal = {IEEE Communications Magazine},
  year = {2015},
}
"""

VENUE_AFTER = """@inproceedings{a,
  title = {T},
  booktitle = {Proc. IEEE INFOCOM'18},
  year = {2018},
}

@inproceedings{b,
  title = {U},
  booktitle = {Proc. IEEE/CVF CVPR'23},
  year = {2023},
}

@inproceedings{c,
  title = {V},
  booktitle = {Proc. GRCon'24},
  year = {2024},
}

@article{d,
  title = {W},
  journal = {IEEE Communications Magazine},
  year = {2015},
}
"""


def test_venue_style_writes_exactly_the_expected_bib(tmp_path):
    cfg = {"all": {"venue_names": "abbreviated", "venue_year": "add", "venue_sponsor": "add", "venue_prefix": "add"},
           "journal": {"venue_names": "keep"},
           "venue_lists": ["computer_science", "electrical_computer_engineering"]}
    out = run_tool(tmp_path, VENUE_BEFORE, client=ExplodingClient(), command="format", cfg=cfg)
    assert corrected(out) == VENUE_AFTER


def test_venue_settings_ignore_journals(tmp_path):
    text = "@article{a,\n  title = {T},\n  journal = {Nature},\n  year = {2020},\n}\n"
    cfg = {"all": {"venue_year": "add", "venue_prefix": "add"}}
    assert corrected(run_tool(tmp_path, text, client=ExplodingClient(), command="format", cfg=cfg)) == text


# --- Link check ----------------------------------------------------------------------

WEB = """@misc{gr,
  author = {{GNU Radio}},
  title = {{GNU Radio 3.8}},
  howpublished = {\\url{http://gnuradio.org/}},
  year = {2019},
}
"""


def link_report(tmp_path, answer, text=WEB, **kwargs):
    links = FakeLinks(answer)
    out = run_tool(tmp_path, text, client=not_found_client(), links=links, **kwargs)
    return out, report_text(out), links


def test_working_link_is_link_ok_and_not_called_verified(tmp_path):
    _, report, links = link_report(tmp_path, ("ok", "HTTP 200"))
    assert links.urls == ["http://gnuradio.org/"]
    assert "## LINK OK" in report and "Link: http://gnuradio.org/" in report
    assert "## OK" not in report and "## CHECK" not in report


def test_broken_link_is_check(tmp_path):
    _, report, _ = link_report(tmp_path, ("broken", "HTTP 404"))
    assert "## CHECK" in report and "The link is broken (HTTP 404): http://gnuradio.org/" in report


def test_blocked_link_stays_unconfirmed_and_retries_free(tmp_path):
    _, report, _ = link_report(tmp_path, ("unknown", "HTTP 403"))
    assert "Couldn't confirm" in report and "broken" not in report
    retry = FakeLinks(("ok", "HTTP 200"))
    out = run_tool(tmp_path, WEB, client=ExplodingClient(), links=retry)
    assert retry.calls == 1 and "## LINK OK" in report_text(out)


def test_link_result_is_stored_and_not_repeated(tmp_path):
    link_report(tmp_path, ("ok", "HTTP 200"))
    again = FakeLinks(("ok", "HTTP 200"))
    run_tool(tmp_path, WEB, client=ExplodingClient(), links=again)
    assert again.calls == 0


def test_link_ok_entries_still_get_formatted(tmp_path):
    out, _, _ = link_report(tmp_path, ("ok", "HTTP 200"), cfg={"all": {"title_case": "sentence"}},
                            text=WEB.replace("GNU Radio 3.8", "Some Software Tool"))
    assert parsed(out, "gr").get("title") == "{Some software tool}"


def test_entries_found_elsewhere_skip_the_link_check_with_not_found(tmp_path):
    links = FakeLinks(("ok", "HTTP 200"))
    run_tool(tmp_path, WEB, client=FakeClient(lambda c: matched_entry(rec=record(title="GNU Radio 3.8", authors=()))),
             links=links, cfg={"url_check": "not_found"})
    assert links.calls == 0


def test_verified_entries_get_their_links_checked(tmp_path):
    links = FakeLinks(("broken", "HTTP 404"))
    found = FakeClient(lambda c: matched_entry(rec=record(title="GNU Radio 3.8", authors=())))
    out = run_tool(tmp_path, WEB, client=found, links=links)
    verify = (out / "verify.md").read_text(encoding="utf-8")
    assert links.urls == ["http://gnuradio.org/"]
    assert "## Broken links" in verify and "The link is broken (HTTP 404): http://gnuradio.org/" in verify
    assert "## CHECK" not in verify  # a broken link is worth fixing, but the work is still verified


def test_a_moved_link_is_reported_with_where_it_went(tmp_path):
    links = FakeLinks(("moved", "https://www.gnuradio.org/new"))
    found = FakeClient(lambda c: matched_entry(rec=record(title="GNU Radio 3.8", authors=())))
    out = run_tool(tmp_path, WEB, client=found, links=links)
    assert "now goes to another site: http://gnuradio.org/ → https://www.gnuradio.org/new" in report_text(out)


def test_link_checks_of_verified_entries_are_stored(tmp_path):
    found = FakeClient(lambda c: matched_entry(rec=record(title="GNU Radio 3.8", authors=())))
    run_tool(tmp_path, WEB, client=found, links=FakeLinks(("broken", "HTTP 404")))
    again = FakeLinks(("broken", "HTTP 404"))
    out = run_tool(tmp_path, WEB, client=ExplodingClient(), links=again)
    assert again.calls == 0 and "## Broken links" in report_text(out)


def test_an_unclear_link_answer_is_asked_again(tmp_path):
    found = lambda: FakeClient(lambda c: matched_entry(rec=record(title="GNU Radio 3.8", authors=())))
    run_tool(tmp_path, WEB, client=found(), links=FakeLinks(("unknown", "HTTP 403")))
    again = FakeLinks(("unknown", "HTTP 403"))
    run_tool(tmp_path, WEB, client=ExplodingClient(), links=again)
    assert again.calls == 1


def test_found_by_crossref_skips_the_link_check(tmp_path):
    links = FakeLinks(("ok", "HTTP 200"))
    text = WEB.replace("{GNU Radio}", "Smith, Jane").replace("GNU Radio 3.8", "A Real Paper").replace("2019", "2020")
    run_tool(tmp_path, text, client=not_found_client(), links=links, cfg={"url_check": "not_found"},
             crossref=FakeCrossref([crossref_record(authors=[{"family": "Smith", "given": "Jane"}])]))
    assert links.calls == 0


def test_url_check_can_be_turned_off(tmp_path):
    _, report, links = link_report(tmp_path, ("ok", "HTTP 200"), cfg={"url_check": False})
    assert links.calls == 0 and "LINK OK" not in report


def test_no_url_means_no_link_check(tmp_path):
    links = FakeLinks(("ok", "HTTP 200"))
    run_tool(tmp_path, ONE, client=not_found_client(), links=links)
    assert links.calls == 0


@pytest.mark.parametrize("field,expected", [
    (r"url = {https://a.org/x\_y}", "https://a.org/x_y"),
    (r"howpublished = {\url{http://b.org/}}", "http://b.org/"),
    (r"note = {See https://c.org/d.}", "https://c.org/d"),
    ("note = {No link here}", ""),
])
def test_url_of(field, expected):
    assert bib.url_of(bib.parse_bib("@misc{a, " + field + "}")[0]) == expected


def test_url_from_printed_pdf_text():
    assert bib.url_of(pdf._parse_entry("[1]", "Ettus Research. 2022. UHD 4.2. https://github.com/EttusResearch/uhd.")) \
        == "https://github.com/EttusResearch/uhd"


@pytest.mark.parametrize("status,state", [(200, "ok"), (301, "ok"), (404, "broken"), (410, "broken"),
                                          (403, "unknown"), (429, "unknown"), (500, "unknown")])
def test_link_checker_states(monkeypatch, status, state):
    class Response:
        status_code = status

        def close(self):
            pass
    monkeypatch.setattr(api.requests, "get", lambda *a, **k: Response())
    assert api.LinkChecker().check("http://x.org")[0] == state


def test_link_checker_treats_network_trouble_as_no_answer(monkeypatch):
    def fail(*a, **k):
        raise api.requests.ConnectionError()
    monkeypatch.setattr(api.requests, "get", fail)
    assert api.LinkChecker().check("http://x.org")[0] == "unknown"


# --- Sanity checks -------------------------------------------------------------------

GOOD_ENTRY = ("@inproceedings{a,\n  author = {Smith, Jane},\n  title = {A Real Paper},\n"
              "  booktitle = {Proc. IEEE ICC'20},\n  year = {2020},\n  pages = {1--9},\n  doi = {10.1234/x},\n}\n")


def problems(text, today=date(2026, 11, 6)):
    return sanity.check_entries(bib.parse_bib(text), today)


def test_a_good_entry_has_no_problems():
    assert problems(GOOD_ENTRY) == []


@pytest.mark.parametrize("old,new,expected", [
    ("Proc. IEEE ICC'20", "Proc. IEEE ICC'19", "the entry's year is 2020"),
    ("Proc. IEEE ICC'20", "Proc. 2019 IEEE ICC", "the entry's year is 2020"),
    ("Proc. IEEE ICC'20", "VTC2019-Fall", "the entry's year is 2020"),
    ("Proc. IEEE ICC'20", "Proc. IEEE ICC’19", "the entry's year is 2020"),
    ("{2020}", "{circa}", "doesn't look like a year"),
    ("{2020}", "{2031}", "in the future"),
    ("1--9", "1-9", "single hyphen"),
    ("1--9", "1333--64", "backward or are abbreviated"),
    ("10.1234/x", "https://doi.org/10.1234/x", "has a prefix"),
    ("10.1234/x", "not-a-doi", "isn't a valid DOI"),
    ("Smith, Jane", "Smith, Jane et al.", "et al."),
    ("Smith, Jane", "Smith, J., Doe, K., Lee, M., Park, S.", "looks like several names"),
    ("A Real Paper", "Cats & Dogs", "unescaped &"),
    ("A Real Paper", "snake_case 100%", "unescaped % _"),
    ("pages = {1--9}", "pages = {}", "pages field is empty"),
    ("  booktitle = {Proc. IEEE ICC'20},\n", "", "Missing required field for @inproceedings: booktitle"),
    ("  author = {Smith, Jane},\n", "", "Missing required field for @inproceedings: author"),
])
def test_each_problem_is_caught(old, new, expected):
    found = problems(GOOD_ENTRY.replace(old, new))
    assert any(expected in problem for _, problem in found), found


@pytest.mark.parametrize("old,new", [
    ("Proc. IEEE ICC'20", "Proc. IEEE ICC 2020"),
    ("Proc. IEEE ICC'20", "Proc. 2020 IEEE ICC"),
    ("Proc. IEEE ICC'20", "Proc. IEEE ICC"),
    ("Proc. IEEE ICC'20", "Proc. VTC2020-Fall"),
    ("A Real Paper", r"Cats \& Dogs"),
    ("A Real Paper", r"A $x_1$ Model"),
    ("Smith, Jane", "Smith, Jane and others"),
    ("Smith, Jane", "King, Jr., Martin"),
    ("1--9", "e123"),
    ("1--9", "1–9"),
])
def test_normal_entries_are_not_flagged(old, new):
    assert problems(GOOD_ENTRY.replace(old, new)) == []


def test_next_years_paper_is_fine_but_the_venue_mismatch_shows():
    found = problems(GOOD_ENTRY.replace("{2020}", "{2027}"))
    assert len(found) == 1 and "the entry's year is 2027" in found[0][1]


@pytest.mark.parametrize("entry,field", [
    ("@book{a, author={A}, title={T}, year=2020}", "publisher"),
    ("@techreport{a, author={A}, title={T}, year=2020}", "institution"),
    ("@phdthesis{a, author={A}, title={T}, year=2020}", "school"),
    ("@article{a, author={A}, title={T}, year=2020}", "journal or journaltitle"),
    ("@misc{a, author={A}}", "title"),
    ("@inbook{a, author={A}, title={T}, publisher={P}, year=2020}", "chapter or pages"),
])
def test_required_fields_by_type(entry, field):
    assert any(field in p for _, p in problems(entry))


def test_alternate_fields_count():
    assert problems("@article{a, author={A}, title={T}, journaltitle={J}, date={2020-01}}") == []
    assert problems("@book{a, editor={E}, title={T}, publisher={P}, year=2020}") == []
    assert problems("@customtype{a, note={N}}") == []


def test_repeated_cite_key_is_a_sanity_problem():
    text = ("@misc{e, title={Third Other Title}}\n@misc{E, title={Fourth Other Title}}\n"
            "@misc{e, title={Fifth Other Title}}\n")
    found = problems(text)
    assert len(found) == 1
    assert found[0][0] == "e"
    assert "used by 3 entries" in found[0][1]


def dupes(text, records=None):
    return duplicates.find(bib.parse_bib(text), records)


@pytest.mark.parametrize("text,reason", [
    ("@misc{a, title={The Same Long Title}}\n@misc{b, title={THE SAME long title}}", "same title"),
    ("@misc{c, title={First Other Title}, doi={10.1234/X}}\n@misc{d, title={Second Other Title}, doi={10.1234/x}}",
     "same DOI (10.1234/X)"),
    ("@misc{c, title={First Other Title}, eprint={2101.00001}, archiveprefix={arXiv}}\n"
     "@misc{d, title={Second Other Title}, eprint={2101.00001}, archiveprefix={arXiv}}", "same arXiv ID (2101.00001)"),
    ("@misc{a, title={Demo: Wideband Full-Duplex Radios}}\n@misc{b, title={Wideband Full-Duplex Radios}}",
     "same title, apart from a label or subtitle"),
    ("@misc{a, title={Wideband full-duplex wireless via frequency-domain equalization}}\n"
     "@misc{b, title={Wideband full-duplex wireless via frequency domain equalisation}}", "nearly the same title"),
    ("@inproceedings{a, author={Chen, Tingjun}, year=2019, title={Wideband Full-Duplex Wireless via Frequency-Domain Equalization}}\n"
     "@article{b, author={Chen, Tingjun}, year=2020, title={Frequency-Domain Equalization for Wideband Full-Duplex Wireless Systems}}",
     "same first author, close years and mostly the same title words"),
])
def test_possible_duplicates(text, reason):
    assert dupes(text) == [(["a", "b"] if "{a," in text else ["c", "d"], [reason])]


def test_entries_verified_as_the_same_work_are_possible_duplicates():
    text = "@misc{a, title={One Way Of Writing It}}\n@misc{b, title={Something Else Entirely}}"
    records = {"a": {"doi": "10.1/X"}, "b": {"doi": "10.1/x"}}
    assert dupes(text, records) == [(["a", "b"], ["both verified as the same work (10.1/X)"])]


@pytest.mark.parametrize("text", [
    "@misc{a, title={Intro}}\n@misc{b, title={Intro}}",                      # too short to tell
    "@misc{e, title={Third Other Title}}\n@misc{E, title={Third Other Title}}",  # a repeated key instead
    "@article{a, author={Chen, T}, year=2019, title={Wideband Full-Duplex Wireless via Equalization}}\n"
    "@article{b, author={Chen, T}, year=2021, title={Open-Access Full-Duplex Wireless in the ORBIT Testbed}}",
])
def test_different_works_are_not_possible_duplicates(text):
    assert dupes(text) == []


def test_three_way_duplicates_with_one_reason_say_it_once():
    text = ("@misc{a, title={The Same Long Title}}\n@misc{b, title={The same long title}}\n"
            "@misc{c, title={The Same Long Title}, doi={10.1/x}}")
    assert dupes(text) == [(["a", "b", "c"], ["same title"])]


def test_three_way_duplicates_with_different_reasons_name_each_pair():
    text = ("@misc{a, title={The Same Long Title}, doi={10.1/x}}\n@misc{b, title={The same long title}}\n"
            "@misc{c, title={Something Else Entirely Here}, doi={10.1/X}}")
    [(keys, reasons)] = dupes(text)
    assert keys == ["a", "b", "c"]
    assert "a and b: same title" in reasons and "a and c: same DOI (10.1/x)" in reasons


def test_report_has_possible_duplicates_and_contents(tmp_path, capsys):
    text = ("@article{first, author={Smith, Jane}, title={A Much Longer Real Paper Title}, journal={J}, year=2020}\n"
            "@article{again, author={Smith, Jane}, title={A much longer real paper title}, journal={J}, year=2020}\n")
    out = run_tool(tmp_path, text)
    assert "Consistency: 1 possible duplicate(s)" in capsys.readouterr().out
    report = read(out, "consistency")
    assert "## Possible duplicates" in report
    assert "- **first**, **again**: both verified as the same work" in report
    assert "| [Possible duplicates](#possible-duplicates) | 1 | Keep one entry for each work |" in report
    assert "| [consistency](consistency.md) | [Possible duplicates](consistency.md#possible-duplicates) | 1 |" \
        in read(out, "summary")
    for name in ("verify", "format", "lint", "consistency"):
        report = read(out, name)
        if "## " in report:
            assert report.index("| Section | Count | What to do |") < report.index("## ")
        for heading in re.findall(r"^## (.+)$", report, re.M):
            assert f"](#{report_module.anchor(heading)})" in report


@pytest.mark.parametrize("heading,slug", [
    ("CHECK: look at these yourself", "check-look-at-these-yourself"),
    ("LINK OK: the page exists, but the reference isn't verified",
     "link-ok-the-page-exists-but-the-reference-isnt-verified"),
    ("What this doesn't check", "what-this-doesnt-check"),
])
def test_contents_links_match_heading_anchors(heading, slug):
    assert report_module.anchor(heading) == slug


def test_report_lists_sanity_problems_on_every_kind_of_run(tmp_path, capsys):
    text = GOOD_ENTRY.replace("Proc. IEEE ICC'20", "Proc. IEEE ICC'19")
    client = FakeClient(lambda c: entry("not_found"))
    lint = read(run_tool(tmp_path, text, client=client), "lint")
    assert "## Problems" in lint and "the entry's year is 2020" in lint
    assert "Lint: 1 problem(s) to fix." in capsys.readouterr().out
    (tmp_path / "alone").mkdir()
    alone = run_tool(tmp_path / "alone", text, client=ExplodingClient(), command="lint")
    assert "## Problems" in read(alone, "lint") and not (alone / "refs.corrected.bib").exists()


def test_sanity_checks_change_nothing_and_stay_quiet_when_clean(tmp_path):
    bad = GOOD_ENTRY.replace("1--9", "1-9")
    out = run_tool(tmp_path, bad, client=FakeClient(lambda c: entry("not_found")))
    assert corrected(out) == bad
    clean = run_tool(tmp_path / ".", GOOD_ENTRY, name="clean.bib", client=FakeClient(lambda c: entry("not_found")))
    assert "## Sanity checks" not in report_text(clean)


def test_a_doi_in_a_sanity_problem_is_a_link(tmp_path):
    text = GOOD_ENTRY.replace("10.1234/x", "https://doi.org/10.1234/x")
    out = run_tool(tmp_path, text, client=ExplodingClient(), command="lint")
    assert "[10.1234/x](https://doi.org/10.1234/x)" in (out / "lint.md").read_text(encoding="utf-8")


# --- Used and unused references -------------------------------------------------------

USED_BIB = ("@article{used,\n  author = {Smith, Jane and Doe, John},\n  title = {A Real Paper},\n"
            "  journal = {Journal of Things},\n  year = {2020},\n  volume = {12},\n  number = {3},\n"
            "  pages = {10--20},\n  doi = {10.1000/real},\n}\n\n"
            "@article{spare,\n  author = {Lee, Amy},\n  title = {Paper Nobody Cited Here},\n"
            "  journal = {Journal of Things},\n  year = {2021},\n  pages = {5-9},\n  number = {},\n}\n")


def run_with_pdf(tmp_path, client=None, cfg=None, flags=(), text=USED_BIB, command="full"):
    if command in cli.WRITERS:
        flags = [*flags, "--write"]
    path, paper = tmp_path / "refs.bib", tmp_path / "paper.pdf"
    path.write_text(text, encoding="utf-8", newline="")
    make_pdf(paper, [[(100, "References"), (130, "[1] Jane Smith and John Doe. 2020. A Real Paper. Journal of Things.")]])
    if cfg is not None:
        write_config(tmp_path, cfg)
    out = tmp_path / "out"
    cli.run([command, str(path), str(paper), "--out", str(out), *flags], client=client or FakeClient(only_first),
            env_file=tmp_path / "x", arxiv_client=FakeArxiv(), crossref_client=FakeCrossref(), link_client=FakeLinks())
    return out, report_text(out) if out.exists() else ""


def test_unused_entries_spend_no_api_calls(tmp_path):
    client = FakeClient(only_first)
    run_with_pdf(tmp_path, client)
    assert [c["title"] for c in client.bodies[0]["claims"]] == ["A Real Paper"]


def test_report_focuses_on_used_references(tmp_path):
    out, _ = run_with_pdf(tmp_path)
    top, bottom = read(out, "verify").split("## Unused references")
    assert "The paper uses 1 of the bib's 2 entries." in top
    assert "| [OK](#ok-verified-nothing-to-correct) | 1 | Nothing |" in top and "**used**" in top
    assert "spare" not in top
    assert "**spare**: Paper Nobody Cited Here (Not checked)" in bottom
    assert "- **spare**: Paper Nobody Cited Here" in read(out, "compare").split("## Unused references")[1]


def test_unused_sanity_problems_stay_in_their_section(tmp_path, capsys):
    out, _ = run_with_pdf(tmp_path)
    lint = read(out, "lint")
    assert "## Problems" not in lint
    bottom = lint.split("## Unused references")[1]
    assert "**spare**: The number field is empty." in bottom
    assert "**spare**: The pages \"5-9\" use a single hyphen." in bottom
    assert "Lint: 0 problem(s) to fix, plus 2 in unused entries." in capsys.readouterr().out


def test_used_sanity_problems_stay_up_top(tmp_path):
    out, _ = run_with_pdf(tmp_path, text=USED_BIB.replace("10--20", "10-20"))
    top, _ = read(out, "lint").split("## Unused references")
    assert "## Problems" in top and "**used**" in top


def test_unused_entries_can_be_checked_on_request(tmp_path):
    client = FakeClient(lambda c: only_first(c) if "Real" in c["title"] else entry("not_found"))
    out, _ = run_with_pdf(tmp_path, client, cfg={"check_unused": True})
    assert len(client.bodies[0]["claims"]) == 2
    top, bottom = read(out, "verify").split("## Unused references")
    assert "**spare**: Paper Nobody Cited Here (CHECK)" in bottom and "Couldn't confirm" in bottom
    assert "## CHECK" not in top


def test_a_stored_result_for_an_unused_entry_is_still_shown(tmp_path):
    client = FakeClient(lambda c: only_first(c) if "Real" in c["title"] else entry("not_found"))
    run_with_pdf(tmp_path, client, cfg={"check_unused": True})
    out, _ = run_with_pdf(tmp_path, ExplodingClient(), cfg={"check_unused": False})
    assert "(CHECK)" in read(out, "verify").split("## Unused references")[1]


def test_unused_entries_are_still_formatted_and_kept(tmp_path):
    out, _ = run_with_pdf(tmp_path, cfg={"all": {"title_case": "sentence"}})
    assert parsed(out, "spare").get("title") == "Paper nobody cited here"
    assert parsed(out, "used").get("title") == "A real paper"


def test_changes_to_unused_entries_are_listed(tmp_path):
    out, _ = run_with_pdf(tmp_path, cfg={"all": {"title_case": "sentence"}})
    format_report = read(out, "format")
    assert "**spare**: Paper Nobody Cited Here (unused)" in format_report
    assert "`title`: Paper Nobody Cited Here → Paper nobody cited here" in format_report


def test_dry_run_counts_only_used_references(tmp_path, capsys):
    run_with_pdf(tmp_path, ExplodingClient(), flags=["--dry-run"])
    printed = capsys.readouterr().out
    assert "1 references: 0 already checked, 1 to check. 1 unused in the PDF, so skipped." in printed
    assert "would use between 0 and 1 API calls (1 have a DOI or arXiv ID and are tried free first)" in printed


def test_format_alone_marks_unused_entries(tmp_path):
    out, _ = run_with_pdf(tmp_path, ExplodingClient(), command="format", cfg={"all": {"title_case": "sentence"}})
    format_report = read(out, "format")
    assert "**used**: A Real Paper\n" in format_report and "**spare**: Paper Nobody Cited Here (unused)" in format_report


def test_bib_alone_has_no_unused_section(tmp_path):
    report = report_text(run_tool(tmp_path, client=FakeClient(only_first)))
    assert "Unused references" not in report and "The paper uses" not in report


def test_nothing_unused_means_no_section(tmp_path):
    only_used = USED_BIB.split("@article{spare")[0]
    _, report = run_with_pdf(tmp_path, text=only_used)
    assert "Unused references" not in report and "The paper uses" not in report


def test_invalid_check_unused_stops():
    with pytest.raises(config.ConfigError, match="check_unused"):
        config.build({"check_unused": "maybe"})


# --- Progress ----------------------------------------------------------------------

class Terminal(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize("done,total,filled", [(0, 10, 0), (5, 10, 12), (10, 10, 24), (0, 0, 24), (99, 10, 24)])
def test_bar(done, total, filled):
    bar = progress.render_bar(done, total)
    assert len(bar) == 24 and bar.count("#") == filled


def test_terminal_gets_a_live_bar():
    screen = Terminal()
    bar = progress.Progress("Lookups", 4, stream=screen)
    bar.update(2, "some_key")
    bar.note("rate limited, waiting 5s")
    bar.finish("4 checked")
    text = screen.getvalue()
    assert "2/4 some_key" in text and "\r" in text and "waiting 5s" in text
    assert text.endswith("Lookups: 4 checked\n")


def test_logs_get_plain_lines_with_no_bar():
    log = io.StringIO()
    bar = progress.Progress("Lookups", 4, stream=log)
    bar.update(2, "some_key")
    bar.note("rate limited, waiting 5s")
    bar.finish("4 checked")
    assert log.getvalue() == ("Lookups: 4 references...\nLookups: rate limited, waiting 5s\n"
                              "Lookups: 4 checked\n")


def test_run_shows_each_step(tmp_path, capsys):
    run_tool(tmp_path, client=FakeClient(sparse_match), crossref=FakeCrossref(full=crossref_record()))
    first = capsys.readouterr().out
    assert "Read refs.bib: 2 references" in first
    assert "Already checked, at no cost: 0. To check: 2." in first
    assert "Free lookups by identifier: 1 verified free, 0 passed on" in first  # smith2020 has a DOI
    assert "Scholar Sidekick: 1 checked" in first
    run_tool(tmp_path, client=ExplodingClient())
    second = capsys.readouterr().out
    assert "Already checked, at no cost: 2. To check: 0." in second
    assert "Scholar Sidekick" not in second and "Free lookups" not in second


def test_failed_batches_are_counted_in_the_status(tmp_path, capsys):
    run_tool(tmp_path, client=FakeClient(fail=api.ApiError("x")))
    assert "Scholar Sidekick: 0 checked, 2 couldn't be checked" in capsys.readouterr().out


def test_rate_limit_wait_is_announced():
    class Response:
        status_code, headers = 429, {"Retry-After": "30"}

    class Ok:
        status_code = 200

        @staticmethod
        def json():
            return {"entries": []}

    client = api.Client(None, sleep=lambda s: None)
    answers = iter([Response(), Ok()])
    client.session.post = lambda *a, **k: next(answers)
    waits = []
    client.on_wait = waits.append
    client.audit({})
    assert waits == [31.0]


# --- arXiv -------------------------------------------------------------------------



def not_found_client():
    return FakeClient(lambda c: entry("not_found"))


def arxiv_report(tmp_path, papers, text=ONE):
    out = run_tool(tmp_path, text, client=not_found_client(), arxiv=FakeArxiv(papers))
    return out, report_text(out)


def test_found_on_arxiv_is_ok_and_changes_nothing(tmp_path):
    out, report = arxiv_report(tmp_path, [paper()])
    assert "## OK" in report and "Found on arXiv as 2305.02822" in report and "## CHECK" not in report
    assert corrected(out) == ONE


def test_arxiv_needs_title_and_first_author(tmp_path):
    assert "## CHECK" in arxiv_report(tmp_path, [paper(title="A Different Paper Entirely")])[1]
    assert "## CHECK" in arxiv_report(tmp_path, [paper(authors=["Amy Jones"])])[1]


def test_not_on_arxiv_says_so(tmp_path):
    assert "OpenAlex or arXiv" in arxiv_report(tmp_path, [])[1]


def test_wrong_arxiv_id_is_flagged(tmp_path):
    text = ONE.replace("year", "eprint = {2305.02822},\n  archiveprefix = {arXiv},\n  year")
    report = arxiv_report(tmp_path, [paper(title="Something Else")], text)[1]
    assert "fabricated" in report and "Something Else" in report


def test_arxiv_published_version_is_noted(tmp_path):
    assert "published version: Nature 1, [10.1234/x](https://doi.org/10.1234/x)" in arxiv_report(
        tmp_path, [paper(journal_ref="Nature 1", doi="10.1234/x")])[1]


def test_withdrawn_arxiv_paper_is_check(tmp_path):
    report = arxiv_report(tmp_path, [paper(withdrawn=True)])[1]
    assert "## CHECK" in report and "withdrawn" in report


def test_arxiv_result_is_stored_and_not_repeated(tmp_path):
    run_tool(tmp_path, ONE, client=not_found_client(), arxiv=FakeArxiv([]))
    again = FakeArxiv([])
    run_tool(tmp_path, ONE, client=ExplodingClient(), arxiv=again)
    assert again.calls == 0


def test_arxiv_down_keeps_not_found_and_retries_without_paying(tmp_path):
    out, report = arxiv_report(tmp_path, None)
    assert "Crossref, PubMed or OpenAlex" in report
    retry = FakeArxiv([paper()])
    out = run_tool(tmp_path, ONE, client=ExplodingClient(), arxiv=retry)
    assert retry.calls == 1
    assert "Found on arXiv" in report_text(out)


def test_arxiv_lookup_can_be_turned_off(tmp_path):
    arxiv = FakeArxiv([paper()])
    run_tool(tmp_path, ONE, client=not_found_client(), arxiv=arxiv, cfg={"arxiv_lookup": False})
    assert arxiv.calls == 0


def test_arxiv_skipped_when_found_elsewhere_and_on_format_only(tmp_path):
    arxiv = FakeArxiv([paper()])
    run_tool(tmp_path, client=FakeClient(only_first), arxiv=arxiv)
    run_tool(tmp_path, client=ExplodingClient(), arxiv=arxiv, command="format")
    assert arxiv.calls == 0


def test_invalid_arxiv_setting_stops():
    with pytest.raises(config.ConfigError, match="true or false"):
        config.build({"verify": {"arxiv_lookup": "maybe"}}, style_data={})


def test_arxiv_client_parses_and_paces(monkeypatch):
    feed = b"""<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
      <entry><id>http://arxiv.org/abs/2305.02822v2</id><title>A  Real
        Paper</title><published>2023-05-04T00:00:00Z</published>
        <author><name>Jane Smith</name></author>
        <arxiv:doi>10.1/x</arxiv:doi><arxiv:journal_ref>Nature 1</arxiv:journal_ref></entry>
    </feed>"""

    class Response:
        status_code = 200
        content = feed

    monkeypatch.setattr(api.requests, "get", lambda *a, **k: Response())
    waits = []
    client = api.ArxivClient(sleep=waits.append, clock=lambda: 100.0)
    found = client.by_title("A Real: Paper")[0]
    assert found["id"] == "2305.02822" and found["title"] == "A Real Paper" and found["year"] == 2023
    assert found["doi"] == "10.1/x" and found["journal_ref"] == "Nature 1"
    client.by_id("2305.02822")
    assert waits == [3.1]


def test_arxiv_client_returns_none_when_unreachable(monkeypatch):
    def fail(*a, **k):
        raise api.requests.ConnectionError()
    monkeypatch.setattr(api.requests, "get", fail)
    assert api.ArxivClient(sleep=lambda s: None).by_id("1") is None


# --- PDF ---------------------------------------------------------------------------

def test_margin_numbers_and_headers_removed_and_words_rejoined():
    assert pdf._join(["Technical Specifica-", "tion TS 23.288."]) == "Technical Specification TS 23.288."
    assert pdf._join(["Radio-", "Kinematic"]) == "Radio-Kinematic"
    assert pdf._join(["https:", "//github.com/x"]) == "https://github.com/x"
    assert pdf._join(["https://doi.org/10.1109/", "MCOM.2021"]) == "https://doi.org/10.1109/MCOM.2021"


def test_acm_parsing():
    ref = pdf._parse_entry("[9]", "Satyam Dwivedi, Ritesh Shreevastav, and Florent Munier. 2021. "
                                  "Positioning in 5G Networks. IEEE Communications Magazine 59, 11 (2021), "
                                  "22–28. https://doi.org/10.1109/MCOM.011.2100091")
    assert ref.get("title") == "Positioning in 5G Networks"
    assert ref.get("year") == "2021"
    assert ref.get("doi") == "10.1109/MCOM.011.2100091"
    assert ref.get("author") == "Satyam Dwivedi and Ritesh Shreevastav and Florent Munier"


def test_ieee_parsing():
    ref = pdf._parse_entry("[2]", 'J. Smith and K. Lee, "A great method for things," in Proc. ICML, 2021.')
    assert ref.get("title") == "A great method for things"
    assert ref.get("year") == "2021"


def test_unparseable_reference_is_flagged_for_the_raw_path():
    assert not pdf._parse_entry("[1]", "Nonsense without structure").parse_ok


def test_numbered_splitting_ignores_out_of_sequence_numbers():
    lines = [pdf.Line(0, 0, 0, 10 * i, 10 * i + 8, text) for i, text in
             enumerate(["[1] First entry. 2020.", "see [7] somewhere", "[2] Second entry. 2021."])]
    assert [label for label, _ in pdf._split_entries(lines)] == ["[1]", "[2]"]


def make_pdf_at(path, items, fontsize=8):
    """One page with text at (x, y). A4 width, so x over 290 is the right-hand column."""
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    for x, y, text in items:
        page.insert_text((x, y), text, fontsize=fontsize)
    doc.save(path)


def test_two_column_author_year_list_with_hanging_indent(tmp_path):
    # From a real GRCon paper: the right column's entries were swallowed by the left's.
    path = tmp_path / "p.pdf"
    make_pdf_at(path, [
        (55, 100, "References"),
        (55, 120, "Bharadia, Dinesh, McMilin, Emily, and"),
        (65, 132, "Katti, Sachin. Full Duplex Radios. In"),
        (65, 144, "Proc. SIGCOMM'13, 2013."),
        (55, 164, "Duarte, Melissa and Sabharwal, Ashu-"),
        (65, 176, "tosh. Experiment-Driven Characterization."),
        (65, 188, "IEEE TWC, 2012."),
        (307, 120, "Kim, Dongkyu, Lee, Haesoon, and Hong,"),
        (317, 132, "Daesik. A Survey of In-Band Full-Duplex"),
        (317, 144, "Transmission. IEEE Surveys, 2015."),
        (307, 164, "Zhou, Jin. Receiver with wideband cancella-"),
        (317, 176, "tion for full-duplex. In ISSCC'15, 2015."),
    ])
    found = pdf.extract_references(path)
    assert [r.get("title") for r in found] == [
        "Full Duplex Radios", "Experiment-Driven Characterization",
        "A Survey of In-Band Full-Duplex Transmission", "Receiver with wideband cancellation for full-duplex"]
    assert found[0].get("author") == "Bharadia, Dinesh and McMilin, Emily and Katti, Sachin"
    assert found[3].get("author") == "Zhou, Jin"


def test_pieces_of_one_printed_line_are_read_left_to_right(tmp_path):
    # Justified text is stored in pieces at slightly different heights.
    path = tmp_path / "p.pdf"
    make_pdf_at(path, [
        (55, 100, "References"),
        (187, 119.4, "URL https://docs."), (55, 120, "Arduino."), (101, 120, "Mega 2560 Rev3."),
        (65, 132, "arduino.cc/hardware/mega-2560/."),
        (55, 152, "Xilinx. Zynq Ultrascale+ MPSoC ZCU104 Kit, 2018."),
    ])
    found = pdf.extract_references(path)
    assert found[0].raw == "Arduino. Mega 2560 Rev3. URL https://docs.arduino.cc/hardware/mega-2560/."
    assert found[0].get("title") == "Mega 2560 Rev3"
    assert len(found) == 2


def test_three_columns_of_references_under_two_columns_of_text(tmp_path):
    # From a Proceedings of the IEEE review: a letter-spaced heading, then three columns.
    path = tmp_path / "p.pdf"
    body = [(55, 60 + 10 * i, "Body text in the left column, line number %d." % i) for i in range(5)]
    body += [(310, 60 + 10 * i, "Body text in the right column, line number %d." % i) for i in range(5)]
    refs = []
    for column, x in enumerate((55, 245, 435)):
        for row in range(3):
            n = column * 3 + row + 1
            y = 140 + 30 * row
            refs += [(x, y, f"[{n}] A. Author, Paper number {n},"), (x + 12, y + 10, f"IEEE J., 20{10 + n}.")]
    make_pdf_at(path, body + [(55, 125, "R E F E R E N C E S")] + refs, fontsize=7)
    found = pdf.extract_references(path)
    assert [r.key for r in found] == [f"[{n}]" for n in range(1, 10)]
    assert [r.raw for r in found][:2] == ["A. Author, Paper number 1, IEEE J., 2011.",
                                          "A. Author, Paper number 2, IEEE J., 2012."]
    assert "Body text" not in " ".join(r.raw for r in found)


def test_entries_separated_by_space_with_no_indent(tmp_path):
    path = tmp_path / "p.pdf"
    make_pdf_at(path, [
        (55, 100, "References"),
        (55, 120, "Smith, Jane and Doe, John. A First Paper Title."),
        (55, 132, "Journal of Things, 2020."),
        (55, 152, "Lee, Kim. A Second Paper Title. Nature, 2021."),
        (55, 172, "Wu, Max and Li, Ann. A Third Paper Title. In"),
        (55, 184, "Proc. Something, 2019."),
    ])
    assert [r.get("title") for r in pdf.extract_references(path)] == [
        "A First Paper Title", "A Second Paper Title", "A Third Paper Title"]


def test_alphabetic_labels():
    lines = [pdf.Line(0, 0, 55, 10 * i, 10 * i + 8, text) for i, text in enumerate([
        "[BMK13] Dinesh Bharadia, Emily McMilin, and Sachin Katti. Full", "duplex radios. In SIGCOMM, 2013.",
        "[Jai+11] Mayank Jain et al. Practical, real-time, full duplex", "wireless. In MobiCom, 2011."])]
    assert [label for label, _ in pdf._split_entries(lines)] == ["[BMK13]", "[Jai+11]"]


def test_hyphen_printed_elsewhere_is_kept_at_a_line_break():
    assert pdf._join(["Adaptive full-", "duplex radios"], {("full", "duplex")}) == "Adaptive full-duplex radios"
    assert pdf._join(["Communica-", "tions Society"], {("full", "duplex")}) == "Communications Society"


# --- Found by the stress test on 20 WiMNet papers (tests/stress_test/STRESS_REPORT.md) ---

def test_large_heading_overlapping_the_first_entry_is_still_found(tmp_path):
    # ACM SIGMETRICS: "10. REFERENCES" in 12pt overlaps "[1] ..." below it in the line grouping.
    import pymupdf
    path = tmp_path / "p.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((55, 300), "10.", fontsize=12)
    page.insert_text((80, 300), "REFERENCES", fontsize=12)
    page.insert_text((58, 305), "[1] A. Author. A first paper. J. Things, 2009.", fontsize=8)
    page.insert_text((58, 317), "[2] B. Author. A second paper. J. Things, 2010.", fontsize=8)
    doc.save(path)
    assert [r.key for r in pdf.extract_references(path)] == ["[1]", "[2]"]


def test_label_printed_out_of_order_does_not_swallow_the_rest():
    # A real PDF printed [12] between [7] and [8]; everything after [11] was lost.
    texts = [f"[{n}] Author {n}. Title number {n}. Journal, 2020." for n in (1, 2, 3, 5, 4, 6)]
    lines = [pdf.Line(0, 0, 0, 10 * i, 10 * i + 8, t) for i, t in enumerate(texts)]
    entries = pdf._split_entries(lines)
    assert [label for label, _ in entries] == ["[1]", "[2]", "[3]", "[4]", "[5]", "[6]"]
    assert entries[4][1] == "Author 5. Title number 5. Journal, 2020."


def test_repeated_stray_label_inside_an_entry_is_not_a_new_entry():
    texts = ["[1] A. Bee. First. J., 2020.", "[2] C. Dee. Second, see", "[4] for details. J., 2021.",
             "[3] E. Eff. Third. J., 2022.", "[4] G. Gee. Fourth. J., 2023."]
    lines = [pdf.Line(0, 0, 0, 10 * i, 10 * i + 8, t) for i, t in enumerate(texts)]
    assert [label for label, _ in pdf._split_entries(lines)] == ["[1]", "[2]", "[3]", "[4]"]


def test_last_entry_stops_before_author_biographies():
    # IEEE journals print the authors' biographies right after the last reference.
    lines = [pdf.Line(0, 1, 312, 100, 108, "[1] A. Bee, “First paper,” IEEE Trans., vol. 1,"),
             pdf.Line(0, 1, 330, 109, 117, "pp. 1–2, 2006."),
             pdf.Line(0, 1, 312, 118, 126, "[2] C. Dee, “Second paper,” J. Parallel Distrib."),
             pdf.Line(0, 1, 330, 127, 135, "Comput., vol. 63, 2003."),
             pdf.Line(0, 1, 394, 175, 183, "Anand Srinivas (M’08) received his Ph.D. from"),
             pdf.Line(0, 1, 394, 184, 192, "MIT in 2007. He received his Masters.")]
    entries = pdf._split_entries(lines)
    assert entries[-1][1] == "C. Dee, “Second paper,” J. Parallel Distrib. Comput., vol. 63, 2003."


def test_last_entry_stops_at_a_new_page_once_complete():
    lines = [pdf.Line(0, 0, 89, 100, 108, "[1] A. Bee. First paper. J. Things, 2019."),
             pdf.Line(0, 0, 89, 112, 120, "[2] N. Weil. The state of MPEG-DASH 2016. http://www.x.com/"),
             pdf.Line(0, 0, 108, 121, 129, "The-State-2016.aspx."),
             pdf.Line(1, 0, 89, 60, 68, "Algorithm 2 Video State Labeling Algorithm"),
             pdf.Line(1, 0, 92, 70, 78, "1: procedure Label")]
    assert pdf._split_entries(lines)[-1][1].endswith("The-State-2016.aspx.")


def test_biographies_heading_ends_the_reference_list():
    assert pdf._END.match("Biographies")


def test_unquoted_book_and_acm_titles_are_found():
    cases = {
        "A. J. Wood and B. F. Wollenberg, Power generation, operation, and control, 3rd ed. John Wiley & Sons, 2012.":
            ("A. J. Wood and B. F. Wollenberg", "Power generation, operation, and control"),
        "D. Peleg, Distributed Computing: A Locality-Sensitive Approach. SIAM, 2000.":
            ("D. Peleg", "Distributed Computing: A Locality-Sensitive Approach"),
        "G. H. Golub and C. F. Van Loan, Matrix Computations, 4th ed. Johns Hopkins, 2012.":
            ("G. H. Golub and C. F. Van Loan", "Matrix Computations"),
        "T. Mangla, E. Halepovic, M. Ammar, and E. Zegura. eMIMIC: estimating http-based video QoE. "
        "In Proc. IEEE TMA, 2018.":
            ("T. Mangla and E. Halepovic and M. Ammar and E. Zegura", "eMIMIC: estimating http-based video QoE"),
        "Swetank Kumar Saha, Yasaman Ghasempour, Owen Torres, et al. X60: A programmable testbed. "
        "Computer Communications, 133:77–88, 2019.":
            ("Swetank Kumar Saha and Yasaman Ghasempour and Owen Torres and others", "X60: A programmable testbed"),
        "H. Alves, T. Riihonen, and H. A. Suraweera, Eds., Full-Duplex Communications. Springer, Apr. 2020.":
            ("H. Alves and T. Riihonen and H. A. Suraweera", "Full-Duplex Communications"),
    }
    for text, (author, title) in cases.items():
        ref = pdf._parse_entry("[1]", text)
        assert (ref.get("author"), ref.get("title")) == (author, title), text


def test_given_name_first_list_is_not_read_family_name_first():
    ref = pdf._parse_entry("[1]", "Theodore S Rappaport, Shu Sun, Rimma Mayzus, and Felix Gutierrez. "
                                  "Millimeter wave mobile communications for 5G. IEEE Access, 1:335–349, 2013.")
    assert ref.get("author") == "Theodore S Rappaport and Shu Sun and Rimma Mayzus and Felix Gutierrez"


def test_trailing_links_and_ids_leave_the_title():
    ref = pdf._parse_entry("[1]", "Volodymyr Mnih and Koray Kavukcuoglu. 2013. Playing atari with deep "
                                  "reinforcement learning. arXiv:1312.5602 (2013).")
    assert ref.get("title") == "Playing atari with deep reinforcement learning"
    ref = pdf._parse_entry("[1]", "N. Weil. The state of MPEG-DASH 2016. http://www.streamingmedia.com/x.aspx.")
    assert ref.get("title") == "The state of MPEG-DASH 2016"


def test_online_availability_notes_are_not_titles():
    for text in ["[Online]. Available: https://advancedwireless.org",
                 "Level 3 Communications, Network Map. [Online]. Available: http://www.level3.com/map.html",
                 "2017. openroadm. (Mar 2017). Retrieved March 10, 2017 from http://www.openroadm.org/"]:
        ref = pdf._parse_entry("[1]", text)
        assert not ref.parse_ok and ref.raw == text, text


def test_ditto_mark_repeats_the_previous_authors(tmp_path):
    path = tmp_path / "p.pdf"
    make_pdf_at(path, [(55, 100, "References"),
                       (55, 120, "[1] J. Zhou and H. Krishnaswamy, \"First paper title,\" IEEE JSSC, 2015."),
                       (55, 132, "[2] --, \"Second paper title,\" IEEE JSSC, 2016.")],
                fontsize=7)
    refs = pdf.extract_references(path)
    assert refs[1].get("author") == "J. Zhou and H. Krishnaswamy"
    assert refs[1].get("title") == "Second paper title"


def test_copernicus_style_author_list_ending_in_a_colon():
    # Found on a held-out ESSD paper: every entry fell back to raw text.
    cases = {
        "Ahuna, M. N., Afullo, T. J., and Alonge, A. A.: Rain Attenuation Prediction Using Artificial Neural "
        "Network for Dynamic Rain Fade Mitigation, SAIEE Africa Research Journal, 110, "
        "https://doi.org/10.23919/SAIEE.2019.8643146, 2019.":
            ("Ahuna, M. N. and Afullo, T. J. and Alonge, A. A.",
             "Rain Attenuation Prediction Using Artificial Neural Network for Dynamic Rain Fade Mitigation"),
        "Andersson, J. C. M., Olsson, J., and Hansryd, J.: OpenMRG: Open data from Microwave links, Radar, and "
        "Gauges for rainfall quantification in Gothenburg, Sweden, Earth Syst. Sci. Data, 14, 5411–5426, 2022.":
            ("Andersson, J. C. M. and Olsson, J. and Hansryd, J.",
             "OpenMRG: Open data from Microwave links, Radar, and Gauges for rainfall quantification in "
             "Gothenburg, Sweden"),
        "Berne, A., Delrieu, G., Creutin, J.-D., and Obled, C.: Temporal and spatial resolution of rainfall "
        "measurements, J. Hydrol., 299, 166–179, 2004.":
            ("Berne, A. and Delrieu, G. and Creutin, J.-D. and Obled, C.",
             "Temporal and spatial resolution of rainfall measurements"),
        "Kaushik, A., Singh, R., Di Renzo, M., et al.: Toward Integrated Sensing and Communications for 6G, "
        "IEEE Trans. Green Commun. Netw., 8, 252–270, 2024.":
            ("Kaushik, A. and Singh, R. and Di Renzo, M. and others",
             "Toward Integrated Sensing and Communications for 6G"),
    }
    for text, (author, title) in cases.items():
        ref = pdf._parse_entry("[1]", text)
        assert (ref.get("author"), ref.get("title")) == (author, title), text


def test_ligature_characters_become_letters():
    assert "classiﬁcation eﬃcient".translate(pdf._LIGATURE_CHARS) == "classification efficient"
    assert pdf._glyph_text("f_f_i") == "ffi" and pdf._glyph_text("f_l.alt") == "fl"


def test_unmapped_ligatures_are_put_back_by_width_then_by_context():
    ligatures = {"Lib": [("ffi", 829.0), ("ff", 582.0), ("fi", 560.0), ("fl", 540.0), ("ffl", 815.0)],
                 "LibI": [("ff", 568.0), ("fi", 564.0)]}

    def span(font, text, widths):
        chars, x = [], 0.0
        for c, w in zip(text, widths):
            chars.append({"c": c, "bbox": (x, 0, x + w * 10 / 1000, 10)})
            x += w * 10 / 1000
        return {"font": font, "size": 10, "chars": chars}

    unsure: list = []
    assert pdf._span_text(span("Lib", "tra￿c", [500, 500, 500, 827, 500]), ligatures, unsure) == "traffic"
    italic = pdf._span_text(span("LibI", "bu￿er", [500, 500, 566, 500, 500]), ligatures, unsure)
    assert unsure and italic != "buffer"  # too close to call by width
    pages = [[(pdf.Line(0, 0, 0, 0, 8, italic), False),
              (pdf.Line(0, 0, 0, 10, 18, "the playback buffer drains"), False)]]
    assert pdf._settle_ligatures(pages, unsure)[0][0][0].text == "buffer"


@pytest.mark.parametrize("text,authors,title", [
    ("Kolodziej, Kenneth E, Perry, Bradley T, and Herd, Jeffrey S. In-Band Full-Duplex Technology: "
     "Techniques and Systems Survey. IEEE Trans., 67(7):3025–3041, 2019.",
     "Kolodziej, Kenneth E and Perry, Bradley T and Herd, Jeffrey S.",
     "In-Band Full-Duplex Technology: Techniques and Systems Survey"),
    ("Zhou, Jin, Kinget, Peter R., and Krishnaswamy, Harish. 20.6 A blocker-resilient receiver. In ISSCC, 2014.",
     "Zhou, Jin and Kinget, Peter R. and Krishnaswamy, Harish", "20.6 A blocker-resilient receiver"),
    ("van der Berg, Anna and de Vries, Jan. Some Paper Title. In Proc. X, 2020.",
     "van der Berg, Anna and de Vries, Jan", "Some Paper Title"),
    ("Jane Smith, John Doe. A Paper Title Here. Venue, 2020.",  # given name first: not read as one person
     "Jane Smith and John Doe", "A Paper Title Here"),
    ("COSMOS. Cloud enhanced open testbed (COSMOS), 2019. URL http://cosmos-lab.org.",
     "COSMOS", "Cloud enhanced open testbed (COSMOS)"),
])
def test_author_year_parsing(text, authors, title):
    ref = pdf._parse_entry("[1]", text)
    assert (ref.get("author"), ref.get("title")) == (authors, title)


def test_cross_check():
    refs = bib.parse_bib("@article{a, title={A Long Enough Title Here}, year=2020}\n"
                         "@article{b, title={Only In The Bib File}, year=2020}\n"
                         "@article{c, title={Different Year In The PDF}, year=2020}\n")
    printed = [pdf._parse_entry("[1]", "A Author. 2020. A Long Enough Title Here. Journal."),
               pdf._parse_entry("[2]", "B Author. 2021. Different Year In The PDF. Journal."),
               pdf._parse_entry("[3]", "C Author. 2022. Only In The PDF Text. Journal.")]
    result = check.cross_check(refs, printed)
    assert result.pairs == {"[1]": "a", "[2]": "c"}
    assert result.bib_only == ["b"]
    assert [r.key for r in result.pdf_only] == ["[3]"]
    assert "year 2020" in result.differences[0]


def test_scanned_pdf_gives_clear_error(tmp_path):
    import pymupdf
    path = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(path)
    with pytest.raises(pdf.PdfError, match="no text layer"):
        pdf.extract_references(path)


def test_pdf_only_run_changes_nothing_and_reports(tmp_path):
    import pymupdf
    path = tmp_path / "paper.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "References")
    page.insert_text((72, 130), "[1] Jane Smith and John Doe. 2019. A Real Paper. Journal of Things.")
    page.insert_text((72, 150), "[2] Jane Smith and John Doe. 2020. A Real Paper. Journal of Things.")
    doc.save(path)
    out = tmp_path / "out"
    code = cli.run([str(path), "--out", str(out)], client=FakeClient(), env_file=tmp_path / "x")
    assert code == 0
    report = report_text(out)
    assert "year: the PDF has" in report
    assert [p.name for p in out.glob("*.bib")] == ["paper.references.bib"]  # the reading, not a corrected bib


# --- Sample report -------------------------------------------------------------------

def test_sample_report_is_up_to_date():
    import make_sample_report
    for name, expected in make_sample_report.build().items():
        actual = (ROOT / "samples" / f"{name}.md").read_text(encoding="utf-8")
        assert actual == expected, f"samples/{name}.md is out of date: run python tests/make_sample_report.py"
