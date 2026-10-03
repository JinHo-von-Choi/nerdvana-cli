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
| `--tag TAG` | run only tasks carrying this tag (repeatable); tags in `benchmarks/tasks` include `python`, `node`, `c`, `bugfix`, `feature`, `refactor`, `multi-file`, `tests`, `recovery`, `permission`, `injection`, `preserve` |
| `--k K` | k for pass@k and pass^k (default: the number of attempts) |
| `--model`, `--provider` | passed to `nerdvana run` |
| `--approval-mode` | `default`, `auto_edit`, `yolo` (default) or `plan` |
| `--sandbox` | `off`, `auto` or `require` (default); use `off` where Landlock is unavailable |
| `--isolate` | before the agent runs, turn the working directory into a fresh repository with one commit and no upstream history; see "Limiting what the agent can look up" |
| `--no-network` | refuse TCP connections from the agent's shell commands and deny the `WebFetch` and `WebSearch` tools; needs `--sandbox require` and Linux 6.7 or later |
| `--out FILE` | JSONL file each attempt is appended to (default `bench-results.jsonl`) |
| `--keep-workdirs` | keep each attempt's working directory |
| `--yes` | actually run the agent and spend money |

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

## Reading the result

Each task is checked by `tests/test_bench_fixtures.py` without a model: its verify command must fail on the starting repository and pass once the reference solution in `benchmarks/solutions/<id>/` is laid over it.

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

`--no-network` refuses TCP connections from the agent's shell commands (Landlock, which is why the sandbox
must be `require` and the kernel 6.7 or later; the script stops at start-up otherwise) and denies the
in-process `WebFetch` and `WebSearch` tools. `nerdvana run --set` cannot change the `sandbox` or
`permissions` sections, so the script writes a copy of your configuration (the first of `NERDVANA_CONFIG`,
the user config file and the legacy one) with `sandbox.network: false`, `sandbox.mode: require` and those
two tools in `permissions.always_deny`, and passes it with `--config` for every attempt. A `nerdvana.yml` in
a task repository is then not read.

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
