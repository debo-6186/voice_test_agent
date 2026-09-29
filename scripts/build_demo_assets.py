"""Build the Phase 1 listening-demo assets from config/conditions.yaml.

Run from the repo root:

    python scripts/build_demo_assets.py

Produces:
  demo/data/conditions.json   the cafe ladder + defaults, exported straight from
                               config/conditions.yaml so the browser and the Python
                               engine can never disagree about what "cafe at 10 dB"
                               means.
  demo/data/beds.json         asset manifest for the rendered ambience bed(s).
  demo/assets/beds/*.mp3      the pre-rendered, hash-pinned cafe babble bed.

The bed is rendered once here with vqlab (the same generator the real corpus
uses) and shipped as a static asset - the browser never synthesises noise itself.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import numpy as np
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from vqlab.audio import write_wav, Audio  # noqa: E402
from vqlab.fx import noise as NZ  # noqa: E402

CONDITIONS_PATH = os.path.join(ROOT, "config", "conditions.yaml")
DEMO_DATA_DIR = os.path.join(ROOT, "demo", "data")
DEMO_BEDS_DIR = os.path.join(ROOT, "demo", "assets", "beds")

WORK_SR = 16000        # matches vqlab.corpus.build's work_sr
BED_DURATION_S = 60.0  # long enough to cover any realistic demo clip


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def build_conditions_json(ccfg: dict) -> dict:
    defaults = ccfg["defaults"]
    ctl = ccfg["blocks"]["ctl"]
    block_a = ccfg["blocks"]["A"]
    block_c = ccfg["blocks"]["C"]

    clean = next(c for c in ctl["conditions"] if c["id"] == "ctl_clean")

    rungs = [{
        "id": clean["id"],
        "label": "Clean",
        "snr_db": None,
    }]
    for c in block_a["conditions"]:
        snr = c["snr_db"]
        rungs.append({
            "id": c["id"],
            "label": f"{snr} dB",
            "snr_db": snr,
        })

    # Block C's network conditions, at the block's fixed cafe SNR floor.
    # C_opus8k is intentionally excluded: it requires a real Opus encoder,
    # which the browser demo does not ship (see docs/stack.md's mu-law-only
    # approach) - g711u is the only codec simulated client-side.
    network_rungs = []
    for c in block_c["conditions"]:
        if "network" not in c:
            continue
        net = c["network"]
        if net["type"] == "packet_loss":
            label = f"Packet loss {net['loss_pct']:g}%"
        elif net["type"] == "jitter":
            label = f"Jitter {net['jitter_ms']:g} ms"
        else:
            continue
        network_rungs.append({
            "id": c["id"],
            "label": label,
            "snr_db": block_c["snr_db"],
            "network": net,
        })

    return {
        "version": ccfg["version"],
        "matrix_id": ccfg["matrix_id"],
        "defaults": {
            "speech_level_dbov": defaults["speech_level_dbov"],
            "pad_head_s": defaults["pad_head_s"],
            "pad_tail_s": defaults["pad_tail_s"],
            "codec": defaults["codec"],
            "codec_sr": defaults["codec_sr"],
            "frame_ms": defaults["frame_ms"],
            "seed_base": defaults["seed_base"],
            "work_sr": WORK_SR,
        },
        "noise": {
            "id": "bed_cafe",
            "kind": block_a["noise"],
            "params": block_a["noise_params"],
        },
        "rungs": rungs,
        "network_rungs": network_rungs,
    }


def build_bed(seed_base: int, noise_kind: str, noise_params: dict) -> dict:
    os.makedirs(DEMO_BEDS_DIR, exist_ok=True)
    rng = np.random.default_rng(seed_base)
    n = int(BED_DURATION_S * WORK_SR)
    samples = NZ.generate(noise_kind, n, WORK_SR, rng, **noise_params)
    audio = Audio(samples, WORK_SR)

    bed_id = "bed_cafe"
    wav_path = os.path.join(DEMO_BEDS_DIR, f"{bed_id}.wav")
    mp3_path = os.path.join(DEMO_BEDS_DIR, f"{bed_id}.mp3")
    write_wav(wav_path, audio)

    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", wav_path,
         "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k", mp3_path],
        check=True,
    )
    os.remove(wav_path)

    return {
        "id": bed_id,
        "file": os.path.relpath(mp3_path, os.path.join(ROOT, "demo")),
        "sha256": sha256(mp3_path),
        "seed": seed_base,
        "sr": WORK_SR,
        "duration_s": BED_DURATION_S,
        "params": {"kind": noise_kind, **noise_params},
    }


def main() -> None:
    ccfg = yaml.safe_load(open(CONDITIONS_PATH, encoding="utf-8"))
    os.makedirs(DEMO_DATA_DIR, exist_ok=True)

    conditions = build_conditions_json(ccfg)
    with open(os.path.join(DEMO_DATA_DIR, "conditions.json"), "w", encoding="utf-8") as f:
        json.dump(conditions, f, indent=2)
    print(f"wrote {os.path.join(DEMO_DATA_DIR, 'conditions.json')}")

    seed_base = int(ccfg["defaults"]["seed_base"])
    noise = conditions["noise"]
    bed = build_bed(seed_base, noise["kind"], noise["params"])
    beds_manifest = {"beds": [bed]}
    with open(os.path.join(DEMO_DATA_DIR, "beds.json"), "w", encoding="utf-8") as f:
        json.dump(beds_manifest, f, indent=2)
    print(f"wrote {os.path.join(DEMO_DATA_DIR, 'beds.json')}")
    print(f"bed asset: {bed['file']} ({bed['sha256'][:12]}...)")


if __name__ == "__main__":
    main()
