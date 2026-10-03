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
python scripts/bench_agent.py benchmarks/tasks --attempts 3 --yes \
    --model claude-sonnet-5-5 --out results.jsonl
```

Run it in a disposable environment such as a container or a throwaway VM. The agent
runs shell commands with `--approval-mode yolo` by default. `--sandbox require` (the
default) limits what they can write to the task's working directory and the temporary
directories on Linux; it does not stop reading, running programs or UDP.

| Option | Meaning |
|-|-|
| `--attempts N` | attempts per task (default 1) |
| `--tag TAG` | run only tasks carrying this tag (repeatable); tags in `benchmarks/tasks` include `python`, `node`, `c`, `bugfix`, `feature`, `refactor`, `multi-file`, `tests`, `recovery`, `permission`, `injection`, `preserve` |
| `--k K` | k for pass@k (default: the number of attempts) |
| `--model`, `--provider` | passed to `nerdvana run` |
| `--approval-mode` | `default`, `auto_edit`, `yolo` (default) or `plan` |
| `--sandbox` | `off`, `auto` or `require` (default); use `off` where Landlock is unavailable |
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
`1 - C(n - c, k) / C(n, k)`), total cost and mean time. The summary also shows how the failed attempts ended and how often each kind of trouble (`cas_rejected`, `new_diagnostics`, `repeat_refused`, `sandbox_denied` and the like, counted by the run itself) occurred in failed against passed attempts, which tells what to fix first. Overall: tasks solved, mean
`pass@1` with a 95% bootstrap interval over tasks, pass rates by tag, total cost and cost per solved task. Costs are the loop's own estimate from
the usage each request reported and `providers/pricing.yml`; a model without a price
reports 0 and the cost ceiling does not apply to it.

A handful of tasks says little. Use enough tasks and attempts that a change of a few
points is larger than the spread between repeated runs of the same build, and compare
builds only on the same tasks, model and attempt count.
