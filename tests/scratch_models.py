"""Extract free models from OpenRouter API response."""
import json

path = r"C:\Users\Ayush\.gemini\antigravity\brain\755e2f7a-115d-49c0-aa7c-b28e1db63ddd\.system_generated\steps\108\content.md"
data = json.load(open(path, encoding="utf-8"))
models = data.get("data", [])

free = []
for m in models:
    pricing = m.get("pricing", {})
    if pricing.get("prompt") == "0" and pricing.get("completion") == "0":
        free.append(m)

free.sort(key=lambda x: x.get("context_length", 0), reverse=True)

print(f"{'MODEL ID':65s} {'CTX':>10s} {'MAX_OUT':>10s}")
print("-" * 90)
for m in free[:40]:
    mid = m["id"]
    ctx = m.get("context_length", 0)
    top = m.get("top_provider", {}).get("max_completion_tokens", "?")
    print(f"{mid:65s} {ctx:>10,} {str(top):>10s}")
