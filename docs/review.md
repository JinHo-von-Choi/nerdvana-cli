# `nerdvana review`

A review of a diff alone cannot tell whether a changed function still fits the code that calls it.
`nerdvana review` starts the read-only `code-reviewer` agent from the changed code and the places that use it.

```bash
nerdvana review --base main                    # the working tree against main
nerdvana review --base HEAD~3 --path src/      # limit to a path
nerdvana review --base main --output-format json --fail-on high
nerdvana review --base main --context-only     # print what the reviewer is given; no model is called
```

## What the reviewer is given

1. The diff (three lines of context), cut at 60,000 characters with a note.
2. For each Python function or class that contains a changed line, its name and range and the lines in
   other places that mention it, found by whole-word text search (`git grep`). Callers in the code are listed
   before tests, at most 12 per symbol. Some of those lines are other things with the same name and dynamic uses
   are missing; the reviewer is told so. Names shorter than three characters are not searched.

Files in other languages are reviewed from the diff alone.

The reviewer can read files and search with its read-only tools and ends with a JSON object of findings
(`file`, `line`, `severity` high, medium or low, `problem`, `evidence`).

## Output and exit codes

`text` prints the findings, most severe first; `json` prints `{"type": "review", "base", "findings", "failed"}`.
Exit code 0 means the review ran, 1 means a finding at or above `--fail-on` (`low`, `medium`, `high`; default
`never`), 2 means it could not run (not a repository, unknown base, no API key, bad option). A tree with no changes
against the base exits 0 without calling a model.

## Model

The model is the configured one, or the one `agents.categories` maps to the category `review`, or `--model`.
A cheaper model that is good at reading code is a good fit; see `scripts/bench_recommend.py`.
