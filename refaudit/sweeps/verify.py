"""verify: is each reference real, with correct details and working links?

Lookups run in stages, cheapest and most certain first:
  1. free, by identifier: a DOI through Crossref, an arXiv ID through arXiv
  2. Scholar Sidekick (paid) for whatever stage 1 couldn't settle
  3. free title searches (Crossref, arXiv) and the link, for anything still not found
  4. a free check of every other entry's link
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
import re

from .. import api, bib, check, config, report
from ..changes import Change
from ..progress import Progress
from .base import ANY, Result, Sweep

SOURCES = {"crossref": "Crossref, by DOI", "arxiv": "arXiv, by ID", "sidekick": "Scholar Sidekick"}
LIMITS = ("This tool checks that each reference points to a real work with correct details. "
          "It doesn't check that a source supports what you cite it for. It's also only as good "
          "as the registries behind it, so skim the corrections.")


def _clients(ctx):
    cfg, c = ctx.cfg.verify, ctx.clients
    key = None
    sidekick = c.sidekick
    if sidekick is None:
        key = config.read_api_key(c.env_file)
        sidekick = api.Client(key, limit=cfg.max_calls_per_run)
        if not key:
            print("No API key in .env, so running anonymously at lower rate limits.")
    arxiv = (c.arxiv or api.ArxivClient()) if cfg.arxiv_lookup else None
    crossref = (c.crossref or api.CrossrefClient()) if cfg.crossref_lookup else None
    links = (c.links or api.LinkChecker()) if cfg.url_check != "off" else None
    return sidekick, arxiv, crossref, links, key


def _plan(ctx):
    """What to check: (references in scope, unique by fingerprint, not yet in the ledger)."""
    cfg = ctx.cfg.verify
    to_check = [r for r in ctx.all_refs if cfg.check_unused or not ctx.is_unused(r)]
    unique = {bib.fingerprint(r): r for r in to_check if not r.parse_ok or bib.plain(r.get("title"))}
    recheck = getattr(ctx.args, "recheck", False)
    pending = {fp: r for fp, r in unique.items() if recheck or ctx.ledger.get(fp) is None}
    return to_check, unique, pending


def _free_candidates(pending: dict, cfg) -> set:
    if cfg.sidekick_confirms_all:
        return set()
    return {fp for fp, r in pending.items() if r.parse_ok and (
        (cfg.crossref_lookup and bib.doi_of(r)) or (cfg.arxiv_lookup and bib.arxiv_of(r)))}


def dry_run(ctx) -> None:
    cfg = ctx.cfg.verify
    to_check, unique, pending = _plan(ctx)
    most = len(api.batches(list(pending.values())))
    free = _free_candidates(pending, cfg)
    least = len(api.batches([r for fp, r in pending.items() if fp not in free]))
    skipped = "" if cfg.check_unused or not ctx.unused_keys else \
        f" {len(ctx.unused_keys)} unused in the PDF, so skipped."
    calls = f"{most} API call(s)" if least == most else \
        f"between {least} and {most} API calls ({len(free)} have a DOI or arXiv ID and are tried free first)"
    print(f"{len(to_check)} references: {len(unique) - len(pending)} already checked, "
          f"{len(pending)} to check.{skipped} This run would use {calls}.")


WORKERS = 6  # link checks in flight at once. Crossref and arXiv allow one request at a time.


def _parallel(items: list, fn) -> list:
    """fn(item) for each item, several at a time, in the items' order. Only for link checks:
    they go to many different sites, while Crossref and arXiv each allow one request at a time."""
    if len(items) < 2:
        return [fn(item) for item in items]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        return list(pool.map(fn, items))


def _free_by_id(pending: dict, ledger, crossref, arxiv, cfg) -> tuple[dict, dict, set]:
    """Stage 1. Returns what's left for Scholar Sidekick, results to fall back on if it can't
    do better (an identifier pointing to another work), and entries whose arXiv ID doesn't exist."""
    rest, fallbacks, no_such_arxiv = dict(pending), {}, set()
    work = _free_candidates(pending, cfg)
    if not work:
        return rest, fallbacks, no_such_arxiv
    bar = Progress("Free lookups by identifier", len(work))
    done = 0
    if crossref is not None:
        by_doi = [fp for fp in sorted(work) if bib.doi_of(pending[fp])]
        lookups = (api.crossref_by_doi(pending[fp], crossref) for fp in by_doi)  # paced by the client
        for fp, result in zip(by_doi, lookups):
            done += 1
            bar.update(done, pending[fp].key)
            if result is None:
                continue
            if result["verdict"] == "matched":
                ledger.put(fp, result)
                rest.pop(fp)
            else:
                fallbacks[fp] = result
        ledger.save()
    by_arxiv = {fp: re.sub(r"v\d+$", "", bib.arxiv_of(pending[fp])).lower()
                for fp in work if fp in rest and bib.arxiv_of(pending[fp])} if arxiv is not None else {}
    if by_arxiv:
        bar.update(done, f"{len(by_arxiv)} arXiv IDs")
        papers = arxiv.by_ids(sorted(set(by_arxiv.values())))
        done += len(by_arxiv)
        if papers is not None:
            for fp, arxiv_id in by_arxiv.items():
                paper = papers.get(arxiv_id)
                if paper is None:
                    no_such_arxiv.add(fp)
                    continue
                result = api.arxiv_by_id(pending[fp], paper)
                if result["verdict"] == "matched":
                    ledger.put(fp, result)
                    rest.pop(fp)
                    fallbacks.pop(fp, None)
                else:
                    fallbacks.setdefault(fp, result)
            ledger.save()
    settled = len(pending) - len(rest)
    bar.finish(f"{settled} verified free, {len(work) - settled} passed on")
    return rest, fallbacks, no_such_arxiv


def _run_api(pending: dict, ledger, client, failures: dict) -> set:
    """Stage 2. Check references batch by batch, saving after each so nothing paid for is lost.
    Returns the fingerprints stored."""
    stored = set()
    groups = api.batches(list(pending.values()))
    if not groups:
        return stored
    bar = Progress("Scholar Sidekick", len(pending))
    client.on_wait = lambda seconds: bar.note(f"rate limited, waiting {seconds:.0f}s")
    finished = 0
    for number, (kind, group) in enumerate(groups, 1):
        bar.update(finished, f"batch {number} of {len(groups)}")
        try:
            data = client.audit(api.request_body(kind, group))
        except api.LimitReached:
            for ref in group:
                failures[bib.fingerprint(ref)] = "Not checked: max_calls_per_run reached."
            finished += len(group)
            continue
        except api.ApiError as err:
            for ref in group:
                failures[bib.fingerprint(ref)] = f"Not checked: {err}."
            if isinstance(err, api.AuthError):
                bar.finish("stopped")
                raise
            finished += len(group)
            continue
        entries = {e.get("index"): e for e in data.get("entries", [])}
        for index, ref in enumerate(group, 1):
            fp = bib.fingerprint(ref)
            entry = entries.get(index)
            if entry is None or entry.get("status") != "ok":
                error = (entry or {}).get("error")
                failures[fp] = f"Not checked: {error}." if error else check.NO_RESULT
            else:
                ledger.put(fp, {**api.trim_entry(entry), "source": "sidekick"})
                stored.add(fp)
        ledger.save()
        finished += len(group)
        bar.update(finished)
    problems = sum(1 for r in pending.values() if bib.fingerprint(r) in failures)
    bar.finish(f"{len(pending) - problems} checked, {problems} couldn't be checked" if problems
               else f"{len(pending)} checked")
    return stored


def _reconcile(fallbacks: dict, no_such_arxiv: set, stored: set, ledger, failures: dict) -> None:
    """After Scholar Sidekick: an identifier pointing to another work beats "not found"."""
    for fp, fallback in fallbacks.items():
        result = ledger.get(fp) if fp in stored else None
        if result is None or result["verdict"] == "not_found":
            ledger.put(fp, fallback)
            failures.pop(fp, None)
    for fp in no_such_arxiv:
        result = ledger.get(fp) if fp in stored else None
        if result and result["verdict"] == "not_found":
            ledger.put(fp, {**result, "arxiv_checked": True})
    ledger.save()


def _needs_free_lookup(ref, result, crossref, arxiv, links=None) -> bool:
    if not result or (not bib.plain(ref.get("title")) and not bib.arxiv_of(ref)):
        return False
    if result["verdict"] == "not_found":
        return bool((crossref and not result.get("crossref_checked"))
                    or (arxiv and not result.get("arxiv_checked"))
                    or (links and bib.url_of(ref) and not result.get("link_checked")))
    return bool(crossref and result["verdict"] == "matched" and result["by_search"]
                and not result.get("details_checked") and result["record"].get("doi")
                and not api.is_preprint_record(result["record"]))


def _second_opinions(unique: dict, ledger, crossref, arxiv, links) -> None:
    """Stage 3. Free sources for anything not found. If one is down, it's tried next run."""
    work = [(fp, ref) for fp, ref in unique.items()
            if _needs_free_lookup(ref, ledger.get(fp), crossref, arxiv, links)]
    if not work:
        return
    bar = Progress("Free lookups", len(work))
    # Each source in turn, across every entry: an entry still goes Crossref, arXiv, then its
    # link, as before, but links can then be checked several at once. Crossref and arXiv each
    # allow one request at a time, and their clients pace themselves.
    for client, flag, apply in ((crossref, "crossref_checked", api.apply_crossref),
                                (arxiv, "arxiv_checked", api.apply_arxiv),
                                (links, "link_checked", api.apply_link)):
        if client is None:
            continue
        due = [(fp, ref, ledger.get(fp)) for fp, ref in work]
        due = [(fp, ref, r) for fp, ref, r in due if r and r["verdict"] == "not_found" and not r.get(flag)]
        if client is links:
            answers = _parallel(due, lambda item: api.apply_link(item[1], item[2], links))
        else:
            answers = [apply(ref, r, client) for _, ref, r in due]
        for (fp, _, _), updated in zip(due, answers):
            if updated:
                ledger.put(fp, updated)
        ledger.save()
    if crossref is not None:
        due = [(fp, ref, ledger.get(fp)) for fp, ref in work]
        due = [(fp, ref, r) for fp, ref, r in due
               if r and r["verdict"] == "matched" and _needs_free_lookup(ref, r, crossref, None)]
        answers = [api.apply_details(ref, r, crossref) for _, ref, r in due]
        for (fp, _, _), updated in zip(due, answers):
            if updated:
                ledger.put(fp, updated)
        ledger.save()
    bar.finish(f"{len(work)} checked")


def _link_health(unique: dict, ledger, links) -> None:
    """Stage 4. Check the links of entries that were found. Not-found entries had theirs in stage 3."""
    urls = []
    for fp, ref in unique.items():
        url = bib.url_of(ref, hidden=False)
        result = ledger.get(fp)
        # A doi.org link only resolves the DOI, which the registry check already covers.
        if url and not bib.is_doi_link(url) and result and result["verdict"] != "not_found" \
                and ledger.get_link(url) is None and url not in urls:
            urls.append(url)
    if not urls:
        return
    bar = Progress("Link checks", len(urls), unit="links")
    answered = 0
    for done, (url, (state, detail)) in enumerate(zip(urls, _parallel(urls, links.check)), 1):
        bar.update(done, url[:60])
        if state != "unknown":  # many sites block scripts; ask again next run
            ledger.put_link(url, state, detail)
            answered += 1
    ledger.save()
    bar.finish(f"{len(urls)} checked" + (f", {len(urls) - answered} gave no clear answer"
                                         if answered < len(urls) else ""))


def _link_note(problem: dict) -> str:
    if problem["state"] == "broken":
        return f"The link is broken ({problem['detail']}): {problem['url']}"
    return f"The link now goes to another site: {problem['url']} → {problem['detail']}"


class Verify(Sweep):
    name = "verify"
    summary = "Is each reference real, with correct details and working links?"
    description = (
        "Looks each reference up in scholarly registries, in four stages, cheapest first:\n"
        "  1. Free, by identifier: a DOI through Crossref, an arXiv ID through arXiv.\n"
        "  2. Scholar Sidekick (Crossref, PubMed, OpenAlex) for whatever stage 1 couldn't\n"
        "     settle: entries with no identifier, DOIs Crossref doesn't hold, and identifiers\n"
        "     that point to a different work.\n"
        "  3. Free title searches on Crossref and arXiv, and the entry's link, for anything\n"
        "     still not found.\n"
        "  4. A free check that every other entry's link still works.\n\n"
        "Flags retractions, expressions of concern, identifiers that point to a different work "
        "(possibly fabricated), title-only matches and broken links. With enough confidence "
        "(auto_correct), it corrects wrong details: title, authors, year, volume, number and pages.\n\n"
        "Results are stored in ledger.json in the output folder, so unchanged references are never "
        "paid for twice. Keep that file, and share it with co-authors.")
    needs = ANY
    cost = ("Free for entries settled by DOI or arXiv ID. Scholar Sidekick API calls for the rest "
            "(one per 25 references), except those already in the ledger. Title searches and "
            "link checks are free.")
    changes_bib = ("Only with --write (a corrected copy) or --apply (in place), and only details a "
                   "registry confirms, at auto_correct confidence.")
    writes = "verify.md, ledger.json and changes.md, plus <name>.bib.diff if anything would be corrected"
    config_file = "verify.yaml"
    examples = [("refaudit verify refs.bib", "verify every entry"),
                ("refaudit verify refs.bib paper.pdf", "verify only what the paper cites"),
                ("refaudit verify refs.bib --dry-run", "how many paid calls would this use?"),
                ("refaudit verify refs.bib --recheck", "ignore stored results, e.g. before submitting")]

    def add_arguments(self, parser) -> None:
        parser.add_argument("--dry-run", action="store_true",
                            help="show how many API calls this would use, and spend or write nothing")
        parser.add_argument("--recheck", action="store_true",
                            help="ignore stored results and check everything again, to catch new retractions")

    def run(self, ctx, prior: dict) -> Result:
        cfg, ledger = ctx.cfg.verify, ctx.ledger
        to_check, unique, pending = _plan(ctx)
        print(f"Already checked, at no cost: {len(unique) - len(pending)}. To check: {len(pending)}.",
              flush=True)
        sidekick, arxiv, crossref, links, key = _clients(ctx)
        failures: dict = {}
        rest, fallbacks, no_such_arxiv = _free_by_id(pending, ledger, crossref, arxiv, cfg)
        try:
            stored = _run_api(rest, ledger, sidekick, failures)
        except api.AuthError as err:
            raise SystemExit(f"Error: {err}. Check SCHOLAR_API_KEY in .env.") from None
        _reconcile(fallbacks, no_such_arxiv, stored, ledger, failures)
        _second_opinions(unique, ledger, crossref, arxiv, links)
        if links is not None and cfg.url_check == "all":
            _link_health(unique, ledger, links)
        ledger.save()

        fresh = set(pending)
        dates = [ledger.checked_on(fp) for fp in unique if ledger.checked_on(fp)]
        by_source = {}
        for fp in fresh:
            stored_result = ledger.get(fp)
            if stored_result:
                source = stored_result.get("source") or "sidekick"
                by_source[source] = by_source.get(source, 0) + 1
        stats = {
            "calls": sidekick.calls if sidekick else 0,
            "free_calls": sum(c.calls for c in (arxiv, crossref, links) if c),
            "reused": len(unique) - len(pending),
            "saved": math.ceil(len(unique) / api.BATCH_SIZE) - math.ceil(len(pending) / api.BATCH_SIZE),
            "oldest": min(dates) if dates else None,
            "anonymous": key is None and getattr(sidekick, "is_anonymous", False),
            "by_source": by_source,
        }
        return self.evaluate(ctx, failures, fresh, stats)

    def evaluate(self, ctx, failures: dict, fresh: set, stats: dict) -> Result:
        """Turn what's in the ledger into each reference's result. No lookups happen here.

        `failures` maps a fingerprint to why it couldn't be checked this run, and `fresh` holds
        the fingerprints looked up this run (the rest were reused)."""
        cfg, ledger = ctx.cfg.verify, ctx.ledger
        outcomes = []
        for ref in ctx.all_refs:
            fp = bib.fingerprint(ref)
            unused = ctx.is_unused(ref)
            if unused and not cfg.check_unused and ledger.get(fp) is None:
                outcome = check.VerifyOutcome(ref=ref, status=check.NOT_CHECKED)
            elif ref.parse_ok and not bib.plain(ref.get("title")):
                outcome = check.verify(ref, None, cfg, ctx.venues, "Not checked: this entry has no title.")
            else:
                result = None if fp in failures else ledger.get(fp)
                outcome = check.verify(ref, result, cfg, ctx.venues, failures.get(fp))
                outcome.reused = fp not in fresh and result is not None
                outcome.checked_on = ledger.checked_on(fp)
                outcome.source = (result or {}).get("source") or ("sidekick" if result else None)
                outcome.confidence = (result or {}).get("confidence")
            outcome.unused = unused
            url = bib.url_of(ref, hidden=False)
            stored_link = ledger.get_link(url) if url else None
            if stored_link and stored_link["state"] in ("broken", "moved") \
                    and outcome.status in (check.OK, check.CORRECTED):
                outcome.link_problem = {"url": url, **stored_link}
                outcome.notes.append(_link_note(outcome.link_problem))
            outcomes.append(outcome)

        result = Result(self.name, data=outcomes, stats=stats)
        for o in outcomes:
            if o.corrections and o.ref.source == "bib":
                why = f"verify: {SOURCES.get(o.source, 'registry')}, {o.confidence or 'high'} confidence"
                result.changes[id(o.ref)] = [
                    Change(name, o.ref.get(name) or None, value, "set" if o.ref.get(name) else "add", [why])
                    for name, value in sorted(o.corrections.items())]

        used = [o for o in outcomes if not o.unused]
        counts = {s: sum(1 for o in used if o.status == s)
                  for s in (check.OK, check.CORRECTED, check.LINK, check.CHECK)}
        summary = ", ".join(f"{n} {s}" for s, n in counts.items() if n) or "no references found"
        broken = sum(1 for o in used if o.link_problem)
        result.console.append(f"Verify: {summary}." + (f" Broken or moved links: {broken}." if broken else "")
                              + f" API calls used: {stats.get('calls', 0)}.")
        return result

    def render(self, result: Result, ctx) -> str:
        outcomes = result.data
        unused = [o for o in outcomes if o.unused]
        used = [o for o in outcomes if not o.unused]
        stats = result.stats
        top = report.intro(ctx, "Verified")
        if unused and ctx.cross:
            top += [f"**The paper uses {len(ctx.cross.pairs)} of the bib's {len(ctx.cross.pairs) + len(unused)} "
                    f"entries.** This report is about those. The other {len(unused)} are listed under "
                    "[Unused references](#unused-references).", ""]
        if stats.get("anonymous"):
            top += ["No API key was found in `.env`, so this ran anonymously at lower rate limits.", ""]
        out = report.Report("Verification", top)

        def entries(chosen, with_changes: bool, suffix=lambda o: "") -> None:
            for o in chosen:
                out.add(f"- {report.label(o.ref, o.title_match)}{suffix(o)}")
                out.add(*[f"  - {reason}" for reason in o.reasons])
                if with_changes:
                    out.add(*report.change_lines(o.ref, result.changes.get(id(o.ref), [])))
                out.add(*[f"  - Note: {note}" for note in o.notes])
            out.add("")

        def status_section(status, heading, todo, with_changes, intro=()) -> None:
            chosen = [o for o in used if o.status == status]
            if chosen:
                out.section(heading, todo, len(chosen), label=status)
                for text in intro:
                    out.add(text, "")
                entries(chosen, with_changes)

        checks = [o for o in used if o.status == check.CHECK]
        intro = []
        if any(o.ref.source == "bib" for o in checks):
            intro.append("Nothing in these was corrected. Format leaves them as written too, unless "
                         "`skip_unverified` is false in format.yaml.")
        if any(o.not_found for o in checks):
            intro.append(report.NOT_FOUND)
        status_section(check.CHECK, "CHECK: look at these yourself", "Look at each one", False, intro)

        broken = [o for o in used if o.link_problem]
        if broken:
            out.section("Broken links", "Fix or remove each link", len(broken))
            out.add("These references are verified, but their link no longer works or now goes "
                    "somewhere else.", "")
            out.add(*[f"- **{o.ref.key}**: {_link_note(o.link_problem)}" for o in broken], "")

        status_section(check.CORRECTED, "CORRECTED: details fixed from the registry",
                       "Check each change in changes.md", True)
        status_section(check.LINK, "LINK OK: the page exists, but the reference isn't verified",
                       "Confirm each page is the right one", False)
        status_section(check.OK, "OK: verified, nothing to correct", "Nothing", False)

        if unused:
            out.section("Unused references", "Remove any you don't need", len(unused))
            out.add(f"These {len(unused)} bib entries aren't in the PDF's reference list.", "")
            if any(o.status == check.NOT_CHECKED for o in unused):
                out.add("*Not checked* means no API call was spent on it. Set `check_unused: true` in "
                        "verify.yaml to verify these too.", "")
            if any(o.not_found for o in unused):
                out.add(report.NOT_FOUND, "")
            entries(unused, True, lambda o: f" ({'Not checked' if o.status == check.NOT_CHECKED else o.status})")

        out.section("API use", "Nothing")
        out.add(f"- Scholar Sidekick calls this run: {stats.get('calls', 0)}",
                f"- Free lookups this run (Crossref, arXiv and links): {stats.get('free_calls', 0)}")
        by_source = stats.get("by_source") or {}
        if by_source:
            out.add("- Checked this run by: " + ", ".join(f"{SOURCES.get(s, s)} {n}" for s, n in
                                                          sorted(by_source.items())))
        reused, saved = stats.get("reused", 0), stats.get("saved", 0)
        if reused:
            calls = "call" if saved == 1 else "calls"
            out.add(f"- Reused from earlier runs: {reused} references, saving {saved} {calls} "
                    f"(one call checks up to {api.BATCH_SIZE})")
        if stats.get("oldest"):
            out.add(f"- Oldest stored check: {stats['oldest']}. Run with `--recheck` before "
                    "submitting to catch new retractions.")
        out.add("")
        out.section("What this doesn't check", "Read once")
        out.add(LIMITS, "")
        result.rows = out.rows
        note = [report.TITLE_MATCH, ""] if any(o.title_match for o in outcomes) else []
        return out.text(note)
