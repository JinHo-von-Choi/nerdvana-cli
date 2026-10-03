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
| `find_referencing_symbols` | top-level symbols of each seed file that other files use | the references reach every file that uses the identifier according to `ast` (a name, an attribute or an import; a file that defines its own function or class of that name and does not import it uses a different symbol and is not expected); the score is the share of those files reached |
| `replace_symbol_body` | `--edits` functions of a temporary copy of the package: preview, then apply, a body with one comment line added | the edited file has the same syntax tree as the original, holds the comment once and is exactly the original plus that one line (the blank lines after the symbol kept) |

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
  (`codeintel/lsp_client.py`) opens a file only when a tool is asked about it, so a reference search across the
  repository sees almost nothing. Anything built on cross-file references (rename, safe delete, an impact check
  before an edit) inherits that limit; it has to be fixed or the files opened before this tool is relied on.
- `replace_symbol_body` applied correctly in 4 of 4 runs, and each time the blank lines after the symbol were not
  kept, because the replaced range runs up to the next line at the symbol's indentation. A body that is only the
  symbol's own lines joins the next definition without the separating blank lines.

Four edit runs and one repository are a small sample. The figures describe this repository with this server; run
the script on another project or server before generalizing.

## Result after the fixes of 2026-10-03

Same repository state plus the changes below, same server (pyright 1.1.414), `--repeat 3`.

| Tool | Runs | Passed | Success rate | Mean score | p50 ms | p95 ms |
|-|-|-|-|-|-|-|
| `find_symbol` | 72 | 72 | 100% | | 3.4 | 7.8 |
| `symbol_overview` | 18 | 18 | 100% | 1.00 | 3.1 | 4.0 |
| `find_referencing_symbols` | 33 | 33 | 100% | 1.00 | 349 | 1397 |
| `replace_symbol_body` | 4 | 4 | 100% | | 59 | 2680 |

What changed:

- Cause of the empty references: pyright reports `textDocument/references` only from the documents it has been
  shown. A probe with a raw JSON-RPC session reproduced it independently of the client: with the file of the
  definition open the answer was two locations in one file, with the one file that uses the symbol opened as well
  it was eighteen in two files, waiting up to ten seconds changed nothing, and passing `workspaceFolders` in
  `initialize` only made the files known after the server had indexed for a few seconds (the first answer is
  still partial). The client now sends `workspaceFolders`, and before a references or rename request it opens
  every workspace file that mentions the identifier (`codeintel/lsp_workspace.py`: whole word, same file suffix, no
  hidden, dependency or build directories, at most 200 files, nearest to the definition first) and resyncs the
  files that are open (a changed file with `didChange`, a deleted one with `didClose`). A request without an
  answer in time fails with a message that names the method and says the server may still be indexing; when more
  than 200 files mention the identifier the result carries a notice that references in the rest may be missing.
  `lsp_rename` shares the path and carries the same notice.
- The expected files of the benchmark no longer include a file that defines its own function or class of the same
  name (three of the 33 runs failed on `MaskResult`, which `core/secrets.py` defines separately; the server was
  right and the expectation was wrong).
- `replace_symbol_body` replaced the range up to the next line at the symbol's indentation. That range holds the
  blank lines after the symbol, and it ends too early at a decorator line or at the closing parenthesis of a
  signature over several lines. It now replaces exactly the extent the language server reports (decorators
  included), and without an extent it stops before the blank and comment lines that end the range. A body with
  no final newline keeps the end of a file that has none. The benchmark now fails an edit whose text differs
  from the original plus the one line.
- `safe_delete_symbol` counted the definition line itself among the references (the server includes the
  declaration), so every symbol was reported as blocked. References inside the symbol are now ignored, the
  deletion takes the blank lines after the symbol with it as before, and a use elsewhere still blocks.

Latency of `find_referencing_symbols` rose from about 9 ms to about 350 ms at the median: each call scans the
workspace for the identifier (about 100 to 200 ms for this repository's 675 Python files) and the server then
analyses the files it was shown, which the first call of a symbol pays for. That is the price of an answer that
reaches the other files; a persistent index of identifiers would remove the scan and is not part of this change.

Limits that remain: a file that uses the symbol only under another name and never mentions the original (an
alias imported elsewhere and re-exported) is not opened by the scan, and pyright reports it only once its own
indexing has reached it. The 200 file cap is a constant of `codeintel/lsp_workspace.py`. The line based fallback for a
server that gives no symbol extent still ends a symbol at the first line at its indentation. Editing a file with
CRLF line endings through any symbol edit tool rewrites it with LF, which is outside this change.
