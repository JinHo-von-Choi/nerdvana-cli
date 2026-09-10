# NerdVana CLI

AI 기반 CLI 개발 도구 — Anthropic Claude, OpenAI, Google Gemini, Groq, Ollama 등 **21개 AI 플랫폼**을 지원합니다.

판올림 1.5.0 · Python 3.11 이상 · MIT 라이선스 · [English README](README.md)

## 기능

- **다중 제공자 지원** — 하나의 CLI로 21개 AI 플랫폼 사용 가능
- **대화형 REPL** — 스트리밍 출력과 슬래시 명령어, 토큰 사용량 표시기
- **비대화형 모드** — 스크립팅을 위한 단일 프롬프트 실행 (`nerdvana run`)
- **31개 내장 도구**: 파일 I/O, 검색, 셸, 웹, 작업 목록, MCP 외에 LSP·심볼·서브에이전트·팀·스웜·태스크 관리 도구
- **편집 품질 게이트 (Phase A)** — `FileRead`가 라인 단위 SHA256 앵커 해시를 발급하여 `FileEdit`의 컨텍스트 충돌을 차단하며, LSP 진단을 통해 편집 직후 회귀를 검출
- **다중 에이전트 오케스트레이션 (Phase B)** — `Agent` / `Swarm` 도구로 6개 빌트인 에이전트 타입을 비동기 실행하고, `TaskPanel`이 TUI에서 진행 상황을 실시간으로 표시
- **에이전트 팀 메시징 (Phase B)** — `TeamCreate` / `SendMessage` / `TaskGet` / `TaskStop`으로 장기 협업 워크플로우 구성
- **자동 복구 훅 (Phase C)** — 컨텍스트 압축, 모델 폴백 체인, 복잡도 기반 계획 게이트, MCP 헬스 체크를 통한 자율 회복
- **실시간 활동 표시기 + 추론 태그 렌더링** — DeepSeek-R1, QwQ, Qwen3-thinking, GLM, Kimi K2.5 thinking, MiniMax M2 가 보내는 `<think>...</think>` 블록을 dim italic 으로 분리 표시하고, `ActivityIndicator` 위젯이 현재 phase(idle / thinking / waiting_api / streaming / tool_running)와 활성 도구 대상을 보여줍니다.
- **시작 시 업데이트 알림**: 실행할 때마다 GitHub 릴리즈를 확인해 새 판이 있으면 한 줄로 알린다 (24시간 캐시). `--no-update-check`, `NERDVANA_NO_UPDATE_CHECK=1`, `nerdvana.yml`의 `session.update_check: false`로 끈다.
- **세션 지속성** — 재개를 위한 JSONL 트랜스크립트 저장
- **자동 제공자 감지** — 모델 이름에서 적절한 제공자 자동 선택
- **MCP 통합** — 외부 MCP 서버를 연결하여 도구 시스템 확장
- **구성 가능** — YAML 설정, 환경 변수, CLI 플래그

## 지원하는 제공자

| 제공자 | 기본 모델 | API 키 환경 변수 |
|--------|-----------|------------------|
| **Anthropic** | claude-sonnet-4-20250514 | `ANTHROPIC_API_KEY` |
| **OpenAI** | gpt-4.1 | `OPENAI_API_KEY` |
| **Google Gemini** | gemini-2.5-flash | `GEMINI_API_KEY` |
| **Groq** | llama-3.3-70b-versatile | `GROQ_API_KEY` |
| **OpenRouter** | anthropic/claude-sonnet-4 | `OPENROUTER_API_KEY` |
| **xAI (Grok)** | grok-3 | `XAI_API_KEY` |
| **Ollama** | qwen3 | `OLLAMA_API_KEY` |
| **vLLM** | Qwen/Qwen3-32B | `VLLM_API_KEY` |
| **DeepSeek** | deepseek-chat | `DEEPSEEK_API_KEY` |
| **Mistral** | mistral-medium-latest | `MISTRAL_API_KEY` |
| **Cohere** | command-r-plus | `CO_API_KEY` |
| **Together AI** | Llama-3.3-70B-Instruct-Turbo | `TOGETHER_API_KEY` |
| **ZAI (GLM)** | glm-4.7 | `ZHIPUAI_API_KEY` |
| **Featherless AI** | featherless-llama-3-70b | `FEATHERLESS_API_KEY` |
| **Xiaomi MiMo** | mimo-v2.5-pro | `MIMO_API_KEY` |
| **Moonshot AI (Kimi)** | kimi-k2-instruct | `MOONSHOT_API_KEY` |
| **Alibaba DashScope (Qwen)** | qwen3-coder-plus | `DASHSCOPE_API_KEY` |
| **MiniMax** | MiniMax-M2 | `MINIMAX_API_KEY` |
| **Perplexity** | sonar-pro | `PERPLEXITY_API_KEY` |
| **Fireworks AI** | accounts/fireworks/models/llama-v3p3-70b-instruct | `FIREWORKS_API_KEY` |
| **Cerebras** | llama-3.3-70b | `CEREBRAS_API_KEY` |

## 설치

### 한 줄 설치 (권장)

```bash
curl -fsSL https://raw.githubusercontent.com/JinHo-von-Choi/nerdvana-cli/main/install.sh | bash
```

이 명령은 NerdVana CLI를 `~/.nerdvana-cli/`에 설치하고 가상환경을 구성한 뒤, `nerdvana` / `nc` 명령어를 PATH에 추가합니다.

요구사항: Python >= 3.11, git

### 수동 설치

```bash
# 저장소 클론 후 모든 제공자와 함께 설치
git clone https://github.com/JinHo-von-Choi/nerdvana-cli.git
cd nerdvana-cli
pip install -e ".[all]"

# 또는 특정 제공자만 설치
pip install -e ".[anthropic]"   # Anthropic만
pip install -e ".[openai]"      # OpenAI만
pip install -e ".[gemini]"      # Gemini만
```

## 빠른 시작

```bash
# 제공자별 API 키 설정
export ANTHROPIC_API_KEY="sk-ant-..."
# 또는
export OPENAI_API_KEY="sk-..."
# 또는
export GEMINI_API_KEY="..."

# 대화형 REPL (모델 이름에서 제공자 자동 감지)
nerdvana

# 명시적으로 제공자 지정
nerdvana --provider anthropic --model claude-opus-4-20250514
nerdvana --provider openai --model gpt-4.1
nerdvana --provider gemini --model gemini-2.5-pro
nerdvana --provider groq --model llama-3.3-70b-versatile
nerdvana --provider ollama --model qwen3

# 단일 프롬프트 실행
nerdvana run "이 프로젝트의 아키텍처 설명"
nerdvana run "이 코드 리팩터링" --provider deepseek

# 모든 제공자 목록 보기
nerdvana providers
```

## CLI 서브명령어

### 메인 명령어

| 서브명령어 | 설명 |
|-|-|
| `nerdvana` | 대화형 REPL 시작 (서브명령어 없이 실행 시 기본 동작) |
| `nerdvana run <프롬프트>` | 단일 프롬프트를 비대화형으로 실행 |
| `nerdvana setup` | 대화형 설정 마법사 — 제공자 선택, API 키 입력, 모델 선택 |
| `nerdvana providers` | 지원하는 모든 AI 제공자 목록 표시 |
| `nerdvana version` | 버전 표시 |
| `nerdvana serve` | NerdVana를 MCP 1.0 서버로 시작 (stdio 또는 HTTP 트랜스포트) |
| `nerdvana doctor` | 설치 상태·API 키·외부 의존성 진단 (`--strict`, `--json`) |
| `nerdvana cost` | 지정 기간의 토큰 사용량과 USD 비용 집계 |

### 세션 기록 (`nerdvana session ...`)

| 서브명령어 | 설명 |
|-|-|
| `nerdvana session list` | `~/.nerdvana/sessions/`에 저장된 JSONL 기록 목록 표시 |
| `nerdvana session resume <id>` | 기존 기록으로 REPL 재개 |
| `nerdvana session purge` | 저장된 기록 삭제 |

### MCP 서버 (`nerdvana mcp ...`)

| 서브명령어 | 설명 |
|-|-|
| `nerdvana mcp list` | `~/.nerdvana/mcp.json`과 `<cwd>/.mcp.json`에 선언된 서버 목록 표시 |
| `nerdvana mcp add <이름>` | 두 파일 중 하나에 서버 항목 추가 |
| `nerdvana mcp remove <이름>` | 서버 항목 제거 |

### 스킬 (`nerdvana skill ...`)

| 서브명령어 | 설명 |
|-|-|
| `nerdvana skill list` | 내장·전역·프로젝트 스킬 목록 표시 |
| `nerdvana skill show <이름>` | 스킬의 프론트매터와 본문 출력 |
| `nerdvana skill install <소스>` | `~/.nerdvana/skills/`에 스킬 설치 |
| `nerdvana skill remove <이름>` | 설치된 스킬 삭제 |

### 프로젝트 메모리 (`nerdvana memory ...`)

| 서브명령어 | 설명 |
|-|-|
| `nerdvana memory list` | 현재 프로젝트에 기록된 메모리 목록 표시 |
| `nerdvana memory add <내용>` | 새 메모리 기록 |
| `nerdvana memory remove <id>` | 메모리 한 건 삭제 |
| `nerdvana memory purge` | 선택한 범위의 메모리 전체 삭제 |

### 훅 브리지 (`nerdvana hook ...`)

| 서브명령어 | 설명 |
|-|-|
| `nerdvana hook pre-tool-use` | pre-tool-use 훅 이벤트 처리 — stdin에서 JSON 읽기, stdout에 응답 출력 |
| `nerdvana hook post-tool-use` | post-tool-use 훅 이벤트 처리 |
| `nerdvana hook prompt-submit` | prompt-submit 훅 이벤트 처리 |
| `nerdvana hook list` | 지원하는 훅 이벤트 타입 목록 표시 |

### ACL 관리 (`nerdvana admin acl ...`)

| 서브명령어 | 설명 |
|-|-|
| `nerdvana admin acl list` | 모든 클라이언트와 할당된 역할 목록 표시 |
| `nerdvana admin acl add <클라이언트> <역할>` | 클라이언트의 역할 추가 또는 갱신 |
| `nerdvana admin acl revoke <접두사>` | 이름이 접두사와 일치하는 클라이언트의 ACL 항목 취소 |

## 디렉토리 구조

NerdVana CLI는 *설치 디렉토리*와 *사용자 데이터*를 분리합니다:

```
~/.nerdvana-cli/     — 설치 루트 (git 저장소 + venv). install.sh가 관리합니다.
                       런타임에서 읽기 전용 — 이 디렉토리를 직접 수정하지 마세요.

~/.nerdvana/         — 사용자 데이터 루트 ($NERDVANA_DATA_HOME으로 변경 가능).
  ├── config.yml     — 전역 설정
  ├── NIRNA.md       — 전역 지침
  ├── mcp.json       — 전역 MCP 서버
  ├── sessions/      — 대화 기록 (JSONL)
  ├── skills/        — 전역 사용자 스킬
  ├── hooks/         — 전역 사용자 훅
  ├── agents/        — 전역 에이전트 정의용 예약 디렉토리 (현재 미사용)
  ├── teams/         — 팀 상태
  ├── cache/         — 런타임 캐시
  └── logs/          — 로그 (예약됨)

<프로젝트>/           — 현재 작업 디렉토리 (선택적 프로젝트 오버라이드)
  ├── nerdvana.yml
  ├── NIRNA.md
  ├── .mcp.json
  └── .nerdvana/
      ├── skills/
      ├── hooks/
      └── agents/
```

### 환경 변수

| 변수 | 설명 | 기본값 |
|---|---|---|
| `NERDVANA_HOME` | 설치 루트 | `~/.nerdvana-cli` |
| `NERDVANA_DATA_HOME` | 사용자 데이터 루트 | `~/.nerdvana` |
| `NERDVANA_CONFIG` | 명시적 설정 파일 경로 | `~/.nerdvana/config.yml` |
| `NERDVANA_NO_UPDATE_CHECK` | `1`로 두면 시작 시 판올림 확인을 건너뛴다 | 미설정 |
| `NERDVANA_EXTERNAL_PROJECTS_ROOT` | 외부 프로젝트 도구가 벗어날 수 없는 경계 루트 | 미설정 |
| `NERDVANA_EXTERNAL_PROJECTS_ENABLED` | 설정 파일 없이 외부 프로젝트 도구를 등록 | `false` |

### 마이그레이션

업그레이드 후 첫 실행 시 `~/.nerdvana-cli/sessions/` 및 `~/.config/nerdvana-cli/`의 데이터를 `~/.nerdvana/`로 이동합니다. `.migrated` 센티넬 파일이 재실행을 방지합니다.

## REPL 슬래시 명령어

| 명령어 | 설명 |
|--------|------|
| `/help` | 사용 가능한 슬래시 명령어 목록 표시 |
| `/clear` | 현재 대화 내용 지우기 |
| `/init` | 현재 디렉토리에 `NIRNA.md` 프로젝트 지침 파일 생성 (별칭: `/setup`) |
| `/model` | 현재 model 표시/변경 (provider 별 마지막 model 이 config.yml 에 기록되어 재실행 시 유지) |
| `/models` | 사용 가능한 model 목록 (cursor 가 현재 active model 에서 시작) |
| `/provider` | provider 추가/전환 (선택은 config.yml 에 저장되어 재실행 시 유지) |
| `/mode` | 모드 프로파일 활성화/비활성화 |
| `/context` | 컨텍스트 프로파일 설정 |
| `/mcp` | 연결된 MCP 서버 상태 표시 |
| `/tokens` | 누적 토큰 사용량 및 컨텍스트 윈도우 사용률 표시 |
| `/skills` | 등록된 에이전트 스킬 목록 표시 |
| `/tools` | 활성화된 모든 내장 도구 및 MCP 도구 목록 표시 |
| `/update` | 최신 버전 확인 및 업데이트 설치 (`/update parism` 입력 시 내장 Parism MCP 패키지를 최신 버전으로 강제 갱신) |
| `/memories` | 프로젝트 메모리 목록 표시 |
| `/undo` | 편집 전 git 체크포인트로 복원 |
| `/redo` | 마지막으로 되돌린 체크포인트 재적용 |
| `/checkpoints` | 세션 체크포인트 목록 표시 |
| `/route-knowledge` | 콘텐츠를 분류하여 WriteMemory 스코프 제안 |
| `/dashboard` | 관찰 가능성 대시보드 토글 |
| `/health` | 7일간 도구 호출 건강 요약 표시 |
| `/thinking` | 인라인 추론 표시 토글 (on/off, config.yml 에 저장) |
| `/activity` | 활동 표시기 위젯 토글 (on/off, config.yml 에 저장) |
| `/quit` | REPL 종료 (별칭: `/exit`, `/q`) |

## 내장 도구

레지스트리는 31개의 내장 도구를 조립합니다. 셸·파일·검색·작업 목록·웹·에이전트·팀 도구는 항상 등록됩니다.
`Parism`은 번들된 Parism MCP 패키지에 접근할 수 있을 때 등록됩니다. LSP·심볼 도구는 호환 언어 서버가 설치된
경우에만 등록되며, 없으면 조용히 생략됩니다. 외부 프로젝트 도구 3종은 설정에서 `external_projects_enabled: true`
를 켜기 전까지 등록되지 않습니다.

| 도구 | 유형 | 설명 |
|------|------|------|
| `Bash` | 쓰기 | 셸 명령어 실행 (타임아웃, 작업 디렉토리, 환경 변수 지원) |
| `FileRead` | 읽기 | 파일 내용 읽기 — 라인 단위 SHA256 앵커 해시를 함께 반환하여 후속 `FileEdit`의 정합성 보장 |
| `FileWrite` | 쓰기 | 파일 생성 또는 덮어쓰기 |
| `FileEdit` | 쓰기 | 문자열 교체 또는 `anchor_hash` 기반 정밀 편집 (Phase A 편집 품질 게이트) |
| `Glob` | 읽기 | 파일 패턴 매칭 |
| `Grep` | 읽기 | 정규식 기반 콘텐츠 검색 |
| `TodoWrite` | 쓰기 | 에이전트가 수행할 작업 목록 관리 |
| `WebFetch` | 읽기 | URL을 가져와 읽을 수 있는 본문으로 변환 |
| `WebSearch` | 읽기 | Brave Search 질의. `BRAVE_API_KEY`가 없으면 호출 시점에 오류 |
| `Parism` | 쓰기 | 화이트리스트된 44개 셸 명령어를 구조화된 JSON 출력으로 실행 |
| `Agent` | 오케스트레이션 | 단일 서브에이전트를 비동기로 발사하고 `task_id` 반환 |
| `Swarm` | 오케스트레이션 | 여러 서브에이전트를 병렬로 발사하여 독립 작업을 분산 |
| `TeamCreate` | 협업 | 장기 협업을 위한 에이전트 팀 생성 |
| `SendMessage` | 협업 | 팀 내부 에이전트에게 메시지 전송 |
| `TaskGet` | 협업 | 비동기 작업의 상태와 결과 조회 |
| `TaskStop` | 협업 | 실행 중인 비동기 작업 중단 |
| `lsp_diagnostics` | LSP | 파일에 대한 LSP 진단(에러·경고) 조회 |
| `lsp_goto_definition` | LSP | 심볼의 정의 위치로 이동 |
| `lsp_find_references` | LSP | 심볼의 모든 참조 위치 검색 |
| `lsp_rename` | LSP | 심볼을 안전하게 일괄 리네임 |
| `symbol_overview` | 읽기 | 파일 또는 디렉토리의 심볼 맵(클래스·함수·변수) 반환 |
| `find_symbol` | 읽기 | 이름 경로로 심볼을 찾고 선택적으로 본문 반환 |
| `find_referencing_symbols` | 읽기 | 지정 심볼을 참조하는 모든 심볼 검색 |
| `restart_language_server` | 쓰기 | 툴체인·의존성 변경 후 언어 서버 재기동 |
| `replace_symbol_body` | 쓰기 | 심볼 본문을 한 번의 원자적 연산으로 교체 |
| `insert_before_symbol` | 쓰기 | 심볼 정의 바로 앞에 코드 삽입 |
| `insert_after_symbol` | 쓰기 | 심볼 정의 바로 뒤에 코드 삽입 |
| `safe_delete_symbol` | 쓰기 | 잔여 참조가 없음을 확인한 후 심볼 삭제 |
| `ListQueryableProjects` | 읽기 | 위임 가능한 등록 외부 nerdvana 프로젝트 카탈로그 조회 |
| `RegisterExternalProject` | 쓰기 | 서브프로세스 격리 쿼리 위임을 위한 외부 nerdvana 프로젝트 등록 |
| `QueryExternalProject` | 읽기 | 등록된 외부 프로젝트에 nerdvana 쿼리를 격리된 서브프로세스로 위임 |

## 에이전트 타입

`Agent` / `Swarm` 도구로 발사할 수 있는 6개 빌트인 에이전트 타입이 있습니다. 각 타입은 허용 도구 화이트리스트와 시스템 프롬프트로 책임 범위를 한정합니다.

| 에이전트 타입 | 허용 도구 | 용도 |
|----------------|-----------|------|
| `general-purpose` | 전체 (`*`) | 연구·코드·다단계 작업을 위한 범용 에이전트 (최대 50턴) |
| `Explore` | `Glob`, `Grep`, `FileRead` | 코드베이스 탐색 전용 — 파일 작성·수정 금지 (최대 20턴) |
| `Plan` | `Glob`, `Grep`, `FileRead` | 구현 계획 수립 전담 — 코드 작성 금지, 오직 설계만 (최대 20턴) |
| `code-reviewer` | `FileRead`, `Grep`, `Glob` | 코드 품질·정확성·보안 검토 (읽기 전용, 최대 15턴) |
| `git-management` | `Bash`, `FileRead` | git status/add/commit/branch/log/diff 등 git 작업 전담 (최대 20턴) |
| `test-writer` | 전체 (`*`) | TDD 방식으로 단위·통합 테스트 작성 및 실행 (최대 30턴) |

서브에이전트는 재귀 발사 및 팀 관리 도구를 보유하지 않으므로 부모 세션의 토큰 컨텍스트를 격리합니다. 비동기 작업의 진행 상태는 TUI 우측 `TaskPanel`에 실시간으로 표시됩니다.

## 자동 복구 훅 (Phase C)

`AgentLoop` 초기화 시점에 자동 등록되는 빌트인 라이프사이클 훅 — 장시간 세션이 사람 개입 없이 자율 회복하도록 돕습니다.

| 훅 | 이벤트 | 동작 |
|-----|--------|------|
| `session_start_context_injection` | `SESSION_START` | 첫 시스템 프롬프트에 도구 목록·세션 설정 요약·`NIRNA.md` 내용을 주입 |
| `context_limit_recovery` | `AFTER_API_CALL` | 모델이 `max_tokens`로 종료되면 마지막 사용자 요청을 인용한 이어쓰기 메시지를 주입하여 작업을 재개 |
| `json_parse_recovery` | `AFTER_TOOL` | 도구 결과의 JSON 파싱이 실패하면 해당 도구 이름을 명시한 정정 요청을 주입 |
| `ralph_loop_check` | `AFTER_API_CALL` | `end_turn`에서 마지막 어시스턴트 메시지에 `TODO`, `FIXME`, `NotImplemented`, `# 구현 필요`, `# 미구현` 마커가 남아 있으면 마무리하라고 지시 (Ralph self-finishing loop) |

훅 파이프라인은 `hooks.session_start`, `hooks.before_tool`, `hooks.after_tool`로 확장 가능합니다. 추가로 `model.fallback_models`(HTTP 429/529/503/timeout 발생 시 다음 모델로 자동 전환)와 `session.planning_gate`(복잡도 기반 `Plan` 에이전트 선행 실행)을 함께 활성화하면 완전한 자율 운용 모드를 구성할 수 있습니다.

## MCP 서버 통합

외부 [MCP](https://modelcontextprotocol.io/) 서버를 붙여 도구 시스템을 넓힙니다. 발견된 도구는 `mcp__{서버}__{도구}` 이름으로 자동 등록됩니다.

서버는 `nerdvana.yml`이 아니라 JSON 파일에 선언합니다. 전역 파일을 먼저, 프로젝트 파일을 나중에 읽으므로 이름이 같으면 프로젝트 쪽이 이깁니다.

| 파일 | 범위 |
|-|-|
| `~/.nerdvana/mcp.json` | 전역, 모든 프로젝트에 적용 |
| `<cwd>/.mcp.json` | 프로젝트 (Claude Code와 같은 파일 이름) |

두 파일 모두 `mcpServers` 객체를 씁니다:

```json
{
  "mcpServers": {
    "my-server": {
      "transport": "stdio",
      "command": "node",
      "args": ["server.js"],
      "env": { "API_KEY": "${MY_API_KEY}" }
    }
  }
}
```

`nerdvana mcp add`, `nerdvana mcp list`, `nerdvana mcp remove`가 이 파일들을 대신 편집합니다. REPL에서는 `/mcp`로 연결 상태를, `/tools`로 MCP 도구를 포함한 전체 도구 목록을 봅니다.

## NIRNA.md, 프로젝트 지침

시스템 프롬프트에 프로젝트별 지침을 주입하는 파일입니다 (Claude Code의 `CLAUDE.md`에 해당).

탐색 순서 (뒤로 갈수록 우선순위가 높음):
1. `~/.nerdvana/NIRNA.md` (전역 사용자 지침)
2. `<cwd>/NIRNA.md` (프로젝트 지침, 저장소에 커밋)
3. `<cwd>/NIRNA.local.md` (로컬 지침, gitignore 대상)

REPL에서 `/init`으로 초안 파일을 만듭니다.

## 설정

### 환경 변수

제공자와 모델은 `--provider` / `--model` 플래그, `/provider`·`/model` 슬래시 명령어, 또는 설정 파일로 고릅니다.
둘 다 환경 변수 오버라이드가 없습니다. `NERDVANA_` 접두사는 위의 환경 변수 표에 적힌 스칼라 설정에만 닿습니다.

```bash
# 실행 위치와 동작
export NERDVANA_DATA_HOME="$HOME/.nerdvana"   # 사용자 데이터 루트
export NERDVANA_CONFIG="$HOME/.nerdvana/config.yml"
export NERDVANA_NO_UPDATE_CHECK=1             # 시작 시 판올림 확인 생략

# API 키 (제공자별 자동 감지)
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
export GEMINI_API_KEY="..."
export GROQ_API_KEY="gsk_..."
export OPENROUTER_API_KEY="sk-or-..."
export XAI_API_KEY="xai-..."
export DEEPSEEK_API_KEY="sk-..."
export MISTRAL_API_KEY="..."
export CO_API_KEY="co-..."
export TOGETHER_API_KEY="..."
export MOONSHOT_API_KEY="..."
export DASHSCOPE_API_KEY="..."
export MINIMAX_API_KEY="..."
export PERPLEXITY_API_KEY="..."
export FIREWORKS_API_KEY="..."
export CEREBRAS_API_KEY="..."

# 웹 검색 (없으면 WebSearch 도구가 호출 시점에 오류를 낸다)
export BRAVE_API_KEY="..."
```

### 설정 파일 (`nerdvana.yml`)

```yaml
model:
  provider: anthropic              # 또는 openai, gemini, groq, ollama, zai 등
  model: claude-sonnet-4-20250514
  api_key: ""                      # 환경 변수 사용 시 비워둠
  base_url: ""                     # API 엔드포인트 재정의
  max_tokens: 8192
  temperature: 1.0
  fallback_models:                 # Phase C — 주 모델 장애 시 순차 폴백
    - claude-haiku-4-5-20251001
    - gpt-4.1
  extended_thinking: false         # 확장 사고 모드 활성화 여부
  thinking_budget: 8192            # 확장 사고에 할당할 토큰 예산
  show_thinking: true              # <think>...</think> 블록을 흐린 이탤릭으로 표시 (/thinking 로 토글)

# 제공자별 마지막 사용 모델. /model 과 /provider 가 자동으로 갱신한다.
model_history: {}

permissions:
  mode: default                    # default | accept-edits | bypass | plan
  always_allow: []
  always_deny: []

session:
  persist: true
  max_turns: 200
  max_context_tokens: 180000
  compact_threshold: 0.8           # 컨텍스트 사용률이 임계값 도달 시 자동 압축
  compact_max_failures: 3          # 압축 연속 실패 허용 횟수 (회로 차단기)
  planning_gate: false             # Phase C — 복잡도 기반 Plan 에이전트 선행 실행
  default_context: standalone      # 기본 런타임 컨텍스트 프로파일 이름
  default_mode: interactive        # 기본 런타임 모드 이름 (interactive, planning 등)
  show_activity: true              # ActivityIndicator 위젯 표시 (/activity 로 토글)
  update_check: true               # 시작 시 새 판 확인 (24시간 캐시)

checkpoint:
  enabled: true
  per_session_max: 50

parism:
  enabled: true
  config_path: ""
  format: json
  fallback_to_bash: true

hooks:                             # Phase C — 자동 복구 훅 파이프라인
  session_start:
    - builtin:context_injection
  before_tool: []
  after_tool: []
  # <cwd>/.nerdvana/hooks/*.py 실행 허용. 켜는 것만으로는 부족하고
  # ~/.nerdvana/trusted_hooks.json 에 기록된 SHA-256 해시와도 일치해야 한다.
  allow_project_hooks: false

# ListQueryableProjects / RegisterExternalProject / QueryExternalProject 등록.
# 등록된 디렉토리를 읽기 가능한 서브프로세스에 넘기므로 기본은 꺼둔다.
external_projects_enabled: false
```

설정 검색 순서. 먼저 존재하는 파일 하나만 읽습니다:
1. `--config` 플래그
2. `NERDVANA_CONFIG` 환경 변수
3. `./nerdvana.yml` (현재 디렉토리)
4. `./nerdvana.yaml` (현재 디렉토리)
5. `~/.nerdvana/config.yml`
6. `~/.config/nerdvana-cli/config.yml` (마이그레이션 이전 위치, 하위 호환을 위해 계속 읽음)

## 로컬 모델 (Ollama / vLLM)

```bash
# Ollama — 먼저 모델 다운로드
ollama pull qwen3
nerdvana --provider ollama --model qwen3

# vLLM — 먼저 서버 시작
vllm serve Qwen/Qwen3-32B
nerdvana --provider vllm --model Qwen/Qwen3-32B
```

## 개발

```bash
# 개발 의존성 설치
pip install -e ".[dev]"

# 테스트 실행
pytest

# 린트
ruff check nerdvana_cli/

# 타입 체크
mypy nerdvana_cli/
```

## 문서

| 문서 | 설명 |
|-|-|
| [docs/configuration.md](docs/configuration.md) | 설정 전체 레퍼런스 |
| [docs/hooks.md](docs/hooks.md) | 훅 이벤트 체계와 브리지 규약 |
| [docs/agents.md](docs/agents.md) | 에이전트 타입, 도구 예산, 스웜 패턴 |
| [docs/mcp-quota.md](docs/mcp-quota.md) | MCP 서버 테넌트별 쿼터 스키마 |
| [docs/testing-live.md](docs/testing-live.md) | 실 제공자 시험 행렬과 비밀값 설정 |
| [CHANGELOG.md](CHANGELOG.md) | 판올림 기록 |

## 라이선스

MIT

## 작성자

최진호 (jinho@nerdvana.kr)
