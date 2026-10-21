# Local Models (Ollama, vLLM)

Both connect through their OpenAI-compatible endpoint. Pull or serve the model first.

| Server | Default endpoint | API key |
|-|-|-|
| Ollama, local | `http://localhost:11434/v1` | none |
| Ollama, another host | your server's `/v1` address | none |
| Ollama Cloud | `https://ollama.com/v1` | `OLLAMA_API_KEY` |
| vLLM | `http://localhost:8000/v1` | `VLLM_API_KEY` if the server requires one |

## Ollama

```bash
ollama pull qwen3
nerdvana --provider ollama --model qwen3
```

A server on another host is set with `model.base_url`, in `nerdvana.yml` for the REPL:

```yaml
model:
  provider: ollama
  model: qwen3
  base_url: http://ollama.example.com:11434/v1
```

or with `--set` for one run:

```bash
nerdvana run --provider ollama --model qwen3 \
  --set model.base_url=http://ollama.example.com:11434/v1 \
  "Explain the structure of this project"
```

Ollama Cloud:

```bash
export OLLAMA_API_KEY="your-key"
nerdvana run --provider ollama --model gpt-oss:120b \
  --set model.base_url=https://ollama.com/v1 \
  "Summarize the differences between the recent local models"
```

## vLLM

```bash
vllm serve Qwen/Qwen3-32B
nerdvana --provider vllm --model Qwen/Qwen3-32B
```

A remote server is set the same way through `model.base_url`:

```bash
nerdvana run --provider vllm --model Qwen/Qwen3-32B \
  --set model.base_url=http://vllm.example.com:8000/v1 \
  "Run the tests of this project"
```
