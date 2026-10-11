"""Telephony codec round-trips via ffmpeg.

Encoding and immediately decoding through a real codec is the honest way to
reproduce what a phone line does to speech. The quantisation noise of G.711,
the band limit of GSM and the warble of a starved Opus stream are all things
no EQ curve will imitate.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from functools import lru_cache

import numpy as np

from ..audio import Audio, read_wav, write_wav

# name -> (ffmpeg encode args, codec sample rate, ffmpeg container/format)
CODECS: dict[str, dict] = {
    "none":      {"args": [], "sr": None, "fmt": None},
    "g711u":     {"args": ["-acodec", "pcm_mulaw"], "sr": 8000,  "fmt": "wav"},
    "g711a":     {"args": ["-acodec", "pcm_alaw"],  "sr": 8000,  "fmt": "wav"},
    "g722":      {"args": ["-acodec", "adpcm_g722"], "sr": 16000, "fmt": "wav"},
    "g726_16":   {"args": ["-acodec", "adpcm_g726", "-b:a", "16k"], "sr": 8000, "fmt": "g726",
                  "dec": ["-f", "g726", "-code_size", "2", "-ar", "8000"]},
    "g726_32":   {"args": ["-acodec", "adpcm_g726", "-b:a", "32k"], "sr": 8000, "fmt": "g726",
                  "dec": ["-f", "g726", "-code_size", "4", "-ar", "8000"]},
    "gsm":       {"args": ["-acodec", "libgsm"],    "sr": 8000,  "fmt": "gsm",
                  "dec": ["-f", "gsm", "-ar", "8000"]},
    "speex_nb":  {"args": ["-acodec", "libspeex"],  "sr": 8000,  "fmt": "ogg"},
    "opus_6k":   {"args": ["-acodec", "libopus", "-b:a", "6k",  "-application", "voip"], "sr": 48000, "fmt": "ogg"},
    "opus_8k":   {"args": ["-acodec", "libopus", "-b:a", "8k",  "-application", "voip"], "sr": 48000, "fmt": "ogg"},
    "opus_16k":  {"args": ["-acodec", "libopus", "-b:a", "16k", "-application", "voip"], "sr": 48000, "fmt": "ogg"},
    "opus_24k":  {"args": ["-acodec", "libopus", "-b:a", "24k", "-application", "voip"], "sr": 48000, "fmt": "ogg"},
}


@lru_cache(maxsize=1)
def available() -> frozenset[str]:
    """Which codecs this ffmpeg build can actually encode."""
    if not shutil.which("ffmpeg"):
        return frozenset({"none"})
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                         capture_output=True, text=True).stdout
    ok = {"none"}
    for name, spec in CODECS.items():
        if name == "none":
            continue
        enc = spec["args"][spec["args"].index("-acodec") + 1]
        if enc in out:
            ok.add(name)
    return frozenset(ok)


def roundtrip(audio: Audio, codec: str = "g711u",
              return_sr: int | None = None) -> Audio:
    """Encode then decode through `codec`, returning audio at `return_sr`.

    Default is to return at the codec's own rate, because that is what the
    far end of a real call receives - upsampling back to 16 kHz would hide
    the band limit you are trying to measure.
    """
    if codec in (None, "", "none"):
        return audio.copy()
    if codec not in CODECS:
        raise KeyError(f"unknown codec '{codec}'. known: {', '.join(sorted(CODECS))}")
    if codec not in available():
        raise RuntimeError(f"codec '{codec}' is not supported by this ffmpeg build")

    spec = CODECS[codec]
    c_sr = spec["sr"]
    tmp = tempfile.mkdtemp(prefix="vqlab_codec_")
    try:
        src = os.path.join(tmp, "in.wav")
        enc = os.path.join(tmp, "enc." + spec["fmt"])
        dec = os.path.join(tmp, "dec.wav")
        write_wav(src, audio.resample(c_sr) if c_sr else audio)
        raw = spec["fmt"] in ("g726", "gsm")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", src, *spec["args"],
                        "-ar", str(c_sr), "-ac", "1",
                        *(["-f", spec["fmt"]] if raw else []), enc],
                       check=True, capture_output=True)
        out_sr = return_sr or c_sr
        subprocess.run(["ffmpeg", "-v", "error", "-y", *spec.get("dec", []), "-i", enc,
                        "-ar", str(out_sr), "-ac", "1", "-acodec", "pcm_s16le", dec],
                       check=True, capture_output=True)
        y = read_wav(dec)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # codecs may add/remove a few samples of priming; trim or pad to match
    n_target = int(round(len(audio) * y.sr / audio.sr))
    x = y.samples
    if len(x) > n_target:
        x = x[:n_target]
    elif len(x) < n_target:
        x = np.pad(x, (0, n_target - len(x)))
    return Audio(x, y.sr)
