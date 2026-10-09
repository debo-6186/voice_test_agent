"""The mock agent's call: a minimal pj.Call subclass (same shape as
sip_probe/local_echo.py's EchoCall - just tracks state, no blocking work in
any PJSIP callback) plus the actual multi-turn conversation loop, which runs
from the server's top-level polling loop, never nested inside a callback -
every other blocking loop in this codebase (probe.py, local_echo.py,
check_capacity.py) follows that same rule, and there is no confirmed-safe
precedent here for calling ep.libHandleEvents() reentrantly from inside one.
"""
from __future__ import annotations

import os
import time
from typing import Optional

import numpy as np
import pjsua2 as pj

from mock_agent import asr, responses
from mock_agent.errors import decide_outcome
from vqlab.corpus import tts_elevenlabs, tts_espeak

WAV_HEADER_BYTES = 44   # fixed-size canonical PCM WAV header


class MockAgentCall(pj.Call):
    def __init__(self, acc: pj.Account, call_id: int):
        super().__init__(acc, call_id)
        self.last_state: Optional[str] = None
        self.media_active = False
        self.disconnect_reason: Optional[str] = None
        self._active_media_index: Optional[int] = None
        # Long-lived refs for the whole call, per sip-harness.md trap #2 -
        # GC'ing a wrapper while PJSIP still holds the port is a segfault.
        self.player: Optional[pj.AudioMediaPlayer] = None
        self.recorder: Optional[pj.AudioMediaRecorder] = None

    def onCallState(self, prm):  # noqa: N802 - PJSUA2 callback naming
        ci = self.getInfo()
        self.last_state = ci.stateText
        if ci.state == pj.PJSIP_INV_STATE_DISCONNECTED:
            self.disconnect_reason = ci.lastReason
            self.player = None
            self.recorder = None

    def onCallMediaState(self, prm):  # noqa: N802 - PJSUA2 callback naming
        ci = self.getInfo()
        for mi in ci.media:
            if mi.type == pj.PJMEDIA_TYPE_AUDIO and mi.status == pj.PJSUA_CALL_MEDIA_ACTIVE:
                self._active_media_index = mi.index
                self.media_active = True

    def audio_media(self) -> pj.AudioMedia:
        return self.getAudioMedia(self._active_media_index)


def _read_new_rms(wav_path: str, last_offset: int) -> tuple[float, int]:
    """RMS of whatever audio bytes were appended since last_offset, read by
    raw byte offset past the fixed WAV header - not through the stdlib
    `wave` module, which trusts the RIFF header's declared data size and is
    unreliable against a file a recorder is still actively writing."""
    if not os.path.exists(wav_path):
        return 0.0, last_offset
    size = os.path.getsize(wav_path)
    if size <= last_offset:
        return 0.0, last_offset
    with open(wav_path, "rb") as f:
        f.seek(last_offset)
        chunk = f.read(size - last_offset)
    usable = len(chunk) - (len(chunk) % 2)
    if usable <= 0:
        return 0.0, last_offset + len(chunk)
    samples = np.frombuffer(chunk[:usable], dtype="<i2").astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0
    return rms, last_offset + len(chunk)


def listen_for_turn(ep: pj.Endpoint, call: MockAgentCall, scratch_wav: str,
                    max_listen_s: float = 15.0, poll_ms: int = 100,
                    silence_floor: float = 0.01, speech_floor: float = 0.02,
                    silence_s: float = 0.7) -> tuple[str, bool]:
    """Blocks until ~silence_s of inbound silence follows detected speech,
    or max_listen_s elapses. Returns (finalized wav path, heard_any_speech).
    """
    if os.path.exists(scratch_wav):
        os.remove(scratch_wav)
    recorder = pj.AudioMediaRecorder()
    recorder.createRecorder(scratch_wav)
    try:
        call.audio_media().startTransmit(recorder)
    except pj.Error:
        # The remote end can hang up in the narrow window between us
        # deciding to listen and wiring the recorder - getAudioMedia() on an
        # already-gone call throws here rather than returning something
        # stale. Treat exactly like "no speech heard", not a crash.
        call.recorder = None
        del recorder
        return scratch_wav, False
    call.recorder = recorder

    last_offset = WAV_HEADER_BYTES
    heard_speech = False
    silence_start: Optional[float] = None
    start = time.time()
    while time.time() - start < max_listen_s and call.last_state != "DISCONNECTED":
        ep.libHandleEvents(poll_ms)
        rms, last_offset = _read_new_rms(scratch_wav, last_offset)
        now = time.time()
        if rms >= speech_floor:
            heard_speech = True
            silence_start = None
        elif rms < silence_floor:
            if silence_start is None:
                silence_start = now
        else:
            silence_start = None   # ambiguous band - don't count toward silence
        if heard_speech and silence_start is not None and (now - silence_start) >= silence_s:
            break

    # stopTransmit() does not finalize the WAV - deleting the recorder does
    # (sip-harness.md trap #4).
    call.recorder = None
    del recorder
    return scratch_wav, heard_speech


def speak(ep: pj.Endpoint, call: MockAgentCall, wav_path: str, max_wait_s: float = 20.0) -> None:
    """Plays wav_path to the caller and blocks until playback finishes.
    No-ops (rather than crashing) if the call has already disconnected -
    same reasoning as listen_for_turn's try/except below."""
    if call.last_state == "DISCONNECTED":
        return
    player = pj.AudioMediaPlayer()
    player.createPlayer(wav_path, pj.PJMEDIA_FILE_NO_LOOP)
    call.player = player
    try:
        player.startTransmit(call.audio_media())
    except pj.Error:
        call.player = None
        del player
        return

    import wave
    with wave.open(wav_path, "rb") as w:
        duration_s = w.getnframes() / float(w.getframerate())

    start = time.time()
    while time.time() - start < min(duration_s + 0.5, max_wait_s):
        ep.libHandleEvents(100)
        if call.last_state == "DISCONNECTED":
            break
    call.player = None
    del player


def synth_response(text: str, lang: str, tts: str, lang_meta: dict, scratch_dir: str) -> str:
    out_path = os.path.join(scratch_dir, "_agent_response.wav")
    if tts == "espeak":
        a = tts_espeak(text, lang_meta["tts_voice"], out_path)
    else:
        a = tts_elevenlabs(text, lang_meta["elevenlabs_voice_id"], out_path)
    from vqlab.audio import write_wav
    write_wav(out_path, a)
    return out_path


def run_conversation(ep: pj.Endpoint, call: MockAgentCall, scenario_id: str, lang: str,
                     lang_meta: dict, tts: str, rng: np.random.Generator,
                     asr_error_rate: float, repair_threshold: float,
                     silent_error_rate: float, scratch_dir: str,
                     max_turn_listen_s: float = 15.0, asr_model: str = "tiny") -> list[dict]:
    """The whole call, start to hangup. Blocks; runs from the server's
    top-level loop, never nested inside a PJSIP callback. Returns the
    per-turn log (what was heard, the decided outcome, confidence) for the
    null-test calibration check."""
    os.makedirs(scratch_dir, exist_ok=True)
    whisper_language = asr.whisper_lang(lang)
    turn_log: list[dict] = []
    turn_index = 0

    while call.last_state != "DISCONNECTED":
        scratch_wav = os.path.join(scratch_dir, f"_turn_{turn_index}.wav")
        wav_path, heard_speech = listen_for_turn(ep, call, scratch_wav, max_listen_s=max_turn_listen_s)
        if call.last_state == "DISCONNECTED":
            break
        if not heard_speech:
            turn_log.append({"turn_index": turn_index, "heard_text": "", "confidence": 0.0,
                             "outcome": "timeout"})
            break

        transcript = asr.transcribe(wav_path, language=whisper_language, model_name=asr_model)
        decision = decide_outcome(transcript.text, transcript.confidence, rng,
                                  asr_error_rate=asr_error_rate,
                                  repair_threshold=repair_threshold,
                                  silent_error_rate=silent_error_rate)
        turn_log.append({
            "turn_index": turn_index, "heard_text": decision.heard_text,
            "raw_transcript": transcript.text, "confidence": round(transcript.confidence, 3),
            "outcome": decision.outcome, "forced_repair": decision.forced_repair,
            "corrupted": decision.corrupted,
        })

        text = responses.response_text(scenario_id, turn_index, decision.outcome, lang)
        response_wav = synth_response(text, lang, tts, lang_meta, scratch_dir)
        speak(ep, call, response_wav)

        if decision.outcome != "repair":
            if responses.is_closing(scenario_id, turn_index, decision.outcome):
                break
            turn_index += 1

    if call.last_state != "DISCONNECTED":
        try:
            call.hangup(pj.CallOpParam(True))
            start = time.time()
            while time.time() - start < 5.0 and call.last_state != "DISCONNECTED":
                ep.libHandleEvents(100)
        except pj.Error:
            pass

    return turn_log
