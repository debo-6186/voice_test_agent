"""In-loop ASR for the mock agent - whisper-base, int8 CPU.

docs/stack.md: in-loop ASR is small/fast/accuracy-tolerant, production target
is "whisper-small/base, GPU sidecar". This machine has no GPU sidecar, so
"base" (not "tiny") is the CPU default here - verified directly, not assumed:
on a clipped/truncated utterance (recorder starting after the agent had
already begun talking, a real live-call failure mode), "tiny" hallucinates
plausible-but-wrong words ("October fees", "The point is that...") rather
than honestly transcribing only what's present, which misleads the intent
classifier more than an honestly incomplete transcript would. "base" never
hallucinated on the same clipped test files, at an acceptable latency cost
(~0.5-0.6s vs ~0.3s, both well under a second; warm load ~0.7s, same as tiny).
Loaded once, lazily, at module scope - never per-call (CLAUDE.md: "one GPU
sidecar, not a model per worker", applied at dev scale to mean one
process-wide model, not one per call).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

_model = None
_model_name = None


def _get_model(model_name: str = "base"):
    global _model, _model_name
    if _model is None or _model_name != model_name:
        from faster_whisper import WhisperModel
        _model = WhisperModel(model_name, device="cpu", compute_type="int8")
        _model_name = model_name
    return _model


@dataclass
class Transcript:
    text: str
    confidence: float   # 0-1 proxy, exp(avg_logprob) clipped to [0, 1]
    avg_logprob: float


def whisper_lang(bcp47: str) -> str:
    """BCP-47 (en-US, en-SG, hi-IN, es-ES, id-ID) -> whisper's ISO-639-1 code.
    Whisper doesn't distinguish en-US/en-SG; the project's own accent-fallback
    convention (docs/vendors.md) already accepts that regional variants
    collapse onto one base code."""
    return bcp47.split("-")[0].lower()


def transcribe(wav_path: str, language: str | None = None, model_name: str = "base") -> Transcript:
    model = _get_model(model_name)
    segments, _info = model.transcribe(wav_path, language=language)
    segs = list(segments)
    if not segs:
        return Transcript(text="", confidence=0.0, avg_logprob=-999.0)
    text = " ".join(s.text.strip() for s in segs)
    avg_logprob = sum(s.avg_logprob for s in segs) / len(segs)
    confidence = max(0.0, min(1.0, math.exp(avg_logprob)))
    return Transcript(text=text, confidence=confidence, avg_logprob=avg_logprob)
