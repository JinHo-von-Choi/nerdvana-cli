# NerdVana CLI

AI 기반 CLI 개발 도구 — Anthropic Claude, OpenAI, Google Gemini, Groq, Ollama 등 **12개 AI 플랫폼**을 지원합니다.

## 기능

- **다중 제공자 지원** — 하나의 CLI로 12개 AI 플랫폼 사용 가능
- **대화형 REPL** — 스트리밍 출력과 함께 대화형 코딩 지원
- **비대화형 모드** — 스크립팅을 위한 단일 프롬프트 실행
- **도구 시스템** — Bash, FileRead, FileWrite, FileEdit, Glob, Grep
- **세션 지속성** — 재개를 위한 JSONL 트랜스크립트 저장
- **자동 제공자 감지** — 모델 이름에서 적절한 제공자 선택
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
| **vLLM** | Qwen/Qwen3-32B | `OPENAI_API_KEY` |
| **DeepSeek** | deepseek-chat | `DEEPSEEK_API_KEY` |
| **Mistral** | mistral-medium-latest | `MISTRAL_API_KEY` |
| **Cohere** | command-r-plus | `CO_API_KEY` |
| **Together AI** | Llama-3.3-70B-Instruct-Turbo | `TOGETHER_API_KEY` |

## 설치

```bash
# 모든 제공자와 함께 설치
cd ~/job/nerdvana-cli
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

## 명령어

| 명령어 | 설명 |
|--------|------|
| `nerdvana` | 대화형 REPL 시작 |
| `nerdvana run "프롬프트"` | 단일 프롬프트 실행 |
| `nerdvana providers` | 지원하는 모든 제공자 목록 보기 |
| `nerdvana --version` | 버전 정보 표시 |

### REPL 슬래시 명령어

| 명령어 | 설명 |
|--------|------|
| `/help` | 도움말 표시 |
| `/quit` | REPL 종료 |
| `/model` | 현재 모델 표시 |
| `/model <이름>` | 모델 변경 (제공자 자동 감지) |
| `/provider` | 현재 제공자 표시 |
| `/provider <이름>` | 제공자 변경 |
| `/providers` | 지원하는 모든 제공자 목록 보기 |
| `/tokens` | 토큰 사용량 표시 |
| `/clear` | 대화 내용 지우기 |
| `/session` | 세션 정보 표시 |
| `/tools` | 사용 가능한 도구 목록 보기 |
| `/verbose` | 상세 모드 전환 |

## 내장 도구

| 도구 | 유형 | 설명 |
|------|------|------|
| Bash | 쓰기 | 셸 명령어 실행 |
| FileRead | 읽기 | 파일 내용 읽기 |
| FileWrite | 쓰기 | 파일 생성/덮어쓰기 |
| FileEdit | 쓰기 | 파일 내 문자열 교체 |
| Glob | 읽기 | 파일 패턴 매칭 |
| Grep | 읽기 | 정규식으로 내용 검색 |

## 설정

### 환경 변수

```bash
# 제공자 선택
export NERDVANA_PROVIDER="anthropic"  # 또는 openai, gemini, groq 등
export NERDVANA_MODEL="claude-sonnet-4-20250514"
export NERDVANA_MAX_TOKENS=8192

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
```

### 설정 파일 (`nerdvana.yml`)

```yaml
model:
  provider: anthropic  # 또는 openai, gemini, groq, ollama 등
  model: claude-sonnet-4-20250514
  api_key: ""  # 환경 변수 사용 시 비워둠
  base_url: ""  # API 엔드포인트 재정의
  max_tokens: 8192
  temperature: 1.0

permissions:
  mode: default
  always_allow: []
  always_deny: []

session:
  persist: true
  max_turns: 200
  max_context_tokens: 180000
  compact_threshold: 0.8
```

설정 검색 순서:
1. `--config` 플래그
2. `NERDVANA_CONFIG` 환경 변수
3. `./nerdvana.yml` (현재 디렉토리)
4. `~/.config/nerdvana-cli/config.yml`

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

## 라이선스

MIT

## 작성자

최진호 (jinho@nerdvana.kr)
