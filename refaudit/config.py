"""Load and validate the config folder: one file per sweep, plus a style file for format."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .bib import ORG_WORDS

TOOL_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = "refaudit_config"
STYLE_DIR = "styles"

# Every per-reference style rule and its allowed values. The first value is the default.
RULES = {
    "title_case": ["keep", "sentence", "title"],
    "protect_capitals": ["keep", "add"],
    "venue_names": ["keep", "abbreviated", "full"],
    "doi": ["keep", "remove", "add"],
    "pages": ["keep", "remove", "add"],
    "url": ["keep", "remove", "remove_if_doi", "show"],
    "venue_year": ["keep", "add", "remove"],
    "venue_sponsor": ["keep", "add", "remove"],
    "venue_prefix": ["keep", "add", "remove"],
    "authors": ["keep", "initials"],
    "month": ["keep", "remove", "show"],
}
LIST_RULES = ("remove_fields", "show_fields")
TYPES = ["conference", "journal", "preprint", "book", "techreport", "other"]
AUTO_CORRECT = ["high", "medium", "off"]
URL_CHECK = ["all", "not_found", "off"]
BACKENDS = ["bibtex", "biber"]
LINT_CHECKS = ["required_fields", "empty_fields", "years", "venue_year", "pages", "doi", "authors",
               "latex_specials", "repeated_keys", "non_ascii", "unprotected_capitals",
               "unknown_fields", "wrong_type_fields"]
CONSISTENCY_SECTIONS = ["duplicates", "venues", "authors", "fields", "conventions"]
# Units are written with capitals on purpose, and a style that lowercases titles is rarely the
# reason they look wrong, so by default they're neither flagged nor braced.
CAPITALS_IGNORE = ["dB", "dBm", "dBi", "Hz", "kHz", "MHz", "GHz", "THz", "mW", "kW", "MW", "GW",
                   "mA", "mV", "kV", "nm", "mm"]

# Which file each setting lives in.
FILES = {
    "verify": ["auto_correct", "crossref_lookup", "arxiv_lookup", "url_check", "sidekick_confirms_all",
               "check_unused", "recheck_after_days", "max_calls_per_run"],
    "format": ["style", "skip_unverified", "hidden_prefix", "keep_capitalized", "capitals_ignore",
               "organization_words", "venue_lists", "abbreviations_file"],
    "lint": ["backend", *LINT_CHECKS],
    "consistency": ["min_share", "duplicate_similarity", "duplicate_word_overlap", "duplicate_year_gap",
                    *CONSISTENCY_SECTIONS],
}
_HOME = {key: name for name, keys in FILES.items() for key in keys}
_STYLE_KEYS = ["all", *TYPES]


class ConfigError(Exception):
    """A problem in the config, with a message written for the user."""


@dataclass
class VerifyConfig:
    auto_correct: str = "high"
    crossref_lookup: bool = True
    arxiv_lookup: bool = True
    url_check: str = "all"
    sidekick_confirms_all: bool = False
    check_unused: bool = False
    recheck_after_days: int | None = 180
    max_calls_per_run: int | None = None


@dataclass
class FormatConfig:
    style: str = "default"
    style_path: Path | None = None
    rules: dict = field(default_factory=dict)  # type name -> rule name -> value
    skip_unverified: bool = True
    hidden_prefix: str | None = "OPT"
    keep_capitalized: list = field(default_factory=lambda: ["Bayesian", "Gaussian", "Markov"])
    capitals_ignore: list = field(default_factory=lambda: list(CAPITALS_IGNORE))
    organization_words: list = field(default_factory=lambda: sorted(ORG_WORDS))
    venue_lists: list = field(default_factory=lambda: ["computer_science",
                                                       "electrical_computer_engineering"])
    abbreviations_file: Path | None = None

    def for_type(self, ref_type: str) -> dict:
        return self.rules.get(ref_type) or self.rules["other"]

    @property
    def style_name(self) -> str:
        """The style's name for reports: "ieee", or "wacky" for a style given as a path."""
        return self.style_path.stem if self.style_path else Path(self.style).stem


@dataclass
class LintConfig:
    backend: str = "bibtex"
    checks: dict = field(default_factory=lambda: {name: True for name in LINT_CHECKS})


@dataclass
class ConsistencyConfig:
    min_share: float = 0.75
    duplicate_similarity: float = 0.88
    duplicate_word_overlap: float = 0.6
    duplicate_year_gap: int = 2
    sections: dict = field(default_factory=lambda: {name: True for name in CONSISTENCY_SECTIONS})


@dataclass
class Config:
    verify: VerifyConfig = field(default_factory=VerifyConfig)
    format: FormatConfig = field(default_factory=FormatConfig)
    lint: LintConfig = field(default_factory=LintConfig)
    consistency: ConsistencyConfig = field(default_factory=ConsistencyConfig)
    folder: Path | None = None


def _one_of(allowed: list) -> str:
    return (", ".join(allowed[:-1]) + " or " + allowed[-1]) if len(allowed) > 1 else allowed[0]


def _check_keys(data: dict, name: str) -> None:
    for key in data:
        if key in FILES[name]:
            continue
        home = _HOME.get(key)
        if home:
            raise ConfigError(f'{key} belongs in {home}.yaml, not {name}.yaml.')
        if key in _STYLE_KEYS or key in RULES or key in LIST_RULES:
            raise ConfigError(f'{key} is a style rule. Put it in a style file under {STYLE_DIR}/, '
                              f'not {name}.yaml.')
        raise ConfigError(f'Unknown setting "{key}" in {name}.yaml. Use {_one_of(FILES[name])}.')


def _bool(data: dict, key: str, current: bool) -> bool:
    if key not in data:
        return current
    if not isinstance(data[key], bool):
        raise ConfigError(f"{key} can't be \"{data[key]}\". Use true or false.")
    return data[key]


def _choice(data: dict, key: str, allowed: list, current: str) -> str:
    if key not in data:
        return current
    value = data[key]
    if value is False:  # YAML reads a bare "off" as False
        value = "off"
    if value not in allowed:
        raise ConfigError(f"{key} can't be \"{value}\". Use {_one_of(allowed)}.")
    return value


def _optional_positive_int(data: dict, key: str, current):
    if key not in data:
        return current
    value = data[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigError(f'{key} can\'t be "{value}". Use a whole number, or null for no limit.')
    return value


def _string_list(data: dict, key: str, current: list) -> list:
    if key not in data:
        return current
    value = data[key] or []
    if not isinstance(value, list):
        raise ConfigError(f"{key} must be a list, like [one, two].")
    return [str(v) for v in value]


def _section(data, name: str) -> dict:
    data = data or {}
    if not isinstance(data, dict):
        raise ConfigError(f'{name}.yaml must contain settings, like "setting: value".')
    _check_keys(data, name)
    return data


def build_verify(data: dict | None) -> VerifyConfig:
    data = _section(data, "verify")
    cfg = VerifyConfig()
    cfg.auto_correct = _choice(data, "auto_correct", AUTO_CORRECT, cfg.auto_correct)
    if data.get("url_check") is True:
        data = {**data, "url_check": "all"}
    cfg.url_check = _choice(data, "url_check", URL_CHECK, cfg.url_check)
    for key in ("crossref_lookup", "arxiv_lookup", "sidekick_confirms_all", "check_unused"):
        setattr(cfg, key, _bool(data, key, getattr(cfg, key)))
    cfg.recheck_after_days = _optional_positive_int(data, "recheck_after_days", cfg.recheck_after_days)
    cfg.max_calls_per_run = _optional_positive_int(data, "max_calls_per_run", cfg.max_calls_per_run)
    return cfg


def _read_rules(section: dict, where: str) -> dict:
    if not isinstance(section, dict):
        raise ConfigError(f'"{where}" must be a list of settings, like "pages: remove".')
    rules = {}
    for key, value in section.items():
        if key in LIST_RULES:
            if not isinstance(value, list):
                raise ConfigError(f'{key} in "{where}" must be a list, like [abstract, file].')
            rules[key] = [str(v).lower() for v in value]
        elif key in RULES:
            if value not in RULES[key]:
                raise ConfigError(f"{key} can't be \"{value}\". Use {_one_of(RULES[key])}.")
            rules[key] = value
        else:
            raise ConfigError(f'Unknown setting "{key}" in "{where}". '
                              f"Use {_one_of([*RULES, *LIST_RULES])}.")
    return rules


def build_style(data: dict | None, where: str = "the style") -> dict:
    """Turn a style file's contents into rules for every reference type."""
    data = data or {}
    if not isinstance(data, dict):
        raise ConfigError(f'{where} must contain sections like "all:" or "conference:".')
    for key in data:
        if key not in _STYLE_KEYS:
            home = _HOME.get(key)
            hint = f" It belongs in {home}.yaml." if home else f" Use {_one_of(_STYLE_KEYS)}."
            raise ConfigError(f'Unknown section "{key}" in {where}.{hint}')
    base = {name: allowed[0] for name, allowed in RULES.items()}
    base.update({name: [] for name in LIST_RULES})
    base.update(_read_rules(data.get("all") or {}, "all"))
    return {t: {**base, **_read_rules(data.get(t) or {}, t)} for t in TYPES}


def style_folders(folder: Path | None) -> list[Path]:
    """Where styles are found: the config folder's styles/, then the tool's."""
    found = [folder / STYLE_DIR] if folder else []
    return found + [TOOL_DIR / STYLE_DIR]


def available_styles(folder: Path | None = None) -> list[str]:
    names = set()
    for place in style_folders(folder):
        names.update(p.stem for p in place.glob("*.yaml"))
    return sorted(names)


def find_style(name: str, folder: Path | None) -> Path:
    """A style by name (looked up in the styles folders) or as a path to a .yaml file."""
    as_path = Path(name).expanduser()
    if name.endswith((".yaml", ".yml")) or len(as_path.parts) > 1:
        if not as_path.is_absolute() and folder is not None and not as_path.is_file():
            as_path = folder / as_path
        if not as_path.is_file():
            raise ConfigError(f"Style file not found: {as_path}")
        return as_path
    for place in style_folders(folder):
        if (place / f"{name}.yaml").is_file():
            return place / f"{name}.yaml"
    raise ConfigError(f'No style named "{name}". Available: {", ".join(available_styles(folder)) or "none"}.')


def _read_yaml(path: Path):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        raise ConfigError(f"Couldn't read {path}: {err}") from err


def build_format(data: dict | None, folder: Path | None = None, style: str | None = None,
                 style_data: dict | None = None) -> FormatConfig:
    """`style` overrides format.yaml's style. `style_data` skips the lookup (for tests)."""
    data = _section(data, "format")
    cfg = FormatConfig()
    cfg.skip_unverified = _bool(data, "skip_unverified", cfg.skip_unverified)
    if "hidden_prefix" in data:
        prefix = data["hidden_prefix"]
        if prefix is not None and (not isinstance(prefix, str) or not prefix.strip()
                                   or not prefix.replace("-", "").replace("_", "").isalnum()):
            raise ConfigError(f'hidden_prefix can\'t be "{prefix}". Use letters, like OPT or x-, '
                              "or null to delete fields instead of hiding them.")
        cfg.hidden_prefix = prefix
    cfg.keep_capitalized = _string_list(data, "keep_capitalized", cfg.keep_capitalized)
    cfg.capitals_ignore = _string_list(data, "capitals_ignore", cfg.capitals_ignore)
    cfg.organization_words = [w.lower() for w in _string_list(data, "organization_words", cfg.organization_words)]
    cfg.venue_lists = _string_list(data, "venue_lists", cfg.venue_lists)
    if data.get("abbreviations_file"):
        path = Path(str(data["abbreviations_file"])).expanduser()
        if not path.is_absolute() and folder is not None:
            path = folder / path
        if not path.is_file():
            raise ConfigError(f"abbreviations_file not found: {path}")
        cfg.abbreviations_file = path
    cfg.style = str(style or data.get("style") or "default")
    if style_data is not None:
        cfg.rules = build_style(style_data)
        return cfg
    cfg.style_path = find_style(cfg.style, folder)
    cfg.rules = build_style(_read_yaml(cfg.style_path), f"{cfg.style_path.name}")
    return cfg


def build_lint(data: dict | None) -> LintConfig:
    data = _section(data, "lint")
    cfg = LintConfig()
    cfg.backend = _choice(data, "backend", BACKENDS, cfg.backend)
    for name in LINT_CHECKS:
        cfg.checks[name] = _bool(data, name, True)
    return cfg


def build_consistency(data: dict | None) -> ConsistencyConfig:
    data = _section(data, "consistency")
    cfg = ConsistencyConfig()
    for key in ("min_share", "duplicate_similarity", "duplicate_word_overlap"):
        if key in data:
            value = data[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 1:
                raise ConfigError(f'{key} can\'t be "{value}". Use a number above 0 and up to 1, like 0.75.')
            setattr(cfg, key, float(value))
    if "duplicate_year_gap" in data:
        value = data["duplicate_year_gap"]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ConfigError(f'duplicate_year_gap can\'t be "{value}". Use a whole number of years, like 2.')
        cfg.duplicate_year_gap = value
    for name in CONSISTENCY_SECTIONS:
        cfg.sections[name] = _bool(data, name, True)
    return cfg


def build(files: dict | None = None, folder: Path | None = None, style: str | None = None,
          style_data: dict | None = None) -> Config:
    """Turn parsed YAML, one dict per file name, into a validated Config."""
    files = files or {}
    for name in files:
        if name not in FILES:
            raise ConfigError(f"Unknown config file {name}.yaml. Use {_one_of([f'{n}.yaml' for n in FILES])}.")
    return Config(verify=build_verify(files.get("verify")),
                  format=build_format(files.get("format"), folder, style, style_data),
                  lint=build_lint(files.get("lint")),
                  consistency=build_consistency(files.get("consistency")),
                  folder=folder)


def find_config(explicit: str | None, first_input: Path | None) -> Path | None:
    """--config, else refaudit_config/ next to the input, else the tool's, else none."""
    if explicit:
        path = Path(explicit)
        if not path.is_dir():
            raise ConfigError(f"Config folder not found: {path}. --config takes the folder that "
                              f"holds verify.yaml, format.yaml and the rest.")
        return path
    places = ([first_input.resolve().parent] if first_input else []) + [TOOL_DIR]
    for folder in places:
        if (folder / CONFIG_DIR).is_dir():
            return folder / CONFIG_DIR
    return None


def load(folder: Path | None, style: str | None = None) -> Config:
    if folder is None:
        return build({}, None, style)
    files = {}
    for name in FILES:
        path = folder / f"{name}.yaml"
        if path.is_file():
            files[name] = _read_yaml(path)
    unknown = sorted(p.name for p in folder.glob("*.yaml") if p.stem not in FILES)
    if unknown:
        raise ConfigError(f"{folder} has {', '.join(unknown)}, which refaudit doesn't read. "
                          f"Config files are {_one_of([f'{n}.yaml' for n in FILES])}.")
    return build(files, folder, style)


def read_api_key(env_file: Path | None = None) -> str | None:
    """Read SCHOLAR_API_KEY from the tool folder's .env. Never from the system environment."""
    path = env_file or TOOL_DIR / ".env"
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        name, sep, value = line.partition("=")
        if sep and name.strip() == "SCHOLAR_API_KEY":
            value = value.strip().strip("\"'")
            return value if value and value != "ssk_your_key_here" else None
    return None
