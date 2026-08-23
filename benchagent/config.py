"""Configuration loading (config/models.yaml + .env).

Non-secret settings (per-agent model names) live in config/models.yaml.
Secrets and provider selection live in a .env file at the project root
(see .env.example). The .env is resolved relative to the config file path,
so the pipeline works regardless of the current working directory.

Provider selection (first match wins):
  1. explicit LLM_PROVIDER=openrouter|generic
  2. LLM_API_KEY / LLM_API_BASE_URL set          -> generic (any OpenAI-compatible endpoint)
  3. OPENROUTER_API_KEY set                       -> openrouter

Model selection (priority high -> low):
  1. CLI --model (applied in cli.py / run_pipeline)
  2. env: LLM_MODEL (global) / LLM_MODEL_<ROLE> (per-agent, e.g. LLM_MODEL_DESIGN)
  3. config/models.yaml per-agent settings
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml
from dotenv import load_dotenv

OPENROUTER_DEFAULT_BASE = "https://openrouter.ai/api/v1"

_ROLES = ("design", "grounding", "allocation", "executor", "verify")


def load_yaml(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_env(config_path: str) -> None:
    """Load .env from CWD and from the project root (parent of the config dir)."""
    load_dotenv()  # CWD
    project_root = Path(config_path).resolve().parent.parent
    load_dotenv(project_root / ".env", override=False)


def _resolve_provider() -> tuple[str, Optional[str], Optional[str]]:
    """Return (provider, api_key, base_url) from the environment."""
    explicit = os.environ.get("LLM_PROVIDER", "").strip().lower()
    llm_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    llm_base = os.environ.get("LLM_API_BASE_URL") or os.environ.get("OPENAI_API_BASE_URL")
    or_key = os.environ.get("OPENROUTER_API_KEY")
    or_base = os.environ.get("OPENROUTER_BASE_URL")

    if explicit == "openrouter" and or_key:
        return "openrouter", or_key, or_base or OPENROUTER_DEFAULT_BASE
    if explicit == "generic" and llm_key:
        return "generic", llm_key, llm_base or None
    if llm_key:  # generic wins when both are configured (explicit, predictable)
        return "generic", llm_key, llm_base or None
    if or_key:
        return "openrouter", or_key, or_base or OPENROUTER_DEFAULT_BASE
    return "generic", llm_key, llm_base or None


def load_model_config(path: str) -> dict[str, Any]:
    """Merge .env secrets/provider with per-agent model settings.

    Returns a dict with keys:
      provider, api_key, base_url, default_model, agents, tools
    """
    _load_env(path)
    cfg = load_yaml(path)
    agents = dict(cfg.get("agents", {}))
    tools = dict(cfg.get("tools", {}))
    provider, api_key, base_url = _resolve_provider()

    # model selection: env overrides yaml (LLM_MODEL global, LLM_MODEL_<ROLE> specific)
    env_global = os.environ.get("LLM_MODEL")
    for role in _ROLES:
        env_role = os.environ.get(f"LLM_MODEL_{role.upper()}")
        if env_role:
            agents[role] = env_role
        elif env_global:
            agents[role] = env_global
    if env_global:
        agents["default"] = env_global

    default_model = (
        agents.get("default")
        or tools.get("default")
        or cfg.get("default_model")
        or "gpt-4o"
    )
    return {
        "provider": provider,
        "api_key": api_key,
        "base_url": base_url,
        "default_model": default_model,
        "agents": agents,
        "tools": tools,
    }
