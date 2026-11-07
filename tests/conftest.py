import pytest

from refaudit import api


class FakeClient:
    """Stands in for the API. `answer` maps a claim's title to an API entry."""

    def __init__(self, answer=None, fail=None, limit=None):
        self.calls = 0
        self.bodies = []
        self.answer = answer or (lambda claim: matched_entry(claim))
        self.fail = fail
        self.limit = limit
        self.is_anonymous = False

    def audit(self, body):
        if self.limit is not None and self.calls >= self.limit:
            raise api.LimitReached("max_calls_per_run reached")
        self.calls += 1
        self.bodies.append(body)
        if self.fail:
            raise self.fail
        entries = []
        for i, claim in enumerate(body.get("claims", body.get("references")), 1):
            entries.append({"index": i, **self.answer(claim)})
        return {"ok": True, "entries": entries}


class ExplodingClient(FakeClient):
    def audit(self, body):
        raise AssertionError("This run should have made no API calls.")


def record(title="A Real Paper", doi="10.1000/real", volume="12", issue="3", first="10", last="20",
           year=2020, authors=(("Smith", "Jane"), ("Doe", "John")), venue="Journal of Things",
           kind="journal-article"):
    return {
        "type": kind, "title": title,
        "authors": [{"family": f, "given": g} for f, g in authors],
        "container": {"title": venue, "volume": volume, "issue": issue},
        "identifiers": [{"type": "doi", "value": doi}] if doi else [],
        "issued": {"year": year},
        "pages": {"first": first, "last": last} if first else {},
    }


def matched_entry(claim=None, confidence="high", rec=None, via="crossref", mismatches=()):
    return {
        "status": "ok", "verdict": "matched", "confidence": confidence,
        "matched": rec or record(), "mismatches": list(mismatches), "candidates": [],
        "retraction": {"checked": True, "isRetracted": False, "hasConcern": False, "hasCorrections": False},
        "_provenance": {"stages_run": ["compare"], "resolved_via": via},
    }


@pytest.fixture
def fake_client():
    return FakeClient
