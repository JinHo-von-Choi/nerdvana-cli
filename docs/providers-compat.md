# Provider Compatibility

Author: 최진호
Created: 2026-10-03

`nerdvana` names 21 providers and talks to them through three adapters. This page states what each adapter and each family supports. Every row is a fact read from the code in `nerdvana_cli/providers/` and pinned by a test in `tests/providers/` (mainly `test_provider_contract.py`, which replays one scenario against every adapter, and `test_openai_responses.py`). Where a fact could only be checked against the vendor documentation and not against a live API, the last section says so.

## Which adapter serves which provider

| Adapter | Class | Providers |
|-|-|-|
| Anthropic | `AnthropicProvider` | `anthropic` |
| Gemini | `GeminiProvider` | `gemini` |
| OpenAI Chat Completions | `OpenAIProvider` | the 19 others: `openai`, `groq`, `openrouter`, `xai`, `ollama`, `vllm`, `deepseek`, `mistral`, `cohere`, `together`, `zai`, `featherless`, `xiaomi_mimo`, `moonshot`, `dashscope`, `minimax`, `perplexity`, `fireworks`, `cerebras` |
| OpenAI Responses | `OpenAIResponsesProvider` | `provider: openai` on `https://api.openai.com/v1` (or an empty `base_url`) when `model.openai_api` is `auto`; any OpenAI-compatible provider when it is `responses` |

`model.openai_api: chat` forces Chat Completions everywhere. An unregistered provider name falls back to the Chat Completions adapter.

## Adapter matrix

| Capability | Anthropic | OpenAI Chat Completions | OpenAI Responses | Gemini |
|-|-|-|-|-|
| Streaming | `messages.create(stream=True)` | `chat.completions.create(stream=True)` with `stream_options.include_usage`; resent without it when the server answers 400 or 422 or the client raises `TypeError` | `responses.create(stream=True)` | `generate_content_stream` |
| Events yielded | `content_delta`, `thinking_delta`, `provider_block`, `tool_use_start`, `tool_use_delta`, `tool_use_complete`, `usage`, `done`, `error` | `content_delta`, `thinking_delta`, `tool_use_complete`, `usage`, `done`, `error` | same as Chat Completions plus `provider_block` | `content_delta`, `tool_use_complete`, `usage`, `done`, `error` |
| Tool declaration | `name`, `description`, `input_schema` | nested `{"type": "function", "function": {...}}` | flat `{"type": "function", "name", "description", "parameters", "strict": false}` | `FunctionDeclaration` |
| Tool call assembly | `input_json_delta` fragments joined at `content_block_stop` | argument fragments joined per call slot; a new id on a used index opens a new slot, so a reused index does not merge two calls | whole call taken from `response.output_item.done`; a repeated item is reported once | the whole call arrives in one part |
| Unparseable arguments | empty input | empty input | empty input | not applicable |
| Tool call id | the server's `tool_use` id | the server's id | the server's `call_id` | minted client side as `call_<name>_<8 hex>`, unique per call |
| Usage report | one `usage` event after the content, before `done` | one `usage` event, the last non-empty report of the stream | one `usage` event from the terminal event | one `usage` event, the last cumulative report |
| `input_tokens` meaning | whole prompt (fresh plus cache writes plus cache reads) | whole prompt | whole prompt | whole prompt |
| Cached tokens | `cache_read_tokens` and `cache_write_tokens` | `cache_read_tokens` from `prompt_tokens_details.cached_tokens`, or `prompt_cache_hit_tokens` (DeepSeek) | `cache_read_tokens` from `input_tokens_details.cached_tokens`; cache writes are not mapped | `cache_read_tokens` from `cached_content_token_count` |
| No usage from the server | no `usage` event | estimated at four characters per token when text streamed, nothing for a call-only reply | no `usage` event | no `usage` event |
| Stop reason | the server's `stop_reason`, default `end_turn` | `stop` and `tool_calls` become `end_turn` or `tool_use` (calls decide), `length` becomes `max_tokens`, other values pass through | `tool_use` when calls were returned, `max_tokens` for an incomplete response, otherwise the incomplete reason or `end_turn` | `tool_use` when calls were returned, else `end_turn`; the finish reason is not read, so a truncated reply ends as `end_turn` |
| Error classification | `classify_exception`: status, SDK class name, timeouts, text | same | same, plus `response.failed` and `error` events by error code | same, from the exception `code` |
| `Retry-After` | read from the response headers | read from the response headers | read from the response headers (request failures) | not available |
| Image input | base64 `image` source in a user block list | `image_url` data URL parts in a user block list | `input_image` data URL parts | `inlineData` bytes in a user block list |
| Tool results | text only | text only | text only (`function_call_output`) | text only (`functionResponse`) |
| `reasoning_effort` | ignored | top-level `reasoning_effort`, sent as written on every request when set | `reasoning.effort`; also `reasoning.summary: auto` unless `show_thinking` is off, `include: ["reasoning.encrypted_content"]`, and no `temperature` unless the effort is `none` | `thinking_config.thinking_level`: `minimal`, `low`, `medium`, `high`; any other value stops the request with an error event |
| Other thinking settings | `extended_thinking`, `thinking_budget`, `show_thinking` per model family (`request_options`) | none | none | none |
| Thinking text | `thinking_delta` from thinking blocks | `thinking_delta` from `<think>` tags in the content; `reasoning_content` fields are not read | `thinking_delta` from reasoning summary and reasoning text events, and from `<think>` tags | none requested, none shown |
| Replayed provider blocks | `thinking` and `redacted_thinking` blocks, unchanged and first in the turn; blocks of other types are dropped | none | `reasoning` items that carry `encrypted_content`, before the turn's text and calls; other block types are dropped | none |
| Statefulness | stateless | stateless | stateless: full input every turn, `store: false`, no `previous_response_id` | stateless |
| Prompt caching | explicit breakpoints on the last tool, the system prompt and the last block | automatic on the server | automatic on the server | automatic on the server |
| Output token limit field | `max_tokens` | `max_tokens` (not `max_completion_tokens`) | `max_output_tokens` | `max_output_tokens` |
| Temperature | omitted for models with fixed sampling | always sent | omitted when a reasoning effort other than `none` is set | always sent |
| Non-streaming `send` | supported, returns `provider_blocks` | supported | supported, returns `provider_blocks` | supported |

Failures are classified into `retryable` (408, 409, 425, 429, 5xx, 529, timeouts, connection errors), `auth` (401, 403), `context_limit` (413, or 400 with a context phrase in the message), `decode` (encoding failures) and `other`. The agent loop retries `retryable` failures with backoff, honours `Retry-After`, and reacts to `context_limit` and `auth` separately. `send()` results carry the error text only, without a kind.

## Duplicate tool ids

An adapter reports each call once and never reuses an id inside one response. Across turns a server can still repeat an id, so the agent loop repairs the history before every request (`core/tool_ids.py`): a repeated or empty id gets a fresh one on the call and on the result that answers it. The Responses adapter pairs a call with its result by `call_id` only and does not send the item `id`, so a repaired id stays consistent.

## OpenAI-compatible families

All 19 providers below use the Chat Completions adapter unless `model.openai_api` or the OpenAI rule above selects Responses. The capability columns come from `nerdvana_cli/providers/variants.yml`. They are descriptive: `/setup` and the providers table print them, and nothing in the request path reads them, so tools, streaming and images are sent whatever the flags say. The two providers marked without tools (`featherless`, `perplexity`) therefore still receive tool declarations.

| Provider | Default endpoint | Tools | Streaming | Vision | Thinking | Context |
|-|-|-|-|-|-|-|
| `openai` | `https://api.openai.com/v1` | yes | yes | yes | no | 1048576 |
| `groq` | `https://api.groq.com/openai/v1` | yes | yes | no | no | 32768 |
| `openrouter` | `https://openrouter.ai/api/v1` | yes | yes | yes | no | 200000 |
| `xai` | `https://api.x.ai/v1` | yes | yes | no | no | 131072 |
| `ollama` | `http://localhost:11434/v1` | yes | yes | yes | no | 131072 |
| `vllm` | `http://localhost:8000/v1` | yes | yes | no | no | 131072 |
| `deepseek` | `https://api.deepseek.com` | yes | yes | no | yes | 65536 |
| `mistral` | `https://api.mistral.ai/v1` | yes | yes | yes | no | 128000 |
| `cohere` | `https://api.cohere.com/v2` | yes | yes | no | no | 128000 |
| `together` | `https://api.together.xyz/v1` | yes | yes | no | no | 131072 |
| `zai` | `https://api.z.ai/api/coding/paas/v4/` | yes | yes | no | no | 128000 |
| `featherless` | `https://api.featherless.ai/v1` | no | no | no | no | 32768 |
| `xiaomi_mimo` | `https://token-plan-sgp.xiaomimimo.com/v1` | yes | yes | yes | yes | 1048576 |
| `moonshot` | `https://api.moonshot.ai/v1` | yes | yes | no | no | 131072 |
| `dashscope` | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1` | yes | yes | yes | yes | 1000000 |
| `minimax` | `https://api.minimaxi.chat/v1` | yes | yes | yes | no | 1000000 |
| `perplexity` | `https://api.perplexity.ai` | no | yes | no | no | 200000 |
| `fireworks` | `https://api.fireworks.ai/inference/v1` | yes | yes | no | no | 131072 |
| `cerebras` | `https://api.cerebras.ai/v1` | yes | yes | no | no | 65536 |

Provider specific handling in the Chat Completions adapter is limited to the DeepSeek cache field above and the `stream_options` retry. Everything else is the OpenAI wire format as the server implements it, so a server that rejects `reasoning_effort`, `max_tokens` or `temperature` surfaces that rejection as the provider error.

## OpenAI limits to know

- From GPT-5.4 on, OpenAI's Chat Completions endpoint does not support tool calling with a `reasoning_effort` other than `none`, and GPT-6 Astra and GPT-6.1 Sol need the Responses API for tool calling. This is why `openai_api: auto` sends OpenAI's own endpoint to the Responses API. With `openai_api: chat` and a set effort the adapter logs a warning once per provider and appends the explanation to a 400 or 422 refusal.
- The Chat Completions adapter sends `max_tokens`. OpenAI's newer reasoning models expect `max_completion_tokens` on that endpoint; the Responses adapter has no such issue.
- The Responses adapter does not carry the assistant message `phase` field and does not use `previous_response_id`, `context_management` or `tool_search`.

## What is verified how

Verified by tests that replay hand-written payloads (no network): every row above for the Anthropic, Chat Completions and Gemini adapters, the Responses request fields, the Item conversion (checked against the openai SDK's own input types), the event to `ProviderEvent` mapping, the usage mapping, the `store: false` request, stateless replay of reasoning items through the session transcript, and the error classification.

Checked only against the OpenAI documentation and the typed models of the installed openai SDK, never against a live API:

- that the live Responses stream emits exactly the events and fields the adapter reads, and that it terminates with `response.completed`, `response.incomplete` or `response.failed`;
- that a replayed reasoning item (with `id` and `encrypted_content`) followed by a `function_call` item without an `id` is accepted when `store` is `false`;
- that `include: ["reasoning.encrypted_content"]` and `reasoning.summary: auto` are accepted by every reasoning model for every organization (summaries have required a verified organization on some models; turn `show_thinking` off if a request is refused for that);
- that omitting `temperature` is required, and `strict: false` accepted, for the models in use;
- that older reasoning models return encrypted reasoning only when a reasoning effort is set, since the adapter requests it only then.
