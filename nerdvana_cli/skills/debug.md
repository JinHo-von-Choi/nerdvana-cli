---
name: debug
description: Systematic debugging — reproduce, isolate, fix, verify
trigger: /debug
---

# Debug Protocol

Follow these steps strictly:

1. **Reproduce**: Identify exact steps to trigger the bug
2. **Isolate**: Find the minimal reproduction case
3. **Hypothesize**: Form 2-3 hypotheses about root cause
4. **Test**: Verify each hypothesis with targeted checks
5. **Fix**: Apply minimal surgical fix
6. **Verify**: Run tests, confirm fix, check for regressions

Rules:
- Never guess. Verify every assumption with evidence.
- Read error messages completely before acting.
- Fix root causes, not symptoms.
- Add a regression test for every fix.
