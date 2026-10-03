# Background runs, cancellation and steering

Three things keep a long run under control: it can be started in the background and found again
after a restart, aborting it stops what it is doing right now, and text typed while it works can
redirect it.

## Durable runs

Two kinds of run are recorded under `<data root>/runs/<id>/` (`~/.nerdvana/runs/` by default):

| Kind | Started by | Owner process |
|-|-|-|
| `session` | `nerdvana agents start` | A monitor process that runs `nerdvana run` for it |
| `task` | The `Agent` tool with `run_in_background: true` | The session that started it |

A run directory holds `record.json` and, for a supervised run, `run.log` (the stream-json output of
`nerdvana run`), `stderr.log`, `monitor.log` and `result.json`. A background task writes its output
to `result.txt`. The record has the id, the prompt, the working directory, the worktree path and
branch, the status, the start and end times, the heartbeat time, the result path, the owner process
id, the session id, the idempotency key, the cost so far and the last signal.

Records are replaced atomically, and only the owner writes a record while its run goes on.

### Statuses and the lease

The owner renews a lease by writing `heartbeat_at` every five seconds. A record that says `running`,
whose heartbeat is older than 20 seconds and whose owner process no longer exists, is shown as
`orphaned`: the process died without finishing the run (a restart, a crash, a kill). The status
`running` with a live owner stays `running` however old the heartbeat is. A process that has ended
but not yet been collected by its parent counts as gone. A process id can be reused by a later
process; the stale heartbeat bounds how long that can hide an abandoned run, but it cannot be ruled
out.

The other statuses are `succeeded`, `failed` and `stopped`.

### Idempotency

`--key K` claims the key atomically. Starting again with the same key, from the same or another
terminal, returns the existing run and starts nothing. `nerdvana agents clean` frees the key with
the record.

## `nerdvana agents`

```
nerdvana agents start "<prompt>" [--worktree] [--max-cost-usd X] [--key K] [--cwd DIR] [--approval-mode M]
nerdvana agents list
nerdvana agents show ID [--lines N]
nerdvana agents attach ID
nerdvana agents stop ID
nerdvana agents resume ID
nerdvana agents clean [--days N]
```

An id can be shortened to any unique prefix.

`start` writes the record, makes a git worktree when `--worktree` is given (core/worktree.py: a new
branch from `HEAD` in a temporary directory), and starts a monitor in a session of its own, so the
run goes on when the terminal closes. The monitor runs `nerdvana run --output-format stream-json`
in its own process group, with the log as its standard output. `--max-cost-usd` becomes the run's
own `--max-cost-usd` together with `--require-price`, so a model without a known price is refused
instead of running unbounded. `--approval-mode` is passed through; without it the run follows your
configuration. A run has no terminal to ask on, so a call that needs approval is refused: allow
what the task needs with `permissions.always_allow` or an approval mode.

A run in a worktree works there, but `sandbox.mode: auto` or `require` confines its commands to
the worktree and the temporary directories only, so `git commit` inside it is refused: the git
directories of the worktree are not added to the write scope the way they are for an `Agent` task
with `isolation: worktree`.

While the run goes on the monitor renews the lease and reads the log: the session id (needed to
resume), the cost so far (the sum of the cost of each reported request) and the last event.

`list` prints id, kind, status, age, cost so far and the last signal, the last event the run
reported with how long ago (`tool Bash (4s ago)`, `text`, `request`, or the result of a finished
run). When a run is orphaned it says how to resume it.

`show` prints the record, the tail of the log and the result JSON. `attach` follows the log until
the run ends; Ctrl+C detaches and leaves the run going.

`stop` sends SIGTERM to the monitor, which ends the run's process group with SIGTERM and then
SIGKILL after the grace period (five seconds), and closes the record as `stopped`. When the monitor
does not answer, it is killed together with the run. An orphaned run is marked `stopped` and a
process it left behind is ended. A background task of a session can only be stopped inside that
session (`TaskStop`), unless it is orphaned.

`resume` restarts an `orphaned`, `stopped` or `failed` run with `nerdvana run --resume <session id>`:
the conversation recorded in the session transcript is restored and the run is told to continue
from where it stopped. It needs the transcript (`~/.nerdvana/sessions/<session id>.jsonl`); a run
without one cannot be resumed and the command says so. The restarted run keeps its id and counts an
attempt. A background task resumed this way runs as an ordinary session, not with its agent type's
role and tool limits, because only the conversation is recorded. A tool call that was running when
the process died is not repeated: the history keeps only complete call and result pairs.

`clean` removes the records, logs and key claims of `succeeded`, `failed` and `stopped` runs that
ended at least `--days` days ago (7 by default; `0` removes all). A worktree that has changes, or
that cannot be checked, is kept and its path is printed; an unchanged one is removed with its
branch. Orphaned runs are never cleaned: resume or stop them first.

`NERDVANA_AGENTS_COMMAND` replaces the command that stands for `nerdvana` (split like a shell
line); the tests set it to a fake.

### Background tasks of a session

An `Agent` task started with `run_in_background` gets a `task` record when it starts, its lease is
renewed while it runs, and the record is closed with `succeeded`, `failed` or `stopped`, the output
as the result and the cost. The sub-agent's conversation is saved under the task id, which is also
the record's session id. If the session's process dies, the record shows `orphaned` in
`nerdvana agents list` and can be resumed. A store that cannot be written never stops the task.

## Cancellation

Aborting a run stops what it is doing now, not at the next chunk of output. The run is one task;
cancelling it reaches every place it waits:

| Waiting in | What happens |
|-|-|
| A provider stream | The stream task is cancelled and the HTTP response closed |
| A `Bash` command | The command's process group gets SIGTERM, then SIGKILL after the grace period (5 seconds), so `sleep 60 &` inside a command is ended too |
| An MCP call, a web request | The request is cancelled at its next await |
| A sub-agent | The run is cancelled, and with it everything above |

`core/cancellation.py` holds the helpers. Aborting a sub-agent (the abort event of
`run_subagent`, `TaskStop`) cancels its run; cancelling the main run (the TUI, ACP) does the same.
A command that exceeds its `timeout` is ended the same way. Work that does not stop within the grace
period plus two seconds is left cancelled and a warning is logged.

## Steering

Text typed while the agent works is held by the input queue. `session.steer_mode` decides what
happens next:

| Mode | Behavior |
|-|-|
| `queue` (default) | The text reaches the model at the start of the next step, after any tool results already in the history. |
| `interrupt` | The step in progress is stopped and the next step starts with the text. |

Interrupting a model response cancels the stream; what it had written is dropped from the history
(the screen keeps it). Interrupting tools gives a tool that is already running one second to finish
by itself, so a quick tool delivers its real result; after that it is cancelled as described above
and its call is answered with an error result saying that it was interrupted, which keeps every
call paired with a result. The typed text follows as the next user message.

`/steer <text>` and Ctrl+T (which sends what is typed in the input line) interrupt whatever
`steer_mode` says; when the agent is idle they are an ordinary prompt. `nerdvana run` and ACP never
type into a running agent, so they are unaffected.
