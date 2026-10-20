"""The three axes of a benchmark run: the method, the substrate and the benchmark.

Task 4.1 of the Harness2/DSH enhancement plan.

A benchmark run is one cell of an experiment, and an experiment is a grid.
The method says how the agent answers a task -- once, or k times with the best
of them kept. The substrate says what answers it -- the default loop of the
CLI, or a sub-agent preset. The benchmark says which tasks it is measured on.
Keeping the three apart is what makes a result readable: "P-P-C with k=2 on a
sub-agent preset over the long tasks" names one cell and nothing else.

Two things keep the experiment honest. The cell carries a mark: a hash of the
three axes and of the settings that decide the run. A results directory that
was made with another mark is not continued, because a run that mixes two
configurations measures neither of them. And the hosts that hold the answers
are unreachable while a task is evaluated, so a score says what the agent
could do and not what it could look up.

Author: 최진호
Date:   2026-10-21
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The methods: one run, or the propose-probe-compose run with k candidates.
METHOD_SINGLE = "single"
METHOD_PPC    = "ppc"

# The substrates: the default loop of the CLI, or a sub-agent preset.
SUBSTRATE_LOOP     = "loop"
SUBSTRATE_SUBAGENT = "subagent"

# The largest k the P-P-C method may take.
MAX_PPC_K = 3

# The hosts that hold the answers: unreachable while a task is evaluated.
LOOKUP_HOSTS: tuple[str, ...] = ("huggingface.co", "github.com", "raw.githubusercontent.com")

# The package registries: a download from them is not looking up an answer.
REGISTRY_HOSTS: tuple[str, ...] = (
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "registry.yarnpkg.com",
    "crates.io",
    "static.crates.io",
    "proxy.golang.org",
)

# The tags that name the language a task is about; the domain of a task is the
# first of them the task carries.
LANGUAGE_TAGS: frozenset[str] = frozenset({
    "bash", "c", "go", "java", "javascript", "js", "node", "php", "python",
    "ruby", "rust", "shell", "ts", "typescript",
})

# The domain a task without a language of its own belongs to.
DEFAULT_DOMAIN = "default"

# The file in a results directory that holds the mark of the run.
RUNMARK_FILE = "runmark"


class BenchError(ValueError):
    """The benchmark cannot be run the way it was asked for."""


class RunmarkMismatchError(BenchError):
    """The results directory was made with another configuration."""


@dataclass(frozen=True)
class BenchMethod:
    """How the agent answers a task: once, or k times with the best of them kept."""

    name: str = METHOD_SINGLE
    k:    int = 1

    @classmethod
    def parse(cls, text: str) -> BenchMethod:
        """The method *text* names: ``single``, ``ppc`` or ``ppc:k``."""
        head, _, tail = text.strip().lower().partition(":")
        if head not in (METHOD_SINGLE, METHOD_PPC):
            raise BenchError(f"unknown method '{text}'; use single or ppc[:k]")
        k = 1
        if head == METHOD_PPC and tail:
            try:
                k = int(tail)
            except ValueError as exc:
                raise BenchError(f"the method's k must be a number, not '{tail}'") from exc
        if not 1 <= k <= MAX_PPC_K:
            raise BenchError(f"the method's k must be between 1 and {MAX_PPC_K}, not {k}")
        return cls(name=head, k=k)

    @property
    def candidates(self) -> int:
        """How many candidates one attempt produces."""
        return 1 if self.name == METHOD_SINGLE else self.k

    def to_dict(self) -> dict[str, Any]:
        """The method as a plain mapping."""
        return {"name": self.name, "k": self.k}


@dataclass(frozen=True)
class BenchSubstrate:
    """What answers the task: the default loop of the CLI, or a sub-agent preset."""

    name:   str = SUBSTRATE_LOOP
    preset: str = ""

    @classmethod
    def parse(cls, text: str) -> BenchSubstrate:
        """The substrate *text* names: ``loop`` or ``subagent[:preset]``."""
        head, _, tail = text.strip().lower().partition(":")
        if head == SUBSTRATE_LOOP:
            return cls(name=SUBSTRATE_LOOP)
        if head == SUBSTRATE_SUBAGENT:
            return cls(name=SUBSTRATE_SUBAGENT, preset=tail)
        raise BenchError(f"unknown substrate '{text}'; use loop or subagent[:preset]")

    def to_dict(self) -> dict[str, Any]:
        """The substrate as a plain mapping."""
        return {"name": self.name, "preset": self.preset}


@dataclass(frozen=True)
class BenchTask:
    """One task of a benchmark: what to do, and how to tell it was done."""

    id:     str
    prompt: str = ""
    verify: str = ""
    path:   str = ""
    tags:   tuple[str, ...] = ()
    domain: str = ""

    @property
    def stratum(self) -> str:
        """The domain this task belongs to: the one its experience is filed under."""
        return domain_of(self.tags, self.domain)

    def to_dict(self) -> dict[str, Any]:
        """The task as a plain mapping."""
        return {"id": self.id, "tags": list(self.tags), "domain": self.stratum}


@dataclass(frozen=True)
class BenchBenchmark:
    """A set of tasks, and where they came from."""

    name:    str
    tasks:   tuple[BenchTask, ...] = ()
    adapter: str = ""
    source:  str = ""


@dataclass(frozen=True)
class BenchSpec:
    """One cell of the experiment: a method, a substrate and a benchmark."""

    method:    BenchMethod
    substrate: BenchSubstrate
    benchmark: BenchBenchmark

    def to_dict(self) -> dict[str, Any]:
        """The cell as a plain mapping.

        The source of the benchmark is part of it: the same task ids read from
        two different places are two different runs, and the mark says so.
        """
        return {
            "method":    self.method.to_dict(),
            "substrate": self.substrate.to_dict(),
            "benchmark": self.benchmark.name,
            "source":    self.benchmark.source,
            "tasks":     [task.id for task in self.benchmark.tasks],
        }

    def runmark(self, settings: Mapping[str, Any] | None = None) -> str:
        """The hash of this cell and of the settings that decide it.

        Two runs with the same mark measure the same thing; two runs with
        different marks do not, and must not be counted together.
        """
        payload = {"cell": self.to_dict(), "settings": dict(settings or {})}
        blob   = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:16]


def domain_of(tags: Sequence[str], explicit: str = "") -> str:
    """The domain a task belongs to.

    The domain is what the experience of the task would be filed under: the
    language the task is about, or the name the task gives itself.
    """
    if explicit:
        return explicit.strip().lower()
    for tag in tags:
        name = str(tag).strip().lower()
        if name in LANGUAGE_TAGS:
            return name
    return DEFAULT_DOMAIN


# ---------------------------------------------------------------------------
# The benchmarks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchmarkAdapter:
    """How a benchmark's tasks are read."""

    name:   str
    source: str
    format: str = "nerdvana"


# The benchmarks that ship with the repository.
BUILTIN_BENCHMARKS: dict[str, BenchmarkAdapter] = {
    "short-20":          BenchmarkAdapter("short-20", "benchmarks/tasks"),
    "long-8":            BenchmarkAdapter("long-8", "benchmarks/long/tasks"),
    "migration-campaign": BenchmarkAdapter("migration-campaign", "benchmarks/campaigns"),
}

# The benchmarks that live elsewhere, and the format they are read in.
EXTERNAL_BENCHMARKS: dict[str, BenchmarkAdapter] = {
    "workbuddy": BenchmarkAdapter("workbuddy", "", "workbuddy"),
    "jobbench":  BenchmarkAdapter("jobbench",  "", "jobbench"),
}


def adapters() -> dict[str, BenchmarkAdapter]:
    """Every benchmark that can be named, the built-in ones and the external ones."""
    return {**BUILTIN_BENCHMARKS, **EXTERNAL_BENCHMARKS}


def adapter_of(name: str) -> BenchmarkAdapter:
    """The adapter of the benchmark *name*."""
    known = adapters()
    if name not in known:
        raise BenchError(f"unknown benchmark '{name}'; use one of {', '.join(sorted(known))}")
    return known[name]


def read_task_file(file: Path) -> BenchTask:
    """The task one YAML file holds."""
    import yaml  # type: ignore[import-untyped,unused-ignore]

    data = yaml.safe_load(file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise BenchError(f"{file}: a task file must hold a mapping")
    for key in ("id", "prompt"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise BenchError(f"{file}: '{key}' is required")
    return BenchTask(
        id     = str(data["id"]).strip(),
        prompt = str(data["prompt"]),
        verify = str(data.get("verify", "") or ""),
        path   = str(data.get("path", "") or ""),
        tags   = tuple(str(tag) for tag in data.get("tags") or ()),
        domain = str(data.get("domain", "") or ""),
    )


def read_workbuddy(file: Path) -> list[BenchTask]:
    """The tasks of a workbuddy file: a JSON object with a ``tasks`` list."""
    import json as _json

    data = _json.loads(file.read_text(encoding="utf-8"))
    entries = data.get("tasks") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise BenchError(f"{file}: a workbuddy file must hold a list of tasks")
    return [
        BenchTask(
            id     = str(entry.get("id", "") or "").strip(),
            prompt = str(entry.get("instruction", "") or entry.get("prompt", "") or ""),
            verify = str(entry.get("test_command", "") or entry.get("verify", "") or ""),
            path   = str(entry.get("repo", "") or ""),
            tags   = tuple(str(tag) for tag in entry.get("tags") or ()),
            domain = str(entry.get("domain", "") or ""),
        )
        for entry in entries
        if isinstance(entry, dict)
    ]


def read_jobbench(file: Path) -> list[BenchTask]:
    """The tasks of a jobbench file: one JSON object per line."""
    import json as _json

    tasks: list[BenchTask] = []
    for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        entry = _json.loads(line)
        tasks.append(BenchTask(
            id     = str(entry.get("id", "") or f"task-{number}"),
            prompt = str(entry.get("prompt", "") or entry.get("instruction", "") or ""),
            verify = str(entry.get("verify", "") or entry.get("test_command", "") or ""),
            path   = str(entry.get("repo_path", "") or entry.get("repo", "") or ""),
            tags   = tuple(str(tag) for tag in entry.get("tags") or ()),
            domain = str(entry.get("domain", "") or ""),
        ))
    return tasks


def load_benchmark(name: str, root: Path, source: str = "") -> BenchBenchmark:
    """The benchmark *name*, its tasks, and where they came from.

    *source* names the directory the tasks are read from; it is what an
    external benchmark is told, since its tasks do not live in the repository.
    """
    adapter = adapter_of(name)
    if adapter.format == "nerdvana":
        return _load_builtin(adapter, root, source)
    return _load_external(adapter, root, source)


def _load_builtin(adapter: BenchmarkAdapter, root: Path, source: str) -> BenchBenchmark:
    """The tasks of a benchmark that ships with the repository."""
    directory = Path(source) if source else root / adapter.source
    files = sorted(directory.glob("*.yml")) if directory.is_dir() else ([directory] if directory.is_file() else [])
    if not files:
        raise BenchError(f"no task files in {directory}")
    return BenchBenchmark(
        name    = adapter.name,
        tasks   = tuple(read_task_file(file) for file in files),
        adapter = adapter.name,
        source  = str(directory.resolve()),
    )


def _load_external(adapter: BenchmarkAdapter, root: Path, source: str) -> BenchBenchmark:
    """The tasks of a benchmark that lives elsewhere."""
    if not source:
        raise BenchError(f"the {adapter.name} benchmark needs a source: where its tasks are")
    directory = Path(source)
    files = sorted(directory.glob("*.json")) if directory.is_dir() else [directory]
    if not files:
        raise BenchError(f"no task files in {directory}")
    reader = read_workbuddy if adapter.format == "workbuddy" else read_jobbench
    tasks: list[BenchTask] = []
    for file in files:
        tasks.extend(reader(file))
    return BenchBenchmark(
        name    = adapter.name,
        tasks   = tuple(tasks),
        adapter = adapter.name,
        source  = str(directory.resolve()),
    )


# ---------------------------------------------------------------------------
# The mark of a run
# ---------------------------------------------------------------------------


def read_runmark(directory: Path) -> str:
    """The mark of a results directory, empty when it carries none."""
    path = directory / RUNMARK_FILE
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def check_runmark(directory: Path, mark: str) -> None:
    """Refuse to continue a results directory that was made with another configuration.

    A directory without a mark is a new one and is let through; a directory
    with a different mark holds the results of another experiment, and mixing
    the two would measure neither.
    """
    stored = read_runmark(directory)
    if stored and stored != mark:
        raise RunmarkMismatchError(
            f"{directory} was written with the runmark {stored}, not {mark}; "
            "a results directory is not continued with another configuration"
        )


def write_runmark(directory: Path, mark: str) -> None:
    """Write the mark of a results directory."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / RUNMARK_FILE).write_text(mark + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# The network of an evaluation
# ---------------------------------------------------------------------------


def evaluation_network(allow_registries: bool = True) -> dict[str, Any]:
    """The sandbox network of an evaluation: the registries, and nothing else.

    The hosts that hold the answers are not among the allowed domains, so they
    are unreachable while a task runs. A download from a package registry is
    not looking up an answer, so the registries stay reachable.
    """
    allowed = list(REGISTRY_HOSTS) if allow_registries else []
    return {"mode": "require", "network": "allowlist", "allowed_domains": allowed}


# ---------------------------------------------------------------------------
# The split of a benchmark into what was seen and what was not
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskSplit:
    """A benchmark split into the tasks a library may learn from and the rest."""

    train: tuple[BenchTask, ...] = ()
    test:  tuple[BenchTask, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """The split as a plain mapping."""
        return {
            "train": [task.id for task in self.train],
            "test":  [task.id for task in self.test],
        }


def split_by_domain(
    tasks: Sequence[BenchTask],
    test_ratio: float = 0.3,
    seed:      int = 0,
) -> TaskSplit:
    """The tasks split into train and test, stratified by the domain they belong to.

    Every domain keeps its share in both sides, so a domain that was learned
    from is measured on tasks of its own: the library is asked whether what it
    learned carries over, not whether it memorized. A domain with a single
    task goes to the train side, since it cannot be split.
    """
    if not 0.0 < test_ratio < 1.0:
        raise BenchError(f"the test share must be between 0 and 1, not {test_ratio}")
    strata: dict[str, list[BenchTask]] = {}
    for task in tasks:
        strata.setdefault(task.stratum, []).append(task)
    train: list[BenchTask] = []
    test:  list[BenchTask] = []
    for domain in sorted(strata):
        members = sorted(strata[domain], key=lambda task: task.id)
        if len(members) < 2:
            train.extend(members)
            continue
        shuffled = list(members)
        _shuffle(shuffled, seed)
        share = max(1, min(len(members) - 1, round(len(members) * test_ratio)))
        test.extend(shuffled[:share])
        train.extend(shuffled[share:])
    return TaskSplit(
        train = tuple(sorted(train, key=lambda task: task.id)),
        test  = tuple(sorted(test,  key=lambda task: task.id)),
    )


def _shuffle(items: list[BenchTask], seed: int) -> None:
    """Shuffle *items* the same way every time, so a split can be reproduced."""
    import random

    random.Random(seed).shuffle(items)
