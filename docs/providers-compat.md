# Provider Compatibility

Author: 최진호
Created: 2026-10-03

`nerdvana` names 21 providers and talks to them through three adapters. This page states what each adapter and each family supports. Every row is a fact read from the code in `nerdvana_cli/providers/` and pinned by a test in `tests/providers/` (mainly `test_provider_contract.py`, which replays one scenario against every adapter, and `test_openai_responses.py`). Where a fact could only be checked against the vendor documentation and not against a live API, the last section says so.

## Which adapter serves which provider

| Adapter | Class | Providers |
|-|-|-|
| Anthropic | `AnthropicProvider` | `anthropic` |
| Gemini generateContent | `GeminiProvider` | `gemini` when `model.gemini_api` is `generate_content` (the default) |
| Gemini Interactions | `GeminiInteractionsProvider` | `gemini` when `model.gemini_api` is `interactions` or `auto` |
| OpenAI Chat Completions | `OpenAIProvider` | the 19 others: `openai`, `groq`, `openrouter`, `xai`, `ollama`, `vllm`, `deepseek`, `mistral`, `cohere`, `together`, `zai`, `featherless`, `xiaomi_mimo`, `moonshot`, `dashscope`, `minimax`, `perplexity`, `fireworks`, `cerebras` |
| OpenAI Responses | `OpenAIResponsesProvider` | `provider: openai` on `https://api.openai.com/v1` (or an empty `base_url`) when `model.openai_api` is `auto`; any OpenAI-compatible provider when it is `responses` |

`model.openai_api: chat` forces Chat Completions everywhere. An unregistered provider name falls back to the Chat Completions adapter. `model.gemini_api` defaults to `generate_content` until the Interactions path is verified against the live API; `auto` already picks Interactions.

## Adapter matrix

| Capability | Anthropic | OpenAI Chat Completions | OpenAI Responses | Gemini generateContent | Gemini Interactions |
|-|-|-|-|-|-|
| Streaming | `messages.create(stream=True)` | `chat.completions.create(stream=True)` with `stream_options.include_usage`; resent without it when the server answers 400 or 422 or the client raises `TypeError` | `responses.create(stream=True)` | `generate_content_stream` | `interactions.create(stream=True)` |
| Events yielded | `content_delta`, `thinking_delta`, `provider_block`, `tool_use_start`, `tool_use_delta`, `tool_use_complete`, `usage`, `done`, `error` | `content_delta`, `thinking_delta`, `tool_use_complete`, `usage`, `done`, `error` | same as Chat Completions plus `provider_block` | `content_delta`, `tool_use_complete`, `usage`, `done`, `error` | `content_delta`, `thinking_delta`, `provider_block`, `tool_use_complete`, `usage`, `done`, `error` |
| Tool declaration | `name`, `description`, `input_schema` | nested `{"type": "function", "function": {...}}` | flat `{"type": "function", "name", "description", "parameters", "strict": false}` | `FunctionDeclaration` | flat `{"type": "function", "name", "description", "parameters"}`, the JSON schema as it is |
| Tool call assembly | `input_json_delta` fragments joined at `content_block_stop` | argument fragments joined per call slot; a new id on a used index opens a new slot, so a reused index does not merge two calls | whole call taken from `response.output_item.done`; a repeated item is reported once | the whole call arrives in one part | `function_call` step: `arguments_delta` fragments joined at `step.stop`, or the arguments given whole on the step when no fragment follows; calls are reported once the interaction completes and are dropped when it is incomplete |
| Unparseable arguments | empty input | empty input | empty input | not applicable | empty input |
| Tool call id | the server's `tool_use` id | the server's id | the server's `call_id` | minted client side as `call_<name>_<8 hex>`, unique per call | the server's `function_call` step id |
| Usage report | one `usage` event after the content, before `done` | one `usage` event, the last non-empty report of the stream | one `usage` event from the terminal event | one `usage` event, the last cumulative report | one `usage` event from `interaction.completed` |
| `input_tokens` meaning | whole prompt (fresh plus cache writes plus cache reads) | whole prompt | whole prompt | whole prompt | whole prompt (`total_input_tokens`) |
| Cached tokens | `cache_read_tokens` and `cache_write_tokens` | `cache_read_tokens` from `prompt_tokens_details.cached_tokens`, or `prompt_cache_hit_tokens` (DeepSeek) | `cache_read_tokens` from `input_tokens_details.cached_tokens`; cache writes are not mapped | `cache_read_tokens` from `cached_content_token_count` | `cache_read_tokens` from `total_cached_tokens`; `output_tokens` also holds `total_thought_tokens` |
| No usage from the server | no `usage` event | estimated at four characters per token when text streamed, nothing for a call-only reply | no `usage` event | no `usage` event | no `usage` event |
| Stop reason | the server's `stop_reason`, default `end_turn` | `stop` and `tool_calls` become `end_turn` or `tool_use` (calls decide), `length` becomes `max_tokens`, other values pass through | `tool_use` when calls were returned, `max_tokens` for an incomplete response, otherwise the incomplete reason or `end_turn` | `tool_use` when calls were returned, `max_tokens` when the candidate's finish reason is `MAX_TOKENS`, otherwise `end_turn` | `completed` and `requires_action` give `tool_use` when calls were made, else `end_turn`; `incomplete` gives `max_tokens`; `failed` and `cancelled` are errors |
| Error classification | `classify_exception`: status, SDK class name, timeouts, text | same | same, plus `response.failed` and `error` events by error code | same, from the exception `code` | same, from the exception `code`; an `error` stream event is classified from its message text |
| `Retry-After` | read from the response headers | read from the response headers | read from the response headers (request failures) | not available | read from the response headers (request failures) |
| Image input | base64 `image` source in a user block list | `image_url` data URL parts in a user block list | `input_image` data URL parts | `inlineData` bytes in a user block list | base64 `image` content block in a `user_input` step |
| Tool results | text only | text only | text only (`function_call_output`) | text only (`functionResponse`) | text only (`function_result` step, with `is_error` when set) |
| `reasoning_effort` | ignored | top-level `reasoning_effort`, sent as written on every request when set | `reasoning.effort`; also `reasoning.summary: auto` unless `show_thinking` is off, `include: ["reasoning.encrypted_content"]`, and no `temperature` unless the effort is `none` | `thinking_config.thinking_level`: `minimal`, `low`, `medium`, `high`; any other value stops the request with an error event | `generation_config.thinking_level` in lower case (`minimal`, `low`, `medium`, `high`; any other value stops the request with an error event); also `thinking_summaries: auto` unless `show_thinking` is off |
| Other thinking settings | `extended_thinking`, `thinking_budget`, `show_thinking` per model family (`request_options`) | none | none | none | none |
| Thinking text | `thinking_delta` from thinking blocks | `thinking_delta` from `<think>` tags in the content; `reasoning_content` fields are not read | `thinking_delta` from reasoning summary and reasoning text events, and from `<think>` tags | none requested, none shown | `thinking_delta` from thought summaries (asked for only when a level is set) |
| Replayed provider blocks | `thinking` and `redacted_thinking` blocks, unchanged and first in the turn; blocks of other types are dropped | none | `reasoning` items that carry `encrypted_content`, before the turn's text and calls; other block types are dropped | none | `thought` blocks that carry a signature, unchanged, each in the position it had among the turn's calls; other block types are dropped |
| Statefulness | stateless | stateless | stateless: full input every turn, `store: false`, no `previous_response_id` | stateless | stateless: full input every turn, `store: false`, no `previous_interaction_id` |
| Prompt caching | explicit breakpoints on the last tool, the system prompt and the last block | automatic on the server | automatic on the server | automatic on the server | automatic on the server |
| Output token limit field | `max_tokens` | `max_tokens` (not `max_completion_tokens`) | `max_output_tokens` | `max_output_tokens` | `generation_config.max_output_tokens` |
| Temperature | omitted for models with fixed sampling | always sent | omitted when a reasoning effort other than `none` is set | always sent | always sent (`generation_config.temperature`) |
| Non-streaming `send` | supported, returns `provider_blocks` | supported | supported, returns `provider_blocks` | supported | supported, returns `provider_blocks` |

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

## Gemini limits to know

- The Interactions path is stateless: it never sends `previous_interaction_id`, so Google stores nothing for the conversation (`store: false`). Thought signatures live on `thought` steps, not on function calls; the adapter keeps each thought as a provider block and replays it unchanged. Switching a session from `generateContent` to Interactions leaves earlier calls without thought steps, because their signatures were stored on the calls.
- An escalation to `escalation_model` drops stored provider blocks, as for every adapter, so replayed thoughts never reach a different model that way.
- The generateContent path now reads the finish reason: a reply cut off at the output limit ends as `max_tokens`. A reply that contains function calls stays `tool_use`.
- Thought tokens count as output on the Interactions path (`total_thought_tokens` is added to `output_tokens`). The generateContent path reports `candidates_token_count` only and leaves thought tokens out.

## What is verified how

Verified by tests that replay hand-written payloads (no network): every row above for the Anthropic, Chat Completions, Gemini generateContent and Gemini Interactions adapters, the Interactions request (validated against the google-genai SDK's own `CreateModelInteraction` model), the step conversion and the replay of thought blocks through the session transcript, the Responses request fields, the Item conversion (checked against the openai SDK's own input types), the event to `ProviderEvent` mapping, the usage mapping, the `store: false` request, stateless replay of reasoning items through the session transcript, and the error classification.

Checked only against the OpenAI documentation and the typed models of the installed openai SDK, never against a live API:

- that the live Responses stream emits exactly the events and fields the adapter reads, and that it terminates with `response.completed`, `response.incomplete` or `response.failed`;
- that a replayed reasoning item (with `id` and `encrypted_content`) followed by a `function_call` item without an `id` is accepted when `store` is `false`;
- that `include: ["reasoning.encrypted_content"]` and `reasoning.summary: auto` are accepted by every reasoning model for every organization (summaries have required a verified organization on some models; turn `show_thinking` off if a request is refused for that);
- that omitting `temperature` is required, and `strict: false` accepted, for the models in use;
- that older reasoning models return encrypted reasoning only when a reasoning effort is set, since the adapter requests it only then.

Checked only against the Gemini documentation and the typed models of the installed google-genai SDK (2.28.0), never against a live API:

- that the live Interactions stream emits exactly the events and fields the adapter reads (`step.start`, `step.delta`, `step.stop`, `interaction.completed`, `error`), including `arguments_delta` fragments for function calls and `thought_signature` deltas for thoughts;
- that a stateless request holding thought steps (signature, optional summary) before function call steps is accepted with `store: false`, and that restoring thoughts to their recorded position among the calls matches what the server expects;
- that `total_thought_tokens` is reported apart from `total_output_tokens` (the adapter adds them);
- that `thinking_summaries: auto` and `generation_config.temperature` are accepted by every model that takes a thinking level;
- that the JSON schema of a tool is accepted as it is, without the type conversion the generateContent path applies;
- that a session which switches from generateContent to Interactions mid conversation, so earlier calls have no thought steps, is accepted.
