# Running nerdvana from GitHub comments

`docs/examples/nerdvana-comment.yml` is a workflow that runs `nerdvana run` when a collaborator comments
`/nerdvana <task>` on an issue or pull request, and replies with the result, the turns, the cost and the files changed.
Copy it to `.github/workflows/` to use it. It is something you start by commenting; nothing runs on its own.

## What the example already limits

| Risk | Limit in the example |
|-|-|
| Anyone can comment | only `OWNER`, `MEMBER` and `COLLABORATOR`, and only comments that start with `/nerdvana ` |
| The job pushes or edits settings | `permissions` are `contents: read`; the checkout keeps no credentials; the job replies by comment only |
| An unbounded bill | `--max-cost-usd 3`, `--max-turns 40` and a 20 minute job timeout |
| The agent changes things outside the checkout | `--sandbox require`: commands can write only in the checkout and the temporary directories (Linux runners), and the run is refused if that cannot be enforced |
| Edits without review | `--approval-mode auto_edit`; the changes stay in the runner's checkout, so nothing reaches the repository unless you add a step that commits them |
| Two runs at once on one thread | a `concurrency` group per issue |

## What you must decide

- The provider key is the repository secret `NERDVANA_API_KEY`. Use a key of an account you accept being charged on, with its own spending limit at the provider. The key is read as an environment variable of the job, so a command the agent runs could print it; secret-looking values in command output are masked before the model sees them (see [secret-masking.md](secret-masking.md)), and GitHub masks registered secrets in logs. Do not give the job more than that one key.
- A comment is an instruction to an agent that can run commands in the checkout. Keep the trigger restricted to people you would give write access to the repository.
- Do not change the trigger to `pull_request_target` or to workflows that check out a fork's code with a token that can write: the agent would then run attacker-controlled instructions with your credentials.
- The result is a comment. To turn the agent's edits into a pull request, add a step that you control (for example `peter-evans/create-pull-request`) and review that pull request like any other.
