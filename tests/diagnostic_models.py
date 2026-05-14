"""Script to test all configured OpenRouter free models."""

import asyncio
import os
import sys
from shellstory.llm.models import MODEL_REGISTRY
from shellstory.llm.openrouter import OpenRouterClient
from shellstory.llm.base import LLMMessage, LLMRateLimitError, LLMProviderError, LLMContextError

async def test_all_models(api_key: str):
    print("===================================================================")
    print("Starting OpenRouter Model Diagnostics")
    print("Testing 13 free models...")
    print("===================================================================\n")
    
    messages = [LLMMessage(role="user", content="Reply with the word 'OK' and nothing else.")]
    
    success_count = 0
    fail_count = 0
    
    for alias, meta in MODEL_REGISTRY.items():
        model_id = meta["id"]
        print(f"Testing {alias:25s} ({model_id}) ... ", end="", flush=True)
        
        client = OpenRouterClient(api_key=api_key, model=model_id, timeout=30.0)
        
        try:
            # We add a 2 second delay between tests to respect the ~20/min free tier rate limit
            await asyncio.sleep(2.0)
            
            resp = await client.complete(messages=messages, max_tokens=10)
            
            # Print success
            print(f"[PASS] Response: {resp.content.strip()!r}")
            success_count += 1
            
        except LLMRateLimitError as e:
            print(f"[RATE LIMITED] {e}")
            fail_count += 1
        except LLMProviderError as e:
            print(f"[PROVIDER ERROR] HTTP {e.status_code} - {e}")
            fail_count += 1
        except Exception as e:
            print(f"[UNEXPECTED ERROR] {type(e).__name__} - {e}")
            fail_count += 1

    print("\n===================================================================")
    print(f"Diagnostics Complete: {success_count} Passed, {fail_count} Failed/Rate Limited")
    print("===================================================================")

if __name__ == "__main__":
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("Error: OPENROUTER_API_KEY environment variable not set.")
        sys.exit(1)
        
    asyncio.run(test_all_models(key))
