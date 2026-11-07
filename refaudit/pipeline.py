"""Run the chosen sweeps in order, then write the corrected bib, the reports and the review files."""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from . import bib, changes as changelog, report
from .sweeps import REGISTRY
from .sweeps.verify import dry_run


def runnable(ctx, names: list[str]) -> list:
    """The sweeps to run. In a full run, ones the inputs don't allow are skipped with a note."""
    chosen = []
    for sweep in REGISTRY.values():
        if sweep.name not in names:
            continue
        why = sweep.missing_input(ctx)
        if why and ctx.command == "full":
            print(f"Skipping {sweep.name}: it {why}.")
            continue
        if why:
            raise SystemExit(f"Error: refaudit {sweep.name} {why}. Run `refaudit {sweep.name} --help` for usage.")
        chosen.append(sweep)
    return chosen


def _merged_changes(ctx, results: dict) -> dict:
    """id(reference) -> changes. Format's changes already credit verify's corrections."""
    if "format" in results:
        return results["format"].changes
    if "verify" in results:
        return results["verify"].changes
    return {}


def _apply(ctx, new_text: str) -> Path:
    """Write the changes into the bib itself, in its own encoding, after backing it up.
    Returns the backup's path."""
    try:
        data = new_text.encode(ctx.bib_encoding)
    except UnicodeEncodeError:
        raise SystemExit(f"Error: {ctx.bib_path.name} is saved as {ctx.bib_encoding}, and the changes hold "
                         "characters it can't store. Nothing was changed. Use --write for a UTF-8 "
                         "corrected copy instead.") from None
    backup = ctx.bib_path.with_name(ctx.bib_path.name + ".bak")
    shutil.copy2(ctx.bib_path, backup)
    ctx.bib_path.write_bytes(data)
    return backup


# Sweeps that only read the bib. They check it as it is on disk at their turn: after --apply,
# that's the changed bib, since that's the one you'll submit.
READERS = ("lint", "consistency")


@dataclass
class Saved:
    """What happened to the bib's changes."""
    changed: list                 # (reference, changes) for each entry with any
    corrected: Path | None = None
    applied: Path | None = None   # the backup, if --apply changed the bib


def run(ctx, names: list[str]) -> int:
    sweeps = runnable(ctx, names)
    ctx.load()
    if getattr(ctx.args, "dry_run", False) and any(s.name == "verify" for s in sweeps):
        dry_run(ctx)
        return 0

    results: dict = {}
    saved = None
    for sweep in sweeps:
        if sweep.name in READERS and saved is None:
            saved = save_changes(ctx, results)
        results[sweep.name] = sweep.run(ctx, results)
    write_outputs(ctx, sweeps, results, saved)
    return 0


def save_changes(ctx, results: dict) -> Saved | None:
    """Write changes.md and the diff, and the corrected copy or the bib itself if asked.
    None if nothing that changes a bib ran. After --apply, the bib is read again."""
    if not ({"verify", "format"} & set(results)) or ctx.bib_path is None:
        return None
    out = ctx.out_dir
    out.mkdir(parents=True, exist_ok=True)
    by_ref = _merged_changes(ctx, results)
    saved = Saved([(ref, by_ref[id(ref)]) for ref in ctx.bib_refs if by_ref.get(id(ref))])
    new_text = bib.write_corrected(ctx.bib_text, [(r, changelog.edits_for(cs)) for r, cs in saved.changed])
    stem = ctx.bib_path.stem
    if getattr(ctx.args, "write", False):
        saved.corrected = out / f"{stem}.corrected.bib"
        saved.corrected.write_bytes(new_text.encode("utf-8"))
    if getattr(ctx.args, "apply", False) and saved.changed:
        saved.applied = _apply(ctx, new_text)
    links = {name: f"{name}.md" for name in results}
    (out / "changes.md").write_text(report.link_dois(changelog.render(
        saved.changed, [p.name for p in ctx.inputs], ctx.today.isoformat(),
        saved.corrected.name if saved.corrected else None, links, saved.applied)), encoding="utf-8")
    diff_path = out / f"{stem}.bib.diff"
    if saved.changed:
        diff_path.write_text(changelog.unified_diff(ctx.bib_text, new_text, ctx.bib_path.name),
                             encoding="utf-8", newline="")
    elif diff_path.exists():
        diff_path.unlink()  # an old diff would describe changes this run didn't make
    if "verify" in results:
        ledger = ctx.ledger
        for ref, cs in saved.changed:
            original = bib.fingerprint(ref)
            if ledger.get(original) is not None:
                ledger.alias(bib.fingerprint(changelog.after(ref, cs)), original)
        ledger.save()
    if saved.applied:
        ctx.reload_bib()
    return saved


def write_outputs(ctx, sweeps: list, results: dict, saved: Saved | None = None) -> list:
    """Write each report and summary.md, saving the bib's changes first if that hasn't
    happened yet. Returns the paths written."""
    if saved is None:
        saved = save_changes(ctx, results)
    out = ctx.out_dir
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for sweep in sweeps:
        path = out / f"{sweep.name}.md"
        path.write_text(sweep.render(results[sweep.name], ctx), encoding="utf-8")
        written.append(path)
    if len(sweeps) > 1:
        path = out / "summary.md"
        path.write_text(report.summary(ctx, [(s, results[s.name]) for s in sweeps]), encoding="utf-8")
        written.insert(0, path)

    for sweep in sweeps:
        for line in results[sweep.name].console:
            print(line)
    changed = saved.changed if saved else None
    if changed is not None:
        fixed = sum(1 for _, cs in changed if any("verify" in c.sweeps for c in cs))
        print(f"{len(changed)} entries changed: {fixed} corrected, {len(changed) - fixed} restyled. "
              f"Before and after: {out / 'changes.md'}")
    print("Reports: " + ", ".join(p.name for p in written) + f" in {out}")
    if saved and saved.corrected:
        print(f"Corrected copy: {saved.corrected}")
        written.append(saved.corrected)
    if saved and saved.applied:
        print(f"Applied to {ctx.bib_path}. The version before is in {saved.applied.name}.")
    elif changed:
        print("Your bib wasn't changed. Add --write for a corrected copy, or --apply to change it in place.")
    if changed is not None:
        written.append(out / "changes.md")
        if changed:
            written.append(out / f"{ctx.bib_path.stem}.bib.diff")
    return written
