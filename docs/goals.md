# Goals: finished means the command passes

A model that stops is not a task that is done. A goal names the command that decides: when the
model says it is finished, the command runs. If it exits with status 0 the run ends. If not, the
end of its output goes back to the model, which keeps working.

## Setting a goal

In the terminal interface:

```
/goal make the parser accept quoted keys --verify "pytest -q tests/test_parser.py"
/goal                    show the goal and how many attempts are used
/goal pause              keep the goal but stop enforcing it
/goal resume             enforce it again, with a fresh count of attempts
/goal clear              drop it
```

From a script or CI:

```bash
nerdvana run "make the parser accept quoted keys" --verify "pytest -q tests/test_parser.py" \
    --verify-attempts 4 --max-cost-usd 2 --output-format json
```

The result of a run with a goal has a `verification` object (`command`, `status`, `attempts`,
`last_exit`). Passing ends the run with `success`. When the attempts run out the run ends with
`error_goal_unmet` and exit code 3.

## Scope

`--scope PATH` (repeatable; `/goal ... --scope tests`, `nerdvana run --verify ... --scope tests`) says what the work is
about. An edit outside those paths by `FileEdit`, `FileWrite` or the symbol edit tools asks the user first, in the
terminal interface through the confirmation window. With nobody to ask (a run without a terminal) it is refused.
The scope does not stop the agent from leaving it, only makes a person decide, and each such edit is counted as
`out_of_goal_scope` in the run's signals. It applies while the goal is active.

## What the check does

- It runs in the project directory, through the shell, under the same `sandbox` policy as the `Bash`
  tool (see [sandbox.md](sandbox.md)). With `sandbox.mode: require` on a system that cannot confine
  commands, nothing runs and the attempt counts as failed.
- It has a time limit, `goal.verify_timeout` (300 seconds). When it runs out, the command and every
  process it started are killed.
- Only the end of a failing output is shown to the model (`goal.output_tail_chars`, 4000), where
  failures usually explain themselves.
- After a failed check the model is told not to change the command or weaken the checks it runs.
  Nothing technical stops it from editing a test, so look at what changed when a goal is met.
- The todo reminders come first: the check runs once the model ends a turn with no open todo items,
  or when the reminders have stopped making progress.
- The goal is saved with the session, so a resumed session or a restarted process continues with it.

## Checking without a goal

`goal.auto_verify: true` gives a run that has no goal the same gate with a command nobody had to write. When the
model says it is finished and the edit tools changed files during the run, the project's test command runs first:

- `pytest -q` when there is a `pyproject.toml`, a `pytest.ini` or Python tests under `tests/`
- `npm test` when `package.json` has a `test` script (npm's placeholder script does not count)
- `cargo test` when there is a `Cargo.toml`
- `go test ./...` when there is a `go.mod`

The first that applies wins, in that order, and only when its program is on the `PATH`. When nothing is detected
there is no check. It runs through the same machinery as a goal: project directory, `sandbox` policy, `goal.verify_timeout`,
a failing output's end is shown to the model, and each failure is counted as `verify_failed`. A run that changed
nothing is not checked, and a passed check is not repeated until more files change. The check is not saved as the
goal of the session, and an explicit goal, even a paused one, replaces it. Sub-agents are not held to it.

`goal.max_attempts` bounds it too: a check that keeps failing ends the run the way an unmet goal does
(`error_goal_unmet`, exit code 3). `verify_failed` is also what starts the model escalation
(`session.escalation_model`, `session.escalation_signals`), so a model that cannot get the tests to pass can
hand over to the stronger one before the attempts run out.

## Runs that go nowhere

Two patterns are noticed, whatever the goal setting: `session.no_progress_failed_edits` edits in a row to one file that
all failed (3), and `session.no_progress_read_turns` turns in a row that only read or searched, with no edit and no
command run (12). A short note is added to the last tool result of that turn, once per stall; a stall ends as soon as
an edit is applied (first pattern) or a turn does anything but read (second). Each note is counted as `no_progress`
in the run's signals. `0` turns a check off. `no_progress` is not among the default `session.escalation_signals`;
list it there to let it start an escalation. The identical-call warning (third repeat) and refusal (fifth) are separate
and unchanged.

## Limits

`goal.max_attempts` (5) bounds the number of failed checks. The turn limit (`session.max_turns`), the
cost limit (`session.max_cost_usd`) and the token limit (`session.max_total_tokens`) still apply and
end the run first when they are reached.
