"""
shellstory.llm.models — Model registry and fallback chains.

Defines the exact OpenRouter model strings and their properties,
along with the optimal model sequence for each agent type.
"""

from __future__ import annotations

from typing import TypedDict


class ModelMeta(TypedDict):
    id: str
    context_window: int
    max_output: int
    strengths: list[str]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Model Registry (May 2026 Free Models)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

MODEL_REGISTRY: dict[str, ModelMeta] = {
    "deepseek-v4-flash": {
        "id": "deepseek/deepseek-v4-flash:free",
        "context_window": 256_000,
        "max_output": 256_000,
        "strengths": ["json", "speed", "large_context"],
    },
    "gemma-4-31b": {
        "id": "google/gemma-4-31b-it:free",
        "context_window": 262_144,
        "max_output": 32_768,
        "strengths": ["reasoning", "low_hallucination"],
    },
    "nemotron-super-120b": {
        "id": "nvidia/nemotron-3-super-120b-a12b:free",
        "context_window": 262_144,
        "max_output": 262_144,
        "strengths": ["orchestration", "structured_output"],
    },
    "qwen3-coder": {
        "id": "qwen/qwen3-coder:free",
        "context_window": 262_000,
        "max_output": 262_000,
        "strengths": ["coding", "editing"],
    },
    "ring-2.6-1t": {
        "id": "inclusionai/ring-2.6-1t:free",
        "context_window": 262_144,
        "max_output": 65_536,
        "strengths": ["agentic_reasoning", "knowledge"],
    },
    "trinity-large-thinking": {
        "id": "arcee-ai/trinity-large-thinking:free",
        "context_window": 262_144,
        "max_output": 80_000,
        "strengths": ["planning", "chain_of_thought"],
    },
    "gpt-oss-120b": {
        "id": "openai/gpt-oss-120b:free",
        "context_window": 131_072,
        "max_output": 131_072,
        "strengths": ["general_purpose"],
    },
    "glm-4.5-air": {
        "id": "z-ai/glm-4.5-air:free",
        "context_window": 131_072,
        "max_output": 96_000,
        "strengths": ["efficiency", "classification"],
    },
    "minimax-m2.5": {
        "id": "minimax/minimax-m2.5:free",
        "context_window": 196_608,
        "max_output": 8_192,
        "strengths": ["task_decomposition", "pitfalls"],
    },
    "cobuddy": {
        "id": "baidu/cobuddy:free",
        "context_window": 131_072,
        "max_output": 65_536,
        "strengths": ["debugging", "tool_calling"],
    },
    "llama-3.3-70b": {
        "id": "meta-llama/llama-3.3-70b-instruct:free",
        "context_window": 65_536,
        "max_output": 16_384,  # Assumed safe max
        "strengths": ["reliability", "instruction_following"],
    },
    "laguna-m1": {
        "id": "poolside/laguna-m.1:free",
        "context_window": 131_072,
        "max_output": 8_192,
        "strengths": ["agentic_coding"],
    },
    # Ultimate fallback that auto-routes
    "openrouter-free": {
        "id": "openrouter/free",
        "context_window": 200_000,
        "max_output": 16_384,
        "strengths": ["availability"],
    },
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Agent Fallback Chains (Primary, Secondary, Last Resort)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

AGENT_MODEL_CHAINS: dict[str, list[str]] = {
    # Precision guardian — low hallucination is critical
    "pii_scanner": ["gemma-4-31b", "deepseek-v4-flash", "llama-3.3-70b"],

    # Fast classifier
    "signal": ["glm-4.5-air", "deepseek-v4-flash", "llama-3.3-70b"],

    # Debug specialist
    "failure": ["cobuddy", "qwen3-coder", "deepseek-v4-flash"],

    # Strategic planner
    "sequence": ["trinity-large-thinking", "ring-2.6-1t", "nemotron-super-120b"],

    # Knowledge powerhouse
    "prereq": ["ring-2.6-1t", "gpt-oss-120b", "gemma-4-31b"],

    # Practical architect
    "annotation": ["minimax-m2.5", "trinity-large-thinking", "glm-4.5-air"],

    # Orchestration engine — merging everything
    "merger": ["nemotron-super-120b", "deepseek-v4-flash", "gpt-oss-120b"],

    # Precision
    "coverage": ["gemma-4-31b", "glm-4.5-air", "llama-3.3-70b"],

    # Knowledge
    "critical_path": ["ring-2.6-1t", "trinity-large-thinking", "gpt-oss-120b"],

    # Code surgeon
    "repair": ["qwen3-coder", "laguna-m1", "nemotron-super-120b"],
}


def get_agent_chain(agent_name: str) -> list[str]:
    """Get the list of OpenRouter model IDs for a specific agent."""
    aliases = AGENT_MODEL_CHAINS.get(agent_name, ["openrouter-free"])
    
    # Always append the auto-router as the absolute final fallback
    if "openrouter-free" not in aliases:
        aliases.append("openrouter-free")
        
    return [MODEL_REGISTRY[alias]["id"] for alias in aliases if alias in MODEL_REGISTRY]
