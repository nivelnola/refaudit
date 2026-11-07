"""Everything a run's sweeps share: inputs, config, the ledger and the network clients."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import cached_property
from pathlib import Path

from . import bib, check, config, format as fmt, pdfcache
from .ledger import Ledger


@dataclass
class Clients:
    """Network clients. Tests pass fakes; None means build the real one when needed."""
    sidekick: object = None
    arxiv: object = None
    crossref: object = None
    links: object = None
    env_file: Path | None = None


class Context:
    def __init__(self, command: str, args, cfg: config.Config, bib_path: Path | None,
                 pdf_path: Path | None, out_dir: Path, today: date | None, clients: Clients):
        self.command = command
        self.args = args
        self.cfg = cfg
        self.bib_path = bib_path
        self.pdf_path = pdf_path
        self.out_dir = out_dir
        self.today = today or date.today()
        self.clients = clients
        self.bib_text = ""
        self.bib_encoding = "utf-8"
        self.bib_refs: list = []
        self.pdf_refs: list = []
        self.cross: check.CrossCheck | None = None
        self.unreadable: list = []   # (key, line, why) for bib entries that couldn't be read
        self.applied = False         # --apply changed the bib, and bib_refs is the new version

    def load(self) -> None:
        """Read the inputs and pair the bib with the PDF."""
        if self.bib_path:
            self.bib_text, self.bib_encoding = bib.read_with_encoding(self.bib_path)
            self.bib_refs = bib.parse_bib(self.bib_text, self.cfg.format.hidden_prefix, self.unreadable)
            print(f"Read {self.bib_path.name}: {len(self.bib_refs)} references")
            if self.unreadable:
                print(f"Skipped {len(self.unreadable)} entries that couldn't be read. Run lint for "
                      "where they are; they're left exactly as written.")
        if self.pdf_path:
            print(f"Reading {self.pdf_path.name}...", flush=True)
            # A dry run writes nothing, not even the saved reading of the PDF.
            save = not getattr(self.args, "dry_run", False)
            self.pdf_refs, note = pdfcache.load(self.pdf_path, self.out_dir, save)
            print(note)
        if self.bib_path and self.pdf_path:
            self.cross = check.cross_check(self.bib_refs, self.pdf_refs)

    def reload_bib(self) -> None:
        """Read the bib again after --apply changed it, for the checks that come after."""
        self.bib_text, self.bib_encoding = bib.read_with_encoding(self.bib_path)
        self.unreadable = []
        self.bib_refs = bib.parse_bib(self.bib_text, self.cfg.format.hidden_prefix, self.unreadable)
        self.applied = True

    @property
    def inputs(self) -> list[Path]:
        return [p for p in (self.bib_path, self.pdf_path) if p]

    @property
    def pdf_only(self) -> list:
        """PDF references with no bib entry. With no bib, every PDF reference."""
        return list(self.cross.pdf_only) if self.cross else list(self.pdf_refs)

    @property
    def all_refs(self) -> list:
        return self.bib_refs + self.pdf_only

    @cached_property
    def unused_keys(self) -> set:
        return set(self.cross.bib_only) if self.cross else set()

    def is_unused(self, ref) -> bool:
        return ref.source == "bib" and ref.key in self.unused_keys

    @cached_property
    def venues(self) -> fmt.Venues:
        return fmt.load_venues(self.cfg.format.venue_lists, self.cfg.format.abbreviations_file)

    @cached_property
    def ledger(self) -> Ledger:
        return Ledger(self.out_dir / "ledger.json", self.cfg.verify.recheck_after_days, self.today)

    def stored_record(self, ref) -> dict | None:
        """A verified record from earlier runs, reached by identifier. Read-only, so free."""
        result = self.ledger.get(bib.fingerprint(ref))
        if result and result["verdict"] == "matched" and not result["by_search"]:
            return result.get("record")
        return None

    def matched_records(self) -> dict:
        """Cite key -> verified record, for every reference with one in the ledger."""
        records = {}
        for ref in self.all_refs:
            result = self.ledger.get(bib.fingerprint(ref))
            if result and result["verdict"] == "matched" and result.get("record"):
                records[ref.key] = result["record"]
        return records
