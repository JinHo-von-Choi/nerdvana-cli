"""Recommend: which model a migration task runs on, and what that choice is estimated to cost.

작성자: 최진호
날짜: 2026-10-05

The package keeps the routing decision for a migration task: the deterministic
AST pass when the rewrite is mechanical, otherwise a surgical LLM pass on
either the high-reasoning tier for critical or wide-context work or the fast
standard tier for an ordinary diagnostic repair.
"""
