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

## Limits

`goal.max_attempts` (5) bounds the number of failed checks. The turn limit (`session.max_turns`), the
cost limit (`session.max_cost_usd`) and the token limit (`session.max_total_tokens`) still apply and
end the run first when they are reached.
