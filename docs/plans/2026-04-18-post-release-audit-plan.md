# Post-Release Audit Plan — 0.9.2 Debt & Bug Triage

작성자: 최진호
작성일: 2026-04-18
대상 브랜치: main (로컬 HEAD a2b5301, origin보다 82+ 커밋 앞)
릴리즈: 0.9.2
상태: 승인 대기 — 본 문서는 플랜이며 수정 작업은 사용자 승인 후 착수한다.

## 1. 정량 통계

| 지표 | 값 |
|------|-----|
| 프로덕션 파이썬 파일 | 95 |
| 프로덕션 LOC (총) | 18,894 |
| 테스트 파일 | 118 |
| pytest collect | 897 |
| pytest pass (deselect=lsp) | 882 passed · 2 xfailed · 11 deselected · 5 warnings |
| ruff `nerdvana_cli/` | 4 errors (F401 ×2, SIM105 ×1, UP017 ×1) |
| ruff `tests/` | 155 errors (I001 63, F401 52, E402 12, UP017 8, 기타) |
| mypy `nerdvana_cli/` (strict) | 9 errors in 3 files |
| mypy `tests/` | 493 errors in 73 files |
| `# type: ignore` 주석 | 24 |
| `# noqa` 주석 | 63 (주로 BLE001) |
| import cycles | 0 (detector 측정치) |
| 지연 import (lazy import inside function) | 수십 건 — 관리 가능 |

상위 모듈 LOC: `tools/symbol_tools.py` 1184 / `ui/app.py` 851 / `tools/memory_tools.py` 530 / `main.py` 510 / `core/lsp_client.py` 497 / `core/symbol.py` 477 / `core/code_editor.py` 468 / `core/agent_loop.py` 434 / `server/mcp_server.py` 423.

---

## 2. 심각도 범례

- Critical — 프로덕션 경로에서 크래시·보안·데이터 손실을 일으키는 결함. 0.9.3 패치로 즉시 해결해야 함.
- Major — 주요 기능이 사용자에게 부분적으로만 동작하거나 감사·보안 레이어가 유명무실. 1.0 이전에 반드시 해소.
- Minor — 스타일·문서·테스트·성능 부채. 1.0 이후 점진 해소 가능.
- Nitpick — 구독 가치 낮은 클린업. 시간 여유 있을 때.

---

## 3. Critical 이슈 (총 5건)

### C-1. `nerdvana serve` 기동 시 TypeError — `Console.print(..., file=sys.stderr)`
- 위치: `nerdvana_cli/main.py:368`, `nerdvana_cli/main.py:375`
- 증거: `rich.console.Console.print()`는 `file` 파라미터를 지원하지 않는다. 실제 호출 시 `TypeError: Console.print() got an unexpected keyword argument 'file'`.
- 영향: http/stdio 어떤 transport든 `nerdvana serve` 커맨드가 banner 출력 시점에 크래시한다. Phase G1/G2/H MCP 서버 기능이 **릴리즈된 바이너리에서 기동 자체가 불가**.
- 재현: `python -c "from rich.console import Console; Console().print('x', file=__import__('sys').stderr)"` — 즉시 TypeError.
- 수정: `rich.Console(stderr=True).print(...)` 로 별도 인스턴스를 만들거나 `from rich import print as rprint` 를 버리고 builtin `print(..., file=sys.stderr)` 사용.
- 우선순위: Hot (0.9.3).
- 테스트: `typer.testing.CliRunner` 로 `serve --help` 뒤 실제 start-up banner 렌더를 검증하는 회귀 테스트 추가.

### C-2. MCP 서버 전체 tool payload가 placeholder stub
- 위치: `nerdvana_cli/server/mcp_server.py:364-373` (`_execute_tool`).
- 증거: 모든 MCP 도구 라우트가 `return f"[nerdvana:{tool_name}] args={args}"` 문자열만 반환. 실제 LSP / memory / symbol 로직과 **wiring 없음**. 주석에도 "the full wiring happens in Phase G2"라 적혀 있으나 현재 0.9.2(Phase G2 이후)까지 미해결.
- 영향: 외부 harness(Claude Code, Cursor 등)가 `mcp__nerdvana__*` 네임스페이스로 호출하면 의미 없는 문자열이 돌아옴. `nerdvana serve` 자체가 동작해도 기능이 없다 (C-1 수정 후 노출).
- 수정: `_execute_tool` 에서 실제 ToolRegistry를 구성하고 해당 tool의 `call()` 경로로 라우팅. 기존 `create_tool_registry()`를 재사용하되 read-only / allow_write 플래그에 맞춰 필터.
- 우선순위: Hot (0.9.3) — 릴리즈 노트가 광고한 "MCP 1.0 server" 기능의 사용성에 직결.
- 테스트: 기존 test_mcp_server 는 placeholder 리턴을 검증 중이므로 통합 테스트를 작성하여 (가능하면 `ReadMemory`, `ListMemories`) 실제 backend 호출을 확인.

### C-3. AuthManager / ACLManager 프로덕션 미연결
- 위치:
  - `nerdvana_cli/server/mcp_server.py:309-362` (`_dispatch`는 `client_identity="anonymous"` 기본값 고정).
  - `nerdvana_cli/server/auth.py:127/155/208` — `authenticate_bearer`, `authenticate_stdio`, `authenticate_mtls` 호출 지점이 프로덕션 코드에 **전무** (`grep` 결과 테스트 외 사용 없음).
- 영향: MCP 서버에 들어오는 모든 요청이 `anonymous` 식별자로 ACL 통과. 설계상의 인증/권한 레이어가 실질 작동하지 않음. mTLS·HTTP Bearer·Unix socket UID 어느 경로도 연결되지 않음.
- 또한 mTLS 경로는 **fail-open** 설계: `authenticate_mtls` 에서 알 수 없는 CN도 `authenticated=True` + `roles=["read-only"]` 반환(`auth.py:238-242`). 프로덕션에서 호출된다면 CN을 위조한 클라이언트가 read-only 무제한 접근.
- 수정:
  1. FastMCP transport hook 또는 미들웨어에서 request → `AuthResult` 를 계산하여 `_dispatch` 에 전파.
  2. `authenticate_mtls` 기본 동작을 fail-closed 로 전환(미등록 CN은 `authenticated=False`).
  3. `authenticate_bearer` 의 해시 비교를 `hmac.compare_digest` 로 교체(C-4 참조).
- 우선순위: Hot (0.9.3) — 네트워크 노출형 transport를 켜면 보안 경계가 0.
- 테스트: 인증 실패 요청이 401/403 으로 끊기는지, stdio 로컬은 UID 불일치 시 거부되는지 end-to-end 검증.

### C-4. `AuthManager.authenticate_bearer` timing attack
- 위치: `nerdvana_cli/server/auth.py:142-148`.
- 증거: `if entry.key_hash == digest:` 파이썬 `==` 는 단락 비교, **상수 시간 아님**. sha256 이긴 하지만 HMAC 슬라이딩 공격 관점에서 표준적으로 `hmac.compare_digest` 사용이 요구된다.
- 영향: 네트워크 RTT가 상수에 가깝고 공격자가 반복 시도 가능하다면 key_hash prefix를 점진 복원 가능.
- 수정: `import hmac; if hmac.compare_digest(entry.key_hash, digest):` 로 교체.
- 우선순위: Hot (0.9.3) — 2 문자 변경 수준. C-3 과 함께 해결.
- 추가: `mcp_keys.yml` 로드 시 파일 모드 0600 검증도 같이 추가(권한 느슨하면 경고 후 거부).

### C-5. `ExternalProjectTool` 계열 권한 체크 메서드명 오타 + 시그니처 불일치
- 위치: `nerdvana_cli/tools/external_project_tools.py:61-65, 146-150, 270-274`.
- 증거:
  - 베이스 클래스가 요구하는 이름은 `check_permissions(args, context)`. 해당 파일은 `check_permission` (단수) 와 `get_permission_behavior` 를 정의 → **아예 디스패치되지 않는 dead override**.
  - 설령 호출된다고 해도 `PermissionBehavior(behavior="allow")` / `PermissionResult(granted=True)` 는 StrEnum/`dataclass` 시그니처에 맞지 않아 TypeError 발생(mypy 역시 보고).
- 영향: `BaseTool.check_permissions` 의 기본값(ALLOW)으로 폴백 → `RegisterExternalProject` / `QueryExternalProject` / `ListExternalProjects` 도구들이 **권한 프롬프트 없이 파일시스템 쓰기/프로세스 실행**을 수행. 게다가 이 도구들은 registry에 등록되지도 않아 있어(아래 M-1 참조) 현재는 사용자에게 노출되지 않지만, 등록 누락을 고치는 순간 보안 구멍이 열린다.
- 수정:
  1. 메서드명을 `check_permissions` 로 통일.
  2. `RegisterExternalProjectTool` 은 디렉터리를 쓰기 가능 위치로 저장하므로 기본 `ASK` 또는 `registered_at` 기준 정책 설계 필요.
  3. `QueryExternalProjectTool` 은 subprocess 실행 → 최소 `ASK` 권장.
  4. `ListExternalProjectsTool` 은 `ALLOW`.
- 우선순위: Hot (0.9.3) — M-1 을 고칠 때 전제 조건.
- 테스트: 올바른 override 가 호출되는지 registry를 통해 확인하는 유닛 + 실제 permission 결정 assertion.

---

## 4. Major 이슈 (총 8건)

### M-1. Phase H ExternalProject 도구들이 메인 ToolRegistry에 등록되지 않음
- 위치: `nerdvana_cli/tools/registry.py:14-83`.
- 증거: `RegisterExternalProjectTool`, `QueryExternalProjectTool`, `ListExternalProjectsTool` 을 import 조차 하지 않음. `create_tool_registry()` 에서 등록 경로 누락. 단위 테스트만 존재.
- 영향: Phase H 핵심 기능이 사용자에게 **노출되지 않음**. CHANGELOG/README 와 실제 상태 drift.
- 수정: C-5 권한 처리를 먼저 확정한 뒤 registry 에 등록. 필요 시 `settings.external_projects_enabled` 플래그로 gating.

### M-2. `AnchorMind` 컨텍스트 주입이 placeholder 반환
- 위치: `nerdvana_cli/server/hook_bridge.py:173-182` — `_maybe_anchormind_context` 는 "[AnchorMind placeholder — topic=…]" 리터럴만 반환.
- 영향: `anchormind_inject=true` 설정을 켜도 실제 recall 안 됨. UserPromptSubmit/PreToolUse 에 의미 없는 문자열이 섞여 sanitizer 통과 후 LLM 컨텍스트에 삽입되어 **오히려 토큰 낭비** 및 로그 노이즈.
- 수정: ① 기능 켜져 있으면 실제 `mcp__anchormind__recall` 을 호출(서버 옵션으로 endpoint/token 지정)하거나 ② 아직 미구현임을 인정하고 `anchormind_inject` 옵션 자체를 일시 제거 + 문서에 "Phase I 예정" 명시.
- 우선순위: Next (1.0).

### M-3. `BashTool` 블랙리스트 커버리지 갭
- 위치: `nerdvana_cli/tools/bash_tool.py:52-98`.
- 증거:
  - `$(...)`, 백틱 `` ` ` ``, `eval`, `exec` 이 차단 대상이 아님.
  - `dd of=/dev/sdX` 가 차단되지 않음 (`dd if=` 만 차단).
  - `args.timeout` 상한 없음. 악의적 도구 호출로 999999 넘겨 장시간 프로세스 점유 가능.
  - sudo 전치 제거 로직이 단순 `^\s*sudo\s+` 매칭 — `FOO=1 sudo rm ...` 같은 env prefix 패턴은 통과.
  - `asyncio.create_subprocess_shell` 사용 → 본질적으로 shell injection 표면. 가능하면 `asyncio.create_subprocess_exec` + `shlex.split` 로 전환하거나 `allowlist` 기반 최상단 gating 추가.
  - `ASK` 판정이 tool_executor 레벨에서 **무조건 자동 거부** (C-6 참조) → curl POST 같은 정상 사용도 막힘.
- 수정: 블랙리스트 패턴 보강 + `timeout` 상한(기본 600초) + 새 `dd of=` 패턴 + 문서화된 allowlist 설정 지원.
- 우선순위: Next.
- 테스트: `tests/test_security_integration.py` 확장, xfail 중인 pycache 케이스와 curl | jq 케이스의 false-positive 패턴 tightening.

### M-4. `PermissionBehavior.ASK` 사용자 확인 흐름 부재
- 위치: `nerdvana_cli/core/tool_executor.py:140-148`.
- 증거: `ASK` 결정은 `"Permission required (auto-denied in current mode)"` 메시지와 함께 자동 거부. 사용자와의 대화형 확인 루트가 존재하지 않는다.
- 영향: `BashTool._ASK_PATTERNS` (env, curl -d, wget --post) 는 실질적으로 DENY 와 동일. UX 제약 + 디버깅 시 혼선.
- 수정: ① TUI 에서 confirm modal 제공, ② yolo 모드에서만 ALLOW, ③ 그 외는 명시적 DENY.
- 우선순위: Next.

### M-5. `ui/app.py` 슬래시 명령 등록 드리프트
- 위치: `nerdvana_cli/ui/app.py:67-89` (`SLASH_COMMANDS` 튜플) vs `nerdvana_cli/ui/app.py:716-747` (`handlers` dict) vs `README.md` 의 슬래시 명령 표.
- 증거: `handlers` 에 22 항목, `SLASH_COMMANDS` 에 21 항목(`/setup` 등은 handler 엔트리만, UI 메뉴에 없음), README 는 12 항목만. `/exit`/`/q` 는 quit 분기에 하드코드.
- 영향: 사용자가 `/setup` 등을 치면 handler 는 실행되지만 auto-complete 에 안 뜸. README 와 drift.
- 수정: 단일 선언 소스로 추출(예: `commands/__init__.py` 의 Registry), UI 메뉴·route·README 표 전부 여기서 생성. 유닛 테스트로 drift 검출.
- 우선순위: Next.

### M-6. main.py 플랫폼 카운트 drift ("12" vs "13")
- 위치: `nerdvana_cli/main.py:23`, `nerdvana_cli/main.py:70`.
- 증거: Typer 도움말 / docstring 은 "Supports 12 AI platforms". pyproject.toml, README.md, NIRNA.md 는 "13 AI platforms/providers".
- 수정: "13" 으로 통일하거나 provider count 를 `factory` 에서 동적 계산하여 문자열 생성.
- 우선순위: Next.

### M-7. `stale` tool_executor state 파라미터
- 위치: `nerdvana_cli/core/tool_executor.py:64` — `state: LoopState, # noqa: ARG002 — reserved for future per-state routing`.
- 증거: `run_batch` 가 `LoopState` 를 받지만 전혀 사용하지 않으며 `# noqa: ARG002` 로 마스킹. Phase 0A 에서 모듈 분리했지만 경계가 모호한 채 남음.
- 수정: 실제로 routing 에 사용하거나 시그니처에서 제거.
- 우선순위: Next.

### M-8. `audit.sqlite` 파일 권한 불일치 (0600 race)
- 위치: `nerdvana_cli/server/audit.py:106-115` (0600 chmod) vs `nerdvana_cli/server/sanitizer.py:228-233` (chmod 없음).
- 증거: 두 클래스가 동일 DB 파일(`~/.nerdvana/audit.sqlite`)에 대해 `sqlite3.connect` 호출 가능. `SanitizerAudit.open` 이 먼저 호출되면 기본 OS umask 모드(대개 0644 또는 0664) 로 파일이 생성되어 이후 `AuditLogger.open` 이 chmod 하기 전까지 세계 읽기 가능한 창이 존재.
- 영향: 멀티 유저 시스템에서 `tool_calls` / `sanitizer_events` 가 일시적으로 타 유저에게 노출. 가능성은 낮으나 supply-chain 감시 관점에서 취약.
- 수정: `sanitizer.SanitizerAudit.open` 에도 동일 `os.chmod(self._db_path, 0o600)` 추가하거나 두 모듈이 공통 헬퍼(`_open_audit_db`) 를 사용.
- 우선순위: Next.

---

## 5. Minor 이슈 (총 10건)

### N-1. `symbol_tools.py:243` stale 주석 ("reserved for 0.5.1")
현재 버전 0.9.2. 필드 실제 미구현이라면 schema 제거 또는 구현.

### N-2. `ruff check nerdvana_cli` 4 errors
- `tools/file_tools.py` F401 (사용 안 하는 import 가능) — 실제 4건 상세는 `ruff check --fix` 로 해결 가능.
- `ui/dashboard_tab.py:25-26` 미사용 import (Binding, VerticalScroll).
- `ui/app.py:848-851` SIM105 `try-except-pass` → `contextlib.suppress(Exception)`.
- `core/tool_executor.py:183` UP017 `datetime.UTC` alias 권장.

### N-3. `ruff check tests` 155 errors (I001 63, F401 52 등)
대부분 import 순서 / 미사용 import. `ruff check tests --fix` 로 127 건 자동 해결. CI 에 `ruff` 게이트 미적용이라 누적.

### N-4. `mypy nerdvana_cli` 9 errors
- `ui/clipboard.py` — `pyperclip` 타입 스텁 누락 → dev deps 에 `types-pyperclip` 추가 또는 `# type: ignore[import-untyped]`.
- `tools/external_project_tools.py` — C-5 로 해결.
- `main.py` — C-1 로 해결.

### N-5. `mypy tests` 493 errors
tests 대부분이 type annotation 이 없거나 import 순서 문제. `pyproject.toml` 의 `strict=true` 를 프로덕션에만 적용하도록 `[tool.mypy.overrides]` 분리 고려.

### N-6. Analytics 쓰기 경로 성능 부채
- `nerdvana_cli/core/analytics.py:262-290` — 매 tool call 마다 `sqlite3.connect` + commit + close.
- 개선: 세션 수명 동안 단일 커넥션 + 배치 flush. 현재 워크로드(초당 수 회)는 허용 범위지만 장시간 UI 세션에서 누적.

### N-7. `rich.Console` 단일 instance + 이중 파이프 혼재
`main.py` 는 `console = Console()` 을 stdout 로 만들고 이후 `file=sys.stderr` 를 건네주는 패턴. 교정 시 stderr 전용 console 을 별도로 두는 것이 깔끔.

### N-8. `lsp_integration` 테스트 로컬 pyright 미설치 시 실패
- `tests/lsp/test_lsp_integration_placeholder.py` 는 마커 `lsp_integration` 으로 gating 되지만 pytest addopts 에는 제외가 들어 있지 않아 `pytest tests` 직행 시 pyright 없는 환경에서 FAIL. CI 에서는 `-m "not lsp_integration"` 로 우회. 로컬 개발자 혼선.
- 개선: `addopts` 에 `-m "not lsp_integration"` 기본 제외 추가 + 명시적으로 돌릴 때만 실행하도록 안내.

### N-9. CHANGELOG / README Slash 테이블 drift
M-5 와 연결. README 12건, NIRNA.md 0건, `/help` (실구현) 22건.

### N-10. Retrospectives gitignore (`retrospectives/`)
`docs/retrospectives/` 는 `.gitignore` 의 `retrospectives/` 패턴에 걸려 `git add -f` 필요. 의도 여부 결정 후 패턴을 `/retrospectives/` 루트 한정 혹은 allowlist 방식으로 바꿀지 결정.

---

## 6. Nitpick (총 4건)

### K-1. `.import_cycles_baseline.json` count=0 인데 모듈 곳곳에 지연 import 남음
`agent_loop.py` 만 봐도 10+ 개의 function-local import. cycle 은 없더라도 "책임 계층" 기준으로 모듈화가 덜 됐다는 신호. 실제 호출 cost 는 무시할 정도이므로 1.0 에서 top-level 로 승격 여부만 결정.

### K-2. `commands/session_commands.py`, `observability_commands.py` 등의 `except ValueError: pass`
사용자 입력 파싱 실패 시 조용히 무시하는 패턴. logger.debug 호출 추가 권장.

### K-3. `external_worker` 테스트의 unawaited AsyncMock 경고 5건
- `tests/server/test_external_worker.py` 의 `test_shutdown_*`, `test_send_query_timeout`, `test_env_injection` 에서 mock.stdin.write 가 AsyncMock 으로 만들어지지만 await 되지 않음.
- 경고만 발생, 실제 동작 영향 없음. mock 을 `MagicMock` 으로 교체하거나 AsyncMock return value 를 `return_value=None` 으로 명시.

### K-4. sanitizer `high-entropy TOKEN` regex 범위
`[A-Za-z0-9+/]{16,}` 은 긴 URL 경로나 base64 덩어리를 과매칭. redaction 이 너무 공격적이어서 LLM 컨텍스트에 들어갈 실제 데이터가 손상될 가능성. 화이트리스트(예: `http[s]?://` 경계) 추가 권장.

---

## 7. 우선순위 버킷

### 7.1 Hot — 0.9.3 패치 (blocker, 이번 주)
- C-1 `Console.print(file=...)` TypeError — `nerdvana serve` 기동 불가
- C-5 ExternalProject 권한 체크 메서드명/시그니처 오타
- C-4 `authenticate_bearer` `compare_digest` 전환 (2문자 diff)
- M-8 `audit.sqlite` 0600 chmod race 수정 (1 라인)
- N-2 `ruff check nerdvana_cli --fix` (4건 자동)

총 예상 작업량: 0.5인일. 릴리즈 차단이거나 보안 수치화 가능한 항목만 포함.

### 7.2 Next — 1.0 릴리즈 전 (Phase I 또는 debt sprint)
- C-2 MCP 서버 `_execute_tool` 실구현 wiring
- C-3 Auth/ACL 레이어 FastMCP transport 통합 + mTLS fail-closed
- M-1 ExternalProjectTool 메인 registry 등록 (C-5 선행)
- M-2 AnchorMind 실제 연동 or 설계상 제거 결정
- M-3 BashTool 블랙리스트 보강 + timeout 상한
- M-4 `ASK` 대화형 확인 UX
- M-5 슬래시 명령 단일 소스화 + drift 테스트
- M-6 main.py "12" → "13" 또는 동적
- M-7 tool_executor `state` 책임 정립

총 예상 작업량: 3~5인일.

### 7.3 Deferred — 무기한 보류 (우선순위 재평가 필요)
- N-3, N-5 테스트 전반의 ruff/mypy 부채 (CI 게이트 도입 후 단계적)
- N-6 Analytics 커넥션 풀링 (현 워크로드에서는 미관측 부하)
- N-8 lsp_integration 기본 exclude marker
- K-1 지연 import 정리 (기능상 영향 0)
- K-2 silent-except 로깅 추가
- K-3 external_worker mock 경고
- K-4 sanitizer token regex tightening
- N-10 retrospectives gitignore 정책

---

## 8. 검증 절차

### 8.1 0.9.3 hot-patch 착수 조건
1. 본 플랜 사용자 승인.
2. 각 Hot 이슈별 회귀 테스트 선행 작성 (RED → 수정 → GREEN).
3. `ruff check nerdvana_cli` = 0 errors, `mypy nerdvana_cli` = 0 errors 로 유지.
4. 기존 882 passing 테스트 유지.

### 8.2 Next 버킷 준공 조건
- MCP 서버 실제 tool 라우팅 통합 테스트 (pyright 부착 환경에서 symbol_overview E2E).
- 보안 시나리오 회귀: 잘못된 bearer → 401, 위조 CN → 403, 권한 없는 tool → ACL 차단.
- BashTool 블랙리스트 fuzz 테스트 보강.
- `/help`, README 표, `SLASH_COMMANDS`, `handlers` dict 4중 drift 가 테스트로 감시됨.

### 8.3 Deferred 재평가 주기
분기 1회 릴리즈 retrospective 시 다시 열어본다.

---

## 9. 금지 사항 (본 플랜 단계에서)

- 코드 수정 금지. 본 문서는 승인 전 플랜.
- 의존성 추가 금지.
- 태그/릴리즈 생성 금지.
- 본 플랜 자체를 릴리즈 노트에 포함하지 않는다(retrospective 경유 요약만).

---

## 10. 발견된 의외의 사실 3가지

1. **MCP 서버가 사실상 동작 불능**: C-1 `Console.print(file=…)` 한 줄 버그로 `nerdvana serve` 가 시작 자체를 못 한다. Phase G1~H 릴리즈 노트가 있으나 End-to-End 스모크 테스트가 누락되어 있었음을 의미.
2. **외부 인증 레이어가 플러그 안 꽂혀 있음**: `AuthManager` 클래스는 200+ 라인 구현/테스트가 갖춰져 있지만 프로덕션 코드 어디에서도 `authenticate_*` 를 호출하지 않는다. 단위 테스트만 있는 "장식용 보안".
3. **ExternalProject 도구 3종이 Dead Code**: 메서드 오타로 override 디스패치 실패(C-5) + 메인 registry 미등록(M-1). 현재는 노출 자체가 안 돼서 사고가 없지만, 두 문제 중 하나만 고치면 나머지 하나가 security hole 을 연다. 순서 반드시 C-5 → M-1.
