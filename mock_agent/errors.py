"""The mock agent's configurable error injection - three independent,
non-overlapping knobs, matching docs/cli.md's `polycall demo` flags.

Pure functions, no PJSIP involved, so this is unit-testable on its own and
the null-test calibration (set a rate, confirm the measured rate matches) can
be checked against this module directly before ever placing a call.

- repair_threshold: REAL, confidence-based. Below this, the agent asks for a
  repair - this is realistic and needs no tuning, since whisper's own
  confidence already drops under degraded SNR (confirmed empirically: 0.81
  clean -> 0.73 at 10dB -> 0.50 at 0dB on this project's own ASR).
- asr_error_rate: SYNTHETIC, independent of real confidence. An *additional*
  flat probability of forcing the repair outcome even when real confidence
  was fine - the calibratable ground-truth knob.
- silent_error_rate: among turns that do NOT repair, the probability the
  logged "heard" value is deliberately wrong while the agent responds as if
  certain - the dangerous, unflagged case from PLAN.md's worked example.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Outcome:
    outcome: str          # "ok" | "repair" | "silent_error"
    heard_text: str        # what the agent "logs" as having heard - possibly corrupted
    confidence: float
    forced_repair: bool    # True if asr_error_rate (not real confidence) caused the repair
    corrupted: bool         # True if silent_error_rate corrupted the logged value


def _corrupt(text: str, rng: np.random.Generator) -> str:
    """Flip one character in the heard text, simulating a plausible single
    digit/letter mishearing rather than wholesale nonsense.

    Only picks among alnum positions - picking a random index into the raw
    string and silently no-op'ing when it lands on punctuation/whitespace
    (common in these full-sentence turns) would log corrupted=True while
    the text stayed byte-identical, which is exactly the kind of lie this
    fixture exists to never tell."""
    chars = list(text)
    eligible = [i for i, c in enumerate(chars) if c.isalnum()]
    if not eligible:
        return text
    idx = eligible[int(rng.integers(0, len(eligible)))]
    c = chars[idx]
    if c.isdigit():
        chars[idx] = str((int(c) + int(rng.integers(1, 9))) % 10)
    else:
        chars[idx] = chr((ord(c.lower()) - ord("a") + int(rng.integers(1, 25))) % 26 + ord("a"))
    return "".join(chars)


def decide_outcome(heard_text: str, confidence: float, rng: np.random.Generator,
                   asr_error_rate: float = 0.0, repair_threshold: float = 0.0,
                   silent_error_rate: float = 0.0) -> Outcome:
    # 1. real-confidence-based repair
    if confidence < repair_threshold:
        return Outcome("repair", heard_text, confidence, forced_repair=False, corrupted=False)

    # 2. synthetic, ground-truth-controllable repair rate
    if rng.random() < asr_error_rate:
        return Outcome("repair", heard_text, confidence, forced_repair=True, corrupted=False)

    # 3. among confident, non-repairing turns: silent (unflagged) corruption
    if rng.random() < silent_error_rate:
        return Outcome("silent_error", _corrupt(heard_text, rng), confidence,
                       forced_repair=False, corrupted=True)

    return Outcome("ok", heard_text, confidence, forced_repair=False, corrupted=False)
