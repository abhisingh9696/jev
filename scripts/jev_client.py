"""Jev Decision Gate client for Hermes - one OpenRouter key.

Jev (typesafe/jev-1.13) answers typed questions (choice / noul / score)
with calibrated probabilities. The reasoning model owns all writing.

Usage:
    import sys; sys.path.insert(0, r"C:\\Users\\bittu\\AppData\\Local\\hermes\\skills\\jev\\scripts")
    from jev_client import jev_decide, jev_choice, jev_gate, jev_case_state
"""
import hashlib
import json
import os
import time
import winreg
from pathlib import Path

API_URL = "https://openrouter.ai/api/alpha/decisions"
MODEL = "typesafe/jev-1.13"


def _get_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    try:  # setx-written value survives in registry even before shell restart
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            val, _ = winreg.QueryValueEx(k, "OPENROUTER_API_KEY")
            if val and val.strip():
                return val.strip()
    except OSError:
        pass
    raise RuntimeError(
        "OPENROUTER_API_KEY not found in env or HKCU\\Environment. "
        "Run: setx OPENROUTER_API_KEY sk-or-v1-..."
    )


# --- response field map (verified against live API) ---------------------------
# noul  -> {"type":"noul",  "noul": 0.96}
# choice-> {"type":"choice","choice": "payments", "probabilities": {...}, "confidence": 0.92}
# score -> {"type":"score", "score": 2 | 0.64, "legend": {...}, "probabilities": {...}}
_TYPE_KEYS = {"noul": "noul", "choice": "choice", "score": "score"}


def _extract_noul(a: dict) -> float:
    """P(yes) from a noul answer. Handles all known shapes."""
    if not isinstance(a, dict):
        return 1.0 if str(a).lower() in ("true", "yes") else 0.0
    for k in ("noul", "p_yes", "probability", "yes"):
        v = a.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    probs = a.get("probabilities") or {}
    if probs:
        for k, v in probs.items():
            if str(k).lower() in ("true", "yes"):
                return float(v)
    val = a.get("answer", a.get("value"))
    if val is not None:
        return 1.0 if str(val).lower() in ("true", "yes") else 0.0
    return 0.0


def _extract_choice(a: dict):
    """(winner, probabilities, confidence) from a choice answer."""
    if not isinstance(a, dict):
        return a, {}, None
    probs = a.get("probabilities") or a.get("distribution") or {}
    winner = a.get("choice") or a.get("answer") or a.get("value")
    if winner is None and probs:
        winner = max(probs, key=probs.get)
    conf = a.get("confidence")
    if conf is None and probs and winner is not None:
        conf = probs.get(winner)
    return winner, probs, conf


def _extract_score(a: dict):
    """Score value (index or weighted float) plus its legend/probabilities."""
    if not isinstance(a, dict):
        return a
    val = a.get("score", a.get("value", a.get("answer")))
    return val


def _log_receipt(state, questions, answers, latency_ms, raw=None):
    try:
        receipt_dir = Path.cwd() / ".jev"
        receipt_dir.mkdir(exist_ok=True)
        receipt = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "state_hash": hashlib.sha256(
                json.dumps(state, sort_keys=True, default=str).encode()
            ).hexdigest()[:16],
            "questions": list(questions.keys()),
            "answers": answers,
            "latency_ms": latency_ms,
        }
        if raw is not None:
            receipt["raw_keys"] = list(raw.keys()) if isinstance(raw, dict) else type(raw).__name__
        with open(receipt_dir / "decisions.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(receipt, ensure_ascii=False) + "\n")
    except Exception:
        pass  # receipts must never break the decision flow


def _post(payload: dict) -> dict:
    import urllib.request

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {_get_key()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    latency_ms = int((time.time() - t0) * 1000)
    return result, latency_ms


def jev_decide(state: dict, questions: dict, raw: bool = False):
    """Bundle call: one request, many typed questions.

    Returns the answers dict {qname: {answer/value/confidence/probabilities}}.
    With raw=True returns (answers, full_provider_response).
    """
    payload = {"model": MODEL, "state": state, "questions": questions}
    result, latency_ms = _post(payload)
    answers = result.get("answers", result)
    _log_receipt(state, questions, answers, latency_ms, raw=result)
    if raw:
        return answers, result
    return answers


def jev_choice(state: dict, question: str, options: dict, instructions: str,
               raw: bool = False):
    """Pick one option. options = {name: when_to_pick_description}.

    Returns (winner, probabilities_dict, confidence).
    """
    questions = {question: {
        "type": "choice",
        "instructions": instructions,
        "criteria": options,
    }}
    answers = jev_decide(state, questions)
    winner, probs, confidence = _extract_choice(answers.get(question, {}))
    out = (winner, probs, confidence)
    if raw:
        return out, answers
    return out


def jev_gate(state: dict, question: str, instructions: str, yes: str, no: str,
             threshold: float = 0.7):
    """Yes/no gate. Returns (passed: bool, p_yes: float)."""
    questions = {question: {
        "type": "noul",
        "instructions": instructions,
        "criteria": {"true": yes, "false": no},
    }}
    answers = jev_decide(state, questions)
    p_yes = _extract_noul(answers.get(question, {}))
    return p_yes >= threshold, p_yes


def jev_case_state(state: dict, choice_question: str, choice_options: dict,
                   evidence_question: str, contradiction_question: str,
                   ready_question: str, ready_scale: list = None,
                   gate_threshold: float = 0.7):
    """The CASE STATE bundle: one call, four questions.

    Returns {
      next_step: (winner, probs, confidence),
      evidence_adequate: (passed, p_yes),
      material_contradiction: (found, p_yes),
      decision_readiness: score_value,
      route: 'continue' | 'investigate_gap' | 'rethink_hypothesis'
    }
    """
    questions = {
        "next_investigation": {
            "type": "choice",
            "instructions": choice_question,
            "criteria": choice_options,
        },
        "evidence_adequate": {
            "type": "noul",
            "instructions": evidence_question,
            "criteria": {
                "true": "Every load-bearing claim in the state is backed by cited evidence.",
                "false": "At least one load-bearing claim lacks supporting evidence.",
            },
        },
        "material_contradiction": {
            "type": "noul",
            "instructions": contradiction_question,
            "criteria": {
                "true": "Two or more evidence items in the state directly conflict on a load-bearing fact.",
                "false": "No direct conflicts between evidence items in the state.",
            },
        },
        "decision_readiness": {
            "type": "score",
            "instructions": ready_question,
            "criteria": ready_scale or [
                "Not ready: core questions unresolved",
                "Partially ready: some gaps remain",
                "Ready: evidence sufficient for a final recommendation",
            ],
        },
    }
    answers = jev_decide(state, questions)

    winner, probs, conf = _extract_choice(answers.get("next_investigation", {}))
    ev_ok = _extract_noul(answers.get("evidence_adequate", {}))
    contradiction = _extract_noul(answers.get("material_contradiction", {}))
    readiness_raw = answers.get("decision_readiness", {})
    readiness = _extract_score(readiness_raw)

    # Contradiction outranks a mere evidence gap: conflicting sources on a
    # load-bearing fact mean the hypothesis itself may be wrong, so gather-more
    # is the wrong remedy.
    if contradiction >= gate_threshold:
        route = "rethink_hypothesis"
    elif ev_ok < gate_threshold:
        route = "investigate_gap"
    else:
        route = "continue"

    return {
        "next_step": (winner, probs, conf),
        "evidence_adequate": (ev_ok >= gate_threshold, ev_ok),
        "material_contradiction": (contradiction >= gate_threshold, contradiction),
        "decision_readiness": readiness,
        "readiness_detail": readiness_raw,
        "route": route,
        "_answers": answers,
    }


def _selftest():
    """Live smoke test: one noul, one choice, one score, plus a CASE STATE bundle."""
    print("Jev self-test -> POST", API_URL, "| model:", MODEL)
    st = {
        "case": "selftest",
        "evidence": [
            "conflict: source A says 12% premium acceptance, source B says 18% - unreconciled",
            "rural elasticity is unmeasured",
        ],
    }
    print("\n[1] noul (expect high P(yes) - a real conflict is present):")
    print("   ", jev_gate(st, "contradiction",
          "Is there an unreconciled conflict on a load-bearing fact?",
          "Two cited sources disagree and the conflict is not resolved.",
          "No cited sources disagree on a load-bearing fact."))

    print("\n[2] choice (expect a strategy alternative):")
    print("   ", jev_choice(st, "pick", {
        "resolve_conflict": "Reconcile the two conflicting premium-acceptance figures first.",
        "measure_rural": "Measure rural elasticity before committing.",
    }, "Which next investigation most reduces decision risk?"))

    print("\n[3] CASE STATE bundle (one call, four questions):")
    r = jev_case_state(
        st,
        choice_question="Which next investigation most reduces decision risk?",
        choice_options={
            "resolve_conflict": "Reconcile the conflicting premium-acceptance figures.",
            "measure_rural": "Measure rural elasticity.",
            "write_now": "Evidence is sufficient; write the recommendation.",
        },
        evidence_question="Is evidence adequate for a final recommendation?",
        contradiction_question="Is there an unreconciled conflict on a load-bearing fact?",
        ready_question="How decision-ready is the case?",
    )
    r.pop("_answers", None)
    r.pop("readiness_detail", None)
    print(json.dumps(r, indent=2, default=str))
    print("\nReceipts ->", Path.cwd() / ".jev" / "decisions.jsonl")


if __name__ == "__main__":
    import sys as _sys
    if "--selftest" in _sys.argv:
        _selftest()
    else:
        print(__doc__)
        print("Run with --selftest to make live decision calls.")
