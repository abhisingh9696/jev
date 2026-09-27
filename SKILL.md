---
name: jev
description: Use when a decision gate is needed - pick next alternative, gate evidence, score readiness.
triggers:
  - "jev"
  - "decision gate"
  - "which alternative"
  - "is evidence sufficient"
  - "decision-ready"
  - "challenge gate"
  - "prioritize"
  - "triage"
  - "which deserves attention"
  - "route to agent"
  - "which option first"
---

# Jev Decision Gate for Hermes

## The contract

**Jev owns decision probabilities; the reasoning model owns everything else.**
Jev NEVER writes text, answers, or deliverables. It only answers pre-defined
typed questions with calibrated probabilities. Hermes reasons, generates
alternatives, writes. Jev gates between steps.

## API

Endpoint: `POST https://openrouter.ai/api/alpha/decisions`
Model: `typesafe/jev-1.13`
Auth: `Authorization: Bearer $OPENROUTER_API_KEY` (same key as reasoning models)

**NOT chat/completions.** Jev is a decisions model - sending it to
`/api/v1/chat/completions` fails. There is no text in or out.

Request shape:
```json
{
  "model": "typesafe/jev-1.13",
  "state": { "structured": "JSON facts of the current situation" },
  "questions": {
    "q_name":  {"type": "choice", "instructions": "...", "criteria": {"opt_a": "when a", "opt_b": "when b"}},
    "q_yesno": {"type": "noul",   "instructions": "...", "criteria": {"true": "when yes", "false": "when no"}},
    "q_scale": {"type": "score",  "instructions": "...", "criteria": ["low anchor", "mid anchor", "high anchor"]}
  }
}
```

One call can carry MANY questions - bundle them (the CASE STATE pattern).
Response: `result["answers"]` = dict of question_name -> typed answer + probabilities.

## Helper (always use this, never hand-roll requests)

```python
import sys; sys.path.insert(0, r"C:\Users\bittu\AppData\Local\hermes\skills\jev\scripts")
from jev_client import jev_decide, jev_choice, jev_gate, jev_case_state
```

- `jev_decide(state, questions)` - raw bundle call, returns answers dict
- `jev_choice(state, question, options, instructions, criteria)` - winner + full probability distribution
- `jev_gate(state, question, instructions, yes, no, threshold=0.7)` - (passed: bool, p_yes: float)
- `jev_case_state(...)` - the 4-question CASE STATE bundle (choice + 2 noul + score)
- `jev_prioritize(state, question, items, ...)` - many candidates (100 features/ideas/risks/leads):
  tournament over chunks of <=8 options, winners advance; returns final winner + runner-up + round log
- `jev_triage(state, item, context, ...)` - ignore | investigate | escalate routing for
  exceptions/risks/cases; low confidence defaults to 'investigate' (never silently ignore)
- `jev_route(state, query, agents, fallback='human', ...)` - agent orchestration:
  which agent handles this; below fallback_threshold confidence -> human fallback

Key resolution order in the client: `$OPENROUTER_API_KEY` env -> `HKCU\Environment`
registry (survives `setx` without restart).

## The workflow (decision-gate loop)

```
Hermes reasons -> generates hypotheses/alternatives -> gathers evidence
  -> JEV GATE (one bundled call):
      choice : which next investigation / which alternative wins
      noul   : is evidence adequate
      noul   : is there a material contradiction
      score  : how decision-ready is the case
  -> route on answers: continue | investigate gap | rethink hypothesis
  -> Hermes analyzes/simulates
  -> JEV CHALLENGE GATE (noul: does the fact base support the draft conclusion?)
  -> Hermes synthesizes final answer
```

Gate rules:
- p_yes < threshold on evidence-adequate -> do NOT write the answer; investigate the named gap first
- choice confidence < 0.5 -> expose next-best option, don't blindly follow
- Every call appends a receipt to `.jev/decisions.jsonl` in the working dir
  (timestamp, state hash, questions, answers, confidences) - audit trail

## The Universal Jev Pattern

Every enterprise use case reduces to one flow:

```
INPUT STATE
   ↓
LLM generates: options / hypotheses / possible actions
   ↓
Analytics evaluates: numbers, constraints, scenarios
   ↓
JEV DECIDES: what deserves attention / confidence
   ↓
LLM continues reasoning
   ↓
Action / recommendation
```

LLM = thinks and creates options. Analytics = calculates.
**Jev = decides which direction deserves attention.** Jev never replaces
the manager — it is the decision-control layer inside the pipeline.

## Enterprise use-case catalog (ranked by value)

| Rank | Area | Jev question | Helper |
|---|---|---|---|
| 1 | AI agent orchestration | which agent handles this query? (billing/support/human) | `jev_route` |
| 2 | Product management | which feature/experiment enters the next sprint? | `jev_prioritize` |
| 3 | Supply chain / S&OP | which plan deserves simulation? (inventory/production/promo/outsource) | `jev_choice` |
| 4 | FMCG innovation | which concept deserves consumer testing first? | `jev_prioritize` |
| 5 | Consulting case solving | which hypothesis should be tested first? | `jev_case_state` |
| 6 | Project management | which risks need immediate attention? (ignore/investigate/escalate) | `jev_triage` |
| 7 | Sales | which leads deserve sales attention? | `jev_prioritize` |
| 8 | Finance | which investment targets deserve due diligence? | `jev_prioritize` |
| 9 | HR | which cases need intervention? (retention, recruitment — human oversight) | `jev_triage` |
| 10 | Marketing | which campaign deserves A/B testing? | `jev_choice` |

## Domain templates (copy-paste, adapt state)

### FMCG NPD pick — which concept gets consumer testing first
```python
jev_choice(state, "concept_first", {
    "protein_chips": "high protein trend, crowded shelf, low differentiation",
    "indian_popcorn": "local flavour moat, strong brand fit, mid margin",
    "vitamin_shots": "premium price, narrow audience, regulatory risk",
}, "Which concept deserves consumer testing first, weighted by differentiation, brand fit and margin?")
```

### PM feature sprint — 100 requests -> next sprint
```python
# Analytics pre-scores (impact, effort, revenue); Jev picks under criteria
jev_prioritize(state, "next_sprint_feature", scored_features,
               "Which feature enters the next sprint? Weight user impact, engineering effort, revenue, strategic fit.",
               instructions="Pick the single feature with best impact-to-effort.")
```

### S&OP plan gate — which plan deserves simulation
```python
jev_choice(sop_state, "plan_to_simulate", {
    "increase_inventory": "protects service level, raises working capital",
    "increase_production": "needs capacity check, longest lead time",
    "reduce_promotion": "protects margin, risks volume",
    "outsource": "fast capacity, quality + dependency risk",
}, "Which plan deserves simulation first under the demand-surge scenario?")
```

### Risk / exception triage — ignore | investigate | escalate
```python
jev_triage(state, "exception_42",
           context="Transaction anomaly: 14x typical value, known merchant, first occurrence.")
# -> ('escalate', 0.91, p)  |  low confidence -> default to 'investigate'
```

### Agent router — who handles this query
```python
jev_route(query_state, "billing", {"billing": "refunds, invoices, payment failures",
    "support": "product usage, troubleshooting", "human": "angry customer, legal threat"},
    fallback="human")
```

## Applications (project-specific)

| Workflow | Jev question | Type |
|---|---|---|
| Case comps (Flipkart/TVS/UN40) | pick strongest strategy alternative under stated criteria | choice |
| Fact-base verification (FACT_BASE.md) | does evidence support this claim before it enters a deck | noul gate |
| Kissan consensus pre-filter | score/rank agent proposals before full deliberation | choice + score |
| Next experiment | which hypothesis merits testing | score |
| Deck QA | is section consistent with fact base | noul |

## Response schema (VERIFIED against live API)

Jev returns one key named after the question TYPE, not generic names:

```
noul   -> {"type":"noul",   "noul": 0.96}                        # P(yes)
choice -> {"type":"choice", "choice":"payments",
           "probabilities":{"payments":0.95,"frontend":0.05}, "confidence":0.92}
score  -> {"type":"score",  "score": 0.25,                        # weighted position
           "legend":{"0":"...","1":"...","2":"..."}, "probabilities":{...}}
```

Reading the wrong key silently yields `0.0` / `None` — the helper's
`_extract_noul` / `_extract_choice` / `_extract_score` handle all shapes.
Always go through the helper; never read `answer`/`value` by hand.

## Pitfalls (learned from live calls)

1. **Contradiction detection needs labels.** Bare facts ("source A: 12%",
   "source B: 18%") score LOW on `material_contradiction` (~0.27) — Jev does
   not infer that two numbers disagree. Write the conflict explicitly in the
   state, e.g. `"conflict": "Source A says 12% premium acceptance, source B
   says 18% — unreconciled"`. Otherwise the gate silently passes.
2. **Contradiction outranks a gap** in routing: if contradiction >= threshold,
   the route is `rethink_hypothesis` (gathering more evidence is the wrong
   remedy when sources conflict).
3. **`score` is a weighted position, not an index.** `decision_readiness` of
   0.45 with legend 0..2 means "mostly not ready", not "level 0.45".
4. **One call, many questions.** Bundling the 4-question CASE STATE costs one
   request — never split into 4 calls.

## CLI

```bash
python "C:/Users/bittu/AppData/Local/hermes/skills/jev/scripts/jev_client.py" --selftest
```

## Phase 2 (BUILT and LIVE-VERIFIED): tool router plugin

Plugin `hermes-jev-universal-loop` @ `~/AppData/Local/hermes/plugins/hermes-jev-universal-loop/`
(5 tools on toolset `jev_universal_loop`; enable with `hermes plugins enable jev-universal-loop`).
`PluginManager().discover_and_load()` is the real loader entry point — there is no
`load_plugins()` in `plugins_loader.py`; registration is checked via
`registry.get_all_tool_names()` / `registry.get_tool_names_for_toolset(name)`
(there is no `registry.list_names()`).

Adapted from the JevRouter / jev-eval-agent pattern:

- state = `{goal, criteria[], constraints[], actions_taken[], facts[]}` (compact, no transcripts)
- `jev_loop_next`: Jev picks one action from the candidate list you pass in (1-8), or
  `respond_to_user` when criteria are evidenced
- evidence gate: **a selection is never evidence** — only `jev_loop_record` with a
  `ref` per criterion marks it complete; `task_status` flips to `complete` when all
  criteria are evidenced
- pending-action guard: routing again while an action is pending returns
  `pending_action` and spends NO API call
- stopped tasks: `record`/`next` on a complete or budget-exhausted task return
  `task_not_active` and counters stay frozen

Live-verified round trip (C: free space, 1 routing call, ~1.0s latency, confidence 1.0
-> `df` -> record -> `status: complete`).

Contract pitfalls (each cost a debugging cycle):

- **Handler return type**: registry `dispatch` expects a **JSON string** from
  `tool_result`/`tool_error`; returning a raw dict double-encodes. Handlers must be
  wrapped (the `_guard` helper) — defining the wrapper without wiring it into every
  handler is the exact bug to watch for.
- **`outcome` enum is `succeeded|failed|blocked`** — not `success`.
- **Plugin must be enabled** (`hermes plugins enable <name>`) and needs a fresh session
  before tools appear; `hermes plugins list` shows `user` source.
- Offline suite (50 tests, stubbed HTTP) lives in `tests/test_plugin.py`; run with
  `python -m unittest tests.test_plugin` from the plugin dir.
- On Windows, heredocs expanding `$TMPDIR` to `C:\tmp` silently break scripts —
  write scratch scripts with an absolute native path.

## Cost discipline

Jev input is billed per token - keep `state` compact (facts, not prose dumps).
Hundreds of decisions cost fractions of a cent. Never put secrets in state.
