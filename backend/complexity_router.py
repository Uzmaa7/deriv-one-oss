"""
Complexity-based model router.

Routing priority (highest wins):

  1. GREETING / CAPABILITY  → ROUTER_MODEL_GREETING (or MODEL)
  2. RETRY / ESCALATE       → ROUTER_MODEL_RETRY (or MODEL)
  3. COMPLEX / REASONING    → ROUTER_MODEL_COMPLEX (or MODEL)
  4. skill_id is set        → ROUTER_MODEL_SKILL (or MODEL)
  5. no skill, short/simple → ROUTER_MODEL_SIMPLE (or MODEL)
  6. no skill, other        → ROUTER_MODEL_MEDIUM (or MODEL)
"""
import os

from model_catalog import platform_default_model

# ── Tier names ────────────────────────────────────────────────────────────────
TIER_GREETING  = "GREETING"
TIER_RETRY     = "RETRY"
TIER_COMPLEX   = "COMPLEX"
TIER_SKILL     = "SKILL"
TIER_SIMPLE    = "SIMPLE"
TIER_MEDIUM    = "MEDIUM"


def _tier_model(env_key: str) -> str:
    return os.environ.get(env_key, "").strip() or platform_default_model()


TIER_MODELS: dict[str, str] = {
    TIER_GREETING: _tier_model("ROUTER_MODEL_GREETING"),
    TIER_RETRY:    _tier_model("ROUTER_MODEL_RETRY"),
    TIER_COMPLEX:  _tier_model("ROUTER_MODEL_COMPLEX"),
    TIER_SKILL:    _tier_model("ROUTER_MODEL_SKILL"),
    TIER_SIMPLE:   _tier_model("ROUTER_MODEL_SIMPLE"),
    TIER_MEDIUM:   _tier_model("ROUTER_MODEL_MEDIUM"),
}

# ── Rule 1: Greeting / capability indicators ──────────────────────────────────
_GREETING_EXACT = {
    "hello", "hi", "hey", "hiya", "howdy",
    "thanks", "thank you", "ty", "thx",
    "ok", "okay", "sure", "yes", "no", "yep", "nope",
    "bye", "goodbye", "good morning", "good afternoon", "good evening",
}

_GREETING_PHRASES = [
    "what can you do",
    "what are you",
    "who are you",
    "how are you",
    "can you help me",
    "what do you do",
    "tell me about yourself",
    "what is this",
    "what's this",
]

# ── Rule 2: Retry / escalation signals ───────────────────────────────────────
_RETRY_PHRASES = [
    "try again",
    "try another",
    "try a different",
    "try something else",
    "try harder",
    "try once more",
    "one more time",
    "doesn't work",
    "not working",
    "still not",
    "still failing",
    "that didn't",
    "that's wrong",
    "that was wrong",
    "incorrect",
    "failed again",
    "still broken",
    "still the same",
    "different approach",
    "another approach",
    "another way",
    "another method",
    "alternative approach",
]

# ── Rule 3: Complexity signals ───────────────────────────────────────────────
_REASONING_MARKERS = [
    "step by step", "think through", "analyze", "analyse", "explain why",
    "walk me through", "reason through", "break down", "deep dive",
    "thoroughly", "in detail", "critically evaluate", "compare and contrast",
    "pros and cons", "trade-off", "trade off", "root cause",
]

_CODE_KEYWORDS = [
    "function", "class", "api", "database", "sql", "query", "endpoint",
    "algorithm", "implement", "debug", "exception", "regex",
    "dockerfile", "kubernetes", "terraform",
]

_TECHNICAL_TERMS = [
    "architecture", "distributed", "encryption", "authentication", "authorization",
    "microservice", "infrastructure", "pipeline", "schema", "migration",
    "load balancer", "circuit breaker", "sharding", "replication", "consensus",
]

_COMPLEX_THRESHOLD = 0.25


def _is_greeting(text: str, tokens: list[str]) -> bool:
    joined = " ".join(tokens)
    if joined in _GREETING_EXACT or tokens[0] in _GREETING_EXACT:
        return True
    return any(ph in text for ph in _GREETING_PHRASES)


def _is_retry(text: str) -> bool:
    return any(ph in text for ph in _RETRY_PHRASES)


def _complexity_score(text: str) -> float:
    reasoning_hits = sum(1 for m in _REASONING_MARKERS if m in text)
    code_hits      = sum(1 for kw in _CODE_KEYWORDS if kw in text)
    tech_hits      = sum(1 for t in _TECHNICAL_TERMS if t in text)
    q_count        = text.count("?")

    score = (
        min(1.0, reasoning_hits / 2) * 0.40
        + min(1.0, code_hits / 3)   * 0.30
        + min(1.0, tech_hits / 3)   * 0.20
        + min(1.0, q_count / 3)     * 0.10
    )
    if reasoning_hits >= 2:
        score = 1.0
    return min(1.0, score)


def score_complexity(
    message: str,
    skill_id: str | None = None,
) -> tuple[float, str, str]:
    """
    Route a user message to the most appropriate model.

    Returns:
        (score, tier, model_slug)
    """
    text   = message.lower().strip()
    tokens = text.split()

    if not tokens:
        return 0.0, TIER_SIMPLE, TIER_MODELS[TIER_SIMPLE]

    score = _complexity_score(text)

    if _is_greeting(text, tokens):
        return score, TIER_GREETING, TIER_MODELS[TIER_GREETING]

    if _is_retry(text):
        return score, TIER_RETRY, TIER_MODELS[TIER_RETRY]

    if score >= _COMPLEX_THRESHOLD:
        return score, TIER_COMPLEX, TIER_MODELS[TIER_COMPLEX]

    if skill_id:
        return score, TIER_SKILL, TIER_MODELS[TIER_SKILL]

    token_count = len(tokens)
    if token_count <= 5:
        return score, TIER_SIMPLE, TIER_MODELS[TIER_SIMPLE]
    return score, TIER_MEDIUM, TIER_MODELS[TIER_MEDIUM]
