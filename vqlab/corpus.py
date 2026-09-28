"""Corpus builder: text -> clean speech -> degraded telephony audio + manifest.

Design notes that matter:

* Silence is padded on BEFORE the noise is mixed, so the ambience runs
  continuously through the head and tail. Endpointing and barge-in
  detection live in that silence; trimming it hides the bug.
* Delivered SNR is re-measured from the finished file, using the padded
  tail as a pure-noise reference. That is the calibration check - it
  confirms the number in the filename survived the codec.
* Every output is hash-pinned. The manifest row is self-describing: it
  carries the full condition parameters inline, so a row never depends on
  a config file the reader may not have.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile

import numpy as np
import yaml

from .audio import (Audio, EPS, db_to_lin, fade, load, pad, rms_dbfs,
                    active_speech_level, set_active_level, write_wav)
from .fx import codec as K
from .fx import network as N
from .fx import noise as NZ

GENERATOR_VERSION = "polycall-corpus/0.1.0"


# --------------------------------------------------------------------------
# TTS backends
# --------------------------------------------------------------------------
def tts_espeak(text: str, voice: str, out_path: str, rate: int = 150) -> Audio:
    """Offline placeholder voice. Real, intelligible, and obviously synthetic
    - which is the point: it lets the whole pipeline run and be verified
    before a single ElevenLabs credit is spent."""
    tmp = out_path + ".raw.wav"
    subprocess.run(["espeak-ng", "-v", voice, "-s", str(rate), "-w", tmp, text],
                   check=True, capture_output=True)
    a = load(tmp)
    os.remove(tmp)
    return a


def tts_elevenlabs(text: str, voice_id: str, out_path: str,
                   model: str = "eleven_multilingual_v2") -> Audio:
    """Production backend. Requires ELEVENLABS_API_KEY."""
    import urllib.request
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("ELEVENLABS_API_KEY not set")
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        "?output_format=pcm_24000",
        data=json.dumps({"text": text, "model_id": model}).encode(),
        headers={"xi-api-key": key, "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req) as r:
        raw = r.read()
    x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return Audio(x, 24000)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def measure_delivered_snr(audio: Audio, head_s: float, tail_s: float,
                          speech_guard_s: float = 0.15) -> float | None:
    """Re-measure SNR on the finished file.

    The padded tail is pure noise by construction, so it gives an exact
    noise-power reference. Everything between the pads is speech + noise.
    This is what catches a carrier or a vendor AGC quietly moving your
    working point.
    """
    sr = audio.sr
    x = audio.samples.astype(np.float64)
    n_head = int((head_s + speech_guard_s) * sr)
    n_tail = int((tail_s - speech_guard_s) * sr)
    if n_tail <= sr * 0.2 or len(x) <= n_head + n_tail + sr * 0.2:
        return None
    noise_seg = x[-n_tail:]
    speech_seg = x[n_head:len(x) - n_tail]
    p_n = float(np.mean(noise_seg ** 2)) + EPS
    p_sn = float(np.mean(speech_seg ** 2)) + EPS
    p_s = max(p_sn - p_n, EPS)
    return round(10.0 * np.log10(p_s / p_n), 2)


def _flatten_conditions(cfg: dict) -> list[dict]:
    """Expand the block structure into a flat, fully-resolved condition list."""
    out = []
    defaults = cfg.get("defaults", {})
    for block_key, block in cfg["blocks"].items():
        inherit = {k: v for k, v in block.items()
                   if k not in ("label", "conditions")}
        for c in block["conditions"]:
            cond = {"block": block_key, "block_label": block["label"]}
            cond.update({k: v for k, v in defaults.items()
                         if k in ("codec", "codec_sr", "frame_ms",
                                  "speech_level_dbov", "pad_head_s",
                                  "pad_tail_s", "level_standard")})
            cond.update(inherit)
            cond.update(c)
            out.append(cond)
    return out


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------
def build(utt_path: str, cond_path: str, out_dir: str,
          tts: str = "espeak", limit_langs: list[str] | None = None,
          work_sr: int = 16000) -> dict:
    ucfg = yaml.safe_load(open(utt_path, encoding="utf-8"))
    ccfg = yaml.safe_load(open(cond_path, encoding="utf-8"))
    conditions = _flatten_conditions(ccfg)
    defaults = ccfg["defaults"]
    seed_base = int(defaults.get("seed_base", 0))

    clean_dir = os.path.join(out_dir, "clean")
    deg_dir = os.path.join(out_dir, "degraded")
    os.makedirs(clean_dir, exist_ok=True)
    os.makedirs(deg_dir, exist_ok=True)

    utt_rows, deg_rows = [], []
    done_speechless: set[tuple] = set()
    langs = ucfg["languages"]

    for u in ucfg["utterances"]:
        lang = u["lang"]
        if limit_langs and lang not in limit_langs:
            continue
        lmeta = langs[lang]
        voice = lmeta["tts_voice"]

        # ---- 1. clean speech ------------------------------------------
        cpath = os.path.join(clean_dir, lang, f"{u['id']}.wav")
        os.makedirs(os.path.dirname(cpath), exist_ok=True)
        if tts == "espeak":
            a = tts_espeak(u["text"], voice, cpath)
        else:
            a = tts_elevenlabs(u["text"], voice, cpath)
        a = a.resample(work_sr)
        a = fade(a, 8.0)
        a = set_active_level(a, float(defaults["speech_level_dbov"]))
        write_wav(cpath, a)
        lvl, act = active_speech_level(a.samples, a.sr)

        utt_rows.append({
            "utterance_id": u["id"], "lang": lang, "lang_name": lmeta["name"],
            "class": u["class"], "text": u["text"], "text_norm": u["text_norm"],
            "entities": u["entities"],
            "code_switch": u.get("code_switch"),
            "tts_backend": tts, "tts_voice": voice,
            "path": os.path.relpath(cpath, out_dir),
            "sha256": sha256(cpath),
            "sr": a.sr, "duration_s": round(a.duration, 3),
            "active_level_dbov": round(lvl, 2),
            "activity_factor": round(act, 3),
        })

        # ---- 2. every condition ---------------------------------------
        for ci, cond in enumerate(conditions):
            seed = seed_base + (abs(hash(u["id"])) % 9973) * 31 + ci
            rng = np.random.default_rng(seed)
            head = float(cond.get("pad_head_s", 0.5))
            tail = float(cond.get("pad_tail_s", 1.5))
            speech_on = cond.get("speech", True)
            if not speech_on:
                if (lang, cond["id"]) in done_speechless:
                    continue          # one per language, not one per utterance
                done_speechless.add((lang, cond["id"]))

            if speech_on:
                sig = pad(a, head, tail)
            else:   # noise-only control: no speech at all
                dur = float(cond.get("duration_s", 30.0))
                sig = Audio(np.zeros(int(dur * work_sr), dtype=np.float32), work_sr)

            # noise across the WHOLE signal, silence included
            nz_name = cond.get("noise")
            snr = cond.get("snr_db")
            if nz_name:
                nparams = cond.get("noise_params") or {}
                nz = NZ.make_noise(nz_name, len(sig), sig.sr, rng, **nparams)
                if speech_on and snr is not None:
                    s_db = rms_dbfs(a.samples)          # speech-only reference
                    nz = nz * db_to_lin(s_db - float(snr) - rms_dbfs(nz))
                else:
                    nz = nz * db_to_lin(-42.0 - rms_dbfs(nz))
                sig = sig.with_samples(sig.samples + nz)

            # network impairment, at RTP frame grain
            net_stats = None
            net = cond.get("network")
            if net:
                if net["type"] == "packet_loss":
                    sig, net_stats = N.packet_loss(
                        sig, net["loss_pct"], net["burst_len"],
                        float(cond.get("frame_ms", 20)), net.get("plc", "hold"), seed)
                elif net["type"] == "jitter":
                    sig, net_stats = N.jitter(
                        sig, net["jitter_ms"], net["buffer_ms"],
                        float(cond.get("frame_ms", 20)), seed)

            # codec last, as on a real leg
            cname = cond.get("codec", "g711u")
            sig = K.roundtrip(sig, cname, return_sr=int(defaults.get("codec_sr", 8000)))

            stem = u["id"] if speech_on else f"_{lang}"
            dpath = os.path.join(deg_dir, lang, cond["id"], f"{stem}.wav")
            write_wav(dpath, sig)

            delivered = measure_delivered_snr(sig, head, tail) if (speech_on and nz_name) else None
            lvl2, _ = active_speech_level(sig.samples, sig.sr)

            deg_rows.append({
                "item_id": (f"{u['id']}__{cond['id']}" if speech_on
                            else f"{lang}__{cond['id']}"),
                "utterance_id": u["id"] if speech_on else None, "lang": lang,
                "condition_id": cond["id"], "block": cond["block"],
                "block_label": cond["block_label"],
                "condition": {                        # self-describing, inline
                    "noise": nz_name, "noise_params": cond.get("noise_params"),
                    "snr_db_target": snr, "codec": cname,
                    "codec_sr": int(defaults.get("codec_sr", 8000)),
                    "network": net, "frame_ms": cond.get("frame_ms", 20),
                    "speech_level_dbov": cond.get("speech_level_dbov"),
                    "level_standard": cond.get("level_standard"),
                    "pad_head_s": head, "pad_tail_s": tail,
                    "speech_present": speech_on,
                },
                "measured": {
                    "snr_db_delivered": delivered,
                    "snr_error_db": (round(delivered - float(snr), 2)
                                     if delivered is not None and snr is not None else None),
                    "active_level_dbov": round(lvl2, 2),
                    "network": net_stats,
                },
                "text_norm": u["text_norm"] if speech_on else "",
                "entities": u["entities"] if speech_on else [],
                "source_sha256": utt_rows[-1]["sha256"],
                "path": os.path.relpath(dpath, out_dir),
                "sha256": sha256(dpath),
                "sr": sig.sr, "duration_s": round(sig.duration, 3),
                "seed": seed, "generator": GENERATOR_VERSION,
            })

    # ---- 3. write manifests -------------------------------------------
    def _jsonl(path, rows):
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    _jsonl(os.path.join(out_dir, "utterances.jsonl"), utt_rows)
    _jsonl(os.path.join(out_dir, "manifest.jsonl"), deg_rows)

    with open(os.path.join(out_dir, "CHECKSUMS.sha256"), "w") as f:
        for r in sorted(utt_rows + deg_rows, key=lambda r: r["path"]):
            f.write(f"{r['sha256']}  {r['path']}\n")

    dataset = {
        "name": "PolyCall Bench sample corpus",
        "generator": GENERATOR_VERSION,
        "matrix_id": ccfg["matrix_id"],
        "tts_backend": tts,
        "languages": sorted({r["lang"] for r in utt_rows}),
        "n_utterances": len(utt_rows),
        "n_conditions": len(conditions),
        "n_items": len(deg_rows),
        "codec_sr": int(defaults.get("codec_sr", 8000)),
        "conditions": [c["id"] for c in conditions],
    }
    with open(os.path.join(out_dir, "dataset.json"), "w") as f:
        json.dump(dataset, f, indent=2, ensure_ascii=False)
    return dataset
