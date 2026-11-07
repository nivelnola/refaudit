"""Keep the references read from a PDF as a .bib file, so the PDF is read once.

The file is both a cache and something you can fix: if the PDF was misread, correct the
entry by hand and run again. Your edits are used, and never overwritten.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from . import bib, pdf

# Raise this whenever pdf.py reads references differently, so cached readings are redone.
EXTRACTOR_VERSION = "1"
_META = re.compile(r"@comment\{refaudit: pdf=(\w+), extractor=(\w+), content=(\w+)\}")
_ESCAPED = re.compile(r"\\([{}\\])")


def cache_path(pdf_path: Path, out_dir: Path) -> Path:
    return out_dir / f"{pdf_path.stem}.references.bib"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:32]


def _escape(text: str) -> str:
    return re.sub(r"([{}\\])", r"\\\1", text)


def _entries(refs: list) -> str:
    """The references as bib entries. Keys are ref1, ref2...; the printed label is kept."""
    blocks = []
    for n, ref in enumerate(refs, 1):
        fields = {"label": ref.key, **ref.fields, "printed": ref.raw}
        width = max(len(name) for name in fields)
        lines = [f"  {name.ljust(width)} = {{{_escape(value)}}}," for name, value in fields.items()]
        kind = "misc" if not ref.parse_ok else ref.etype
        blocks.append(f"@{kind}{{ref{n},\n" + "\n".join(lines) + "\n}\n")
    return "\n".join(blocks)


def _header(pdf_name: str, pdf_hash: str, content: str) -> str:
    return (f"% refaudit read these references from {pdf_name}. If it misread one, fix the entry here:\n"
            "% your edits are used from now on, and this file is never overwritten once you've changed it.\n"
            "% An entry with no title is one refaudit couldn't split into fields; \"printed\" is its text.\n"
            f"@comment{{refaudit: pdf={pdf_hash}, extractor={EXTRACTOR_VERSION}, content={_sha(content.encode('utf-8'))}}}\n\n")


def write(path: Path, pdf_name: str, pdf_hash: str, refs: list) -> None:
    content = _entries(refs)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_header(pdf_name, pdf_hash, content) + content, encoding="utf-8", newline="\n")


def read(text: str) -> list:
    """References as the PDF reader would have made them."""
    refs = []
    for entry in bib.parse_bib(text, hidden_prefix=None):
        fields = {name: _ESCAPED.sub(r"\1", value) for name, value in entry.fields.items()}
        label = fields.pop("label", entry.key)
        printed = fields.pop("printed", "")
        ref = bib.Reference(key=label, source="pdf", raw=printed, fields=fields,
                            parse_ok=bool(fields.get("title")))
        if ref.parse_ok:
            ref.etype = entry.etype
        refs.append(ref)
    return refs


def load(pdf_path: Path, out_dir: Path, save: bool = True) -> tuple[list, str]:
    """The PDF's references, and a line saying where they came from.

    Read from the cache when it was made from this same PDF, by this extractor, or when you've
    edited it. Otherwise the PDF is read and, if `save`, the cache is written again, unless
    you've edited it, in which case it's left alone and the PDF's own reading is used."""
    path = cache_path(pdf_path, out_dir)
    pdf_hash = _sha(pdf_path.read_bytes())
    edited = False
    if path.is_file():
        text = path.read_text(encoding="utf-8")
        meta = _META.search(text)
        if meta:
            content = text[meta.end():].lstrip("\n")
            edited = _sha(content.encode("utf-8")) != meta.group(3)
            if meta.group(1) == pdf_hash and (edited or meta.group(2) == EXTRACTOR_VERSION):
                how = "your edited copy" if edited else "the copy saved last time"
                refs = read(text)
                return refs, f"Read {len(refs)} references from {path.name} ({how} of {pdf_path.name})"
    refs = pdf.extract_references(pdf_path)
    note = f"Found {len(refs)} references in the PDF"
    if edited:
        note += (f". {pdf_path.name} has changed since you edited {path.name}, so your edits weren't "
                 "used, and the file was left as it is. Delete it to save the new reading")
    elif save:
        write(path, pdf_path.name, pdf_hash, refs)
    return refs, note
