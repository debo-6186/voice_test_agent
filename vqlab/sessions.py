"""Session corpus builder (PLAN.md Phase 4 / CLAUDE.md "the corpus is
SESSIONS, not utterances").

Builds on vqlab.corpus / vqlab.audio / vqlab.fx unchanged - this module adds
one new idea on top of them: lay several caller turns onto one nominal
timeline, generate ONE continuous noise bed plus ONE continuous network/codec
pass for the whole session, and only slice into per-turn WAV files at the
very end. Degrading per-turn independently is the bug this exists to avoid -
the caller would appear to teleport between cafes, and codec/PLC state would
reset at every turn boundary instead of carrying through like a real call.
"""
from __future__ import annotations

import json
import os

import numpy as np
import yaml

from .audio import Audio, db_to_lin, fade, pad, rms_dbfs, set_active_level, write_wav
from .corpus import GENERATOR_VERSION, _flatten_conditions, measure_delivered_snr, sha256, tts_elevenlabs, tts_espeak
from .fx import codec as K
from .fx import network as N
from .fx import noise as NZ


def _tts(text: str, lang_meta: dict, tts: str, scratch_path: str, work_sr: int) -> Audio:
    if tts == "espeak":
        a = tts_espeak(text, lang_meta["tts_voice"], scratch_path)
    else:
        a = tts_elevenlabs(text, lang_meta["elevenlabs_voice_id"], scratch_path)
    a = a.resample(work_sr)
    return fade(a, 8.0)


def build_sessions(scenarios_path: str, utt_path: str, cond_path: str, out_dir: str,
                   tts: str = "espeak", limit_langs: list[str] | None = None,
                   limit_scenarios: list[str] | None = None,
                   work_sr: int = 16000) -> dict:
    scfg = yaml.safe_load(open(scenarios_path, encoding="utf-8"))
    ucfg = yaml.safe_load(open(utt_path, encoding="utf-8"))
    ccfg = yaml.safe_load(open(cond_path, encoding="utf-8"))
    conditions = _flatten_conditions(ccfg)
    defaults = ccfg["defaults"]
    seed_base = int(defaults.get("seed_base", 0))
    codec_sr = int(defaults.get("codec_sr", 8000))

    langs_meta = ucfg["languages"]
    langs = limit_langs or list(langs_meta.keys())
    scenario_ids = limit_scenarios or list(scfg["scenarios"].keys())

    sessions_dir = os.path.join(out_dir, "sessions")
    os.makedirs(sessions_dir, exist_ok=True)
    manifest_rows: list[dict] = []
    all_hashes: list[tuple[str, str]] = []   # (sha256, path-relative-to-out_dir)

    for lang in langs:
        lmeta = langs_meta[lang]
        # Keyed by turn text, not by (scenario, condition): the same turn
        # text recurs across S2/S3 and across every condition (only the
        # degradation differs), so synthesizing per-condition would be a
        # 15x-redundant, real-money ElevenLabs call for identical audio.
        tts_cache: dict[str, "Audio"] = {}

        def _synth(text: str, _scratch_dir: str) -> "Audio":
            if text not in tts_cache:
                scratch = os.path.join(_scratch_dir, "_scratch_tts")
                a = _tts(text, lmeta, tts, scratch, work_sr)
                tts_cache[text] = set_active_level(a, float(defaults["speech_level_dbov"]))
            return tts_cache[text]

        for scen_id in scenario_ids:
            scen = scfg["scenarios"][scen_id]
            turns_def = scen["turns"]
            if not all(lang in t["text"] for t in turns_def):
                continue  # this scenario has no content for this language yet

            for cond in conditions:
                seed = seed_base + (abs(hash((lang, scen_id, cond["id"]))) % 999983)
                rng = np.random.default_rng(seed)
                head = float(cond.get("pad_head_s", 0.5))
                tail = float(cond.get("pad_tail_s", 1.5))

                tmp_dir = os.path.join(sessions_dir, lang, scen_id, cond["id"])
                os.makedirs(tmp_dir, exist_ok=True)

                # ---- 1. synthesize (cached) each turn, lay onto one timeline
                offsets, padded_turns, raw_turns = [], [], []
                t_cursor = 0.0
                for t in turns_def:
                    t_cursor += float(t.get("gap_before_s", 4.0))
                    a = _synth(t["text"][lang], tmp_dir)
                    raw_turns.append(a)
                    a_padded = pad(a, head, tail)
                    offsets.append(t_cursor)
                    padded_turns.append(a_padded)
                    t_cursor += a_padded.duration

                total_dur = t_cursor
                n_total = int(round(total_dur * work_sr))

                # ---- 2. one continuous composite - caller speech only -----
                composite = np.zeros(n_total, dtype=np.float32)
                turn_spans = []
                for off, a_padded in zip(offsets, padded_turns):
                    start = int(round(off * work_sr))
                    end = min(start + len(a_padded.samples), n_total)
                    composite[start:end] += a_padded.samples[: end - start]
                    turn_spans.append((start, end))

                # speech-only reference level for SNR mixing, measured from
                # the raw (unpadded) turns - same convention as corpus.py,
                # which measures from clean speech before padding/mixing.
                speech_rms_db = rms_dbfs(np.concatenate([a.samples for a in raw_turns]))

                # ---- 3. ONE continuous noise bed for the whole session ----
                nz_name = cond.get("noise")
                snr = cond.get("snr_db")
                bed_samples = None
                if nz_name:
                    nparams = cond.get("noise_params") or {}
                    bed_samples = NZ.make_noise(nz_name, n_total, work_sr, rng, **nparams)
                    if snr is not None:
                        bed_samples = bed_samples * db_to_lin(speech_rms_db - float(snr) - rms_dbfs(bed_samples))
                    else:
                        bed_samples = bed_samples * db_to_lin(-42.0 - rms_dbfs(bed_samples))
                    mixed = Audio(composite + bed_samples, work_sr)
                else:
                    mixed = Audio(composite, work_sr)

                # ---- 4. network + codec ONCE over the whole session -------
                net_stats = None
                net = cond.get("network")
                if net:
                    if net["type"] == "packet_loss":
                        mixed, net_stats = N.packet_loss(
                            mixed, net["loss_pct"], net["burst_len"],
                            float(cond.get("frame_ms", 20)), net.get("plc", "hold"), seed)
                    elif net["type"] == "jitter":
                        mixed, net_stats = N.jitter(
                            mixed, net["jitter_ms"], net["buffer_ms"],
                            float(cond.get("frame_ms", 20)), seed)

                cname = cond.get("codec", "g711u")
                mixed = K.roundtrip(mixed, cname, return_sr=codec_sr)
                scale = mixed.sr / work_sr

                # ---- 5. slice the processed composite into turn_NN.wav ----
                turns_out = []
                for i, (t, (start, end)) in enumerate(zip(turns_def, turn_spans), start=1):
                    s2, e2 = int(round(start * scale)), int(round(end * scale))
                    turn_audio = Audio(mixed.samples[s2:e2], mixed.sr)
                    turn_path = os.path.join(tmp_dir, f"turn_{i:02d}.wav")
                    write_wav(turn_path, turn_audio)
                    delivered = measure_delivered_snr(turn_audio, head, tail) if nz_name else None
                    rel = os.path.relpath(turn_path, out_dir)
                    h = sha256(turn_path)
                    all_hashes.append((h, rel))
                    turns_out.append({
                        "id": t["id"], "field": t.get("field"),
                        "offset_s": round(start / work_sr, 3),
                        "duration_s": round((end - start) / work_sr, 3),
                        "text": t["text"][lang], "text_norm": t["text_norm"][lang],
                        "entities": t["entities"][lang],
                        "path": rel, "sha256": h,
                        "snr_db_delivered": delivered,
                        "snr_error_db": (round(delivered - float(snr), 2)
                                        if delivered is not None and snr is not None else None),
                    })

                # bed.wav - pre-degradation pure noise reference, full
                # duration. Debugging/contiguity artifact, not re-degraded.
                bed_path = os.path.join(tmp_dir, "bed.wav")
                bed_audio = Audio(bed_samples if bed_samples is not None
                                  else np.zeros(n_total, dtype=np.float32), work_sr)
                write_wav(bed_path, bed_audio)
                bed_rel = os.path.relpath(bed_path, out_dir)
                bed_hash = sha256(bed_path)
                all_hashes.append((bed_hash, bed_rel))

                timeline = {
                    "lang": lang, "scenario": scen_id, "condition_id": cond["id"],
                    "total_duration_s": round(total_dur, 3),
                    "bed_path": bed_rel, "bed_sha256": bed_hash,
                    "turns": turns_out,
                    "seed": seed, "generator": GENERATOR_VERSION,
                }
                timeline_path = os.path.join(tmp_dir, "timeline.json")
                with open(timeline_path, "w", encoding="utf-8") as f:
                    json.dump(timeline, f, indent=2, ensure_ascii=False)

                manifest_rows.append({
                    "session_id": f"{lang}__{scen_id}__{cond['id']}",
                    "lang": lang, "scenario": scen_id,
                    "condition_id": cond["id"], "block": cond["block"],
                    "block_label": cond["block_label"],
                    "condition": {
                        "noise": nz_name, "noise_params": cond.get("noise_params"),
                        "snr_db_target": snr, "codec": cname, "codec_sr": codec_sr,
                        "network": net, "frame_ms": cond.get("frame_ms", 20),
                        "pad_head_s": head, "pad_tail_s": tail,
                    },
                    "measured": {"network": net_stats},
                    "n_turns": len(turns_out),
                    "total_duration_s": round(total_dur, 3),
                    "timeline_path": os.path.relpath(timeline_path, out_dir),
                    "seed": seed, "generator": GENERATOR_VERSION,
                })

    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "manifest.jsonl"), "w", encoding="utf-8") as f:
        for r in manifest_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    with open(os.path.join(out_dir, "CHECKSUMS.sha256"), "w") as f:
        for h, rel in sorted(all_hashes, key=lambda x: x[1]):
            f.write(f"{h}  {rel}\n")

    dataset = {
        "name": "PolyCall Bench session corpus",
        "generator": GENERATOR_VERSION,
        "tts_backend": tts,
        "languages": sorted({r["lang"] for r in manifest_rows}),
        "scenarios": sorted({r["scenario"] for r in manifest_rows}),
        "n_conditions": len(conditions),
        "n_sessions": len(manifest_rows),
    }
    with open(os.path.join(out_dir, "dataset.json"), "w") as f:
        json.dump(dataset, f, indent=2, ensure_ascii=False)
    return dataset
