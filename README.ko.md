# NerdVana CLI

AI 기반 CLI 개발 도구 — Anthropic Claude, OpenAI, Google Gemini, Groq, Ollama 등 **21개 AI 플랫폼**을 지원합니다.

판올림 1.6.0 · Python 3.11 이상 · MIT 라이선스 · [English README](README.md)

## 기능

- **다중 제공자 지원** — 하나의 CLI로 21개 AI 플랫폼 사용 가능
- **대화형 REPL** — 스트리밍 출력과 슬래시 명령어, 토큰 사용량 표시기
- **비대화형 모드** — 스크립팅을 위한 단일 프롬프트 실행 (`nerdvana run`)
- **30개 내장 도구**: 파일 I/O, 검색, 셸, 웹, 작업 목록, MCP 외에 LSP·심볼·서브에이전트·스웜·태스크 관리 도구
- **편집 무결성**: `FileRead`가 각 줄에 `N#hhhhhh`(줄 번호와 내용 해시)를 붙입니다. `FileEdit`와 `FileWrite`는 세션에서 읽지 않았거나 읽은 뒤 바뀐 파일을 고치지 않으며, 자기 편집으로 밀린 앵커는 다시 찾아 맞춥니다. `find_symbol`에 `include_body`를 주면 심볼 소스가 앵커와 함께 나오고, 그 줄들은 파일 전체를 읽지 않고도 고칠 수 있습니다. 편집 직후 언어 서버가 그 편집으로 새로 생긴 오류를 알려 줍니다.
- **서브에이전트와 백그라운드 작업**: `Agent` / `Swarm`이 서브에이전트를 각자의 시스템 프롬프트, 턴 한도, 도구 범위로 실행합니다. 제공자당 동시 실행 수는 `session.max_parallel_agents`로 묶입니다. 백그라운드 작업이 끝나면 결과가 모델에게 자동으로 전달되고, 쉬고 있던 세션은 스스로 깨어나 결과를 검토합니다.
- **복구와 완주**: 제공자 실패를 분류해 백오프로 재시도한 뒤 다른 모델이나 제공자로 넘어갑니다. 컨텍스트 초과는 압축 후 재시도하고, 멈춘 스트림은 시간 제한으로 끊습니다. 열린 todo 항목이 있으면 진척이 멈출 때까지 작업을 이어 갑니다. [자동 복구](#자동-복구) 참고.
- **권한 정책**: `--approval-mode`, `permissions.mode`, `always_allow`, `always_deny`가 서브에이전트를 포함한 모든 도구 호출에 적용됩니다. [권한과 승인 모드](#권한과-승인-모드) 참고.
- **되묻기**: `AskUser` 도구로 모델이 추측 대신 사용자에게 묻습니다. 에이전트가 일하는 동안 입력한 글은 다음 단계에 반영되고, 파일 변경 확인에는 diff가 표시됩니다.
- **사용자 명령과 훅**: 마크다운 명령 템플릿과 셸 명령 훅. [사용자 명령과 명령 훅](#사용자-명령과-명령-훅) 참고.
- **Claude Code 호환 지침**: 루트의 `AGENTS.md`와 `CLAUDE.md`를 `NIRNA.md` 다음에 읽고, 하위 디렉터리의 규칙 파일은 그 안의 파일을 처음 다룰 때 주입합니다. 스킬은 `SKILL.md` 디렉터리 형식도 지원합니다.
- **실시간 활동 표시기 + 추론 태그 렌더링** — DeepSeek-R1, QwQ, Qwen3-thinking, GLM, Kimi K2.5 thinking, MiniMax M2 가 보내는 `<think>...</think>` 블록을 dim italic 으로 분리 표시하고, `ActivityIndicator` 위젯이 현재 phase(idle / thinking / waiting_api / streaming / tool_running)와 활성 도구 대상을 보여줍니다.
- **시작 시 업데이트 알림**: 실행할 때마다 GitHub 릴리즈를 확인해 새 판이 있으면 한 줄로 알린다 (24시간 캐시). `--no-update-check`, `NERDVANA_NO_UPDATE_CHECK=1`, `nerdvana.yml`의 `session.update_check: false`로 끈다.
- **세션 지속성**: JSONL 트랜스크립트를 저장하고 `nerdvana session resume <id>`로 대화를 복원합니다
- **자동 제공자 감지** — 모델 이름에서 적절한 제공자 자동 선택
- **MCP 통합** — 외부 MCP 서버를 연결하여 도구 시스템 확장
- **구성 가능** — YAML 설정, 환경 변수, CLI 플래그

## 지원하는 제공자

| 제공자 | 기본 모델 | API 키 환경 변수 |
|--------|-----------|------------------|
| **Anthropic** | claude-sonnet-5-5 | `ANTHROPIC_API_KEY` |
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

### PyPI 설치

패키지가 PyPI에 게시된 이후부터 사용할 수 있습니다. 그 전에는 위의 한 줄 설치를 사용하십시오.

```bash
uv tool install nerdvana-cli
# 또는
pipx install nerdvana-cli

# 모든 제공자 SDK 포함
uv tool install "nerdvana-cli[all]"
```

이전 릴리스로 되돌리려면 버전을 고정해 다시 설치합니다.

```bash
uv tool install nerdvana-cli==<이전-버전>
# 또는
pipx install --force nerdvana-cli==<이전-버전>
```

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
nerdvana --provider anthropic --model claude-opus-5-5
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

## 사용자 명령과 명령 훅

**명령.** `~/.nerdvana/commands/` 또는 `<프로젝트>/.nerdvana/commands/` 아래의 마크다운 파일이 슬래시 명령이 됩니다. `review.md`는 `/review`, `git/commit.md`는 `/git:commit`입니다. 입력하면 파일 내용이 프롬프트로 전송되고, `$ARGUMENTS`는 명령 뒤에 친 전체 텍스트로, `$1`~`$9`는 각 단어로 바뀝니다(공백이 든 단어는 따옴표로 묶음). 자리표시자가 없으면 인자를 본문 뒤에 붙입니다. YAML 머리말의 `description:`은 명령 메뉴에 표시됩니다. 같은 이름이면 프로젝트 파일이 전역 파일을 대체하고, 내장 명령과 스킬이 항상 우선합니다.

**명령 훅.** `~/.nerdvana/hooks.yml`과 `<프로젝트>/.nerdvana/hooks.yml`이 에이전트 이벤트마다 셸 명령을 실행합니다.

```yaml
hooks:
  - event: before_tool        # before_tool, after_tool, session_start, session_end
    match: "Bash"             # 도구 이름 glob (before_tool, after_tool), 기본 "*"
    command: "scripts/check-command.sh"
    timeout: 5                # 초, 1~30
```

명령은 프로젝트 디렉터리에서 실행되고 stdin으로 JSON 객체 하나(`event`, `tool_name`, `tool_input`, `cwd`, `after_tool`이면 `tool_result`)를 받으며, `NERDVANA_HOOK_EVENT`와 `NERDVANA_TOOL_NAME`이 설정됩니다. 종료 코드 `0`은 계속 진행합니다. 종료 코드 `2`는 `before_tool` 호출을 막고 명령의 출력을 모델에게 알리며, `after_tool`이면 출력을 메시지로 모델에게 전달합니다. 그 밖의 종료 코드, 시간 초과, 시작하지 못한 명령은 기록만 하고 무시하므로 고장 난 훅이 에이전트를 멈추지 않습니다. 훅이 도는 동안 에이전트가 기다리므로 빠르게 유지하세요.

프로젝트의 `hooks.yml`은 저장소에 딸린 셸 명령을 실행하므로 프로젝트 파이썬 훅과 같은 규칙을 따릅니다. `hooks.allow_project_hooks: true`와 승인된 해시(`nerdvana hook trust <경로>`)가 필요하고, 파일을 고치면 승인이 취소됩니다.

## 헤드리스 실행

`nerdvana run`은 프롬프트 하나를 실행하고 끝납니다. 스크립트, CI, 에이전트를 내장하는 프로그램에서 쓰는 방식입니다.

```bash
nerdvana run "실패하는 테스트를 고쳐" --approval-mode yolo --max-turns 30 --max-cost-usd 2 --output-format json
```

| 옵션 | 의미 |
|-|-|
| `--output-format text\|json\|stream-json` | `text`(기본)는 사람이 읽는 스트림입니다. `json`은 끝에 결과 객체 하나를 출력합니다. `stream-json`은 한 줄에 이벤트 하나를 출력하고 같은 결과 객체로 끝납니다. 두 JSON 형식에서 stdout에는 JSON만 나가고 안내 문구는 stderr로 갑니다. |
| `--max-turns N` | 모델 턴이 N번이 되면 멈춥니다. |
| `--max-total-tokens N` | 모든 요청의 입력과 출력 토큰 합이 N에 이르면 멈춥니다. 가격을 몰라도 모든 모델에서 동작합니다. |
| `--image PATH` | 프롬프트에 이미지(PNG, JPEG, GIF, WebP, 5 MB 이하, 최대 6개)를 첨부합니다(반복 가능). 형식은 파일 첫 바이트로 판단하며, 이미지를 받지 못하는 모델은 제공자의 오류로 답합니다. |
| `--set section.field=value` | 이 실행에서만 설정 하나를 덮어씁니다(반복 가능, 값은 YAML로 읽음). 예: `--set session.compact_threshold=0.5`. `permissions`, `hooks`, `sandbox` 섹션은 이 방법으로 바꿀 수 없고 각자의 옵션을 씁니다. |
| `--scope PATH` | `--verify` 와 함께: 작업이 다루는 경로(반복 가능). 그 밖의 편집은 먼저 묻고, 물을 사람이 없으면 거부합니다. |
| `--verify COMMAND` | 작업이 끝났는지 판정하는 명령입니다. 모델이 끝났다고 하면 이 명령을 실행하고, 종료 코드가 0이 아니면 출력의 끝부분을 모델에 돌려주어 계속 일하게 합니다. 통과하거나, `--verify-attempts N`번 실패하거나(기본 `goal.max_attempts`, 5), 턴·비용 한도에 이르면 끝납니다. 결과에 `verification` 객체가 붙습니다. |
| `--sandbox off\|auto\|require` | 이 실행에서 셸 명령의 쓰기 범위를 제한하며 `sandbox.mode` 보다 우선합니다([docs/sandbox.md](docs/sandbox.md)). |
| `--require-price` | `--max-cost-usd` 를 줬는데 모델의 가격을 모르면 실행을 거부합니다. |
| `--max-cost-usd X` | 실행의 추정 비용이 X달러에 이르면 멈춥니다(모델의 가격을 알아야 동작). |
| `--approval-mode default\|auto_edit\|yolo\|plan` | 권한 프리셋입니다. 터미널이 없으면 확인 요청이 거부되므로, 파일을 쓰는 무인 실행은 대개 `yolo`가 필요합니다. |

종료 코드: `0` 성공, `1` 실행 실패(제공자 오류, 예기치 않은 오류), `2` 옵션이나 설정 오류(API 키 없음 포함), `3` 턴, 비용, 토큰 또는 검증 한도로 중단.

결과 객체(`schema_version` 1, 필드는 추가만 하고 바꾸지 않음):

```json
{"type": "result", "schema_version": 1, "subtype": "success", "is_error": false,
 "result": "최종 답변", "session_id": "ab12cd34", "provider": "anthropic",
 "model": "claude-sonnet-5-5", "num_turns": 4, "duration_ms": 18234, "total_cost_usd": 0.0421,
 "usage": {"input_tokens": 51230, "output_tokens": 2210, "cache_read_tokens": 38000, "cache_write_tokens": 9000},
 "signals": {"cas_rejected": 1, "new_diagnostics": 2}}
```

`receipt`(파일을 바꿨거나 `--verify` 목표가 있었을 때만)는 주장 옆에 증거를 둡니다: `files_changed`(파일별 적용된 편집 수), `verification`, `sandbox` 정책, `cost_by_agent`(에이전트 종류별 요청 수와 USD, 서브에이전트 포함), `problems`(오래된 파일 편집 거부, 새 언어 서버 오류, 거부된 반복, 범위 밖 편집, 가려진 비밀, 실패한 검증, 승급의 횟수). 실행이 직접 측정한 값으로 만들고 모델은 아무것도 쓰지 않습니다. `receipt_version`은 1입니다. `signals`는 실행 중 무엇이 잘못됐는지를 종류별로 센 값입니다(`cas_rejected`, `repeat_refused`, `new_diagnostics`, `invalid_input`, `permission_denied_user`, `sandbox_denied`, `tool_error`, `todo_nudge`, `provider_retry`, `provider_fallback`, `compaction` 등). 일어나지 않은 종류는 없습니다. `subtype`은 `success`, `error_max_turns`, `error_max_cost`, `error_max_total_tokens`, `error_goal_unmet`, `error_unpriced`, `error_max_tokens`, `error_provider`, `error_during_run`, `error_config` 중 하나이고, 오류 결과에는 원인을 아는 경우 `error` 문자열이 붙습니다. `result`는 모델이 마지막 도구 호출 뒤에 쓴 텍스트입니다.

`stream-json`은 결과 앞에 이벤트를 한 줄씩 냅니다. `system`(subtype `init`, 세션 id·제공자·모델), `text`(답변 조각), `notice`(재시도나 폴백 같은 에이전트 자체 안내), `tool_start`(`name`, `summary`), `tool_done`(`name`, `is_error`), `request`(요청 하나의 `provider`, `model`, `agent_type`, `turn`, `last_tool`, 캐시 토큰을 포함한 토큰 수, `cost_usd`), `compaction`, `context`(창 사용률)입니다.

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
| `nerdvana review` | 작업 트리를 git ref 와 비교해, 바뀐 함수와 그것을 쓰는 줄에서 출발하는 읽기 전용 에이전트로 리뷰합니다(`--base`, `--context-only`, `--fail-on`). [docs/review.md](docs/review.md) |
| `nerdvana approvals` | 계속 승인하는 권한 질문에 대해 `always_allow` 규칙(`Bash(git status)`)을 제안합니다. 설정은 바뀌지 않습니다 |
| `nerdvana cost` | 지정 기간의 토큰·캐시 토큰 사용량과 USD 비용 집계 (요청마다 보고된 사용량 기준). `--by provider\|model\|agent\|category\|tool` 로 비용이 어디에 쓰였는지 봅니다 |

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
| `nerdvana hook trust <경로>` | 프로젝트 로컬 훅을 승인해 실행 가능하게 함 |
| `nerdvana hook revoke <경로>` | 프로젝트 로컬 훅의 승인 기록 해제 |
| `nerdvana hook trusted` | 승인된 프로젝트 훅 목록과 내용이 바뀐 항목 표시 |

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
| `/rewind` | `/rewind [N]` 은 마지막 N개 프롬프트 이전으로 되돌립니다. 그 메시지는 버려지고 편집 도구가 한 편집은 취소됩니다(셸 명령이 바꾼 것은 제외) |
| `/redo` | 마지막으로 되돌린 체크포인트 재적용 |
| `/checkpoints` | 세션 체크포인트 목록 표시 |
| `/route-knowledge` | 콘텐츠를 분류하여 WriteMemory 스코프 제안 |
| `/dashboard` | 관찰 가능성 대시보드 토글 |
| `/health` | 7일간 도구 호출 건강 요약 표시 |
| `/image` | `/image <경로> [<경로> ...] <질문>` 은 앞쪽의 이미지 파일을 첨부해 질문합니다. 기록에는 그림이 아니라 파일 이름만 남습니다 |
| `/btw` | `/btw <질문>` 은 대화를 맥락으로 곁질문을 합니다. 질문도 답도 이력에 남지 않고, 요청의 캐시된 앞부분을 재사용합니다 |
| `/goal` | `/goal <목표> --verify <명령>` 은 에이전트가 끝났다고 할 때마다 명령을 실행하고, 종료 코드가 0이 될 때까지 실패를 돌려보냅니다. `/goal`, `/goal pause`, `/goal resume`, `/goal clear` |
| `/thinking` | 인라인 추론 표시 토글 (on/off, config.yml 에 저장) |
| `/activity` | 활동 표시기 위젯 토글 (on/off, config.yml 에 저장) |
| `/quit` | REPL 종료 (별칭: `/exit`, `/q`) |

## 내장 도구

레지스트리는 30개의 내장 도구를 조립합니다. 셸·파일·검색·작업 목록·웹·에이전트·태스크 도구는 항상 등록됩니다.
`Parism`은 번들된 Parism MCP 패키지에 접근할 수 있을 때 등록됩니다. LSP·심볼 도구는 호환 언어 서버가 설치된
경우에만 등록되며, 없으면 조용히 생략됩니다. 외부 프로젝트 도구 3종은 설정에서 `external_projects_enabled: true`
를 켜기 전까지 등록되지 않습니다.

| 도구 | 유형 | 설명 |
|------|------|------|
| `Bash` | 쓰기 | 셸 명령어 실행 (타임아웃, 작업 디렉토리, 환경 변수 지원) |
| `FileRead` | 읽기 | 파일 내용을 읽고 각 줄에 `N#hhhhhh`(줄 번호와 내용 해시)를 붙이며, 세션에 파일 다이제스트를 기록. 바이너리 파일은 종류와 크기만 알림 |
| `FileWrite` | 쓰기 | 새 파일 생성, 또는 이 세션에서 읽은 뒤 바뀌지 않은 파일 덮어쓰기 |
| `FileEdit` | 쓰기 | 문자열 교체 또는 앵커(`N#hhhhhh`) 기반 편집. 읽은 뒤 파일이 바뀌었으면 거부 |
| `Glob` | 읽기 | 파일 패턴 매칭 |
| `Grep` | 읽기 | 정규식 기반 콘텐츠 검색 |
| `TodoWrite` | 쓰기 | 에이전트가 수행할 작업 목록 관리 |
| `AskUser` | 메타 | 모호한 요구사항에서 사용자에게 선택지 2~4개와 자유 입력으로 질문. 사용자가 없는 실행(단발 실행, MCP 서버, 서브에이전트)에서는 오류 반환 |
| `WebFetch` | 읽기 | URL을 가져와 읽을 수 있는 본문으로 변환 |
| `WebSearch` | 읽기 | Brave Search 질의. `BRAVE_API_KEY`가 없으면 호출 시점에 오류 |
| `Parism` | 쓰기 | 화이트리스트된 44개 셸 명령어를 구조화된 JSON 출력으로 실행 |
| `Agent` | 오케스트레이션 | 서브에이전트를 전경 또는 백그라운드로 실행. 백그라운드면 `task_id` 반환 |
| `Swarm` | 오케스트레이션 | 여러 서브에이전트를 병렬로 발사하여 독립 작업을 분산 |
| `TaskGet` | 협업 | 백그라운드 작업의 상태와 결과 조회 (끝난 작업은 자동으로도 보고됨) |
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

`Agent` / `Swarm` 도구는 6개 빌트인 에이전트 타입 중 하나로 작업을 보냅니다. 타입마다 시스템 프롬프트, 턴 한도, 허용 도구 목록이 있고 모두 실제로 적용됩니다. `*`는 에이전트 생성, 작업 제어, `AskUser`를 뺀 세션의 모든 도구를, `@read`는 읽기 전용 도구 전부(LSP 조회, 심볼 조회, 웹 읽기)를 허용합니다.

| 에이전트 타입 | 최대 턴 | 허용 도구 | 용도 |
|-|-|-|-|
| `general-purpose` | 50 | `*` | 범용 에이전트 |
| `Explore` | 12 | `Glob`, `Grep`, `FileRead`, `@read` | 코드베이스 탐색 전용, 파일 수정 불가 |
| `Plan` | 15 | `Glob`, `Grep`, `FileRead`, `@read` | 구현 계획 수립 전용, `planning_gate`와 함께 동작 |
| `code-reviewer` | 12 | `FileRead`, `Grep`, `Glob`, `@read` | 코드 품질과 정확성 검토, 읽기 전용 |
| `git-management` | 20 | `Bash`, `FileRead` | git 작업 전담 |
| `test-writer` | 30 | `*` | 테스트 작성과 실행 |

서브에이전트는 세션의 언어 서버와 MCP 연결을 함께 쓰고, 같은 권한 정책을 따르며, 다른 에이전트를 만들 수 없습니다. 진행 상황은 TUI 우측 `TaskPanel`에 표시되고, 백그라운드 작업이 끝나면 결과가 모델에게 전달됩니다. `TaskStop`으로 중단합니다.

## 자동 복구

| 장치 | 동작 |
|-|-|
| 제공자 복구 | 실패를 일시 오류(429, 5xx, 시간 초과), 컨텍스트 초과, 인증, 디코딩으로 분류합니다. 일시 오류는 백오프나 서버의 `Retry-After`에 따라 `model.max_retries`번 재시도한 뒤 `model.fallback_models`의 다음 항목으로 넘어갑니다(`provider:model`이면 제공자도 바꿈). 컨텍스트 초과는 한 번 압축한 뒤 다시 요청합니다. 응답 일부가 이미 출력됐으면 재시도하지 않습니다. |
| 스트림 시간 제한 | `session.stream_idle_timeout`초 동안 응답이 없거나 `session.stream_total_timeout`초를 넘기면 일시 오류로 처리합니다. |
| todo 가드 | 열린 `TodoWrite` 항목이 남은 채 턴이 끝나면 계속하라고 요청합니다. 세 번 연속 진척이 없으면 멈추고 남은 항목을 보고합니다. 압축 뒤에는 열린 항목을 다시 알려 줍니다. |
| `context_limit_recovery` | `max_tokens`로 끝나면 마지막 요청을 인용해 이어쓰기를 요청합니다. |
| `json_parse_recovery` | 도구 결과의 JSON 파싱이 실패하면 올바른 JSON을 요청합니다. |
| `ralph_loop_check` | `end_turn`에 `TODO`, `FIXME`, `NotImplemented`, `# 구현 필요`, `# 미구현` 표시가 남아 있으면 마무리를 요청합니다. 턴 종료 훅으로 이어지는 진행은 프롬프트당 세 번까지입니다. |
| 반복 호출 차단 | 같은 인자의 같은 도구 호출이 연속 세 번째면 경고하고, 다섯 번째면 거부합니다(`TaskGet` 폴링 제외). |
| 출력 상한 | 도구 결과는 약 30,000토큰(`WebFetch`는 10,000)으로 앞뒤를 남겨 자르고, 전문은 `~/.nerdvana/tool-output/`에 저장합니다. |

## 권한과 승인 모드

메인 루프, 서브에이전트, 백그라운드 에이전트의 모든 도구 호출은 다음 순서로 판정합니다.

1. `permissions.always_deny`(도구 이름, glob 패턴 가능): 거부
2. 현재 모드가 제외한 도구: 거부하고 모델에게도 숨김
3. 도구 자체의 거부(예: 차단된 셸 명령): 거부
4. `permissions.always_allow`: 묻지 않고 허용
5. 모드의 신뢰 수준: `strict`는 모든 쓰기 전에, `balanced`는 파괴적 도구와 확인이 필요한 도구 전에 묻고, `yolo`는 묻지 않음

| `--approval-mode` | 모드 프로필 | 신뢰 수준 | 효과 |
|-|-|-|-|
| `default` | `interactive` | balanced | 일반 대화형 사용 |
| `auto_edit` | `editing` | balanced | 같은 도구, 편집 중심 프롬프트 |
| `yolo` | `one-shot` | yolo | 확인 없음 |
| `plan` | `planning` | strict | 쓰기 도구와 `Bash` 숨김 |

`--approval-mode`나 `session.default_mode`가 없으면 `nerdvana.yml`의 `permissions.mode`(`default`, `accept-edits`, `bypass`, `plan`)가 같은 프로필을 고릅니다. 터미널이 없으면 확인 요청은 거부로 처리됩니다.

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

이어서 프로젝트 루트의 `AGENTS.md`와 `CLAUDE.md`를 읽습니다. 도구가 하위 디렉터리의 파일을 처음 다루면, 그 디렉터리부터 프로젝트 루트 사이에 있는 규칙 파일(`NIRNA.md`, `AGENTS.md`, `CLAUDE.md`)을 가까운 순서로 한 번씩 주입합니다. 세션당 상한은 32KB입니다.

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
  model: claude-sonnet-5-5
  api_key: ""                      # 환경 변수 사용 시 비워둠
  base_url: ""                     # API 엔드포인트 재정의
  max_tokens: 8192
  temperature: 1.0
  max_retries: 2                   # 일시 오류 시 같은 모델 재시도 횟수
  prompt_caching: true             # 처리된 프롬프트 앞부분을 요청 사이에 재사용
  fallback_models:                 # 재시도를 다 쓰면 순서대로 사용
    - claude-haiku-4-5-20251001
    - openai:gpt-4.1               # provider:model 이면 제공자도 전환
  extended_thinking: false         # 선택 사항인 Anthropic 모델에서 사고 기능 켜기 (Claude 5 계열은 기본으로 사고)
  thinking_budget: 8192            # 수동 예산을 받는 모델(Haiku 4.5)의 사고 토큰 예산
  show_thinking: true              # 사고 요약을 요청하고 <think>...</think> 블록을 흐린 이탤릭으로 표시 (/thinking 로 토글)

# 제공자별 마지막 사용 모델. /model 과 /provider 가 자동으로 갱신한다.
model_history: {}

permissions:
  mode: default                    # default | accept-edits | bypass | plan
  always_allow: []                 # 도구 이름 또는 glob 패턴, 예: ["FileRead", "lsp_*", "Bash(git status)"]
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
  stream_idle_timeout: 300         # 제공자 응답이 이 초 동안 없으면 요청 포기
  stream_total_timeout: 3600
  post_edit_diagnostics: true      # 편집 후 언어 서버로 새 오류 확인
  max_parallel_agents: 5           # 제공자당 동시 서브에이전트 수

skills:
  include_claude_skills: false     # ~/.claude/skills, ./.claude/skills 도 읽기

agents:
  categories: {}                   # 서브에이전트 카테고리별 모델 (예: quick: claude-haiku-4-5-20251001)

goal:
  verify_timeout: 300              # 검증 명령을 중단하기까지의 초
  max_attempts: 5                  # 목표를 포기하기까지 허용하는 검증 실패 횟수
  output_tail_chars: 4000          # 실패한 출력 중 모델에 보여주는 끝부분의 길이

sandbox:
  mode: off                        # off | auto | require: OS 수준으로 Bash 쓰기 범위 제한 (Linux Landlock)
  network: true                    # false 면 TCP 연결도 차단 (Linux 6.7 이상)
  write_paths: []                  # 프로젝트·임시 디렉터리 외에 쓰기를 허용할 경로

checkpoint:
  enabled: true
  per_session_max: 50

parism:
  enabled: true
  config_path: ""
  format: json
  fallback_to_bash: true

hooks:
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
