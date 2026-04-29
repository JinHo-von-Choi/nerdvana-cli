# API Key and Secrets Management

This document defines the operational standard for API keys and secrets used in nerdvana-cli. It covers the 21 provider API keys, GitHub Actions secrets, and local `.env` files.

---

## Secret Classification and Scope

GitHub Secrets come in two scopes. Choose based on sensitivity and workflow type.

| Scope | When to use |
|-|-|
| Repository secret | General CI workflows with no external cost or side effects |
| Environment secret | Live smoke tests, provider billing calls, any workflow that triggers real API usage |

Workflows that make live provider calls (e.g., `live-smoke`) must use environment secrets attached to a protected environment. Require manual approval or branch protection rules before the environment secret is exposed to a job.

Organization-level secrets are not recommended. Their blast radius covers all repositories in the org and makes scope auditing difficult.

---

## GitHub Actions Masking

All secrets injected into a workflow step must be masked before use.

Masking pattern:

```yaml
- name: mask secrets
  run: |
    echo "::add-mask::${{ secrets.OPENAI_API_KEY }}"
```

Use `$GITHUB_OUTPUT` for passing values between steps. The deprecated `set-output` command is not permitted.

Checklist before merging any workflow file:

- No `echo $SECRET_VAR` in any `run` block, even in debug steps
- No `run: cat .env` or equivalent
- No secret value embedded in a step `name` or `if` condition
- `add-mask` applied before the first step that uses the secret

---

## Rotation

Recommended rotation cycle: 90 days.

Rotation procedure:

1. Open the provider dashboard (see revoke URLs below).
2. Revoke the current key.
3. Generate a new key in the same dashboard.
4. Update the corresponding GitHub secret via Settings > Secrets and variables > Actions.
5. Trigger the `live-smoke` workflow manually to verify the new key works end-to-end.

---

## Suspected Leak: Immediate Response

1. Revoke the key immediately in the provider dashboard. Do not wait.
2. Check the provider audit log for any usage that did not originate from your workloads.
3. Generate a replacement key and update GitHub secrets.
4. Review git history and PR comments for the leaked value; if found, contact GitHub support to scrub the content.

Provider revoke URLs:

| Provider | Dashboard / Revoke URL |
|-|-|
| anthropic | https://console.anthropic.com/settings/keys |
| openai | https://platform.openai.com/api-keys |
| gemini | https://aistudio.google.com/app/apikey |
| groq | https://console.groq.com/keys |
| openrouter | https://openrouter.ai/keys |
| xai | https://console.x.ai/ |
| ollama | https://ollama.com/settings/keys (cloud); local is N/A |
| vllm | self-hosted; contact the instance administrator |
| deepseek | https://platform.deepseek.com/api_keys |
| mistral | https://console.mistral.ai/api-keys |
| cohere | https://dashboard.cohere.com/api-keys |
| together | https://api.together.ai/settings/api-keys |
| zai | https://z.ai/manage-apikey/apikey-list |
| featherless | https://featherless.ai/account |
| xiaomi_mimo | https://token-plan-sgp.xiaomimimo.com/ |
| moonshot | https://platform.moonshot.ai/console/api-keys |
| dashscope | https://dashscope.console.aliyun.com/apiKey |
| minimax | https://www.minimaxi.com/user-center/basic-information/interface-key |
| perplexity | https://www.perplexity.ai/settings/api |
| fireworks | https://fireworks.ai/account/api-keys |
| cerebras | https://cloud.cerebras.ai/platform/ |

URLs above may become stale; verify accuracy when acting on them and open a separate PR if a redirect is detected.

---

## Local .env Files

Verify `.env` is excluded from version control before running any provider test locally:

```bash
grep -E "^\.env" .gitignore
```

Expected output includes `.env` and `.env.local`. If either is missing, add it before proceeding.

A pre-commit secret scanner such as `detect-secrets` is recommended to catch accidental credential staging. Integration with `.pre-commit-config.yaml` is covered separately.

---

## Live Smoke and Environment Isolation

Tests under `tests/live/` read provider keys exclusively from environment variables and never from committed files. When GitHub Actions runs the live smoke suite, it should target an environment named `live-smoke` so that the key exposure is gated by environment protection rules, not repository-level access alone.
