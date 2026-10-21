# Agent success-rate benchmark

`scripts/bench_agent.py` measures how often the agent solves fixed repository tasks.
It is a manual tool: it calls the real model API, bills for it, and runs outside
pytest and CI. Only the exit status of each task's own verify command decides pass
or fail; output text is never compared.

## Running it

```bash
# print the tasks and the worst-case spend, run nothing
python scripts/bench_agent.py benchmarks/tasks --attempts 3

# run for real
python scripts/bench_agent.py benchmarks/tasks --attempts 4 --yes \
    --model claude-sonnet-5-5 --out results.jsonl
```

Use 4 attempts per task or more. A single run of a task is one draw: the pass rate of repeated runs of
the same build moves by several points, and pass^k (below) is undefined for one attempt. The default
stays 1 so that a first run is cheap; the dry run recommends 4 and the summary warns when a task has
fewer than 3 attempts. Use a fresh `--out` file for each run: the file is appended to, and
`scripts/bench_compare.py` reads one file as one run.

Run it in a disposable environment such as a container or a throwaway VM. The agent
runs shell commands with `--approval-mode yolo` by default. `--sandbox require` (the
default) limits what they can write to the task's working directory and the temporary
directories on Linux; it does not stop reading, running programs or UDP.

| Option | Meaning |
|-|-|
| `--attempts N` | attempts per task (default 1; 4 or more recommended) |
| `--set SECTION.FIELD=VALUE` | override a setting in every attempt (repeatable); run the same tasks with different values of `session.compact_threshold`, `session.escalation_model` or `model.fallback_models` and compare |
| `--gate` | pass each task's verify command to `nerdvana run --verify`, so the agent is told when it fails and keeps working; compare a gated run with an ungated one to see what the check is worth |
| `--tag TAG` | run only tasks carrying this tag (repeatable); tags in `benchmarks/tasks` include `python`, `node`, `c`, `bugfix`, `feature`, `refactor`, `multi-file`, `tests`, `recovery`, `permission`, `injection`, `preserve`; the tasks in `benchmarks/long` carry `long` |
| `--k K` | k for pass@k and pass^k (default: the number of attempts) |
| `--model`, `--provider` | passed to `nerdvana run` |
| `--approval-mode` | `default`, `auto_edit`, `yolo` (default) or `plan` |
| `--sandbox` | `off`, `auto` or `require` (default); use `off` where Landlock is unavailable |
| `--isolate` | before the agent runs, turn the working directory into a fresh repository with one commit and no upstream history; see "Limiting what the agent can look up" |
| `--no-network` | let the agent's shell commands reach the package registries only -- the hosts that hold the answers (`github.com`, `huggingface.co`, `raw.githubusercontent.com`) are unreachable -- and deny the `WebFetch` and `WebSearch` tools; needs `--sandbox require` and Linux 6.7 or later |
| `--method METHOD` | how the agent answers a task: `single` (one run) or `ppc` / `ppc:K` (the propose-probe-compose run with K candidates; every candidate is run and the best of them is kept) |
| `--substrate SUBSTRATE` | what answers the task: `loop` (the default loop of the CLI) or `subagent:PRESET` (a sub-agent preset, run in this process) |
| `--benchmark NAME` | the benchmark to run: `short-20`, `long-8`, `migration-campaign`, or an external one (`workbuddy`, `jobbench`) with `--source`; the task path is used when it is not given |
| `--results-dir DIR` | the directory the results are kept in; it carries the mark of the run that made it, and a directory made with another configuration is not continued |
| `--out FILE` | JSONL file each attempt is appended to (default `bench-results.jsonl`, or `results.jsonl` in `--results-dir`) |
| `--keep-workdirs` | keep each attempt's working directory |
| `--yes` | actually run the agent and spend money |

## The three axes of a run

A run is one cell of an experiment, and it says so: the method that answers a
task, the substrate that answers it, and the benchmark it is measured on. The
run prints the cell and the mark that belongs to it:

```
method: ppc:2 | substrate: loop | benchmark: short-20 | runmark: e869519fd0936921
```

The mark is a hash of the cell and of the settings that decide the run. A
results directory carries the mark of the run that made it, and one made with
another configuration is refused rather than continued -- two experiments
must never be counted as one. The same guard holds for the tasks themselves:
the ad-hoc task path is part of the mark, so the same options over different
tasks are two different runs.

## Keeping the experiment honest

The hosts that hold the answers are unreachable while a task runs. With
`--no-network` the agent's shell commands reach the package registries through
the egress proxy and nothing else, so `github.com`, `huggingface.co` and
`raw.githubusercontent.com` are refused at the source; a download from a
registry is not looking up an answer. The `WebFetch` and `WebSearch` tools are
denied as well.

## Learning from one part and measuring on another

The domain library is only worth something if what it learned carries over. The
benchmark is therefore split into the tasks a library may learn from and the
tasks it may not, stratified by the domain a task belongs to (the language it
is about), so a domain that was learned from is measured on tasks of its own:

```bash
python scripts/split_tasks.py benchmarks/tasks --out benchmarks/split
python scripts/split_tasks.py benchmarks/tasks --test-ratio 0.4 --seed 7
```

The split is written as `train.json` and `test.json`, and it is reproducible:
the same tasks and the same seed always give the same split. A domain with a
single task goes to the train side, since it cannot be split.

### Carrying what was learned over

The split is only half the experiment: something has to carry the learning from
the train side to the test side, or the test side starts from nothing. That is
the domain library (`nerdvana_cli.core.harness.library`), and the benchmark
reaches it with two options:

| Option | Meaning |
|-|-|
| `--library DIR` | the directory the domain library is kept in; a task of a domain starts from what that domain learned |
| `--learn` | let a task leave behind what it learned, so the next task of its domain starts from it (needs `--library`) |

The library keeps what a task left behind: the skill it wrote and the helper
script it wrote, whole, under the domain the task belongs to. A task that
passed and changed something teaches; a task that failed teaches nothing, and
one that changed nothing has nothing to teach.

What the next task receives arrives in two channels, and both of them are ones
a run reads without asking: the helper scripts, which the agent runs with its
shell, and the local project instructions (`NIRNA.local.md`), which the prompt
always carries. A skill travels as its text — what the next task starts from is
the knowledge, not the mechanism that produced it.

So the experiment is two runs over two halves of the split:

```bash
# the train half: the tasks may learn from, and what they learn is kept
python scripts/bench_agent.py benchmarks/split/train --library lib --learn --yes

# the test half: the tasks may not learn from, and they start from the library
python scripts/bench_agent.py benchmarks/split/test --library lib --yes
```

The test run inherits and does not write, so what it is measured on is what the
train half learned and nothing else. The library is part of the mark of the
run, so the two runs cannot be counted as one.

The lesson is written for the next task and not for this one: the instruction
the run is given says so, and the note the library keeps names the domain and
not the task. A lesson that named one task would not be a lesson the next task
of the domain could use, and the measurement would say nothing.

### What the library learned, and what it was worth

Measured on the 20 short tasks, MiniMax-M3.1-Flash-Preview. The tasks were split by
domain into a train half of 14 and a test half of 6, and each half was run with 4
attempts per task. The train half ran with `--learn` and filled the library; the test
half ran twice, once with nothing and once with `--library`, so the two runs differ
in one thing only: what the test tasks started from.

The train half solved 14 of 14 tasks (mean pass@4 1.00, mean pass^4 0.64, $1.58 for
56 attempts) and left behind 12 skills and 14 helper scripts, kept whole under the
domain each belongs to:

| Domain | Skills | Scripts |
|-|-|-|
| c | `ring-buffer-invariants` | `stress_ring.c` |
| node | `deduping-duplicated-js-functions`, `emitter-listener-management`, `extending-callback-registry-api`, `testing-pure-js-helpers` | |
| python | `extend-stock-ledger`, `fix-ceil-division-boundary`, `fix-first-separator-split`, `fix-inclusive-range-endpoint`, `fix-one-based-table-lookup`, `fix-suffix-overflow-width`, `write-interval-merge-tests` | 13 |

The lessons are about the kind of task and not about the task: `fix-inclusive-range-endpoint`
says how to read the endpoint contract before touching a bound, and names no task.

| Test half | Passed | mean pass^4 | Mean input tokens | Mean cost |
|-|-|-|-|-|
| without the library | 22/24 (92%) | 0.67 | 97,309 | $0.0109 |
| with the library | 17/24 (71%) | 0.33 | 215,509 (+121.5%) | $0.0239 (+119.9%) |

The plan wanted the test half's pass^4 to be 10 points or more higher with the library.
It was 33 points lower, and the difference in pass rate is -20.8 points with a 95
percent bootstrap interval of -0.542 to +0.125: the interval contains zero, so at 6
tasks and 4 attempts the two runs cannot be told apart. Per task, one task gained
(`add-py-slug-max-length`, pass^4 0 to 1), three lost (`fix-js-median`, `multi-js-config-loader`,
`recover-shared-rate`, pass^4 1 to 0) and two were unchanged.

The mechanism is visible in the turns. The library hands over everything its domain
holds, and two things follow. The helper scripts are written for the task that made
them and travel under their own names: the python shelf carries `check_state_leak.py`
and `check_pure_accumulate.py` from a mutable-default task, and a test task about
shared state is handed checks written for a different concern. And the inherited
lessons sit in the always-on context of every request, so the run starts from more
text and reads more: the mean attempt more than doubled its input tokens and its cost.

What this says about the plan: the recursion works -- the model writes real, general,
domain-level lessons on its own, and they are kept and handed over -- but handing a
task the whole shelf of its domain is not the same as handing it the lesson it needs.
The measurement also cannot say much at this size: 6 test tasks means one task is
16.7 points of pass^4, and the interval of a six-task comparison is half the scale it
is meant to detect. A next version would let the library offer its lessons and let
the task choose, and would measure on a test half large enough to have a resolution
finer than the effect being looked for.

## Task files

One YAML file per task in `benchmarks/tasks/`:

```yaml
id: sum-range                  # unique, equal to the file name
path: ../fixtures/sum-range    # a local directory, relative to this file; or use repo + ref
# repo: https://example.com/project.git
# ref: 3f2a9c1
setup: pip install -e .        # optional, runs once in the fresh working directory
prompt: "calc.sum_range(1, 4) should return 10 but it does not. Fix the bug in calc.py."
verify: python check.py        # exit status 0 means solved
max_turns: 15                  # default 30
max_cost_usd: 0.5              # default 1.0; the run stops at this estimated cost
timeout: 600                   # seconds for the agent, default 1200
tags: [python, bugfix]         # used by --tag and for the per-tag summary
```

Every attempt starts from a fresh copy of the repository, so attempts never see each
other's changes and the source repository is never modified.

## Long-horizon tasks

The 20 tasks in `benchmarks/tasks` average about 8 turns, so the context features (observation masking, compaction, server-side
compaction) never trigger in them and their effect cannot be measured: masking fired in none of 80 attempts. `benchmarks/long/`
holds 8 tasks built to need 25 to 60 tool turns and to read enough text that the history grows past 60k tokens. They use the same
layout as the short tasks, one level down:

```
benchmarks/long/tasks/<id>.yml       task files (id, path, prompt, verify, max_turns 80, max_cost_usd 1.0, tags)
benchmarks/long/fixtures/<id>/       the starting repository, with the task's check.py or tests
benchmarks/long/solutions/<id>/      the reference solution, laid over the fixture by tests/scripts/test_bench_fixtures.py
benchmarks/long/generate.py          the generator that writes all of the above from fixed seeds
```

Every task carries the tag `long`. Point the script at the directory (`--tag long` selects them in any directory that holds them):

```bash
python scripts/bench_agent.py benchmarks/long/tasks --tag long --attempts 4 --yes --out long.jsonl
```

The fixtures are generated, not written by hand: `python benchmarks/long/generate.py` rewrites `tasks/`, `fixtures/` and `solutions/`,
and `python benchmarks/long/generate.py --check` reports any committed file that differs from a fresh generation (a test runs it). Each
fixture stays under 300 KB. `python benchmarks/long/generate.py --sizes` prints the figures below. Edit the generator, never the
generated files.

| Task | What the agent does | Files to read or patch | Characters | Tokens (about) |
|-|-|-|-|-|
| `long-rename-snake-case` | rename camelCase functions to snake_case in 25 of 40 modules and fix every import, call and string reference | the 39 files that change (modules and the route table) | 193k | 48k |
| `long-data-consistency-repair` | repair 30 JSON data files against a rule spec: ids, regions, currencies, prices, totals, dates | all 30 data files and the spec | 201k | 50k |
| `long-log-analysis` | answer five questions from ten 1000-line logs in three formats and several time zones, written to `answer.json` | the 10 logs and the format notes | 197k | 50k, more in practice (digits and short lines tokenise densely) |
| `long-move-function` | move three names out of a helper module and update 20 importers that use six import styles, plus the tests | the 25 changed files and the helper module | 156k | 39k |
| `long-docs-summary-table` | read eight project records of about 29k characters each and tabulate owner, budget, go-live, vendor and open risks, where the latest dated sentence wins | the 8 documents | 234k | 58k |
| `long-bughunt-ledger` | find two defects behind failing statement tests, four modules deep, among about 30 look-alike modules | the package (the failing path alone is about 6k characters) | 204k | 51k |
| `long-add-field-six-layers` | add a field to tickets through migration, model, repository, service, API, views and `openapi.json` in an app with six entities | the app files | 161k | 40k |
| `long-config-migration` | convert 15 INI service configs to TOML by a written schema with unit, boolean and default rules | the 15 configs and the schema | 226k | 57k |

Characters count the files an agent has to open if it reads each in full; tokens are characters divided by four. An agent that searches
instead of reading reads less, but each of these tasks needs several passes (find, read, edit, run the check, read the check output), so
the history of a typical attempt exceeds the figure for the first pass. The verify commands are the checks `tests/scripts/test_bench_fixtures.py`
runs without a model (fail on the fixture, pass with the solution overlay), and the same test file checks the contract of the long tasks:
tag `long`, 80 turns, a cost ceiling of 1.0 and a fixture between 100 KB and 300 KB. The expected answers inside the check scripts of
`long-log-analysis`, `long-docs-summary-table`, `long-data-consistency-repair` and `long-config-migration` are digests, so the check
scripts show what is wrong without showing the answer.

## Reading the result

Each task is checked by `tests/scripts/test_bench_fixtures.py` without a model: its verify command must fail on the starting repository and pass once the reference solution in `benchmarks/solutions/<id>/` is laid over it.

For each task: attempts, passes, `pass@1` (share of attempts that passed), `pass@k`
(chance that at least one of k attempts passes, the unbiased estimator
`1 - C(n - c, k) / C(n, k)` for n attempts with c passes), `pass^k` (chance that all k attempts pass,
`C(c, k) / C(n, k)`; it measures reliability where `pass@k` measures reach, and falls fast as k grows
for a task the agent solves only sometimes), total cost and mean time. A k above n is cut to n. The summary also shows how the failed attempts ended and how often each kind of trouble (`cas_rejected`, `new_diagnostics`, `repeat_refused`, `sandbox_denied` and the like, counted by the run itself) occurred in failed against passed attempts, which tells what to fix first. Overall: tasks solved, mean
`pass@1` with a 95% bootstrap interval over tasks, mean `pass@k` and `pass^k`, pass rates by tag, total cost and cost per solved task. Costs are the loop's own estimate from
the usage each request reported and `providers/pricing.yml`; a model without a price
reports 0 and the cost ceiling does not apply to it.

## The environment record

Resources change scores, so every result line and the summary carry what the run used: `cpu_count`,
`memory_total_mb`, `timeout_s`, `max_turns` and `max_cost_usd` (the task's limits), `model` and `provider`
(the ones that finished the attempt, as the run reported them; the requested ones when it reported none),
`sandbox`, `network`, `isolate`, `gate`, `overrides` (the `--set` values), `nerdvana_version`, `git_commit`
and `git_dirty` (the repository the script belongs to). In the summary a fact that varies between attempts
(the limits differ per task) shows the list of its values. Results written before this record existed
carry none.

## Limiting what the agent can look up

A repository cloned from upstream holds the commit that fixed the task. An agent that reads it scores
without solving anything, so a score measured with the history reachable overstates the agent.

`--isolate` removes the repository's `.git`, runs `git init`, and commits the tree as one commit, before
the task's `setup` command runs. The agent then finds no history, tags, branches, remotes or reflog. A task
whose `setup` needs real history (a version derived from tags, for example) cannot use it.

`--no-network` refuses TCP connections from the agent's shell commands to everything but the
package registries (Landlock, which is why the sandbox must be `require` and the kernel 6.7 or later;
the script stops at start-up otherwise) and denies the in-process `WebFetch` and `WebSearch` tools.
`nerdvana run --set` cannot change the `sandbox` or `permissions` sections, so the script writes a copy
of your configuration (the first of `NERDVANA_CONFIG`, the user config file and the legacy one) with
`sandbox.network: allowlist`, the registries in `sandbox.allowed_domains`, `sandbox.mode: require` and
those two tools in `permissions.always_deny`, and passes it with `--config` for every attempt. A
`nerdvana.yml` in a task repository is then not read.

What these two do not cover, honestly:

- UDP is open, and so is DNS. A command can still send data out by UDP; a name lookup works although the
  connection after it is refused.
- Reading is not confined. The agent can read any file the user can, including the source repository
  of a `path` task, `benchmarks/solutions` and other checkouts on the machine. Run the benchmark from a
  directory the solutions are not reachable from, in a disposable container or VM, for a result that
  has to be trusted.
- Only the shell commands are confined. What the application itself starts or runs, such as an MCP
  server, is not.
- Local sockets and programs the agent starts stay available. Isolation does not stop a model that
  has memorised the upstream fix from reproducing it.

## Transcript audit

Each attempt is run with `--output-format stream-json`, which ends with the same result object as the
plain `json` format, and the `tool_start` events in it are scanned for signs of looking an answer up. The
plain result object carries no tool calls (only totals, signals and the receipt), so the audit needs the
events. Findings go into the attempt's `audit` field as `{kind, tool, excerpt}`:

| Kind | Raised by |
|-|-|
| `git_history` | `git log`, `show`, `reflog`, `rev-list`, `cat-file`, `blame`, `fetch`, `pull`, `ls-remote`, `clone`, `show-ref`, `for-each-ref` |
| `git_internals` | a path under `.git/objects`, `packed-refs`, `refs`, `logs`, `ORIG_HEAD` or `FETCH_HEAD` |
| `package_download` | `pip`/`uv pip`/`pipx install` or `download`, `uv add`, `npm`/`pnpm`/`yarn install`, `add`, `pack`, `view`, `info` |
| `network_fetch` | `curl` or `wget` with a URL outside the package registries (pypi, npm, yarn, crates, the Go proxy), or with no URL in sight |
| `solutions_read` | a path under `benchmarks/solutions` or `solutions/<task id>` |
| `web_tool` | a `WebFetch` or `WebSearch` call |

The summary counts the flagged attempts, how many of them passed, and the attempts per kind. A flag is
evidence to read, not a verdict: `pip install` may be a legitimate dependency. The limits: the loop
reports only the first 80 characters of a call's arguments, so a long command can hide a lookup, and a
program the agent writes and runs (a Python script that fetches a URL) is invisible to it. A clean audit
shows the absence of the obvious lookups, not of all of them.

## Comparing two runs

```bash
python scripts/bench_compare.py before.jsonl after.jsonl
```

For each task and overall it prints each run's pass rate, `pass^k` (k is the fewest attempts any task has
unless `--k` is given), mean input tokens and mean cost per attempt. The difference in pass rate (second
minus first, averaged over the tasks both runs contain) comes with a 95% bootstrap interval. Each round
draws the tasks with replacement and then each drawn task's attempts with replacement, separately for the
two runs, with a fixed seed, so the same files always give the same interval. An interval that contains 0
means the runs cannot be told apart at that many tasks and attempts. Tasks present in one run only are
listed and left out of the difference. Runs whose recorded environments differ are flagged with the
differing facts: a score gap between a 4 CPU and an 8 CPU run, or between two models, is not a gap
between two builds. The commit and version of the two runs will differ when the runs are of two builds;
that is the point of the comparison.

## Prompt overhead A/B

Measured on the shipped tasks before and after the prompt overhead change: 20 tasks, 4 attempts each
(80 attempts per arm), MiniMax-M3.

| Arm | Passed | Input tokens | Cost |
|-|-|-|-|
| before | 76/80 | 4,233,080 | $1.3228 |
| after | 77/80 | 1,591,295 | $0.5337 |

The pass difference is not significant (chi-square p = 1). Input tokens fell by 62.4 percent and cost by
59.7 percent. This is a single-model measurement with 80 attempts per arm: it shows that the change did
not cost passes at this size, and it cannot rule out a drop of a few points; another model, or a harder
task set, may behave differently.

## Choosing a model per kind of task

Run the tasks once per model with `--model` and `--out`, then let `scripts/bench_recommend.py` compare the runs
by tag:

```bash
python scripts/bench_recommend.py benchmarks/tasks \
    claude-haiku-4-5-20251001=haiku.jsonl claude-sonnet-5-5=sonnet.jsonl
```

For each tag it prints each run's pass rate with a Wilson interval, its cost per attempt and the runs that
no other run beats on both counts, then names the cheapest run whose pass rate cannot be told apart from
the best one and prints an `agents.categories` block to paste into the configuration. With fewer than
8 attempts per run on a tag it says "not enough data" instead of guessing (`--min-attempts` changes the
limit). Nothing is written for you.

A handful of tasks says little. Use enough tasks and attempts that a change of a few
points is larger than the spread between repeated runs of the same build, and compare
builds only on the same tasks, model and attempt count.

## Observation masking, measured

`session.observation_masking` clears old read-type tool output in batches (see `docs/configuration.md`). It was measured twice with MiniMax-M3, which caches 95 percent of its input, so cached input is already cheap.

| Task set | Attempts per arm | Pass off / on | Mean input tokens | Mean cost | Masking fired |
|-|-|-|-|-|-|
| 20 short tasks (about 8 turns) | 80 | 76 of 80 / 76 of 80 | +0.3 percent | +2.6 percent | 0 of 80 |
| 8 long tasks (`benchmarks/long`, about 32 turns) | 24 | 23 of 24 / 22 of 24 | -5.1 percent | +13.4 percent | 16 of 24 |

On the long tasks masking cleared 712 results in total and the runs took 30 percent more turns (41.5 against 31.9 on average): the model read the cleared files again. The cache share of input fell from 95.1 to 88.9 percent, because a cleared message changes the start of the request. The pass rates cannot be told apart (95 percent interval of the difference -0.21 to +0.04). On this model and these tasks masking saves nothing, so it stays off by default. A model without cheap cached input, or a task set whose reads are never repeated, may behave differently and has not been measured.

## The token cost of the harness work, measured

The harness work since 1.9.1 was measured on the eight long tasks, 4 attempts each (32 attempts per arm), MiniMax-M3.1-Flash-Preview. Each arm is a build of this repository, run from a worktree of its own commit; the tasks and the fixtures are byte-identical in all of them.

| Build | Passed | Mean input tokens | Mean cost | Mean turns | Input per turn |
|-|-|-|-|-|-|
| 1.9.1 (`ccfa23e`) | 18/32 | 372,921 | $0.0359 | 13.1 | 21,392 |
| + SpillPolicy (`a6f9dc2`) | 19/32 | 339,711 (-8.9%) | $0.0348 | 14.8 | 18,620 (-13.0%) |
| + SpillPolicy + PTC (`6607ab0`) | 18/32 | 577,291 (+54.8%) | $0.0525 | 22.6 | 21,139 |
| HEAD (`57727d6`) | 19/32 | 522,464 (+40.1%) | $0.0475 | 20.3 | 21,134 |

The plan wanted the mean input tokens to fall by 30 percent or more with the SpillPolicy and the PTC runtime. They did not: they rose by 54.8 percent. The two features are worth separating, because they do different things.

The SpillPolicy does what it was built for: a tool result that would have been inlined whole is previewed and spilled, and the request that carries it is 13 percent smaller. That saving does not reach the attempt, because the agent took more turns (14.8 against 13.1), and every turn sends the whole conversation again. The two effects nearly cancel: 8.9 percent less per attempt, against 13.0 percent less per request.

The PTC runtime is what turns the number around. It gives the agent a way to run a program with the read-only tools in it, and on these tasks the agent uses it: 22.6 turns against 13.1, 73 percent more requests, each carrying a conversation that is no longer smaller per request than it was in 1.9.1 (21,139 against 21,392, the saving of the SpillPolicy gone). The cost per attempt follows: +54.8 percent against the base, +46.2 percent in dollars.

No pair of the four builds can be told apart on the pass rate. Every difference has a 95 percent bootstrap interval that contains zero, and the largest is the SpillPolicy against 1.9.1 at +3.1 points (-0.250 to +0.312). A single model on 32 attempts over 8 tasks is a weak instrument: it shows that the pass rate did not fall, and it cannot rule out a fall of a few points.

What this says about the plan: the SpillPolicy's claim is about a request, and it holds. The claim of a 30 percent cut to an attempt was a claim about the whole run, and it needs the agent to take fewer turns, not the same turns with a smaller body. The PTC runtime makes the agent take more turns, and its value has to be paid for somewhere else.

Each arm is a build of this repository run from a worktree of its own commit, so the four numbers come from four trees:

```bash
for build in ccfa23e a6f9dc2 6607ab0 57727d6; do
    git worktree add "/tmp/arm-$build" "$build"
    (cd "/tmp/arm-$build" && uv sync --extra all && \
     uv run python scripts/bench_agent.py benchmarks/long/tasks --attempts 4 \
        --model MiniMax-M3.1-Flash-Preview --provider minimax --out "/tmp/gate4/long-$build.jsonl" --yes)
done
uv run python scripts/bench_compare.py /tmp/gate4/long-ccfa23e.jsonl /tmp/gate4/long-57727d6.jsonl
```
