"""Per-call scoring orchestration: transcribe every agent turn via the
referee, match each scenario field's entity against ground truth, correlate
unmatched fields against logged repair attempts, produce PLAN.md section 6's
worked-example shape - "1/3 slots, 11 turns, 74s, 3 repairs, 1 silent error."
"""
from __future__ import annotations

import os

from referee.entities import find_entity_match
from referee.groq_asr import transcribe_turn


def _wav_path_for_turn(call_dir: str, i: int) -> str | None:
    name = "_agent_opening.wav" if i == -1 else f"_agent_turn_{i}.wav"
    path = os.path.join(call_dir, name)
    return path if os.path.exists(path) else None


def transcribe_call(call_dir: str, turn_log: list[dict], lang: str) -> list[dict]:
    """Mutates nothing - returns turn_log with a referee_text key added to
    rows that have audio, so a later pass can re-run without re-transcribing
    (transcribe_turn's own cache_path handles the actual API-call skip)."""
    out = []
    for row in turn_log:
        row = dict(row)
        i = row.get("i")
        if i is not None:
            wav_path = _wav_path_for_turn(call_dir, i)
            if wav_path:
                cache_path = os.path.join(call_dir, f"_referee_turn_{i}.json")
                transcript = transcribe_turn(wav_path, lang, cache_path=cache_path)
                row["referee_text"] = transcript.text
        out.append(row)
    return out


def _entity_for_field(scenario_turns: list[dict], field: str, lang: str) -> tuple[str, dict] | None:
    for t in scenario_turns:
        if t["field"] == field:
            entities = t["entities"][lang]
            return entities[0]["type"], entities[0]
    return None


def score_call(call_log: dict, scenario_turns: list[dict]) -> dict:
    """call_log: the call_log.json dict written by scripts/run_caller.py
    (already has turn_log rows enriched with referee_text via transcribe_call).
    scenario_turns: config/scenarios.yaml's scenarios[scenario]['turns']."""
    lang = call_log["lang"]
    turn_log = call_log["turns"]

    # The full REQUIRED set, from the scenario definition - not just the
    # fields that happen to appear in turn_log. A field the call never even
    # reached (ended early) still counts as a miss in the denominator, not
    # an exclusion - PLAN.md's own "1/3 slots" worked example includes an
    # unreached field this way.
    all_fields = [t["field"] for t in scenario_turns]
    fields_seen = {row["field"] for row in turn_log if row.get("field")}
    fields_result = {}

    for field in all_fields:
        entity_lookup = _entity_for_field(scenario_turns, field, lang)
        if entity_lookup is None:
            continue
        field_type, expected = entity_lookup

        if field not in fields_seen:
            fields_result[field] = {
                "expected": expected.get("value"), "extracted_from_turn_i": None,
                "matched_script": None, "entity_match": False, "silent_error": False,
                "repair_status": "never_reached", "method": "never_reached",
            }
            continue

        supplying_turn_i = next(row["i"] for row in turn_log if row.get("field") == field)
        referee_rows = [{"i": row["i"], "agent_text": row.get("referee_text", row.get("agent_text", ""))}
                        for row in turn_log if row.get("i") is not None]

        match = find_entity_match(field, field_type, expected, lang, referee_rows,
                                  search_from_i=supplying_turn_i + 1)

        # The flag signal is specifically the agent's IMMEDIATE next turn,
        # not any repair anywhere later in the call - PLAN.md's own worked
        # example has a silent error on phone (wrong value, agent moves
        # straight to the next question) followed MUCH later by an unrelated
        # repair (confusion from a different, later turn) - that later
        # repair must not retroactively excuse the earlier silent one.
        immediate_next = next((row for row in turn_log if row.get("i") == supplying_turn_i + 1), None)
        immediately_flagged = bool(immediate_next and immediate_next.get("intent") == "repair")

        silent_error = False
        repair_status = None
        if not match.matched:
            if immediately_flagged:
                repair_status = "repaired_but_still_wrong"
            else:
                silent_error = True
                repair_status = "silent_error"
        elif immediately_flagged:
            repair_status = "repaired_correctly"

        fields_result[field] = {
            "expected": match.expected, "extracted_from_turn_i": match.source_turn_i,
            "matched_script": match.matched_script, "entity_match": match.matched,
            "silent_error": silent_error, "repair_status": repair_status,
            "method": match.method,
        }

    n_slots_correct = sum(1 for f in fields_result.values() if f["entity_match"])
    n_slots_total = len(fields_result)
    n_repairs = sum(1 for row in turn_log if row.get("intent") == "repair")
    n_silent_errors = sum(1 for f in fields_result.values() if f["silent_error"])

    return {
        "call_id": call_log["call_id"], "scenario": call_log["scenario"], "lang": lang,
        "vendor": call_log.get("vendor"), "referee_model": "whisper-large-v3-groq",
        "fields": fields_result,
        "n_slots_correct": n_slots_correct, "n_slots_total": n_slots_total,
        "task_success": n_slots_total > 0 and n_slots_correct == n_slots_total,
        "n_turns": len([r for r in turn_log if r.get("i") is not None]),
        "duration_s": call_log.get("duration_s"),
        "n_repairs": n_repairs, "n_silent_errors": n_silent_errors,
    }
