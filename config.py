"""Altavela configuration — facts only: model map, caps, market setup."""

import os
from pathlib import Path

from dotenv import load_dotenv

_ENV_FILE = os.environ.get("ALTAVELA_ENV", ".env")
load_dotenv(_ENV_FILE, override=True)

DATA_DIR = Path(os.environ.get("ALTAVELA_DATA", "~/.altavela")).expanduser()
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Model map — role → tier. Same pattern as AlphaDesk.
# ---------------------------------------------------------------------------

TIERS = ["opus", "sonnet", "haiku"]

MODEL_MAP: dict[str, str] = {
    "enrichment": "haiku",
    "scout": "sonnet",
    "researcher": "sonnet",
    "critic": "opus",
    "judge": "opus",
    "head": "opus",
    "loner": "opus",
    "plan": "sonnet",
    "evidence": "haiku",       # news/polls/stats brief
}

if os.environ.get("CHEAP_MODELS", "1") not in ("0", "", "false", "False", "no"):
    for _r in ("critic", "judge", "loner", "head"):
        MODEL_MAP[_r] = "sonnet"

for _role in list(MODEL_MAP):
    _override = os.environ.get(f"MODEL_{_role.upper()}")
    if _override:
        MODEL_MAP[_role] = _override

# ---------------------------------------------------------------------------
# Transport — one provider for all roles.
# ---------------------------------------------------------------------------

MODEL_PROVIDERS = ("claude_sdk", "kimi", "deepseek")
MODEL_PROVIDER = os.environ.get("MODEL_PROVIDER", "claude_sdk").strip().lower()
if MODEL_PROVIDER not in MODEL_PROVIDERS:
    raise RuntimeError(
        f"MODEL_PROVIDER={MODEL_PROVIDER!r} not understood. "
        f"Pick one of {MODEL_PROVIDERS}."
    )

PROVIDER_MODELS: dict[str, dict[str, str]] = {
    "claude_sdk": {"opus": "opus", "sonnet": "sonnet", "haiku": "haiku"},
    "kimi": {
        "opus": os.environ.get("KIMI_MODEL_OPUS", "kimi-k2.6"),
        "sonnet": os.environ.get("KIMI_MODEL_SONNET", "kimi-k2.6"),
        "haiku": os.environ.get("KIMI_MODEL_HAIKU", "kimi-k2.6"),
    },
    "deepseek": {
        "opus": os.environ.get("DEEPSEEK_MODEL_OPUS", "deepseek-v4-pro"),
        "sonnet": os.environ.get("DEEPSEEK_MODEL_SONNET", "deepseek-v4-flash"),
        "haiku": os.environ.get("DEEPSEEK_MODEL_HAIKU", "deepseek-v4-flash"),
    },
}

PROVIDER_ENDPOINTS = {
    "kimi": {
        "base_url": os.environ.get("KIMI_BASE_URL", "https://api.moonshot.ai/v1"),
        "key_envs": ("KIMI_API_KEY", "MOONSHOT_API_KEY"),
    },
    "deepseek": {
        "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"),
        "key_envs": ("DEEPSEEK_API_KEY",),
    },
}

# ---------------------------------------------------------------------------
# Caps and limits
# ---------------------------------------------------------------------------
MAX_PICKS_PER_WINDOW = 5
LLM_MAX_INPUT_CHARS = int(os.environ.get("LLM_MAX_INPUT_CHARS", "48000"))
LLM_MAX_CONCURRENCY = int(os.environ.get("LLM_MAX_CONCURRENCY", "4"))
LLM_HTTP_MAX_CONCURRENCY = int(os.environ.get("LLM_HTTP_MAX_CONCURRENCY", "8"))
LLM_TIMEOUT_S = int(os.environ.get("LLM_TIMEOUT_S", "120"))
LLM_TOOL_TIMEOUT_S = int(os.environ.get("LLM_TOOL_TIMEOUT_S", "300"))
LLM_TOOL_BUDGET_USD = float(os.environ.get("LLM_TOOL_BUDGET_USD", "0.50"))
LLM_HTTP_MAX_TOKENS = int(os.environ.get("LLM_HTTP_MAX_TOKENS", "4096"))

# Kimi-specific (unused on deepseek, kept for compatibility)
KIMI_THINKING = os.environ.get("KIMI_THINKING", "disabled").strip().lower()
if KIMI_THINKING not in ("enabled", "disabled"):
    raise RuntimeError("KIMI_THINKING must be 'enabled' or 'disabled'")
KIMI_K3_REASONING_EFFORT = os.environ.get("KIMI_K3_REASONING_EFFORT", "low")

SOLO_ARM_EVERY_N = int(os.environ.get("SOLO_ARM_EVERY_N", "0"))

# Polymarket API
POLYMARKET_BASE = os.environ.get("POLYMARKET_BASE", "https://gamma-api.polymarket.com")
# Polymarket CLOB (order book — if Paper Trading is on)
POLYMARKET_CLOB_BASE = os.environ.get("POLYMARKET_CLOB_BASE", "https://clob.polymarket.com")
POLYMARKET_PRIVATE_KEY = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_PROXY_WALLET = os.environ.get("POLYMARKET_PROXY_WALLET", "")

# Paper trading
PAPER_TRADING = os.environ.get("PAPER_TRADING", "0") not in ("0", "", "false", "False", "no")
PM_BASE_USD = float(os.environ.get("PM_BASE_USD", "100"))
PM_MAX_POSITION_USD = float(os.environ.get("PM_MAX_POSITION_USD", "500"))
PM_MAX_POSITIONS = int(os.environ.get("PM_MAX_POSITIONS", "20"))

# Concentration cap — at most 2 positions per category (politics, sports, crypto, etc.)
CONCENTRATION_MAX_PER_CATEGORY = int(os.environ.get("CONCENTRATION_MAX_PER_CATEGORY", "2"))

# Scout coverage
SCOUT_MAX_CANDIDATES = int(os.environ.get("SCOUT_MAX_CANDIDATES", "60"))

# Friction — Polymarket charges no fees, just spread/slippage
FRICTION_BPS_PER_SIDE = 5  # tighter than stock markets

# Autorun
AUTORUN_INTERVAL_HOURS = float(os.environ.get("AUTORUN_INTERVAL_HOURS", "0.25"))
AUTORUN_START_ET = os.environ.get("AUTORUN_START_ET", "00:00").strip()
AUTORUN_END_ET = os.environ.get("AUTORUN_END_ET", "23:59").strip()

# Don't re-debate the same market within this many hours unless price moved > threshold
REPICK_COOLDOWN_HOURS = float(os.environ.get("REPICK_COOLDOWN_HOURS", "6"))
REPICK_MIN_PRICE_MOVE_PCT = float(os.environ.get("REPICK_MIN_PRICE_MOVE_PCT", "5"))

# Web dashboard
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
DASHBOARD_HOST = os.environ.get("DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.environ.get("DASHBOARD_PORT", "8001"))
