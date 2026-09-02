"""Experiment configuration loaded from YAML.

One YAML file describes a whole comparison: which binaries to run, which
arguments each is given, which benchmarks to run them over, and the run
budget. Adding a solver configuration is an edit to that file, never to the
code.

Settings are layered. `configs/defaults.yaml` ships with the repo and holds
the house defaults -- timeout, repeats, revalidation policy -- so an
experiment file carries only what it actually changes, and raising the
default timeout is one edit rather than one per experiment. An experiment
file overrides those; command-line flags override both.

The first solver listed is the baseline; every other one is scored against
it in the final tables.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Shipped alongside the code, so it is found from any working directory.
DEFAULTS_PATH = Path(__file__).resolve().parent.parent / "configs" / "defaults.yaml"

# Sections merged key-by-key across layers. Everything else replaces wholesale:
# `solvers` is a list, and merging two lists of solvers is guesswork.
MERGED_SECTIONS = ("benchmarks", "run", "revalidation", "report",
                   "binaries", "arg_groups")

# Two ways of saying which files to run, so setting one has to clear the
# other. Otherwise an experiment naming a file_list would silently inherit
# `dirs` from the defaults and quietly run the wrong corpus.
EXCLUSIVE_KEYS = {"benchmarks": ("dirs", "file_list")}

TOP_LEVEL_KEYS = {"name", "output_prefix", "benchmarks", "run", "revalidation",
                  "report", "binaries", "arg_groups", "solvers"}
SOLVER_KEYS = {"name", "binary", "arg_groups", "args"}
SECTION_KEYS = {
    "benchmarks": {"dirs", "file_list"},
    "run": {"timeout", "runs", "workers", "wall_hours", "incremental"},
    "revalidation": {"enabled", "ratio", "timeout"},
    "report": {"virtual_timeouts"},
}


class ConfigError(Exception):
    """A configuration file that cannot be used as written."""


@dataclass(frozen=True)
class Solver:
    """One solver configuration: a binary plus the arguments it is given."""

    name: str
    binary: Path
    args: list[str]


@dataclass
class ExperimentConfig:
    source: Path
    defaults_source: Path | None
    name: str
    output_prefix: str
    solvers: list[Solver]
    dirs: list[Path] = field(default_factory=list)
    file_list: Path | None = None
    timeout: float = 30.0
    runs: int = 3
    workers: int = 0            # 0 -> os.cpu_count()
    wall_hours: float = 24.0
    incremental: bool = False
    revalidate: bool = True
    revalidate_ratio: float = 3.0
    revalidate_timeout: float | None = None   # None -> max(4 * timeout, 120)
    virtual_timeouts: list[float] = field(default_factory=lambda: [24, 120])

    @property
    def base(self) -> Solver:
        return self.solvers[0]

    @property
    def alts(self) -> list[Solver]:
        return self.solvers[1:]

    @property
    def solver_names(self) -> list[str]:
        return [s.name for s in self.solvers]

    def resolved_revalidate_timeout(self) -> float:
        if self.revalidate_timeout is not None:
            return self.revalidate_timeout
        return max(self.timeout * 4, 120.0)


def _check_keys(where, got, allowed):
    unknown = sorted(set(got) - allowed)
    if unknown:
        raise ConfigError(
            f"{where}: unknown key(s) {', '.join(unknown)} "
            f"(allowed: {', '.join(sorted(allowed))})")


def _section(doc, name):
    value = doc.get(name) or {}
    if not isinstance(value, dict):
        raise ConfigError(f"`{name}` must be a mapping, got {type(value).__name__}")
    _check_keys(name, value, SECTION_KEYS[name])
    return value


def _string_list(where, value):
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{where}: expected a list, got {type(value).__name__}")
    return [str(v) for v in value]


def _parse_solvers(doc):
    entries = doc.get("solvers")
    if not entries:
        raise ConfigError("`solvers` is required and must list at least two entries")
    if not isinstance(entries, list):
        raise ConfigError("`solvers` must be a list")
    if len(entries) < 2:
        raise ConfigError(
            "`solvers` needs at least two entries: the first is the baseline "
            "and the rest are scored against it")

    binaries = doc.get("binaries") or {}
    if not isinstance(binaries, dict):
        raise ConfigError("`binaries` must be a mapping of name -> path")

    arg_groups = doc.get("arg_groups") or {}
    if not isinstance(arg_groups, dict):
        raise ConfigError("`arg_groups` must be a mapping of name -> list of arguments")
    arg_groups = {k: _string_list(f"arg_groups.{k}", v) for k, v in arg_groups.items()}

    solvers = []
    seen = set()
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ConfigError(f"solvers[{i}]: expected a mapping")
        _check_keys(f"solvers[{i}]", entry, SOLVER_KEYS)

        name = entry.get("name")
        if not name:
            raise ConfigError(f"solvers[{i}]: `name` is required")
        if name in seen:
            raise ConfigError(f"solvers[{i}]: duplicate solver name {name!r}")
        seen.add(name)

        binary = entry.get("binary")
        if not binary:
            raise ConfigError(f"solvers[{i}] ({name}): `binary` is required")
        # A bare name refers to the `binaries` map; anything else is a path.
        binary = binaries.get(binary, binary)

        args = []
        for group in _string_list(f"solvers[{i}].arg_groups", entry.get("arg_groups")):
            if group not in arg_groups:
                raise ConfigError(
                    f"solvers[{i}] ({name}): unknown arg group {group!r} "
                    f"(defined: {', '.join(sorted(arg_groups)) or 'none'})")
            args.extend(arg_groups[group])
        args.extend(_string_list(f"solvers[{i}].args", entry.get("args")))

        solvers.append(Solver(name=name, binary=Path(str(binary)).expanduser(), args=args))

    return solvers


def _read_yaml(path, required=True):
    """Parse one YAML file into a mapping, or None if it is absent."""
    try:
        with open(path) as f:
            doc = yaml.safe_load(f)
    except FileNotFoundError:
        if required:
            raise ConfigError(f"no such config file: {path}") from None
        return None
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: {exc}") from None

    if doc is None:
        if required:
            raise ConfigError(f"{path}: file is empty")
        return None
    if not isinstance(doc, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    _check_keys(str(path), doc, TOP_LEVEL_KEYS)
    return doc


def merge_layers(base, override):
    """Overlay one config document on another.

    Mappings in MERGED_SECTIONS merge key-by-key, so an experiment that sets
    `run.timeout` keeps the inherited `run.runs`. Everything else replaces.
    """
    merged = dict(base)
    for key, value in override.items():
        if key in MERGED_SECTIONS and isinstance(value, dict):
            inherited = dict(merged.get(key) or {})
            for group in EXCLUSIVE_KEYS.get(key, ()):
                if group in value:
                    for other in EXCLUSIVE_KEYS[key]:
                        inherited.pop(other, None)
                    break
            inherited.update(value)
            merged[key] = inherited
        else:
            merged[key] = value
    return merged


def load_config(path, defaults=DEFAULTS_PATH) -> ExperimentConfig:
    """Read and validate an experiment YAML file, layered over the defaults.

    `defaults` may be None to use the built-in defaults alone. A missing
    defaults file is not an error: the code defaults match what it ships with.
    """
    path = Path(path)
    doc = _read_yaml(path)

    defaults_doc = _read_yaml(defaults, required=False) if defaults else None
    if defaults_doc is not None:
        doc = merge_layers(defaults_doc, doc)
    else:
        defaults = None

    solvers = _parse_solvers(doc)

    benchmarks = _section(doc, "benchmarks")
    run = _section(doc, "run")
    reval = _section(doc, "revalidation")
    report = _section(doc, "report")

    dirs = [Path(d).expanduser() for d in _string_list("benchmarks.dirs",
                                                       benchmarks.get("dirs"))]
    file_list = benchmarks.get("file_list")
    if not dirs and not file_list:
        raise ConfigError("benchmarks: one of `dirs` or `file_list` is required")

    virtual_timeouts = report.get("virtual_timeouts", [24, 120])
    if not isinstance(virtual_timeouts, list) or not virtual_timeouts:
        raise ConfigError("report.virtual_timeouts must be a non-empty list")

    config = ExperimentConfig(
        source=path,
        defaults_source=Path(defaults) if defaults else None,
        name=str(doc.get("name") or path.stem),
        output_prefix=str(doc.get("output_prefix") or path.stem),
        solvers=solvers,
        dirs=dirs,
        file_list=Path(str(file_list)).expanduser() if file_list else None,
        timeout=float(run.get("timeout", 30.0)),
        runs=int(run.get("runs", 3)),
        workers=int(run.get("workers") or 0) or (os.cpu_count() or 1),
        wall_hours=float(run.get("wall_hours", 24.0)),
        incremental=bool(run.get("incremental", False)),
        revalidate=bool(reval.get("enabled", True)),
        revalidate_ratio=float(reval.get("ratio", 3.0)),
        revalidate_timeout=(float(reval["timeout"])
                            if reval.get("timeout") is not None else None),
        virtual_timeouts=[float(v) for v in virtual_timeouts],
    )

    if config.runs < 1:
        raise ConfigError("run.runs must be at least 1")
    if config.timeout <= 0:
        raise ConfigError("run.timeout must be positive")

    return config
