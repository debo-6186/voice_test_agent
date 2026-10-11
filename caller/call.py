"""The real caller (Phase 6): listens to the agent, transcribes, classifies
intent, and plays the matching canonical/utility/adaptive turn - replacing
scripts/test_mock_agent_call.py's fixed-order driver. Same blocking,
top-level-loop shape as mock_agent/call.py's run_conversation - never nested
inside a PJSIP callback, matching every other blocking loop in this codebase.
"""
from __future__ import annotations

import os

from mock_agent import asr
from mock_agent.call import MockAgentCall, listen_for_turn, speak_and_listen, synth_response

from caller import utility
from caller.adaptive import generate_reply_text, synth_and_degrade
from caller.intent import classify
from caller.state_machine import CallerState

# MockAgentCall is generic (tracks media_active/last_state/player/recorder
# only) - reused unchanged as the caller's own call object, same as
# scripts/test_mock_agent_call.py already does.
CallerCall = MockAgentCall


def _listen_and_transcribe(ep, call, scratch_path, max_listen_s, silence_s,
                           whisper_language, asr_model):
    """Pure listen, no playback - used for the opening greeting-check (we
    haven't said anything yet) and for the empty-transcript retry (we
    already said our piece; just giving the far end a second chance to be
    heard, not repeating ourselves). Returns (transcript_or_None, heard)."""
    wav_path, heard = listen_for_turn(
        ep, call, scratch_path, max_listen_s=max_listen_s, silence_s=silence_s)
    if not heard:
        return None, False
    return asr.transcribe(wav_path, language=whisper_language, model_name=asr_model), True


def _speak_and_transcribe(ep, call, wav_path, scratch_path, max_wait_s, max_listen_s,
                          silence_s, whisper_language, asr_model):
    """Plays wav_path while simultaneously listening for the response
    (mock_agent.call.speak_and_listen), so a far-end barge-in is captured
    from its actual start - verified live this matters: a real vendor's
    "What date works for you?" was recorded as just "date works for you."
    because the previous sequential speak-then-listen design wasn't
    listening yet when the far end started talking. Returns
    (transcript_or_None, heard)."""
    wav_out, heard = speak_and_listen(
        ep, call, wav_path, scratch_path, max_wait_s=max_wait_s,
        max_listen_s=max_listen_s, silence_s=silence_s)
    if not heard:
        return None, False
    return asr.transcribe(wav_out, language=whisper_language, model_name=asr_model), True


def _retry_if_empty(ep, call, scratch_dir, tag, transcript, heard, max_listen_s,
                    silence_s, whisper_language, asr_model):
    """One free retry on an empty transcript - a pure listen (nothing to
    replay; we already said our piece). An empty transcript guarantees tier
    3 escalation with nothing real to classify - usually because the
    recorder started listening a beat after the agent did and clipped a
    short utterance, not because the agent said nothing. Returns
    (transcript_or_None, heard, retried)."""
    if not heard or transcript.text.strip() or call.last_state == "DISCONNECTED":
        return transcript, heard, False
    retry_path = os.path.join(scratch_dir, f"_agent_{tag}_retry.wav")
    transcript2, heard2 = _listen_and_transcribe(
        ep, call, retry_path, max_listen_s, silence_s, whisper_language, asr_model)
    if heard2:
        return transcript2, True, True
    return transcript, heard, True   # retry found nothing either - keep the original (still empty)


def _react(agent_text: str, i: int, state: CallerState, lang: str, lang_meta: dict,
          tts: str, condition: dict, scratch_dir: str, tier3_threshold: float) -> tuple[dict, str | None]:
    """Classifies one heard (already-transcribed) agent utterance and
    resolves the reply WAV to play next. Returns (log_row,
    reply_path_or_None) - reply_path is None only for the "wait" action
    (agent is closing)."""
    cls = classify(agent_text, tier3_threshold=tier3_threshold)
    action = state.next_action(cls)

    row = {
        "i": i, "agent_text": agent_text, "intent": cls.intent,
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
        agent_text, lang, remaining,
        best_guess_intent=cls.intent, best_guess_confidence=cls.confidence)
    row["reply_text"] = reply_text
    row["divergence_reason"] = row["divergence_reason"] or reason
    # Pass the caller's real --tts through, not a forced "espeak": an
    # earlier version always forced espeak here for speed, but verified
    # live that cost real clarity - a vendor's own ASR mis-transcribed a
    # robotic reply ("Wait, I haven't given you the date yet." came back
    # as "Wade... the name yet."), which can cause more confusion than the
    # ~2s it saved. caller/adaptive.py's synth_and_degrade() already picks
    # ElevenLabs' low-latency eleven_flash_v2_5 model when tts=="elevenlabs"
    # (0.39s, verified live - barely slower than espeak's 0.07s), so this
    # gets real voice clarity without paying the full 3.19s default-model cost.
    reply_path = synth_and_degrade(
        reply_text, lang, lang_meta, tts, condition,
        os.path.join(scratch_dir, f"_adaptive_{i}.wav"), scratch_dir)
    return row, reply_path


def run_call(ep, call: CallerCall, turns_def: list[dict], lang: str, lang_meta: dict,
            tts: str, condition: dict, scratch_dir: str, max_turn_listen_s: float = 15.0,
            asr_model: str = "base", tier3_threshold: float = 0.75,
            max_turns: int = 20, opening_wait_s: float = 4.0,
            silence_s: float = 0.7) -> list[dict]:
    """silence_s: how long a gap must be before we conclude the agent
    stopped talking (docs/sip-harness.md's 700ms convention, matching real
    human turn-taking). Lower = less dead air per turn, but higher risk of
    cutting the agent off mid-sentence during a natural pause - tune with
    that tradeoff in mind, not as a free latency win."""
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
    # listeners deadlocking forever. (No playback yet here, so this is a
    # pure listen - nothing to combine it with.)
    opening_path = os.path.join(scratch_dir, "_agent_opening.wav")
    opening_transcript, heard_greeting = _listen_and_transcribe(
        ep, call, opening_path, opening_wait_s, silence_s, whisper_language, asr_model)
    opening_transcript, heard_greeting, opening_retried = _retry_if_empty(
        ep, call, scratch_dir, "opening", opening_transcript, heard_greeting,
        opening_wait_s, silence_s, whisper_language, asr_model)
    if call.last_state == "DISCONNECTED":
        return turn_log

    reply_path: str | None
    if heard_greeting:
        row, reply_path = _react(opening_transcript.text, -1, state, lang, lang_meta, tts,
                                 condition, scratch_dir, tier3_threshold)
        if opening_retried:
            row["retried_empty_transcript"] = True
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

    for i in range(max_turns):
        scratch_wav = os.path.join(scratch_dir, f"_agent_turn_{i}.wav")
        transcript, heard = _speak_and_transcribe(
            ep, call, reply_path, scratch_wav, max_turn_listen_s, max_turn_listen_s,
            silence_s, whisper_language, asr_model)
        transcript, heard, retried = _retry_if_empty(
            ep, call, scratch_dir, f"turn_{i}", transcript, heard,
            max_turn_listen_s, silence_s, whisper_language, asr_model)
        if call.last_state == "DISCONNECTED":
            # Every iteration attempted gets a log row, even this one - a
            # silent gap here previously made two real races (model cold-
            # load latency, then adaptive-reply latency, each separately
            # outrunning the far end's listen window) look like the loop
            # just stopped, instead of showing where and why.
            turn_log.append({"i": i, "outcome": "disconnected_while_listening", "retried": retried})
            break
        if not heard:
            turn_log.append({"i": i, "outcome": "timeout", "retried": retried})
            break

        row, reply_path = _react(transcript.text, i, state, lang, lang_meta, tts, condition,
                                 scratch_dir, tier3_threshold)
        if retried:
            row["retried_empty_transcript"] = True
        turn_log.append(row)
        if reply_path is None:   # "wait" - agent is closing
            break

    return turn_log
