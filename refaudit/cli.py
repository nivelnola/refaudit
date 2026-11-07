"""The refaudit command: subcommands, help, and dispatch to the sweeps."""
from __future__ import annotations

import argparse
import shutil
import sys
import textwrap
from datetime import date
from pathlib import Path

from . import __version__, api, bib, config, pdf, pipeline
from .context import Clients, Context
from .sweeps import REGISTRY

FULL = "full"
COMMANDS = [FULL, *REGISTRY]
WRITERS = (FULL, "verify", "format")  # the commands that can change a bib
_HELP = ("-h", "--help", "help")
_INTRO = ("Audit a paper's references: verify them against scholarly registries, apply a "
          "citation style, and check the bib for errors and inconsistencies.")
_OUTPUT = ("Output goes to a refaudit/ folder next to your input (or --out): one report per "
           "command, summary.md when several ran, and changes.md and <name>.bib.diff with every "
           "proposed change, before and after. Your bib isn't changed unless you ask: --write "
           "writes a corrected copy, and --apply changes your bib itself, keeping a backup.")


def _width() -> int:
    return min(100, max(60, shutil.get_terminal_size().columns - 2))


def _wrap(text: str, indent: str = "") -> str:
    """Wrap paragraphs, leaving lines that start with spaces (lists) as they are."""
    out = []
    for para in text.split("\n\n"):
        lines = para.split("\n")
        if any(line.startswith(" ") for line in lines[1:]) or para.startswith(" "):
            out.append("\n".join(indent + line for line in lines))
        else:
            out.append(textwrap.fill(" ".join(lines), _width(), initial_indent=indent,
                                     subsequent_indent=indent, break_on_hyphens=False))
    return "\n\n".join(out)


def _facts(rows: list[tuple[str, str]]) -> str:
    width = max(len(name) for name, _ in rows) + 2
    lines = []
    for name, value in rows:
        lines.append(textwrap.fill(value, _width(), initial_indent=(name + ":").ljust(width),
                                   subsequent_indent=" " * width, break_on_hyphens=False))
    return "\n".join(lines)


def _examples(rows: list[tuple[str, str]]) -> str:
    width = max(len(cmd) for cmd, _ in rows) + 3
    return "examples:\n" + "\n".join(f"  {cmd.ljust(width)}{what}" for cmd, what in rows)


def main_help() -> str:
    width = max(len(c) for c in COMMANDS) + 3
    commands = [f"  {FULL.ljust(width)}Run every check the inputs allow (the default when no command is given)"]
    commands += [f"  {s.name.ljust(width)}{s.summary}" for s in REGISTRY.values()]
    return "\n".join([
        "usage: refaudit [COMMAND] FILE... [options]", "",
        _wrap(_INTRO), "",
        "commands:", *commands, "",
        "global options (every command):",
        "  --config DIR    config folder (default: refaudit_config/ next to the input, then the tool's)",
        "  --out FOLDER    where reports go (default: refaudit/ next to the input)",
        "  -h, --help      show this help; for one command's help: refaudit verify --help",
        "  --version       show the version", "",
        "output options (verify, format and full):",
        "  --write         also write <name>.corrected.bib in the output folder",
        "  --apply         make the changes in your bib itself, keeping the old one as <name>.bib.bak",
        "",
        _examples([("refaudit refs.bib paper.pdf", "full audit of what the paper cites"),
                   ("refaudit lint refs.bib", "quick offline check while writing"),
                   ("refaudit format refs.bib --style ieee", "propose IEEE style changes, change nothing"),
                   ("refaudit format refs.bib --style ieee --apply", "make them in refs.bib"),
                   ("refaudit verify refs.bib --dry-run", "how many paid API calls would this use?")]),
        "", _wrap(_OUTPUT), ""])


def _sweep_help(sweep) -> str:
    return "\n\n".join([
        _wrap(sweep.summary), _wrap(sweep.description),
        _facts([("Inputs", sweep.inputs_text), ("Network & cost", sweep.cost),
                ("Changes your bib", sweep.changes_bib), ("Writes", sweep.writes + ", in the output folder"),
                ("Reads config from", sweep.config_file + (" (in refaudit_config/)"
                                                           if sweep.config_file != "none" else ""))])])


def _full_help() -> str:
    rows = [(s.name, f"{s.summary} (needs {s.inputs_text})") for s in REGISTRY.values()]
    return "\n\n".join([
        "Run every check the inputs allow. This is what refaudit does when no command is given.",
        "It runs, in order:\n" + "\n".join(f"  {name.ljust(13)}{what}" for name, what in rows),
        _wrap("A check the inputs don't allow is skipped with a note: with only a bib, compare is "
              "skipped; with only a PDF, so are format and lint. Options for any of the checks "
              "can be given here."),
        _facts([("Network & cost", REGISTRY["verify"].cost),
                ("Changes your bib", "Only with --write (a corrected copy) or --apply (in place). Proposes "
                                     "verified corrections and style changes in changes.md either way."),
                ("Writes", "summary.md and each check's report, changes.md, the .bib.diff and "
                           "ledger.json, in the output folder"),
                ("Reads config from", "every file in refaudit_config/")])])


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise SystemExit(f"Error: {message}. Run `{self.prog} --help` for usage.")


def _parser(command: str) -> _Parser:
    sweeps = list(REGISTRY.values()) if command == FULL else [REGISTRY[command]]
    sweep = None if command == FULL else sweeps[0]
    examples = sweep.examples if sweep else [
        ("refaudit refs.bib paper.pdf", "everything, focused on what the paper cites"),
        ("refaudit full refs.bib --style ieee", "everything, with the IEEE style")]
    parser = _Parser(prog=f"refaudit {command}", usage=f"refaudit {command} FILE... [options]",
                     description=_full_help() if command == FULL else _sweep_help(sweep),
                     epilog=_examples(examples), formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("inputs", nargs="+", metavar="FILE", help="a .bib file, a PDF, or one of each")
    common = parser.add_argument_group("global options")
    common.add_argument("--config", metavar="DIR",
                        help="config folder (default: refaudit_config/ next to the input, then the tool's)")
    common.add_argument("--out", metavar="FOLDER", help="where reports go (default: refaudit/ next to the input)")
    for s in sweeps:
        group = parser.add_argument_group(f"{s.name} options")
        s.add_arguments(group)
    if command in WRITERS:
        output = parser.add_argument_group(
            "output options", "Without these, your bib isn't changed: changes.md and <name>.bib.diff propose the changes.")
        output.add_argument("--write", action="store_true",
                            help="also write <name>.corrected.bib in the output folder")
        output.add_argument("--apply", action="store_true",
                            help="make the changes in your bib itself, keeping the old one as <name>.bib.bak next to it")
    return parser


def _split_inputs(names: list) -> tuple[Path | None, Path | None]:
    bib_path = pdf_path = None
    for name in names:
        path = Path(name)
        if not path.is_file():
            raise SystemExit(f"Error: file not found: {path}")
        suffix = path.suffix.lower()
        if suffix == ".bib" and bib_path is None:
            bib_path = path
        elif suffix == ".pdf" and pdf_path is None:
            pdf_path = path
        else:
            raise SystemExit(f"Error: expected one .bib and/or one .pdf, but got {path.name}.")
    return bib_path, pdf_path


def run(argv: list | None = None, client: api.Client | None = None, today: date | None = None,
        env_file: Path | None = None, arxiv_client=None, crossref_client=None, link_client=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in _HELP:
        topic = argv[1] if len(argv) > 1 and argv[0] == "help" else None
        if topic in COMMANDS:
            _parser(topic).print_help()
        elif topic:
            raise SystemExit(f"Error: there's no command \"{topic}\". Commands: {', '.join(COMMANDS)}.")
        else:
            print(main_help())
        return 0
    if argv[0] == "--version":
        print(f"refaudit {__version__}")
        return 0
    command = argv.pop(0) if argv[0] in COMMANDS else FULL
    if command == FULL and argv and not argv[0].startswith("-") and not Path(argv[0]).suffix:
        raise SystemExit(f"Error: there's no command \"{argv[0]}\". Commands: {', '.join(COMMANDS)}. "
                         "Run `refaudit --help` for usage.")
    args = _parser(command).parse_args(argv)
    bib_path, pdf_path = _split_inputs(args.inputs)
    first = bib_path or pdf_path
    folder = config.find_config(args.config, first)
    cfg = config.load(folder, getattr(args, "style", None))
    out_dir = Path(args.out) if args.out else first.resolve().parent / "refaudit"
    clients = Clients(sidekick=client, arxiv=arxiv_client, crossref=crossref_client, links=link_client,
                      env_file=env_file)
    ctx = Context(command, args, cfg, bib_path, pdf_path, out_dir, today, clients)
    names = list(REGISTRY) if command == FULL else [command]
    return pipeline.run(ctx, names)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):  # paths with accents shouldn't crash a Windows console
        sys.stdout.reconfigure(errors="replace")
    try:
        sys.exit(run())
    except (config.ConfigError, bib.BibError, pdf.PdfError) as err:
        raise SystemExit(f"Error: {err}") from None


if __name__ == "__main__":
    main()
