"""
nerdvana_cli 패키지 내 순환 import 감지 스크립트.

작성자: 최진호
작성일: 2026-04-18

nerdvana_cli/ 하위 모든 .py 파일을 AST로 파싱하여 import 관계를
networkx.DiGraph에 구축하고, simple_cycles로 순환을 탐지한다.

사용법:
    python scripts/check_import_graph.py [--root nerdvana_cli]
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

import networkx as nx

# ---------------------------------------------------------------------------
# 모듈 경로 → 정규화된 모듈명 변환
# ---------------------------------------------------------------------------

def _path_to_module(py_file: Path, root: Path) -> str:
    """
    파일 경로를 nerdvana_cli.sub.module 형식의 정규화된 모듈명으로 변환한다.

    Args:
        py_file: 변환할 .py 파일 경로.
        root:    루트 패키지 디렉토리 (예: Path("nerdvana_cli")).

    Returns:
        점(.) 구분 모듈명 문자열.
    """
    rel = py_file.relative_to(root.parent)
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


# ---------------------------------------------------------------------------
# 단일 파일 파싱 → 내부 import 목록 추출
# ---------------------------------------------------------------------------

def _collect_imports(
    py_file: Path,
    root: Path,
    pkg_prefix: str,
) -> list[str]:
    """
    단일 .py 파일에서 nerdvana_cli.* 범위의 import 대상 모듈명을 수집한다.

    외부 라이브러리(예: anthropic, networkx)는 결과에 포함하지 않는다.
    상대 import는 현재 파일의 패키지 경로를 기준으로 절대 모듈명으로 변환한다.

    Args:
        py_file:    파싱할 .py 파일.
        root:       루트 패키지 디렉토리.
        pkg_prefix: 내부 패키지 접두사 (예: "nerdvana_cli").

    Returns:
        import 대상 절대 모듈명 목록.  파싱 실패 시 빈 리스트 반환.
    """
    try:
        source = py_file.read_text(encoding="utf-8")
        tree   = ast.parse(source, filename=str(py_file))
    except (SyntaxError, UnicodeDecodeError, OSError) as exc:
        print(f"warning: cannot parse {py_file}: {exc}", file=sys.stderr)
        return []

    # 상대 import 해석을 위한 현재 파일의 패키지 경로 계산
    rel_parts = list(py_file.relative_to(root.parent).with_suffix("").parts)
    current_pkg_parts = rel_parts[:-1] if rel_parts[-1] == "__init__" else rel_parts[:-1]

    targets: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            # import foo, import foo.bar
            for alias in node.names:
                mod = alias.name
                if mod == pkg_prefix or mod.startswith(pkg_prefix + "."):
                    targets.append(mod)

        elif isinstance(node, ast.ImportFrom):
            level  = node.level or 0
            module = node.module or ""

            if level == 0:
                # 절대 import: from foo.bar import baz
                if module == pkg_prefix or module.startswith(pkg_prefix + "."):
                    targets.append(module)
            else:
                # 상대 import: from . import x  /  from ..bar import x
                # level=1 → 현재 패키지, level=2 → 부모 패키지
                base_parts = current_pkg_parts[: len(current_pkg_parts) - (level - 1)]
                abs_mod = ".".join(base_parts + [module]) if module else ".".join(base_parts)

                if abs_mod == pkg_prefix or abs_mod.startswith(pkg_prefix + "."):
                    targets.append(abs_mod)

    return targets


# ---------------------------------------------------------------------------
# 그래프 구축
# ---------------------------------------------------------------------------

def build_graph(root: Path) -> tuple[nx.DiGraph, int]:
    """
    root 패키지 하위 모든 .py 파일을 파싱하여 import 의존 그래프를 구축한다.

    Args:
        root: 분석 대상 패키지 루트 디렉토리 (예: Path("nerdvana_cli")).

    Returns:
        (graph, file_count) — DiGraph와 처리된 .py 파일 수.

    Raises:
        SystemExit: root 디렉토리가 존재하지 않으면 stderr 메시지 후 exit(1).
    """
    if not root.is_dir():
        print(f"error: root directory not found: {root}", file=sys.stderr)
        sys.exit(1)

    pkg_prefix = root.name
    graph      = nx.DiGraph()
    py_files   = sorted(root.rglob("*.py"))

    for py_file in py_files:
        src_mod = _path_to_module(py_file, root)
        graph.add_node(src_mod)

        for tgt_mod in _collect_imports(py_file, root, pkg_prefix):
            if tgt_mod != src_mod:
                graph.add_node(tgt_mod)
                graph.add_edge(src_mod, tgt_mod)

    return graph, len(py_files)


# ---------------------------------------------------------------------------
# 순환 탐지 및 결과 출력
# ---------------------------------------------------------------------------

def check_cycles(graph: nx.DiGraph) -> list[list[str]]:
    """
    그래프에서 단순 순환(simple cycles)을 탐지하여 반환한다.

    Args:
        graph: import 의존 방향 그래프.

    Returns:
        순환 경로 목록.  각 원소는 사이클을 구성하는 모듈명 리스트.
    """
    return list(nx.simple_cycles(graph))


def _format_cycle(cycle: list[str]) -> str:
    """사이클 리스트를 사람이 읽기 쉬운 문자열로 변환한다."""
    return " -> ".join(cycle + [cycle[0]])


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="nerdvana_cli 패키지 내 순환 import 탐지기",
    )
    parser.add_argument(
        "--root",
        default="nerdvana_cli",
        metavar="DIR",
        help="분석 대상 패키지 루트 디렉토리 (기본값: nerdvana_cli)",
    )
    return parser.parse_args()


def main() -> None:
    """CLI 진입점. 순환 발견 시 exit(1), 정상 시 exit(0)."""
    args = _parse_args()
    root = Path(args.root)

    graph, _file_count = build_graph(root)
    cycles             = check_cycles(graph)
    n_modules          = graph.number_of_nodes()

    if cycles:
        for cycle in cycles:
            print(f"cycle: {_format_cycle(cycle)}", file=sys.stderr)
        sys.exit(1)

    print(f"import graph: {n_modules} modules, no cycles")


if __name__ == "__main__":
    main()
