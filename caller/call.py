"""The real caller (Phase 6): listens to the agent, transcribes, classifies
intent, and plays the matching canonical/utility/adaptive turn - replacing
scripts/test_mock_agent_call.py's fixed-order driver. Same blocking,
top-level-loop shape as mock_agent/call.py's run_conversation - never nested
inside a PJSIP callback, matching every other blocking loop in this codebase.
"""
from __future__ import annotations

import os
import time

import pjsua2 as pj

from mock_agent import asr
from mock_agent.call import MockAgentCall, listen_for_turn, speak_and_listen, synth_response

from caller import utility
from caller.adaptive import generate_reply_text, synth_and_degrade
from caller.intent import classify
from caller.state_machine import CallerState
from referee.entities import match_entity
from referee.normalize import normalize_for_lang

# MockAgentCall is generic (tracks media_active/last_state/player/recorder
# only) - reused unchanged as the caller's own call object, same as
# scripts/test_mock_agent_call.py already does.
CallerCall = MockAgentCall


def _listen_and_transcribe(ep, call, scratch_path, max_listen_s, silence_s,
                           whisper_language, asr_model, asr_backend="local"):
    """Pure listen, no playback - used for the opening greeting-check (we
    haven't said anything yet) and for the empty-transcript retry (we
    already said our piece; just giving the far end a second chance to be
    heard, not repeating ourselves). Returns (transcript_or_None, heard)."""
    wav_path, heard = listen_for_turn(
        ep, call, scratch_path, max_listen_s=max_listen_s, silence_s=silence_s)
    if not heard:
        return None, False
    return asr.transcribe(wav_path, language=whisper_language, model_name=asr_model,
                          backend=asr_backend), True


def _speak_and_transcribe(ep, call, wav_path, scratch_path, max_wait_s, max_listen_s,
                          silence_s, whisper_language, asr_model, asr_backend="local"):
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
    return asr.transcribe(wav_out, language=whisper_language, model_name=asr_model,
                          backend=asr_backend), True


def _retry_if_empty(ep, call, scratch_dir, tag, transcript, heard, max_listen_s,
                    silence_s, whisper_language, asr_model, asr_backend="local"):
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
        ep, call, retry_path, max_listen_s, silence_s, whisper_language, asr_model, asr_backend)
    if heard2:
        return transcript2, True, True
    return transcript, heard, True   # retry found nothing either - keep the original (still empty)


def _react(agent_text: str, i: int, state: CallerState, lang: str, lang_meta: dict,
          tts: str, condition: dict, scratch_dir: str, tier3_threshold: float) -> tuple[dict, str | None]:
    """Classifies one heard (already-transcribed) agent utterance and
    resolves the reply WAV to play next. Returns (log_row,
    reply_path_or_None) - reply_path is None only for the "wait" action
    (agent is closing).

    Before classifying intent at all: does agent_text ITSELF already
    confirm one of the still-outstanding fields? Deliberately
    intent-independent - confirmed live this has to be, not keyed off a
    "confirm" label: the real stuck-loop case had the agent say "Just to
    confirm you'd like an appointment on October 15th... You're all set...
    Goodbye!" in ONE utterance that Laya could only label as "closing"
    (confidence 0.475, since the utterance IS mostly a closing) - there is
    no intent label for "closing AND incidentally restates a field," so
    checking cls.intent for "confirm" (tried first, see git history) never
    fires on this exact real case. Checking the agent's own words directly
    against each remaining field (same match_entity containment check the
    offline scorer uses) catches it regardless of what single intent label
    Laya assigns. Matters architecturally, not just for this one call:
    without this, state_machine.py's own closing_with_fields_remaining
    guardrail keeps correctly detecting "date still looks outstanding" and
    correctly re-routing to adaptive, forever - the guardrail was never
    wrong, the field tracking feeding it was."""
    normalized_agent_text = normalize_for_lang(agent_text, lang)
    confirmed_by_agent = []
    for field_name in state.remaining_fields():
        turn = state.turn_for_field(field_name)
        entities = turn.get("entities", {}).get(lang) if turn else None
        if not entities:
            continue
        field_type, expected = entities[0]["type"], entities[0]
        matched, _, _ = match_entity(field_type, agent_text, normalized_agent_text, lang, expected)
        if matched:
            state.collected[field_name] = True
            confirmed_by_agent.append(field_name)

    cls = classify(agent_text, tier3_threshold=tier3_threshold)
    action = state.next_action(cls, agent_text, confirmed_by_agent)

    row = {
        "i": i, "agent_text": agent_text, "intent": cls.intent,
        "confidence": round(cls.confidence, 3), "tier": cls.tier,
        "action": action.kind, "divergence": action.divergence,
        "divergence_reason": action.divergence_reason,
    }
    # This turn's row is the only record referee/score.py's score_call() has
    # of WHICH turn supplied a field - confirmed live, hi-IN/A_cafe_snr05:
    # this pre-check correctly marked state.collected["date"]=True from the
    # agent's own turn-1 speech ("15th of October"), the call ended cleanly
    # with no live bug - but never having recorded THAT TURN as the one that
    # did it, score_call() found no row with field=="date" anywhere in the
    # log and scored it "never_reached" (a false negative - the date was
    # real audio, correctly heard, confirmed live by a human listening to
    # the recording). field/fields_also_addressed must include every field
    # this turn confirmed, not just whatever action.field happens to be.
    logged_fields = list(confirmed_by_agent)
    if action.field and action.field not in logged_fields:
        logged_fields.append(action.field)
    if logged_fields:
        row["field"] = logged_fields[0]
        if len(logged_fields) > 1:
            row["fields_also_addressed"] = logged_fields[1:]
    # Which of this row's fields were confirmed in the AGENT's OWN incoming
    # speech (confirmed_by_agent) vs the CALLER's own outgoing action
    # (action.field - giving/restating a field to the vendor). referee/
    # score.py needs this distinction to pick the right search window:
    # confirmed live, hi-IN/ctl_noise_only, that both the phone and date
    # confirmations were correctly heard IN THIS SAME TURN'S agent speech
    # (literally the words that triggered confirmed_by_agent), but
    # score_call() anchors its search to start AFTER the NEXT turn - built
    # for the opposite case, where the CALLER states a field and the search
    # must skip past that turn to avoid matching the caller's own words as
    # if the vendor had said them. Anchoring to "after next turn" when the
    # vendor's confirmation is already sitting in THIS turn skips right past
    # real, correct content - a false negative on two fields that were
    # genuinely, audibly correct.
    if confirmed_by_agent:
        row["fields_confirmed_in_agent_speech"] = confirmed_by_agent

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
    remaining_values = state.remaining_field_values(lang)
    # state_machine.py sets action.field on a play_adaptive Action only for
    # the "already-collected field being re-asked, canonical replay already
    # tried once" case (field_already_collected_restate) - that field is
    # deliberately absent from remaining/remaining_values since it's really
    # done, but generate_reply_text still needs its reference value so it
    # restates the known-correct fact instead of inventing one. Confirmed
    # live this matters: with no reference at all here, Gemini invented
    # "tomorrow" in place of October 15th, and the vendor closed the call
    # on that wrong date, having taken our own fabrication at face value.
    if action.field and action.field not in remaining:
        turn = state.turn_for_field(action.field)
        if turn and lang in turn.get("text", {}):
            remaining = remaining + [action.field]
            remaining_values = {**remaining_values, action.field: turn["text"][lang]}
    reply_text, reason = generate_reply_text(
        agent_text, lang, remaining, remaining_values,
        best_guess_intent=cls.intent, best_guess_confidence=cls.confidence)
    row["reply_text"] = reply_text
    row["divergence_reason"] = row["divergence_reason"] or reason

    # Did this adaptive reply actually VOLUNTEER one of the remaining
    # fields (now that it's handed the correct reference - see
    # generate_reply_text's docstring)? Only play_canonical ever set
    # state.collected[field]/row["field"] before - an adaptive reply that
    # correctly supplies a field was invisible to both the state machine
    # (remaining_fields() kept listing it forever) and referee/score.py's
    # fields_seen check (scored "never_reached" despite being said and
    # correctly confirmed). Confirmed live: under C_loss5, the caller
    # correctly said "the fifteenth of October" at turn 1 and the agent
    # confirmed "October 15th" by turn 4, but because "date" never got
    # marked collected, the call looped for 20 more turns - every
    # "closing" from the agent kept getting redirected by this project's
    # own closing_with_fields_remaining guardrail, which still (correctly,
    # given the bug) saw date as outstanding. Reuses referee/entities.py's
    # match_entity - the exact same containment check the offline scorer
    # uses - against the caller's own OUTGOING text, not the agent's.
    normalized_reply = normalize_for_lang(reply_text, lang)
    addressed_fields = []
    for field_name in remaining:
        turn = state.turn_for_field(field_name)
        entities = turn.get("entities", {}).get(lang) if turn else None
        if not entities:
            continue
        field_type, expected = entities[0]["type"], entities[0]
        matched, _, _ = match_entity(field_type, reply_text, normalized_reply, lang, expected)
        if matched:
            addressed_fields.append(field_name)

    # REMOVED (confirmed live, hi-IN): used to extend the same blanket
    # "confirm means every remaining field is settled" trust from
    # state_machine.py's CONFIDENT confirm branch down into this tier-3,
    # BELOW-threshold path - on the theory that Laya's rough guess was
    # still real signal even under threshold. It wasn't, for this specific
    # case: Laya tier-3-classified a content-free filler
    # ("ठीक है प्रिया जी, मैं आपकी नई appointment book करने में मदद करूँगी" -
    # "OK, I'll help you book a new appointment" - not a readback of
    # anything) as "confirm" at 0.586/0.726 confidence, and this extension
    # then marked phone AND date collected with neither ever having been
    # given. Combined with this file's own "closing + nothing remaining ->
    # end the call" fix, that false completion now ends the call
    # immediately and silently instead of eventually working itself out
    # through more turns - confirmed on 2 of 15 real hi-IN/Telnyx calls in
    # one batch (ctl_clean, A_cafe_snr05). This extension's own comment
    # already flagged it as "NOT itself confirmed against a real failure" -
    # unlike the content-based check above (match_entity against the
    # agent's actual words), which stays: it only ever credits a field the
    # agent's text really contains.
    for field_name in addressed_fields:
        state.collected[field_name] = True
    if addressed_fields:
        # MERGE with whatever the top-of-function pre-check already put in
        # row["field"]/fields_also_addressed (the agent's OWN incoming
        # speech confirming a field) - not an overwrite. Clobbering that
        # here would reintroduce the exact "never_reached" scoring bug this
        # row-tracking exists to prevent, just from the opposite direction
        # (the caller's own outgoing reply addressing a field would have
        # erased the record of the agent having already confirmed a
        # DIFFERENT one earlier in this same turn).
        existing = [row["field"]] if row.get("field") else []
        existing += row.get("fields_also_addressed", [])
        merged = existing + [f for f in addressed_fields if f not in existing]
        row["field"] = merged[0]
        if len(merged) > 1:
            row["fields_also_addressed"] = merged[1:]
        elif "fields_also_addressed" in row:
            del row["fields_also_addressed"]

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
            asr_model: str = "base", asr_backend: str = "local", tier3_threshold: float = 0.75,
            max_turns: int = 20, opening_wait_s: float = 4.0,
            silence_s: float = 0.7) -> list[dict]:
    """silence_s: how long a gap must be before we conclude the agent
    stopped talking (docs/sip-harness.md's 700ms convention, matching real
    human turn-taking). Lower = less dead air per turn, but higher risk of
    cutting the agent off mid-sentence during a natural pause - tune with
    that tradeoff in mind, not as a free latency win.

    TWO SEPARATE continuous recorders run for the whole call, neither torn
    down between turns the way the per-turn recorders below are:
    - `_full_call.wav` (call.full_recorder): AGENT AUDIO ONLY, same
      incoming-only tap as every per-turn recorder - this is what
      referee/score.py actually scores from. Confirmed live that the
      per-turn recorders below (torn down on OUR OWN silence_s heuristic,
      not on anything about the actual audio) can permanently cut off real
      agent speech that silence_s guessed was a pause; this fixes that by
      never tearing down early.
    - `_full_call_both_sides.wav` (call.mix_recorder): BOTH sides mixed -
      also fed the caller's own outgoing TTS (mock_agent.call.speak_and_
      listen wires this in). For a human to listen to and verify a call by
      ear; NEVER read by scoring - mixing the caller's own scripted lines
      (which already contain the correct name/phone/date) into the scoring
      file would make every field look "preserved" regardless of whether
      the agent ever said it back.
    The per-turn recorders and all live turn-taking logic are untouched by
    either of these. Each turn_log row gets a `t_offset_s`: the second into
    the recordings when THIS turn started listening - a coarse marker for
    referee/score.py to search forward from, not a precise per-turn
    boundary (applies to `_full_call.wav`'s timeline; both recorders start
    at the same call_start_ts so the same offset lands at the same moment
    in either file)."""
    os.makedirs(scratch_dir, exist_ok=True)
    whisper_language = asr.whisper_lang(lang)
    state = CallerState(turns=turns_def)
    turn_log: list[dict] = []

    call_start_ts = time.time()
    full_call_wav = os.path.join(scratch_dir, "_full_call.wav")
    full_recorder = pj.AudioMediaRecorder()
    full_recorder.createRecorder(full_call_wav)
    try:
        call.audio_media().startTransmit(full_recorder)
        call.full_recorder = full_recorder
    except pj.Error:
        # Call already gone in the narrow window before we could wire this
        # up - same as every other startTransmit() call site in this
        # codebase: treat as "nothing to record", not a crash.
        full_recorder = None

    mix_call_wav = os.path.join(scratch_dir, "_full_call_both_sides.wav")
    mix_recorder = pj.AudioMediaRecorder()
    mix_recorder.createRecorder(mix_call_wav)
    try:
        call.audio_media().startTransmit(mix_recorder)
        call.mix_recorder = mix_recorder
    except pj.Error:
        mix_recorder = None

    try:
        # A real vendor agent typically greets first ("Thanks for calling,
        # can I take your name?" - PLAN.md's own worked example); our
        # scenario turns are themselves written as caller-initiated opening
        # lines ("Hi there, yes, my name is..."). Try a short listen for a
        # greeting first; if one arrives, react to it normally; if nothing
        # arrives, this caller opens unprompted - same as a real person
        # would, rather than two silent listeners deadlocking forever. (No
        # playback yet here, so this is a pure listen - nothing to combine
        # it with.)
        opening_path = os.path.join(scratch_dir, "_agent_opening.wav")
        opening_listen_start = time.time()
        opening_transcript, heard_greeting = _listen_and_transcribe(
            ep, call, opening_path, opening_wait_s, silence_s, whisper_language, asr_model,
            asr_backend)
        opening_transcript, heard_greeting, opening_retried = _retry_if_empty(
            ep, call, scratch_dir, "opening", opening_transcript, heard_greeting,
            opening_wait_s, silence_s, whisper_language, asr_model, asr_backend)
        if call.last_state == "DISCONNECTED":
            return turn_log

        reply_path: str | None
        if heard_greeting:
            row, reply_path = _react(opening_transcript.text, -1, state, lang, lang_meta, tts,
                                     condition, scratch_dir, tier3_threshold)
            row["t_offset_s"] = round(opening_listen_start - call_start_ts, 2)
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
                             "field": first_turn["field"], "reply_text": first_turn["text"][lang],
                             "t_offset_s": 0.0})
            reply_path = first_turn["_wav_path"]

        for i in range(max_turns):
            scratch_wav = os.path.join(scratch_dir, f"_agent_turn_{i}.wav")
            listen_start = time.time()
            transcript, heard = _speak_and_transcribe(
                ep, call, reply_path, scratch_wav, max_turn_listen_s, max_turn_listen_s,
                silence_s, whisper_language, asr_model, asr_backend)
            transcript, heard, retried = _retry_if_empty(
                ep, call, scratch_dir, f"turn_{i}", transcript, heard,
                max_turn_listen_s, silence_s, whisper_language, asr_model, asr_backend)

            if call.last_state == "DISCONNECTED":
                # A turn that WAS actually heard must still be reacted to
                # and scored, even though the far end has already hung up.
                # Confirmed live (hi-IN/S3, mock agent): the far end
                # routinely hangs up immediately after speaking its closing
                # line, with no linger - a normal, successful end of call,
                # not a mid-listen drop. The old code logged
                # "disconnected_while_listening" here unconditionally and
                # threw the captured, correctly-transcribed closing turn
                # away unscored, even though _speak_and_transcribe had
                # already captured and transcribed it fine. Only skip
                # reacting when there is truly nothing captured (not heard,
                # or heard but ASR returned empty) - same condition that
                # already guards the react call below for the normal path.
                if heard and transcript.text.strip():
                    row, _ = _react(transcript.text, i, state, lang, lang_meta, tts,
                                    condition, scratch_dir, tier3_threshold)
                    row["t_offset_s"] = round(listen_start - call_start_ts, 2)
                    if retried:
                        row["retried_empty_transcript"] = True
                    turn_log.append(row)
                else:
                    # Every iteration attempted gets a log row, even this
                    # one - a silent gap here previously made two real
                    # races (model cold-load latency, then adaptive-reply
                    # latency, each separately outrunning the far end's
                    # listen window) look like the loop just stopped,
                    # instead of showing where and why.
                    turn_log.append({"i": i, "outcome": "disconnected_while_listening",
                                     "retried": retried})
                break
            if not heard:
                turn_log.append({"i": i, "outcome": "timeout", "retried": retried})
                break

            row, reply_path = _react(transcript.text, i, state, lang, lang_meta, tts, condition,
                                     scratch_dir, tier3_threshold)
            row["t_offset_s"] = round(listen_start - call_start_ts, 2)
            if retried:
                row["retried_empty_transcript"] = True
            turn_log.append(row)
            if reply_path is None:   # "wait" - agent is closing
                break

        return turn_log
    finally:
        # del, not just None - deleting the recorder is what finalizes the
        # WAV (sip-harness.md trap #4: stopTransmit() alone does not).
        if full_recorder is not None:
            call.full_recorder = None
            del full_recorder
        if mix_recorder is not None:
            call.mix_recorder = None
            del mix_recorder
