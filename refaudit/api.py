"""Scholar Sidekick client: batching, rate-limit retries, and trimmed results."""
from __future__ import annotations

import difflib
import html
import re
import threading
import time
import xml.etree.ElementTree as ET

import requests

from . import bib

BASE_URL = "https://scholar-sidekick.com"
BATCH_SIZE = 25
CANDIDATE_SCORE = 0.9
MAX_ATTEMPTS = 4
_PREPRINT_TYPES = {"preprint", "posted-content"}


class ApiError(Exception):
    """The API couldn't be used for this batch."""


class AuthError(ApiError):
    """The API rejected the key, so no further call can succeed."""


class LimitReached(ApiError):
    """max_calls_per_run has been used up."""


class Client:
    def __init__(self, api_key: str | None, limit: int | None = None, timeout: int = 90,
                 sleep=time.sleep):
        self.calls = 0
        self.limit = limit
        self.on_wait = None  # told how long a rate-limit wait will be
        self.is_anonymous = not api_key
        self.session = requests.Session()
        self.timeout = timeout
        self.sleep = sleep
        if api_key:
            self.session.headers["Authorization"] = f"Bearer {api_key}"

    def audit(self, body: dict) -> dict:
        """POST /api/audit, waiting and retrying on rate limits and brief outages."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self.limit is not None and self.calls >= self.limit:
                raise LimitReached("max_calls_per_run reached")
            try:
                self.calls += 1
                resp = self.session.post(f"{BASE_URL}/api/audit", json=body, timeout=self.timeout)
            except requests.RequestException as err:
                if attempt == MAX_ATTEMPTS:
                    # Never echo the exception's request details; give the type only.
                    raise ApiError(f"network problem ({type(err).__name__})") from None
                self.sleep(2 ** attempt)
                continue
            if resp.status_code in (401, 403):
                raise AuthError("the API rejected the key in .env")
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 502, 503) and attempt < MAX_ATTEMPTS:
                delay = _retry_delay(resp, attempt)
                if self.on_wait:
                    self.on_wait(delay)
                self.sleep(delay)
                continue
            raise ApiError(f"API returned HTTP {resp.status_code}")
        raise ApiError("API kept failing")


class ArxivClient:
    """The free arXiv API. No key, and arXiv asks for at most one request every 3 seconds."""
    URL = "https://export.arxiv.org/api/query"
    NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}

    def __init__(self, sleep=time.sleep, clock=time.monotonic, timeout: int = 30):
        self.calls = 0
        self.sleep = sleep
        self.clock = clock
        self.timeout = timeout
        self.last = None

    def _get(self, params: dict) -> list | None:
        """Matching papers, or None if arXiv couldn't be reached."""
        for attempt in range(1, 4):
            if self.last is not None:
                self.sleep(max(0, 3.1 - (self.clock() - self.last)))
            self.calls += 1
            try:
                resp = requests.get(self.URL, params=params, timeout=self.timeout,
                                    headers={"User-Agent": "refaudit"})
            except requests.RequestException:
                resp = None
            self.last = self.clock()
            if resp is not None and resp.status_code == 200:
                try:
                    return self._parse(resp.content)
                except ET.ParseError:
                    return None
            if resp is not None and resp.status_code not in (429, 500, 502, 503):
                return None
            self.sleep(5 * attempt)
        return None

    def _parse(self, content: bytes) -> list:
        papers = []
        for item in ET.fromstring(content).findall("a:entry", self.NS):
            def text(path):
                return _text(item.findtext(path, "", self.NS))
            identifier = text("a:id")
            if "/abs/" not in identifier:  # arXiv answers an unknown ID with an error entry
                continue
            papers.append({
                "id": re.sub(r"v\d+$", "", identifier.split("/abs/")[1]),
                "title": text("a:title"),
                "authors": [_text(a.findtext("a:name", "", self.NS))
                            for a in item.findall("a:author", self.NS)],
                "year": int(text("a:published")[:4] or 0) or None,
                "doi": text("x:doi"),
                "journal_ref": text("x:journal_ref"),
                "withdrawn": "withdrawn" in text("x:comment").lower(),
            })
        return papers

    def by_id(self, arxiv_id: str) -> list | None:
        return self._get({"id_list": arxiv_id, "max_results": 1})

    def by_ids(self, arxiv_ids: list) -> dict | None:
        """Papers by ID, up to 50 per request: {id: paper}. None if arXiv couldn't be reached."""
        found = {}
        for i in range(0, len(arxiv_ids), 50):
            chunk = arxiv_ids[i:i + 50]
            papers = self._get({"id_list": ",".join(chunk), "max_results": len(chunk)})
            if papers is None:
                return None
            for paper in papers:
                found[paper["id"].lower()] = paper
        return found

    def by_title(self, title: str) -> list | None:
        words = re.sub(r"[^\w\s]", " ", title)
        return self._get({"search_query": f'ti:"{" ".join(words.split())}"', "max_results": 5})


class CrossrefClient:
    """The free Crossref API, searched directly when Scholar Sidekick finds nothing."""
    URL = "https://api.crossref.org/works"

    # Crossref's limits without registering: one request at a time, at most 5 a second for a
    # DOI and 1 a second for a search. Its responses say the current limits, and are obeyed.
    _INTERVALS = {"doi": 0.2, "search": 1.0}

    def __init__(self, sleep=time.sleep, timeout: int = 30, clock=time.monotonic):
        self.calls = 0
        self.sleep = sleep
        self.clock = clock
        self.timeout = timeout
        self._lock = threading.Lock()   # one request in flight, even when called from threads
        self._interval = dict(self._INTERVALS)
        self._next = {kind: 0.0 for kind in self._INTERVALS}

    def _get(self, kind: str, url: str, params: dict | None = None):
        """One paced request. None if it couldn't be sent."""
        with self._lock:
            wait = self._next[kind] - self.clock()
            if wait > 0:
                self.sleep(wait)
            self.calls += 1
            try:
                resp = requests.get(url, params=params, timeout=self.timeout, headers={"User-Agent": "refaudit"})
            except requests.RequestException:
                resp = None
            headers = getattr(resp, "headers", None) or {}
            try:
                limit = int(headers.get("x-rate-limit-limit", 0))
                period = float(str(headers.get("x-rate-limit-interval", "1s")).rstrip("s"))
                if limit > 0:
                    self._interval[kind] = max(self._INTERVALS[kind], period / limit)
            except ValueError:
                pass
            self._next[kind] = self.clock() + self._interval[kind]
            return resp

    def by_title(self, title: str, author: str = "", year: int | None = None) -> list | None:
        """Likely records, or None if Crossref couldn't be reached. The first author and year
        help rank the right record first when a title is short, like "Full Duplex Radios"."""
        query = " ".join(str(part) for part in (title, author, year) if part)
        for attempt in range(1, 4):
            resp = self._get("search", self.URL, {"query.bibliographic": query, "rows": 5})
            if resp is not None and resp.status_code == 200:
                try:
                    return [self._record(i) for i in resp.json()["message"]["items"]]
                except (ValueError, KeyError, TypeError):
                    return None
            if resp is not None and resp.status_code not in (429, 500, 502, 503):
                return None
            self.sleep(3 * attempt)
        return None

    def by_doi(self, doi: str) -> dict | None:
        """The full record. {} if Crossref doesn't have this DOI, None if it couldn't be reached."""
        for attempt in range(1, 4):
            resp = self._get("doi", f"{self.URL}/{requests.utils.quote(doi, safe='')}")
            if resp is not None and resp.status_code == 200:
                try:
                    return self._record(resp.json()["message"])
                except (ValueError, KeyError, TypeError):
                    return None
            if resp is not None and resp.status_code == 404:
                return {}
            if resp is not None and resp.status_code not in (429, 500, 502, 503):
                return None
            self.sleep(3 * attempt)
        return None

    @staticmethod
    def _record(item: dict) -> dict:
        def first(name):
            values = item.get(name) or [""]
            return values[0]
        parts = ((item.get("issued") or {}).get("date-parts") or [[None]])[0]
        title = _text(first("title"))
        updates = sorted({str(u.get("type") or "").lower() for u in item.get("updated-by") or []} - {""})
        if re.match(r"(retracted|withdrawn)\s*:", title, re.I):
            updates = sorted(set(updates) | {"retraction"})
            title = re.sub(r"^(retracted|withdrawn)\s*:\s*", "", title, flags=re.I)
        record = {
            "type": item.get("type") or "", "title": title,
            "authors": [{"family": _text(a.get("family")), "given": _text(a.get("given"))}
                        for a in item.get("author") or [] if a.get("family")],
            "year": parts[0] if parts else None, "venue": _text(first("container-title")),
            "volume": _text(item.get("volume")), "number": _text(item.get("issue")),
            "pages": _text(item.get("page")).replace("-", "--"), "doi": _text(item.get("DOI")),
        }
        if updates:
            record["updates"] = updates
        return record


def apply_crossref(ref: bib.Reference, result: dict, client: CrossrefClient) -> dict | None:
    """Second opinion on a not_found result. None means Crossref couldn't be reached."""
    authors, _ = bib.authors_of(ref)
    records = client.by_title(bib.plain(ref.get("title")), authors[0][0] if authors else "", bib.year_of(ref))
    if records is None:
        return None
    updated = {**result, "crossref_checked": True}
    family = bib.fold(bib.authors_of(ref)[0][0][0]) if bib.authors_of(ref)[0] else ""
    year = bib.year_of(ref)
    for record in records:
        if not bib.same_title(ref.get("title"), record["title"]):
            continue
        if family and not any(family in bib.fold(a["family"]) for a in record["authors"]):
            continue
        if year and record["year"] and abs(year - record["year"]) > 1:
            continue
        record = dict(record)
        updates = record.pop("updates", [])
        updated.update(verdict="matched", confidence="medium", by_search=True, record=record,
                       candidates=[], caveats=[], crossref_match=True, details_checked=True)
        if updates:
            updated["retraction"] = _merge_retraction(result.get("retraction"), updates)
        break
    return updated


def _merge_retraction(known: dict | None, updates: list) -> dict:
    """Add Crossref's notices to what's already known. A notice is never taken away."""
    found = _retraction(updates)
    known = known or {}
    return {key: bool(found.get(key) or known.get(key)) for key in ("checked", "retracted", "concern", "corrections")}


def apply_details(ref: bib.Reference, result: dict, client: CrossrefClient) -> dict | None:
    """Fill in a title match's sparse record from Crossref, so pages and volume get compared.

    Title-search records usually have authors but no pages or volume. None means Crossref
    couldn't be reached. A record with a different title is ignored.
    """
    full = client.by_doi(result["record"]["doi"])
    if full is None:
        return None
    updated = {**result, "details_checked": True}
    if full and bib.same_title(ref.get("title"), full["title"]):
        full = dict(full)
        updates = full.pop("updates", [])
        updated["record"] = full
        if updates:
            updated["retraction"] = _merge_retraction(result.get("retraction"), updates)
    return updated


class LinkChecker:
    """Checks whether a web page exists. Nothing but the request itself is sent."""

    def __init__(self, timeout: int = 20):
        self.calls = 0
        self.timeout = timeout

    def check(self, url: str) -> tuple[str, str]:
        """("ok" | "broken" | "unknown", detail). Sites often block scripts, so 403 is "unknown"."""
        self.calls += 1
        try:
            resp = requests.get(url, stream=True, timeout=self.timeout, allow_redirects=True,
                                headers={"User-Agent": "Mozilla/5.0 (compatible; refaudit)"})
            resp.close()
        except requests.RequestException as err:
            return "unknown", type(err).__name__
        if resp.status_code < 400:
            final = getattr(resp, "url", "") or url
            # A resolver (doi.org, a handle) is meant to send you elsewhere, so that's no move.
            if _host(final) and _host(final) != _host(url) and _host(url) not in _RESOLVERS:
                return "moved", final
            return "ok", f"HTTP {resp.status_code}"
        if resp.status_code in (404, 410):
            return "broken", f"HTTP {resp.status_code}"
        return "unknown", f"HTTP {resp.status_code}"


def apply_link(ref: bib.Reference, result: dict, client: LinkChecker) -> dict | None:
    """Check a not_found entry's link. None means no answer, so it's tried again next run."""
    url = bib.url_of(ref)
    if not url:
        return None
    state, detail = client.check(url)
    if state == "unknown":
        return None
    if state == "moved":  # the page exists, somewhere else
        state, detail = "ok", f"moved to {detail}"
    return {**result, "link_checked": True, "link": {"url": url, "state": state, "detail": detail}}


_RESOLVERS = {"doi.org", "dx.doi.org", "hdl.handle.net", "purl.org", "n2t.net", "identifiers.org"}


def _host(url: str) -> str:
    m = re.match(r"https?://(?:www\.)?([^/:?#]+)", url or "", re.I)
    return m.group(1).lower() if m else ""


# --- Free lookups by identifier ---------------------------------------------------------

def _retraction(updates) -> dict:
    updates = set(updates or [])
    return {"checked": True, "retracted": bool(updates & {"retraction", "withdrawal", "removal"}),
            "concern": any("concern" in u for u in updates),
            "corrections": bool(updates & {"correction", "erratum", "corrigendum"})}


def _same_authors_loosely(ref: bib.Reference, record: dict) -> bool:
    """The entry's first author is on the record. True if either side lists nobody."""
    authors, _ = bib.authors_of(ref)
    if not authors or not record["authors"]:
        return True
    family = bib.fold(authors[0][0])
    return any(family and (family in bib.fold(a["family"]) or bib.fold(a["family"]) in family)
               for a in record["authors"])


def _identifier_result(ref: bib.Reference, record: dict, source: str, retraction: dict,
                       mismatch_title: str) -> dict:
    """A ledger result for a record reached through the entry's own identifier."""
    score = difflib.SequenceMatcher(None, bib.fold(bib.plain(ref.get("title"))),
                                    bib.fold(record["title"])).ratio()
    title_ok = bib.same_title(ref.get("title"), record["title"]) or score >= 0.9
    result = {"verdict": "matched", "confidence": "high", "record": record, "by_search": False,
              "mismatches": [], "candidates": [], "caveats": [], "retraction": retraction,
              "source": source}
    if not title_ok:
        result.update(verdict="mismatch", record=None, mismatches=[{
            "field": "title", "claimed": bib.plain(ref.get("title")), "resolved": mismatch_title,
            "similarity": round(score, 2)}])
    elif not _same_authors_loosely(ref, record) or _years_differ(ref, record):
        # Like Scholar Sidekick: a different year is often the online versus print date, so
        # it's shown for you to decide, not corrected (auto_correct: high needs high).
        result["confidence"] = "medium"
    return result


def _years_differ(ref: bib.Reference, record: dict) -> bool:
    year = bib.year_of(ref)
    return bool(year and record.get("year") and year != record["year"])


def crossref_by_doi(ref: bib.Reference, client: CrossrefClient) -> dict | None:
    """Verify an entry through its DOI, for free. None if Crossref couldn't settle it."""
    doi = bib.doi_of(ref)
    record = client.by_doi(doi) if doi else None
    if not record:  # unreachable, or a DOI Crossref doesn't hold (DataCite, for one)
        return None
    record = dict(record)
    updates = record.pop("updates", [])
    return _identifier_result(ref, record, "crossref", _retraction(updates),
                              f"{record['title']} (doi:{record['doi'] or doi})")


def arxiv_record(paper: dict) -> dict:
    return {"type": "preprint", "title": paper["title"], "year": paper["year"],
            "authors": [{"family": n.split()[-1], "given": " ".join(n.split()[:-1])}
                        for n in paper["authors"] if n.split()],
            "venue": "arXiv", "volume": "", "number": "", "pages": "", "doi": ""}


def arxiv_by_id(ref: bib.Reference, paper: dict) -> dict:
    """Verify an entry through its arXiv ID, from a paper arXiv returned for that ID."""
    result = _identifier_result(ref, arxiv_record(paper), "arxiv", _retraction([]),
                                f"{paper['title']} (arXiv:{paper['id']})")
    result["arxiv"] = {k: paper[k] for k in ("id", "doi", "journal_ref", "withdrawn")}
    result["arxiv_checked"] = True
    return result


def _author_found(ref: bib.Reference, paper: dict) -> bool:
    """Whether the entry's first author is on the paper. True if the entry lists nobody."""
    authors, _ = bib.authors_of(ref)
    if not authors:
        return True
    family = bib.fold(authors[0][0])
    return any(family and family in bib.fold(name) for name in paper["authors"])


def _title_score(ref: bib.Reference, paper: dict) -> float:
    ours, theirs = bib.fold(bib.plain(ref.get("title"))), bib.fold(paper["title"])
    return difflib.SequenceMatcher(None, ours, theirs).ratio()


def apply_arxiv(ref: bib.Reference, result: dict, client: ArxivClient) -> dict | None:
    """Second opinion on a not_found result. None means arXiv couldn't be reached, so try later."""
    arxiv_id = bib.arxiv_of(ref)
    papers = client.by_id(arxiv_id) if arxiv_id else client.by_title(bib.plain(ref.get("title")))
    if papers is None:
        return None
    updated = {**result, "arxiv_checked": True}
    match = next((p for p in papers if _title_score(ref, p) >= 0.95 and _author_found(ref, p)), None)
    if match is None:
        if arxiv_id and papers:
            paper = papers[0]
            updated["verdict"] = "mismatch"
            updated["confidence"] = "high"
            updated["record"] = None
            updated["mismatches"] = [{"field": "title", "claimed": bib.plain(ref.get("title")),
                                      "resolved": f"{paper['title']} (arXiv:{paper['id']})",
                                      "similarity": round(_title_score(ref, paper), 2)}]
        return updated
    updated.update(
        verdict="matched", confidence="medium", by_search=True, candidates=[], caveats=[],
        record={"type": "preprint", "title": match["title"], "year": match["year"],
                "authors": [{"family": n.split()[-1], "given": " ".join(n.split()[:-1])}
                            for n in match["authors"] if n.split()],
                "venue": "arXiv", "volume": "", "number": "", "pages": "", "doi": ""},
        arxiv={k: match[k] for k in ("id", "doi", "journal_ref", "withdrawn")},
    )
    return updated


def _retry_delay(resp, attempt: int) -> float:
    try:
        return min(float(resp.headers.get("Retry-After", "")) + 1, 120)
    except ValueError:
        return 5 * attempt


def claim_for(ref: bib.Reference) -> dict:
    """Build the API's claim. Only reference details are sent, never paper text."""
    claim = {"title": bib.plain(ref.get("title"))[:2000]}
    authors, _ = bib.authors_of(ref)
    people = []
    for family, given in authors[:50]:
        person = {"family": family[:200]}
        if given:
            person["given"] = given[:200]
        people.append(person)
    if people:
        claim["authors"] = people
    year = bib.year_of(ref)
    if year:
        claim["year"] = year
    venue = bib.venue_field(ref)
    if venue and not bib.is_arxiv_venue(ref.get(venue)):
        claim["container"] = bib.plain(ref.get(venue))[:500]
    if bib.doi_of(ref):
        claim["doi"] = bib.doi_of(ref)[:200]
    if bib.arxiv_of(ref):
        claim["arxiv"] = bib.arxiv_of(ref)[:50]
    if ref.get("isbn"):
        claim["isbn"] = bib.plain(ref.get("isbn"))[:50]
    return claim


def batches(refs: list) -> list[tuple[str, list]]:
    """Group references into API batches. PDF text we couldn't split up goes separately."""
    claims = [r for r in refs if r.parse_ok and bib.plain(r.get("title"))]
    prose = [r for r in refs if not r.parse_ok]
    groups = [("claims", claims[i:i + BATCH_SIZE]) for i in range(0, len(claims), BATCH_SIZE)]
    groups += [("references", prose[i:i + BATCH_SIZE]) for i in range(0, len(prose), BATCH_SIZE)]
    return groups


def request_body(kind: str, group: list) -> dict:
    options = {"checks": ["retraction"], "semantic_title": True}
    if kind == "claims":
        return {"claims": [claim_for(r) for r in group], "options": options}
    return {"references": [r.raw[:5000] for r in group], "options": options}


def _text(value) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", "", str(value or ""))).replace("‐", "-")
    return re.sub(r"\s+", " ", text).strip()


def trim_record(item: dict | None) -> dict | None:
    """Keep only the fields we use. Handles the record shape the live API returns."""
    if not item:
        return None
    container = item.get("container") or {}
    if isinstance(container, str):
        container = {"title": container}
    pages = item.get("pages") or {}
    if isinstance(pages, dict):
        first, last = pages.get("first"), pages.get("last")
        page_text = f"{first}--{last}" if first and last and first != last else (first or "")
    else:
        page_text = str(pages).replace("-", "--")
    doi = item.get("DOI") or ""
    for identifier in item.get("identifiers") or []:
        if identifier.get("type") == "doi":
            doi = identifier.get("value") or doi
    authors = [{"family": _text(a.get("family")), "given": _text(a.get("given"))}
               for a in item.get("authors") or [] if a.get("family")]
    issued = item.get("issued") or {}
    return {
        "type": item.get("type") or "",
        "title": _text(item.get("title")),
        "authors": authors,
        "year": issued.get("year") if isinstance(issued, dict) else None,
        "venue": _text(container.get("title")),
        "volume": _text(container.get("volume") or item.get("volume")),
        "number": _text(container.get("issue") or item.get("number")),
        "pages": _text(page_text),
        "doi": _text(doi),
    }


def is_preprint_record(record: dict | None) -> bool:
    if not record:
        return False
    return record.get("type") in _PREPRINT_TYPES or "arxiv" in record.get("venue", "").lower() \
        or record.get("doi", "").lower().startswith("10.48550/")


def trim_entry(entry: dict) -> dict:
    """Reduce an API entry to what the checks and the ledger need."""
    provenance = entry.get("_provenance") or {}
    stages = provenance.get("stages_run") or []
    via = str(provenance.get("resolved_via") or "")
    retraction = entry.get("retraction") or {}
    candidates = []
    for candidate in entry.get("candidates") or []:
        # Weaker guesses are unrelated papers that share a few words, so they aren't kept.
        record = trim_record(candidate.get("item"))
        if record and candidate.get("score", 0) >= CANDIDATE_SCORE and len(candidates) < 3:
            candidates.append({"score": candidate.get("score", 0), "record": record})
    return {
        "verdict": entry.get("verdict"),
        "confidence": entry.get("confidence"),
        "record": trim_record(entry.get("matched")),
        # A title-search match isn't reached through the entry's own identifier, so it may
        # be a different version of the work. Such a record never corrects anything.
        "by_search": via.endswith("-search") or (bool(stages) and stages[0] == "search"),
        "mismatches": [{k: m.get(k) for k in ("field", "claimed", "resolved", "similarity")}
                       for m in entry.get("mismatches") or []],
        "candidates": candidates,
        "caveats": [_text(c) for c in provenance.get("caveats") or []],
        "retraction": {
            "checked": bool(retraction.get("checked")),
            "retracted": bool(retraction.get("isRetracted")),
            "concern": bool(retraction.get("hasConcern")),
            "corrections": bool(retraction.get("hasCorrections")),
        },
    }
