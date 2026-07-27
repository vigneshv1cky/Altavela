"""Altavela team — researcher, critic, judge prompts for prediction markets."""

import json
import logging

from altavela.llm import LLMError, call_role

log = logging.getLogger("altavela.team")

# ---------------------------------------------------------------------------
# Researcher — builds the probability case
# ---------------------------------------------------------------------------

_RESEARCHER_SYSTEM = (
    "You are a researcher on a prediction-market desk. Your job: estimate the TRUE "
    "probability of a binary outcome and recommend BUY_YES or BUY_NO.\n\n"
    "Given a prediction market (question, current price, volume, end date) and "
    "any available evidence (news, polls, data), form a thesis:\n"
    "  • est_probability: your estimate of the TRUE probability (0.0 to 1.0)\n"
    "  • direction: BUY_YES if your estimate > current price, BUY_NO if < price\n"
    "  • score: confidence in your estimate (0-100, >50 favors your direction)\n"
    "  • thesis: 2-4 sentence reasoning with specific evidence\n"
    "  • The CURRENT market price is the baseline — your job is to find the gap "
    "between that and reality.\n\n"
    'Return ONLY JSON: {{"est_probability": 0.XX, "direction": "BUY_YES|BUY_NO", '
    '"score": 0-100, "thesis": "..."}}'
)

_RESEARCHER_SCHEMA = {
    "est_probability": {"type": (int, float), "min": 0.0, "max": 1.0},
    "direction": {"type": str, "enum": ["BUY_YES", "BUY_NO"]},
    "score": {"type": (int, float), "min": 0, "max": 100},
    "thesis": {"type": str, "maxlen": 600},
}


def researcher_case(question: str, direction: str, current_price: float,
                    evidence: list[str], decision_id: str | None = None) -> dict:
    ev_str = "\n".join(f"- {e}" for e in evidence[:5]) if evidence else "No evidence available"
    user = (
        f"Market: {question}\n"
        f"Current YES price: {current_price}\n"
        f"Scout direction: {direction}\n\n"
        f"Evidence:\n{ev_str}"
    )
    out = call_role("researcher", _RESEARCHER_SYSTEM, user,
                    schema=_RESEARCHER_SCHEMA, decision_id=decision_id)
    out.pop("_downgraded_model", None)
    return out


# ---------------------------------------------------------------------------
# Critic — attacks the assumptions
# ---------------------------------------------------------------------------

_CRITIC_SYSTEM = (
    "You are the critic on a prediction-market desk. Your job: attack the "
    "researcher's probability estimate. Find flaws, missing information, "
    "alternative interpretations. Then decide:\n"
    "  • SUPPORT: the thesis is sound\n"
    "  • FLIP: the opposite direction is correct (e.g., BUY_YES → BUY_NO)\n"
    "  • STAND_ASIDE: no edge either way\n\n"
    "Return up to 3 pointed concerns with evidence.\n\n"
    'Return ONLY JSON: {{"concerns": [{{"claim": "...", "evidence": "..."}}], '
    '"stance": "SUPPORT|FLIP|STAND_ASIDE", '
    '"counter_direction": "BUY_YES|BUY_NO" (if FLIP), "counter": "..."}}'
)

_CRITIC_SCHEMA = {
    "concerns": {"type": list, "maxitems": 3, "items": {
        "claim": {"type": str, "maxlen": 300},
        "evidence": {"type": str, "maxlen": 300},
    }},
    "stance": {"type": str, "enum": ["SUPPORT", "FLIP", "STAND_ASIDE"]},
    "counter_direction": {"type": str, "optional": True,
                          "enum": ["BUY_YES", "BUY_NO"]},
    "counter": {"type": str, "optional": True, "maxlen": 300},
}


def critic_challenge(question: str, thesis: dict, evidence: list[str],
                     decision_id: str | None = None) -> dict:
    user = (
        f"Market: {question}\n"
        f"Researcher: {thesis['direction']} at est probability {thesis['est_probability']} "
        f"(score {thesis['score']}/100)\n"
        f"Thesis: {thesis['thesis']}\n"
    )
    out = call_role("critic", _CRITIC_SYSTEM, user,
                    schema=_CRITIC_SCHEMA, decision_id=decision_id)
    out.pop("_downgraded_model", None)
    return out


# ---------------------------------------------------------------------------
# Rebuttal
# ---------------------------------------------------------------------------

_REBUTTAL_SYSTEM = (
    "You are the researcher replying to the critic's pushback. Address each "
    "concern. Adjust your probability estimate if persuaded. Be honest — concede "
    "valid points.\n\n"
    'Return ONLY JSON: {{"revised_score": 0-100, "concede": true|false, '
    '"rebuttal": "..."}}'
)

_REBUTTAL_SCHEMA = {
    "revised_score": {"type": (int, float), "min": 0, "max": 100},
    "concede": {"type": bool},
    "rebuttal": {"type": str, "maxlen": 500},
}


def researcher_reply(question: str, thesis: dict, concerns: list[dict],
                     decision_id: str | None = None) -> dict:
    c_str = "\n".join(f"- {c['claim']}: {c['evidence']}" for c in concerns)
    user = (
        f"Market: {question}\n"
        f"Your thesis ({thesis['direction']}, prob {thesis['est_probability']}): "
        f"{thesis['thesis']}\n\nCritic's concerns:\n{c_str}"
    )
    out = call_role("researcher", _REBUTTAL_SYSTEM, user,
                    schema=_REBUTTAL_SCHEMA, decision_id=decision_id)
    out.pop("_downgraded_model", None)
    return out


# ---------------------------------------------------------------------------
# Judge — commits to a verdict
# ---------------------------------------------------------------------------

_JUDGE_SYSTEM = (
    "You are the judge on a prediction-market desk. Read the full debate "
    "transcript. Always commit to a direction (BUY_YES or BUY_NO — never neutral).\n"
    "  • approved: true = conviction call (size up), false = thin lean (track only)\n"
    "  • adjusted_probability: your final probability estimate (0-1)\n"
    "  • adjusted_score: final confidence (0-100)\n"
    "  • verdict: STRONG = high conviction, SOFT = moderate, PASS = thin lean\n"
    "  • summary: one-line explanation of your decision\n\n"
    'Return ONLY JSON: {{"final_direction": "BUY_YES|BUY_NO", '
    '"approved": true|false, "adjusted_probability": 0.XX, '
    '"adjusted_score": 0-100, "verdict": "STRONG|SOFT|PASS", "summary": "..."}}'
)

_JUDGE_SCHEMA = {
    "final_direction": {"type": str, "enum": ["BUY_YES", "BUY_NO"]},
    "approved": {"type": bool},
    "adjusted_probability": {"type": (int, float), "min": 0.0, "max": 1.0},
    "adjusted_score": {"type": (int, float), "min": 0, "max": 100},
    "verdict": {"type": str, "enum": ["STRONG", "SOFT", "PASS"]},
    "summary": {"type": str, "maxlen": 300},
}


def judge_verdict(question: str, thesis: dict, concerns: list[dict],
                  rebuttal: dict, decision_id: str | None = None) -> dict:
    c_str = "\n".join(f"- {c['claim']}" for c in concerns)
    user = (
        f"Market: {question}\n"
        f"Researcher ({thesis['direction']}, prob {thesis['est_probability']}, "
        f"score {thesis['score']}/100): {thesis['thesis']}\n"
        f"Critic concerns: {c_str}\n"
        f"Rebuttal (score {rebuttal['revised_score']}/100, concede={rebuttal['concede']}): "
        f"{rebuttal['rebuttal']}"
    )
    out = call_role("judge", _JUDGE_SYSTEM, user,
                    schema=_JUDGE_SCHEMA, decision_id=decision_id)
    out.pop("_downgraded_model", None)
    return out
