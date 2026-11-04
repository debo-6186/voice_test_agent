"""The single referee ASR model for Phase 8's scoring pipeline - Whisper
large-v3, run via Groq's hosted API rather than self-hosted (the EC2 box
used throughout this project is CPU-only; large-v3 self-hosted there would
be impractically slow for anything beyond a one-off check).

A DELIBERATE, STATED DEVIATION from this project's own CLAUDE.md
("per-language referees are mandatory") and PLAN.md ("two referees, publish
their agreement"): user's explicit choice after being shown the cost (on
Singapore English, Whisper large-v3-turbo scores 32.02% WER vs
MERaLiON-2-10B-ASR's 4.62% on the same corpus - a uniform referee partly
measures Whisper's own weak spots on en-SG/hi-IN, not just the vendor).
MERaLiON itself is a free HuggingFace open-weight model, not a paid API,
but needs real GPU inference this project doesn't currently have - avoided
here rather than provisioning GPU compute. No dual-referee/agreement metric
either, for the same reason. Both documented here and in the project plan
file, not silently diverged from - same pattern already used for descoping
Vapi/Bland/Pipecat (CLAUDE.md/PLAN.md).
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass

from mock_agent.asr import whisper_lang
from vendors.http import VendorHTTPError, env, post_multipart
from vqlab import audio as vqlab_audio

GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODEL = "whisper-large-v3"


class RefereeASRError(VendorHTTPError):
    pass


@dataclass
class RefereeTranscript:
    text: str
    language: str | None
    words: list[dict] | None      # only populated when response_format="verbose_json" -
                                   # confirmed live Groq never actually fills this in,
                                   # kept only so a future format change is visible here
    segments: list[dict] | None   # verbose_json's segment list: [{start, end, text}, ...] -
                                   # the per-field time markers referee/entities.py searches by
    raw: dict                     # full response, kept for provenance/debugging


def transcribe_wav(wav_path: str, bcp47_lang: str, *,
                   response_format: str = "verbose_json",
                   cache_path: str | None = None) -> RefereeTranscript:
    """Transcribes one WAV via Groq's hosted whisper-large-v3. Called ONCE
    per call against the continuous `_full_call.wav` (caller/call.py), not
    once per turn against a per-turn file - a per-turn recorder is torn
    down on our own live silence-detection heuristic, not on anything about
    the actual audio, and confirmed live to sometimes cut off real agent
    speech before this function ever saw it. No ASR model, however good,
    can transcribe audio a recorder never captured.

    cache_path, when given, is checked first and written after a successful
    call - makes a scoring run idempotent/resumable and avoids re-paying
    Groq for a call already scored (scripts/score_run.py's primary use)."""
    if cache_path and os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            raw = json.load(f)
        return RefereeTranscript(text=raw.get("text", ""), language=raw.get("language"),
                                 words=raw.get("words"), segments=raw.get("segments"), raw=raw)

    headers = {"Authorization": f"Bearer {env('GROQ_API_KEY', error_cls=RefereeASRError)}"}
    fields = {"model": MODEL, "language": whisper_lang(bcp47_lang),
             "response_format": response_format}
    resp = post_multipart(GROQ_TRANSCRIBE_URL, fields, "file", wav_path, headers,
                          file_content_type="audio/wav", error_cls=RefereeASRError,
                          timeout_s=60.0)

    if cache_path:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(resp, f, ensure_ascii=False, indent=2)

    return RefereeTranscript(text=resp.get("text", ""), language=resp.get("language"),
                             words=resp.get("words"), segments=resp.get("segments"), raw=resp)


def transcribe_wav_chunked(wav_path: str, bcp47_lang: str, *,
                           cache_path: str | None = None) -> RefereeTranscript:
    """Like transcribe_wav(), but VAD-chunks the audio via
    vqlab.audio.speech_regions() first - one Groq call per speech region
    instead of one call for the whole file. Confirmed live on id-ID, on
    BOTH the referee's own multi-minute `_full_call.wav` scoring and the
    caller's in-loop ASR on a single per-turn clip: a long stretch of
    silence (interleaved pauses across a whole call, or just the vendor's
    own several-second response latency before it starts talking on one
    turn) makes Whisper silently collapse real speech into a short
    hallucinated fragment. A real Telnyx turn - "Halo, Budi Santoso...
    Bisa saya dapatkan nomor telepon yang bisa dihubungi?", ~8s of real
    speech after ~9.6s of leading silence in an 18s clip - came back as
    just "Terima kasih." unchunked, and transcribed correctly in full once
    isolated to its own speech region. Returns the merged RefereeTranscript
    - segments shifted back to absolute time within wav_path, text joined
    with spaces - same shape as transcribe_wav(), so callers never need to
    know chunking happened."""
    if cache_path and os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            raw = json.load(f)
        return RefereeTranscript(text=raw.get("text", ""), language=raw.get("language"),
                                 words=raw.get("words"), segments=raw.get("segments"), raw=raw)

    audio = vqlab_audio.load(wav_path)
    regions = vqlab_audio.speech_regions(audio.samples, audio.sr)

    all_segments: list[dict] = []
    text_parts: list[str] = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for i, (start_s, end_s) in enumerate(regions):
            start_i, end_i = int(start_s * audio.sr), int(end_s * audio.sr)
            chunk = vqlab_audio.Audio(audio.samples[start_i:end_i], audio.sr)
            chunk_path = os.path.join(tmpdir, f"chunk_{i}.wav")
            vqlab_audio.write_wav(chunk_path, chunk)

            t = transcribe_wav(chunk_path, bcp47_lang)
            if t.text.strip():
                text_parts.append(t.text.strip())
            for seg in (t.segments or []):
                all_segments.append({**seg, "start": seg["start"] + start_s, "end": seg["end"] + start_s})

    all_segments.sort(key=lambda s: s["start"])
    merged_text = " ".join(text_parts)
    raw = {"text": merged_text, "segments": all_segments, "language": bcp47_lang,
          "_chunked": True, "_n_chunks": len(regions)}
    if cache_path:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(raw, f, ensure_ascii=False, indent=2)
    return RefereeTranscript(text=merged_text, language=bcp47_lang, words=None,
                             segments=all_segments, raw=raw)
