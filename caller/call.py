"""The real caller (Phase 6): listens to the agent, transcribes, classifies
intent, and plays the matching canonical/utility/adaptive turn - replacing
scripts/test_mock_agent_call.py's fixed-order driver. Same blocking,
top-level-loop shape as mock_agent/call.py's run_conversation - never nested
inside a PJSIP callback, matching every other blocking loop in this codebase.
"""
from __future__ import annotations

import os

from mock_agent import asr
from mock_agent.call import MockAgentCall, listen_for_turn, speak, synth_response

from caller import utility
from caller.adaptive import generate_reply_text, synth_and_degrade
from caller.intent import classify
from caller.state_machine import CallerState

# MockAgentCall is generic (tracks media_active/last_state/player/recorder
# only) - reused unchanged as the caller's own call object, same as
# scripts/test_mock_agent_call.py already does.
CallerCall = MockAgentCall


def _react(wav_path: str, i: int, state: CallerState, lang: str, lang_meta: dict,
          tts: str, condition: dict, scratch_dir: str, whisper_language: str,
          asr_model: str, tier3_threshold: float) -> tuple[dict, str | None]:
    """Transcribes one heard agent utterance, classifies it, and resolves
    the reply WAV to play next. Returns (log_row, reply_path_or_None) -
    reply_path is None only for the "wait" action (agent is closing)."""
    transcript = asr.transcribe(wav_path, language=whisper_language, model_name=asr_model)
    cls = classify(transcript.text, tier3_threshold=tier3_threshold)
    action = state.next_action(cls)

    row = {
        "i": i, "agent_text": transcript.text, "intent": cls.intent,
        "confidence": round(cls.confidence, 3), "tier": cls.tier,
        "action": action.kind, "divergence": action.divergence,
        "divergence_reason": action.divergence_reason,
    }

    if action.kind == "wait":
        return row, None
    if action.kind == "play_canonical":
        row["reply_text"] = action.turn["text"][lang]
        return row, action.turn["_wav_path"]
    if action.kind == "play_utility":
        text = utility.UTILITY_PHRASES[action.utility_key][lang]
        row["reply_text"] = text
        return row, synth_response(text, lang, tts, lang_meta, scratch_dir)

    # play_adaptive
    remaining = state.remaining_fields()
    reply_text, reason = generate_reply_text(
        transcript.text, lang, remaining,
        best_guess_intent=cls.intent, best_guess_confidence=cls.confidence)
    row["reply_text"] = reply_text
    row["divergence_reason"] = row["divergence_reason"] or reason
    reply_path = synth_and_degrade(
        reply_text, lang, lang_meta, tts, condition,
        os.path.join(scratch_dir, f"_adaptive_{i}.wav"), scratch_dir)
    return row, reply_path


def run_call(ep, call: CallerCall, turns_def: list[dict], lang: str, lang_meta: dict,
            tts: str, condition: dict, scratch_dir: str, max_turn_listen_s: float = 15.0,
            asr_model: str = "tiny", tier3_threshold: float = 0.75,
            max_turns: int = 20, opening_wait_s: float = 4.0) -> list[dict]:
    os.makedirs(scratch_dir, exist_ok=True)
    whisper_language = asr.whisper_lang(lang)
    state = CallerState(turns=turns_def)
    turn_log: list[dict] = []

    # A real vendor agent typically greets first ("Thanks for calling, can I
    # take your name?" - PLAN.md's own worked example); our scenario turns
    # are themselves written as caller-initiated opening lines ("Hi there,
    # yes, my name is..."). Try a short listen for a greeting first; if one
    # arrives, react to it normally; if nothing arrives, this caller opens
    # unprompted - same as a real person would, rather than two silent
    # listeners deadlocking forever.
    opening_wav = os.path.join(scratch_dir, "_agent_opening.wav")
    opening_path, heard_greeting = listen_for_turn(ep, call, opening_wav, max_listen_s=opening_wait_s)
    if call.last_state == "DISCONNECTED":
        return turn_log

    reply_path: str | None
    if heard_greeting:
        row, reply_path = _react(opening_path, -1, state, lang, lang_meta, tts, condition,
                                 scratch_dir, whisper_language, asr_model, tier3_threshold)
        turn_log.append(row)
        if reply_path is None:   # agent's opening line was already "closing" - nothing to do
            return turn_log
    else:
        first_turn = turns_def[0]
        state.collected[first_turn["field"]] = True
        state.last_played_turn = first_turn
        turn_log.append({"i": -1, "action": "play_canonical", "opening": True,
                         "reply_text": first_turn["text"][lang]})
        reply_path = first_turn["_wav_path"]

    speak(ep, call, reply_path, max_wait_s=max_turn_listen_s)
    if call.last_state == "DISCONNECTED":
        return turn_log

    for i in range(max_turns):
        scratch_wav = os.path.join(scratch_dir, f"_agent_turn_{i}.wav")
        wav_path, heard = listen_for_turn(ep, call, scratch_wav, max_listen_s=max_turn_listen_s)
        if call.last_state == "DISCONNECTED":
            # Every iteration attempted gets a log row, even this one - a
            # silent gap here previously made two real races (model cold-
            # load latency, then adaptive-reply latency, each separately
            # outrunning the far end's listen window) look like the loop
            # just stopped, instead of showing where and why.
            turn_log.append({"i": i, "outcome": "disconnected_while_listening"})
            break
        if not heard:
            turn_log.append({"i": i, "outcome": "timeout"})
            break

        row, reply_path = _react(wav_path, i, state, lang, lang_meta, tts, condition,
                                 scratch_dir, whisper_language, asr_model, tier3_threshold)
        turn_log.append(row)
        if reply_path is None:   # "wait" - agent is closing
            break

        speak(ep, call, reply_path, max_wait_s=max_turn_listen_s)
        if call.last_state == "DISCONNECTED":
            break

    return turn_log
