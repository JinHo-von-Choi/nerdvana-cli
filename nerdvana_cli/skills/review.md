---
name: code-review
description: Systematic code review — security, performance, readability, tests
trigger: /review
---

# Code Review Protocol

Review the specified code with these checks:

1. **Security**: OWASP Top 10, input validation, auth/authz, secrets exposure
2. **Performance**: N+1 queries, unnecessary loops, memory leaks, caching opportunities
3. **Readability**: Naming conventions, function length, cognitive complexity
4. **Tests**: Coverage gaps, missing edge cases, test isolation
5. **Architecture**: Layer violations, coupling, SOLID principles

Output format:
- **CRITICAL** (must fix): [issue] at [file:line]
- **WARNING** (should fix): [issue] at [file:line]
- **SUGGESTION** (nice to have): [issue]
