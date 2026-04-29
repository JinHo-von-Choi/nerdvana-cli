"""Fetch or report updated pricing for a single provider.

Usage:
  python scripts/update_pricing.py --provider anthropic
  python scripts/update_pricing.py --provider openai
  python scripts/update_pricing.py --provider google
  python scripts/update_pricing.py --provider groq   # manual URL printed

Exit codes:
  0  auto-fetch attempted (result printed; manual verification recommended)
  2  provider is not auto-fetchable or fetch failed (manual URL printed)
"""

import argparse
import re
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

REPO_ROOT    = Path(__file__).resolve().parent.parent
PRICING_FILE = REPO_ROOT / "nerdvana_cli" / "providers" / "pricing.yml"
TODAY        = date.today().isoformat()

AUTO_PROVIDERS: dict[str, str] = {
    "anthropic": "https://www.anthropic.com/pricing",
    "openai":    "https://openai.com/api/pricing",
    "google":    "https://ai.google.dev/pricing",
}

MANUAL_URLS: dict[str, str] = {
    "groq":        "https://console.groq.com/docs/openai",
    "mistral":     "https://mistral.ai/technology/",
    "deepseek":    "https://platform.deepseek.com/api-docs/pricing",
    "cohere":      "https://cohere.com/pricing",
    "together":    "https://www.together.ai/pricing",
    "openrouter":  "https://openrouter.ai/models",
    "xai":         "https://x.ai/api",
    "featherless": "https://featherless.ai/pricing",
    "xiaomi_mimo": "https://token-plan-sgp.xiaomimimo.com",
    "moonshot":    "https://platform.moonshot.ai/docs/pricing",
    "dashscope":   "https://help.aliyun.com/document_detail/2840914.html",
    "minimax":     "https://www.minimaxi.chat/document/pricing",
    "perplexity":  "https://docs.perplexity.ai/guides/pricing",
    "fireworks":   "https://fireworks.ai/pricing",
    "cerebras":    "https://inference-docs.cerebras.ai/introduction#pricing",
    "ollama":      "https://ollama.com",
    "vllm":        "https://docs.vllm.ai",
    "lmstudio":    "https://lmstudio.ai",
    "zai":         "https://open.bigmodel.cn/pricing",
}

SNAPSHOT_RE = re.compile(
    r"(#\s*\d{4}-\d{2}-\d{2}\s+snapshot\s+—\s+)(\S+)"
)


def _fetch_html(url: str, timeout: int = 10) -> str | None:
    headers = {"User-Agent": "nerdvana-cli/pricing-updater (+https://github.com/nerdvana-ai/nerdvana-cli)"}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError) as exc:
        print(f"  fetch failed: {exc}", file=sys.stderr)
        return None


def _update_snapshot_comment(provider: str, url: str) -> bool:
    """Replace the snapshot date for *provider* block in pricing.yml. Returns True on success."""
    text   = PRICING_FILE.read_text(encoding="utf-8")
    lines  = text.splitlines(keepends=True)
    inside = False
    found  = False
    out: list[str] = []

    provider_re  = re.compile(rf"^{re.escape(provider)}:\s*$")
    any_provider = re.compile(r"^[a-z_][a-z0-9_]*:\s*$")

    for line in lines:
        if provider_re.match(line):
            inside = True
            out.append(line)
            continue

        if inside and any_provider.match(line) and not provider_re.match(line):
            inside = False

        if inside and SNAPSHOT_RE.search(line):
            new_line = SNAPSHOT_RE.sub(rf"\g<1>{url}", line)
            new_line = re.sub(r"#\s*\d{4}-\d{2}-\d{2}", f"# {TODAY}", new_line)
            out.append(new_line)
            found = True
        else:
            out.append(line)

    if found:
        PRICING_FILE.write_text("".join(out), encoding="utf-8")

    return found


def handle_auto(provider: str, url: str) -> int:
    print(f"Fetching pricing page for '{provider}': {url}")
    html = _fetch_html(url)

    if html is None:
        print(f"\nAuto-fetch failed. Check pricing manually: {url}")
        return 2

    # Best-effort: print a short excerpt so the operator can verify values.
    # Full parsing is provider-specific and brittle; we surface the raw page
    # excerpt and update the snapshot date to today.
    lines = [ln.strip() for ln in html.splitlines() if ln.strip()]
    excerpt_lines = [ln for ln in lines if re.search(r"\$|per.*(token|1[Kk]|million)", ln, re.I)][:20]

    if excerpt_lines:
        print("\nPrice-related lines found (verify before committing):")
        for ln in excerpt_lines:
            safe = ln[:120]
            print(f"  {safe}")
    else:
        print("  No price lines detected in response. Verify page manually.")

    updated = _update_snapshot_comment(provider, url)
    if updated:
        print(f"\nSnapshot date updated to {TODAY} in pricing.yml.")
        print("Review model values manually against the page above, then commit.")
    else:
        print(
            f"\nNo existing snapshot comment found for '{provider}' in pricing.yml.\n"
            "Add `# YYYY-MM-DD snapshot — <url>` as the first comment under the provider key."
        )
        return 2

    return 0


def handle_manual(provider: str) -> int:
    url = MANUAL_URLS.get(provider)
    if url:
        print(f"Provider '{provider}' requires manual update.")
        print(f"Pricing source: {url}")
        print(f"Edit nerdvana_cli/providers/pricing.yml and update the snapshot comment to {TODAY}.")
    else:
        all_known = sorted(list(AUTO_PROVIDERS) + list(MANUAL_URLS))
        print(f"Unknown provider: '{provider}'")
        print("Known providers: " + ", ".join(all_known))
    return 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Update pricing snapshot for a provider.")
    parser.add_argument("--provider", required=True, help="Provider name (e.g. anthropic, openai, groq).")
    args   = parser.parse_args()
    name   = args.provider.lower()

    if name in AUTO_PROVIDERS:
        return handle_auto(name, AUTO_PROVIDERS[name])
    return handle_manual(name)


if __name__ == "__main__":
    sys.exit(main())
