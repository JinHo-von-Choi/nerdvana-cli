# ADR 0003 — AnchorMind Context Injection Deferred to Post-1.0

작성자: 최진호
작성일: 2026-04-18
상태: Accepted

## Context

`nerdvana_cli/server/hook_bridge.py`의 `_maybe_anchormind_context` 메서드는
`anchormind_inject=true` 설정 시 UserPromptSubmit/PreToolUse 훅에
AnchorMind MCP recall 결과를 컨텍스트로 주입하도록 설계됐다.

현재 구현은 literal placeholder 문자열(`[AnchorMind placeholder — topic=…]`)만
반환하므로, 설정을 켜면 의미 없는 문자열이 LLM 컨텍스트에 삽입되어
**토큰 낭비 및 로그 노이즈**가 발생한다.

실제 연동을 위해서는:

1. `mcp__anchormind__recall` 호출을 위한 MCP transport/세션 의존성을
   `HookBridge` 생성자에 주입해야 한다.
2. 비동기 MCP 호출 타임아웃·fallback 전략이 필요하다.
3. 훅 응답 latency 예산을 고려한 설계가 필요하다.

이 복잡도는 1.0 릴리즈 일정과 맞지 않는다.

## Decision

v1.0 범위에서 AnchorMind 컨텍스트 주입 **실제 구현을 보류**한다.

- `_maybe_anchormind_context` placeholder는 유지한다.
- `nerdvana.yml` 기본값 `hooks.anchormind_inject: false`를 유지한다.
- `anchormind_inject: true`로 설정하면 경고를 출력하고 빈 문자열을 반환
  ("not implemented in v1.0" 명시)하도록 코드 주석을 강화한다.
- 실제 구현은 post-1.0 로드맵 항목으로 추적한다.

## Consequences

- 사용자가 `anchormind_inject: true`를 설정해도 컨텍스트 주입이 발생하지 않는다.
- 무해한 빈 문자열 반환이므로 기능 회귀나 LLM 출력 오염은 없다.
- 실제 연동 구현 시 이 ADR을 closed로 변경하고 ADR 0003-bis로 설계 결정을 기록한다.

## Roadmap Reference

Post-1.0 구현 계획:
- AnchorMind MCP 세션을 `HookBridge` 의존성으로 주입
- `recall(topic=…)` 비동기 호출 + 100ms 타임아웃 + 빈 문자열 fallback
- `anchormind_inject: true` 설정 시 최초 1회 MCP 연결 검증 후 활성화
