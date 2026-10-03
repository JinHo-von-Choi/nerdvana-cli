# Symbol tool success rate and latency

Author: 최진호
Script: `scripts/bench_symbol_tools.py`

The symbol tools (`find_symbol`, `symbol_overview`, `find_referencing_symbols`, `replace_symbol_body`) sit on a
language server. This benchmark measures how often their answers are right and how long they take, before
anything is added on top of them (a repository map, a search index). It calls no model: the questions and the
expected answers come from the source files themselves (`ast`), the answers come from the real tools and the
real language server.

```bash
uv run python scripts/bench_symbol_tools.py --repeat 3 --out symbol-tools.json
```

Needs `pyright-langserver` on PATH (`pip install pyright` provides it). Without it the report says `skipped`
and the exit status is 2; the exit status is 1 when a case failed and 0 when every case passed. A unit test
covers the report logic without a server (`tests/test_bench_symbol_tools.py`).

## Cases

The cases are discovered from six seed files of the package (`--seed` replaces them), so they follow the code.

| Tool | Question | Passes when |
|-|-|-|
| `find_symbol` | up to `--per-file` functions, classes and methods per seed file by name path, plus one substring query per file | the answer has the name path, the file and the line (a decorated symbol starts at its first decorator, as the server reports it) |
| `symbol_overview` | the symbols of each seed file | every top-level function and class of the file is listed; the score is the share listed |
| `find_referencing_symbols` | top-level symbols of each seed file that other files use | the references reach every file that uses the identifier according to `ast` (a name, an attribute or an import); the score is the share of those files reached |
| `replace_symbol_body` | `--edits` functions of a temporary copy of the package: preview, then apply, a body with one comment line added | the edited file has the same syntax tree as the original and holds the comment once |

For `replace_symbol_body` the report also notes whether the text is exactly the original plus that one line. That is
not a failure condition: the tool replaces up to the next line at the symbol's indentation, so the blank lines
after the symbol go with it unless the body brings them along.

Read-only cases run `--repeat` times against the repository, edit cases once against the copy, each with its own
language server process. Latency is wall-clock time of the whole case (one tool call, two for an edit), and the
first calls of a server include its start up and indexing.

## Report

JSON with `summary.overall` and `summary.by_tool` (`runs`, `passed`, `success_rate`, `mean_score` where a case has
one, `mean_ms`, `p50_ms`, `p95_ms`, `max_ms`, `notes`), `failures` (the failed runs with the reason) and `runs` (every
run).

## Result of 2026-10-03

Repository at the commit of the benchmark change, pyright 1.1.414 started through the `pyright` pip wrapper,
Python 3.13, `--repeat 3`.

| Tool | Runs | Passed | Success rate | Mean score | p50 ms | p95 ms |
|-|-|-|-|-|-|-|
| `find_symbol` | 72 | 72 | 100% | | 3.3 | 15.8 |
| `symbol_overview` | 18 | 18 | 100% | 1.00 | 3.0 | 3.2 |
| `find_referencing_symbols` | 33 | 0 | 0% | 0.00 | 9.0 | 11.6 |
| `replace_symbol_body` | 4 | 4 | 100% | | 42.2 | 2521 |

What the numbers say:

- Finding a symbol and listing a file's symbols work, and are fast once the server is up.
- `find_referencing_symbols` returned the definition itself and nothing from any other file in all 33 runs. A
  probe confirmed why: the server answers only for files it has open, plus what those import. After two other
  files were opened by diagnostics calls the same query returned seven locations in four files. The client
  (`core/lsp_client.py`) opens a file only when a tool is asked about it, so a reference search across the
  repository sees almost nothing. Anything built on cross-file references (rename, safe delete, an impact check
  before an edit) inherits that limit; it has to be fixed or the files opened before this tool is relied on.
- `replace_symbol_body` applied correctly in 4 of 4 runs, and each time the blank lines after the symbol were not
  kept, because the replaced range runs up to the next line at the symbol's indentation. A body that is only the
  symbol's own lines joins the next definition without the separating blank lines.

Four edit runs and one repository are a small sample. The figures describe this repository with this server; run
the script on another project or server before generalizing.
