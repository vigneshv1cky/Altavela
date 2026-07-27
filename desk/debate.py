"""Altavela debate core — shared researcher→critic→judge pipeline."""

import asyncio
import logging

from altavela.config import MODEL_MAP
from altavela.desk import team
from altavela.ledger import store

log = logging.getLogger("altavela.debate")


async def deliberate(market: dict, pick: dict, evidence: list[str],
                     trigger_src: str, decision_id: str | None = None):
    """Run the full team debate on one market pick and write its ledger row.

    Yields event dicts for streaming, then a terminal _result event.
    """
    loop = asyncio.get_running_loop()
    question = pick.get("question", market.get("question", ""))
    direction = pick["direction"]
    prices = market.get("prices", [0.5, 0.5])
    current_price = prices[0] if direction == "BUY_YES" else prices[1]

    # Researcher
    thesis = await loop.run_in_executor(
        None, lambda: team.researcher_case(
            question, direction, current_price, evidence, decision_id))
    model_tags = {"researcher": thesis.pop("_downgraded_model", MODEL_MAP["researcher"])}
    yield {"type": "thesis", "market_id": market["id"], "question": question, **thesis}

    # Critic
    concerns_out = await loop.run_in_executor(
        None, lambda: team.critic_challenge(question, thesis, evidence, decision_id))
    model_tags["critic"] = concerns_out.pop("_downgraded_model", MODEL_MAP["critic"])
    concerns = concerns_out.get("concerns", [])
    for c in concerns:
        yield {"type": "concern", "market_id": market["id"], **c}

    counter = {
        "stance": concerns_out.get("stance", "SUPPORT"),
        "counter_direction": concerns_out.get("counter_direction", ""),
        "counter": concerns_out.get("counter", ""),
    }
    if counter["stance"] != "SUPPORT":
        yield {"type": "counter", "market_id": market["id"], **counter}

    # Rebuttal
    rebuttal = await loop.run_in_executor(
        None, lambda: team.researcher_reply(question, thesis, concerns, decision_id))
    rb_model = rebuttal.pop("_downgraded_model", None)
    if rb_model and rb_model != MODEL_MAP["researcher"]:
        model_tags["researcher"] = rb_model
    yield {"type": "rebuttal", "market_id": market["id"], **rebuttal}

    # Judge
    verdict = await loop.run_in_executor(
        None, lambda: team.judge_verdict(question, thesis, concerns, rebuttal, decision_id))
    model_tags["judge"] = verdict.pop("_downgraded_model", MODEL_MAP["judge"])

    final_dir = verdict.get("final_direction") or direction
    booked_dir = final_dir if final_dir in ("BUY_YES", "BUY_NO") else direction
    flipped = booked_dir != direction

    yield {
        "type": "decision",
        "market_id": market["id"],
        "question": question,
        "direction": booked_dir,
        "flipped": flipped,
        "approved": verdict["approved"],
        "adjusted_score": verdict["adjusted_score"],
        "verdict": verdict["verdict"],
        "summary": verdict["summary"],
    }

    # Write to ledger
    pick_id = store.record_pick({
        "market_id": market["id"],
        "question": question,
        "arm": "TEAM",
        "edge": pick.get("edge_hint", "MISPRICING"),
        "trigger_src": trigger_src,
        "direction": booked_dir,
        "est_probability": thesis["est_probability"],
        "score": thesis["score"],
        "adjusted_score": verdict["adjusted_score"],
        "confidence": thesis["score"],
        "verdict": verdict["verdict"],
        "approved": int(bool(verdict["approved"])),
        "triage_reason": pick["reason"],
        "thesis": thesis["thesis"],
        "debate": {
            "concerns": concerns,
            "rebuttal": rebuttal,
            "judge_summary": verdict["summary"],
            "critic_stance": counter["stance"],
            "counter_direction": counter["counter_direction"],
            "flipped": flipped,
        },
        "model_tags": model_tags,
        "market_yes_price": prices[0] if len(prices) > 0 else 0.5,
        "market_no_price": prices[1] if len(prices) > 1 else 0.5,
        "market_volume": market.get("volume", 0),
        "market_liquidity": market.get("liquidity", 0),
        "market_end_date": market.get("end_date", ""),
        "taken": 1,
    })

    yield {
        "type": "_result",
        "pick_id": pick_id,
        "market_id": market["id"],
        "question": question,
        "direction": booked_dir,
        "approved": bool(verdict["approved"]),
        "score": verdict["adjusted_score"],
        "verdict": verdict["verdict"],
        "flipped": flipped,
        "thesis": thesis,
        "verdict_raw": verdict,
    }
