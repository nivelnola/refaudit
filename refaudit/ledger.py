"""Remember what's been checked, so unchanged references cost no API calls."""
from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path


class Ledger:
    def __init__(self, path: Path, max_age_days: int | None, today: date | None = None):
        self.path = path
        self.max_age_days = max_age_days
        self.today = today or date.today()
        self.results: dict = {}
        self.aliases: dict = {}
        self.links: dict = {}
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self.results = data.get("results", {})
                self.aliases = data.get("aliases", {})
                self.links = data.get("links", {})
                for stored in self.results.values():  # older ledgers kept weak search guesses
                    result = stored["result"]
                    result["candidates"] = [c for c in result.get("candidates", [])
                                            if c.get("score", 0) >= 0.9][:3]
            except (json.JSONDecodeError, AttributeError):
                # A damaged ledger only costs a re-check, so start again.
                print(f"Warning: couldn't read {path}. Starting a new ledger.")

    def get(self, fp: str) -> dict | None:
        """The stored result for a fingerprint, unless it has expired."""
        stored = self.results.get(self.aliases.get(fp, fp))
        if stored is None:
            return None
        if self.max_age_days is not None:
            age = (self.today - date.fromisoformat(stored["checked"])).days
            if age > self.max_age_days:
                return None
        return stored["result"]

    def _fresh(self, checked: str) -> bool:
        return self.max_age_days is None or (self.today - date.fromisoformat(checked)).days <= self.max_age_days

    def get_link(self, url: str) -> dict | None:
        """A stored link check, unless it has expired: {"state", "detail", "checked"}."""
        stored = self.links.get(url)
        return stored if stored and self._fresh(stored["checked"]) else None

    def put_link(self, url: str, state: str, detail: str) -> None:
        self.links[url] = {"state": state, "detail": detail, "checked": self.today.isoformat()}

    def checked_on(self, fp: str) -> str | None:
        stored = self.results.get(self.aliases.get(fp, fp))
        return stored["checked"] if stored else None

    def put(self, fp: str, result: dict) -> None:
        self.results[fp] = {"checked": self.today.isoformat(), "result": result}
        self.aliases.pop(fp, None)

    def alias(self, fp: str, original_fp: str) -> None:
        """Let another fingerprint (the corrected entry's) reuse a stored result."""
        target = self.aliases.get(original_fp, original_fp)
        if fp != target and target in self.results:
            self.aliases[fp] = target

    def save(self) -> None:
        """Write atomically, so an interruption can't damage the ledger."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({"results": self.results, "aliases": self.aliases, "links": self.links}, indent=1,
                                   ensure_ascii=False), encoding="utf-8")
        os.replace(temp, self.path)
