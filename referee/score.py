"""Per-call scoring orchestration: transcribe every agent turn via the
referee, match each scenario field's entity against ground truth, correlate
unmatched fields against logged repair attempts, produce PLAN.md section 6's
worked-example shape - "1/3 slots, 11 turns, 74s, 3 repairs, 1 silent error."
"""
from __future__ import annotations

import os

from referee.entities import EntityMatch, find_entity_match_in_segments
from referee.groq_asr import transcribe_wav


def transcribe_call(call_dir: str, lang: str) -> list[dict] | None:
    """Transcribes the ONE continuous whole-call recording
    (`_full_call.wav`, caller/call.py's second, never-torn-down recorder) -
    not the per-turn files, which end wherever our own live
    silence-detection heuristic decided a turn was over, not wherever the
    agent actually stopped talking (confirmed live losing real agent
    speech this way). Returns Groq's verbose_json segments
    ([{start, end, text}, ...]), or None if this call predates the
    continuous recorder and has no `_full_call.wav` to score from."""
    wav_path = os.path.join(call_dir, "_full_call.wav")
    if not os.path.exists(wav_path):
        return None
    cache_path = os.path.join(call_dir, "_referee_full_call.json")
    transcript = transcribe_wav(wav_path, lang, cache_path=cache_path)
    return transcript.segments or []


def _entity_for_field(scenario_turns: list[dict], field: str, lang: str) -> tuple[str, dict] | None:
    for t in scenario_turns:
        if t["field"] == field:
            entities = t["entities"][lang]
            return entities[0]["type"], entities[0]
    return None


def score_call(call_log: dict, scenario_turns: list[dict], segments: list[dict] | None) -> dict:
    """call_log: the call_log.json dict written by scripts/run_caller.py.
    scenario_turns: config/scenarios.yaml's scenarios[scenario]['turns'].
    segments: transcribe_call()'s return - the continuous-call transcript's
    Groq verbose_json segments, or None if this call has no
    `_full_call.wav` to score from (every field then scores as
    no_continuous_recording rather than silently matching nothing)."""
    lang = call_log["lang"]
    turn_log = call_log["turns"]

    # The full REQUIRED set, from the scenario definition - not just the
    # fields that happen to appear in turn_log. A field the call never even
    # reached (ended early) still counts as a miss in the denominator, not
    # an exclusion - PLAN.md's own "1/3 slots" worked example includes an
    # unreached field this way.
    all_fields = [t["field"] for t in scenario_turns]
    # A row can name a field two ways - "field" (the primary one) or
    # "fields_also_addressed" (any others the same turn also confirmed,
    # caller/call.py's _react()). Checking "field" alone here undercounts:
    # confirmed live, hi-IN, a field recorded only in fields_also_addressed
    # was invisible to scoring the same way an unrecorded field was -
    # scored "never_reached" despite being correctly confirmed on tape.
    def _row_fields(row: dict) -> list[str]:
        f = [row["field"]] if row.get("field") else []
        return f + row.get("fields_also_addressed", [])

    fields_seen = {f for row in turn_log for f in _row_fields(row)}
    fields_result = {}

    for field in all_fields:
        entity_lookup = _entity_for_field(scenario_turns, field, lang)
        if entity_lookup is None:
            continue
        field_type, expected = entity_lookup

        if field not in fields_seen:
            fields_result[field] = {
                "expected": expected.get("value"), "extracted_from_offset_s": None,
                "matched_script": None, "entity_match": False, "silent_error": False,
                "repair_status": "never_reached", "method": "never_reached",
            }
            continue

        supplying_turn_i = next(row["i"] for row in turn_log if field in _row_fields(row))
        supplying_row = next(row for row in turn_log if row.get("i") == supplying_turn_i)
        next_row = next((row for row in turn_log if row.get("i") == supplying_turn_i + 1), None)
        if field in supplying_row.get("fields_confirmed_in_agent_speech", []):
            # The AGENT's own speech THIS turn is what confirmed the field
            # (caller/call.py's pre-check) - the real content is sitting in
            # this turn, not a later one. Confirmed live, hi-IN/
            # ctl_noise_only: anchoring to the next turn (built for the
            # opposite case below) skipped past the vendor correctly saying
            # both the phone number and the date in the turn that already
            # triggered this match, scoring two genuinely correct fields as
            # misses. Start from this turn's own offset instead.
            search_from_offset_s = supplying_row.get("t_offset_s", 0.0)
        else:
            # Coarse lower bound: the next turn's t_offset_s (roughly when
            # the caller moved on from this field), falling back to this
            # field's own supplying turn if there's no next one (call ended
            # right after). Not a precise boundary - see
            # find_entity_match_in_segments's docstring for why that
            # distinction matters. Deliberately skips the supplying turn
            # itself here: in this branch the CALLER is the one who said the
            # field (giving or restating it to the vendor), so including
            # that turn would risk matching the caller's own words as if the
            # vendor had said them back.
            search_from_offset_s = (next_row or supplying_row).get("t_offset_s", 0.0)

        if segments is None:
            match = EntityMatch(field=field, expected=expected.get("value"), extracted_text="",
                                matched=False, method="no_continuous_recording")
        else:
            match = find_entity_match_in_segments(field, field_type, expected, lang,
                                                  segments, search_from_offset_s)

        # The flag signal is specifically the agent's IMMEDIATE next turn,
        # not any repair anywhere later in the call - PLAN.md's own worked
        # example has a silent error on phone (wrong value, agent moves
        # straight to the next question) followed MUCH later by an unrelated
        # repair (confusion from a different, later turn) - that later
        # repair must not retroactively excuse the earlier silent one.
        immediately_flagged = bool(next_row and next_row.get("intent") == "repair")

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
            "expected": match.expected, "extracted_from_offset_s": match.source_offset_s,
            "matched_script": match.matched_script, "entity_match": match.matched,
            "silent_error": silent_error, "repair_status": repair_status,
            "method": match.method,
        }

    n_slots_correct = sum(1 for f in fields_result.values() if f["entity_match"])
    n_slots_total = len(fields_result)
    n_repairs = sum(1 for row in turn_log if row.get("intent") == "repair")
    n_silent_errors = sum(1 for f in fields_result.values() if f["silent_error"])

    # Resolution-path breakdown - PLAN.md's own divergence metric, and the
    # thing that actually governs this benchmark's validity per-turn: tier
    # 1/2 (regex, or Laya trusted >= tier3_threshold) resolve through the
    # pre-built, condition-degraded canonical WAV or a fixed utility phrase.
    # Tier 3 (needs_adaptive=True, Laya under threshold) USED to always mean
    # action=="play_adaptive" - a FRESH LLM reply through synth_and_degrade(),
    # which mixes in an independently-sampled noise clip rather than
    # continuing the session's real degraded recording (caller/adaptive.py's
    # own docstring). Tonight's fixes decoupled the two (closing-with-
    # nothing-remaining, repair, and a confirmed-by-agent field no longer
    # always escalate just because confidence was low), so n_tier3 and
    # n_adaptive_action are reported separately rather than assumed equal -
    # n_adaptive_action is the one that actually counts turns where the
    # vendor heard freshly-synthesized audio instead of this condition's
    # real corpus file.
    n_tier1 = sum(1 for row in turn_log if row.get("tier") == 1)
    n_tier2 = sum(1 for row in turn_log if row.get("tier") == 2)
    n_tier3 = sum(1 for row in turn_log if row.get("tier") == 3)
    n_adaptive_action = sum(1 for row in turn_log if row.get("action") == "play_adaptive")

    return {
        "call_id": call_log["call_id"], "scenario": call_log["scenario"], "lang": lang,
        "vendor": call_log.get("vendor"), "referee_model": "whisper-large-v3-groq",
        "fields": fields_result,
        "n_slots_correct": n_slots_correct, "n_slots_total": n_slots_total,
        "task_success": n_slots_total > 0 and n_slots_correct == n_slots_total,
        "n_turns": len([r for r in turn_log if r.get("i") is not None]),
        "duration_s": call_log.get("duration_s"),
        "n_repairs": n_repairs, "n_silent_errors": n_silent_errors,
        "n_tier1": n_tier1, "n_tier2": n_tier2, "n_tier3": n_tier3,
        "n_adaptive_action": n_adaptive_action,
    }
