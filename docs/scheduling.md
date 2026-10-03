# Scheduled runs

`nerdvana schedule` runs a prompt on a cron schedule or at an interval. A local daemon
starts each job when it is due by running `nerdvana run` in a separate process with the
limits of the job, and saves what the run reported.

```
nerdvana schedule add "0 3 * * *" --prompt "Run the tests and summarize failures" --name nightly
nerdvana schedule list
nerdvana schedule run nightly
nerdvana schedule daemon
nerdvana schedule remove nightly
nerdvana schedule install-systemd
```

## Adding a job

```
nerdvana schedule add "<cron or interval>" --prompt "..." [--cwd DIR] [--max-cost-usd X] [--approval-mode plan|default] [--name N]
```

| Option | Meaning |
|-|-|
| `--prompt` | The prompt every run gets. Required. |
| `--cwd` | Directory the run works in; default is the current directory. |
| `--max-cost-usd` | Cost ceiling of one run in USD, default 1. `0` means no ceiling. A ceiling also makes the run require a known price for the model, so it cannot silently run unbounded. |
| `--approval-mode` | `plan` (default) is read-only; `default` is the normal approval mode. |
| `--name` | Job name of letters, digits, `.`, `_`, `-` (up to 64, starting with a letter or digit). Default is `job-1`, `job-2`, and so on. |

## Schedule syntax

Five cron fields, `minute hour day-of-month month day-of-week`, or an interval.

Each cron field takes `*`, a number, a range `a-b`, a step `*/n`, `a-b/n` or `a/n` (from a
to the field maximum), and comma separated lists of these. Day of week is 0 to 7 with both
0 and 7 meaning Sunday. When day-of-month and day-of-week are both restricted (neither
starts with `*`) a day matches if either does, as in classic cron; otherwise both must
match. Names such as `mon` and shortcuts such as `@daily` are not accepted, and an
expression that can never fire, such as `0 0 30 2 *`, is refused.

An interval is `every 15m`, `every 2h` or `every 1d`, at least one minute. It counts from
the daemon start and then from the start of each run.

Times are the machine's local time at minute resolution.

## Where things are stored

Everything is under `schedule/` in the data root (`~/.nerdvana`, or `NERDVANA_DATA_HOME`):

| Path | Content |
|-|-|
| `schedule/jobs.yml` | The job definitions (a `jobs:` list; `nerdvana schedule add` writes it, and it may be edited by hand). The daemon rereads it on every check. |
| `schedule/runs/<job>/<stamp>.json` | One record per run: status, exit code, start and end time, cost, the command, and `result`, the JSON object `nerdvana run --output-format json` printed. |
| `schedule/runs/<job>/<stamp>.log` | What the run process wrote to standard error. |
| `schedule/locks/<job>.lock` | The lock that keeps two runs of one job apart. |

Statuses are `success`, `failed`, `config_error` (exit code 2), `limit_reached` (a turn,
cost or token limit, exit code 3), `timeout`, `interrupted`, `error` (the process could not
start), `skipped_overlap` and `skipped_budget`. Skipped runs get a record too, so a job that
never runs says why.

## The daemon

`nerdvana schedule daemon` runs in the foreground, checks every 15 seconds (`--tick-seconds`)
and logs one line per start, finish and skip.

- Runs missed while the daemon was down are not replayed. Every job starts counting when the
  daemon starts, and a gap longer than two minutes between checks (a suspended machine)
  is cut to the last two minutes, so at most the firing just behind it is run.
- A job never overlaps itself. The daemon and `nerdvana schedule run` both take the job's
  lock file first; the lock names the process of the run, so a daemon that was killed does
  not leave a running job unprotected, and a lock left by a dead process is taken over.
- Cost ceilings: each run gets its job's `--max-cost-usd`. The daemon also keeps a daily
  ceiling, `--max-daily-cost-usd` (default 10, `0` for none). It starts a job only while the
  cost recorded for runs that started that day, plus the ceilings of the runs still going,
  is below it; otherwise the run is recorded as `skipped_budget`. A job can therefore
  overshoot the daily ceiling by at most its own ceiling.
- A run is stopped after one hour and recorded as `timeout`.
- On SIGTERM or Ctrl+C the runs still going are stopped and recorded as `interrupted`.

`nerdvana schedule run <name>` runs one job now and waits. Its exit code is the run's: 0 on
success, 1 for a failed run or one already going, 2 for invalid input or configuration, 3 for
a reached limit.

## Running it under systemd

`nerdvana schedule install-systemd` prints a user unit and installs nothing. Save it as
`~/.config/systemd/user/nerdvana-schedule.service` and enable it as the comments at the top
of the output say. A systemd user service does not inherit the shell environment, so the
provider credentials must be in the configuration file (`~/.nerdvana/config.yml`) or in an
`EnvironmentFile=` line you add to the unit.

`NERDVANA_SCHEDULE_COMMAND` replaces the command that starts nerdvana (default: this
interpreter running `nerdvana_cli.main`); the unit and the runs use it.

## Managed policy

Each run is a normal `nerdvana run`, so a [managed policy](managed-policy.md) applies to it:
its model, MCP, hook, deny-rule, cost and sandbox limits hold for scheduled runs too.
