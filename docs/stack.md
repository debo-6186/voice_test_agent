# Tech stack, intent classification, and hardware

## No agent framework — deliberately

Do not reach for LangChain, LangGraph, CrewAI, or any agent orchestration library.

The caller is a **deterministic state machine**, not an agent. Determinism is the
point: the canonical path must produce byte-identical audio for every vendor.
An agent framework injects non-determinism exactly where we are fighting to remove
it, and adds orchestration latency inside a turn-taking loop where milliseconds
change agent behaviour.

An LLM appears in exactly one place — the adaptive fallback, as a single
temperature-0 call, on turns that are excluded from the headline comparison anyway.

## Stack

| Layer | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | PJSUA2 bindings, the ASR ecosystem |
| DSP | numpy + scipy, stdlib `wave` | Already built and tested in `vqlab` |
| Codecs | ffmpeg subprocess | 12 codecs verified working |
| SIP | PJSUA2 (pjproject 2.16+, custom `config_site.h`) | Only client doing negotiation + WAV play + record in one process |
| Concurrency | `multiprocessing`, **not** asyncio | PJSUA2 is threaded C; asyncio fights it. Process shards also dodge GIL deadlock #3125 |
| Work queue | SQLite, WAL mode | 6,300 jobs does not justify Redis. Workers claim jobs atomically |
| In-loop ASR | faster-whisper small/base, GPU sidecar | Latency-critical, accuracy-tolerant — see below |
| Referee ASR | Per-language models, batched offline | Accuracy-critical, no latency constraint — see `docs/referees.md` |
| Intent classification | **Laya** (`convaiinnovations/laya-multilingual`) | See below |
| Scoring | jiwer + whisper-normalizer | Indic normalisers with `preserve_marks=True` |
| Statistics | scipy.stats, statsmodels, psignifit | Bootstrap, GLM, psychometric threshold fitting |
| Results store | DuckDB over Parquet | Columnar matters: 10,000 bootstrap resamples over 6,300 calls x turns |
| Config | pydantic + YAML | Schema-validated. Vendor config is versioned, never hardcoded |

---

## The listening demo — a separate stack, deliberately

Phase 1 is a **static page with no backend**. Not a scaled-down version of the
Python harness — a different implementation, in the browser, sharing only the
parameter definitions.

That is a real duplication and it is worth it: a static page costs nothing to host,
scales to a front-page traffic spike, needs no accounts, and lets us say *"your audio
never leaves your browser"* — which is the right answer for the security-conscious
audience that will not paste an API key anywhere.

| Step | Browser implementation |
|---|---|
| Load / record audio | `File` API + `MediaRecorder`, decoded via `AudioContext.decodeAudioData` |
| Mix noise at target SNR | Pre-rendered ambience beds shipped as compressed audio; gain computed from measured RMS |
| Band-limit, reverb | `BiquadFilterNode`, `ConvolverNode` with a shipped RIR |
| G.711 µ-law round-trip | A 256-entry lookup table in JS — µ-law is not a real codec, it is a curve |
| Resample to 8 kHz | `OfflineAudioContext` at `sampleRate: 8000` |
| Packet loss | Zero or hold 20 ms frames in a `Float32Array` |
| Transcribe each rung | `transformers.js` with `whisper-tiny` (WASM/WebGPU) |
| Render | Waveform + spectrogram on `<canvas>`; audio elements per rung |

### Rules for the demo

- **No build-time server, no runtime server.** GitHub Pages or any static host.
- **Nothing is uploaded.** All processing in the page. Say so prominently on it.
- **Parameters come from the same source of truth** as the Python engine. Export
  `config/conditions.yaml` to JSON at build time; never re-type the numbers.
  If the browser and the CLI disagree about what "café at 10 dB" means, the project
  loses its central claim.
- **Ship the ambience beds as assets**, do not synthesise them in-browser. The
  Python generator is authoritative; the browser replays fixed, hash-pinned beds.
  This also keeps the page small and the output identical to the corpus.
- **Degrade gracefully without WebGPU.** whisper-tiny on WASM is slow but works;
  show a progress state rather than blocking, and let the audio play before the
  transcript arrives.
- **Mobile matters.** Most shares are opened on a phone. Test there first.

### What the demo must show

Not a settings panel. One flow:

1. Drop a file or press record
2. Hear the ladder, rung by rung, with a play button on each
3. **Underneath each rung: what the ASR heard** — this is the payload. Seeing
   "nine one four two three three" become "nine one four to three" is the moment
   the concept lands.
4. One sentence naming the rung where it broke

---

## The two ASR roles must never be the same model

This is the architectural decision that resolves the latency/accuracy tension.

| | **In-loop ASR** | **Referee ASR** |
|---|---|---|
| Job | Transcribe the agent's turn *during* the call so the classifier can pick the next caller turn | Score the recorded tapes *after* the run |
| Audio | Agent's clean TTS — we never added noise to it | Both legs, including noisy caller side |
| Constraint | **Latency-critical** — must fit in the turn gap | **Accuracy-critical** — it is the measurement instrument |
| Error tolerance | **High.** A mis-transcription causes a wrong turn selection, which surfaces as divergence, gets logged, and excludes that call from the headline comparison | **None.** Its errors become wrong scores |
| Model | Small — `whisper-base`/`small`, one model for all languages | Large, per-language: MERaLiON (en-SG), Sarvam Saaras V3 (hi), Gemini/MERaLiON (id), Whisper large-v3 (es/en) |
| Batching | Online, small batches | Fully batched offline |

Keeping these separate means the accuracy-critical ASR has **no latency budget at
all**, and the latency-critical ASR is allowed to be wrong.

**Optimisation:** start transcribing the agent's turn *while it is still speaking* —
chunk-and-append in ~1 s windows — so when the turn ends only the final chunk
remains. This cuts effective in-loop ASR latency to roughly one chunk.

---

## Intent classification — three-tier cascade

After the agent speaks, decide what it asked so you can pick the right pre-rendered
caller turn.

**The constraint is the turn gap.** A human starts replying in roughly 200–500 ms.
If classification takes two seconds you have created an unnaturally long caller
pause — and some agents will re-prompt into it, which the harness then counts as a
**repair you caused**. The instrument manufactures the failure it is measuring.

| Tier | Method | GPU | CPU (int8) | Notes |
|---|---|---|---|---|
| 1 | Regex + slot-state prior | ~0 ms | ~0 ms | Free, deterministic, catches the common case |
| 2 | **Laya `choice`** | 7 ms batched / 33 ms single | ~150–180 ms (4 cores) | Primary classifier |
| 3 | LLM, temperature 0 | 300–900 ms | — | Only below confidence threshold; **this firing IS the divergence signal** |

Tier 3 being slow does not matter: when it fires you have already left the canonical
path, and that call is excluded from the headline comparison. The slow path is
exactly the path that does not count. **Log which tier fired on every turn** — the
tier-3 rate per vendor is the divergence metric.

### Why Laya rather than embedding cosine

The `choice` primitive takes **natural-language criteria descriptions**, not
exemplars:

```python
import laya
agent = laya.load("convaiinnovations/laya-multilingual")

result = agent.predict(
    {"body": agent_transcript},
    {"intent": {"type": "choice",
                "instructions": "What is the agent asking the caller for?",
                "criteria": {
                    "ask_name":  "requesting the caller's name",
                    "ask_phone": "requesting a phone or contact number",
                    "ask_date":  "requesting a date or appointment time",
                    "confirm":   "reading a value back for confirmation",
                    "repair":    "asking the caller to repeat or clarify",
                    "closing":   "ending the call",
                }}})
intent = result["answers"]["intent"]["choice"]
```

With embeddings we would author exemplars **per intent per language** — roughly
5 languages x 8 intents x 6 phrasings = 240 hand-written strings in Hindi and
Bahasa that we could not review. With Laya: **eight criteria descriptions, written
once**, multilingual checkpoint covers 100+ languages.

It is also **deterministic** — non-autoregressive encoder, no sampling. Same input,
same weights, same output. An LLM classifier in tier 2 would have quietly broken
canonical-path reproducibility.

### Two caveats — read before writing the classifier

**1. It ships uncalibrated, despite the marketing.** The product page claims
"mathematically calibrated confidence scores". The model card says it **"ships
uncalibrated"** with systematic over-confidence, mean confidence 0.75–0.83.

This is not cosmetic. The entire tier-2 → tier-3 escalation hangs on a confidence
threshold. An over-confident classifier will not escalate when it should, and will
silently pick a wrong turn instead of falling through. **Calibrate it ourselves** —
temperature scaling or isotonic regression on a held-out set of our own agent
transcripts, per language. This is a Phase 3 task, not a footnote.

**2. Read the MASSIVE numbers in context.** Reported macro accuracy is 0.366 across
51 languages; Hindi 0.387. MASSIVE is a **60-intent** task where random is ~1.7%, so
Hindi is ~23x random. Our task should do substantially better — 8 intents not 60,
criteria descriptions supplied, slot-state prior narrowing candidates, clean TTS
input. But that is a prediction. **Benchmark on our own agent transcripts in
Phase 0** and treat the MASSIVE figure as a floor.

Also from the card: keep choice sets under ~20 options (we have 8), and the ordinal
`score` primitive is the weakest — do not use it.

### A second job for Laya

The `noul` primitive (boolean + probability) covers two other labelling jobs that
would otherwise need an LLM, both over the agent's clean transcript:

- *"Did the agent signal uncertainty about the value it read back?"* →
  **silent-error detection**, a tier-1 metric
- *"Did the agent ask the caller to repeat?"* → **repair detection**

Both are currently the fuzziest part of the scoring layer. Prototype alongside the
intent test in Phase 0.

---

## Hardware

**Laya was never what drives the GPU requirement. In-loop ASR is.**

Required in-loop throughput at 50 concurrent calls, ~4 agent turns per 90 s call:
**2.2 turn-events/sec**, each needing ~3 s of ASR plus one classification.

| Component | Device utilisation at 50 concurrent |
|---|---|
| ASR whisper-small fp16, GPU | 13.3% |
| ASR whisper-base int8, GPU | 6.7% |
| ASR whisper-tiny int8, **CPU** (4 cores) | 46.7% |
| ASR whisper-base int8, **CPU** (4 cores) | **100%** |
| Laya batched, GPU | 1.6% |
| Laya single, GPU | 7.3% |
| Laya int8 ONNX, **CPU** (4 cores) | 36.7% |

**One T4 runs the entire harness at ~15% utilisation** (whisper-small + Laya
batched) serving all 50 concurrent calls. The T4 figure on Laya's model card is
*single-stream latency*; what we need is *aggregate throughput*, where batching is
in our favour — multiple calls end turns at roughly the same moment, so the sidecar
batches naturally and gets 7 ms/question rather than 33 ms.

### Architecture: one GPU sidecar, not one model per worker

Do **not** load Laya or Whisper inside each of the N worker processes — that wastes
VRAM and loses the batching win. Run a single small inference service the workers
call over localhost, serving both in-loop ASR and Laya, with a short (~20 ms)
batching window.

### Cost

| | |
|---|---|
| In-loop sidecar, 2.3 h full sweep @ $0.40/GPU-h | **$0.92** |
| Offline referee pass, 6.3 GPU-h | **$2.52** |
| **Total GPU for the entire project** | **~$3.50** |

### CPU-only path

Viable for **development and CI at low concurrency**, not for the full run:

- Laya int8 ONNX: ~150–180 ms per decision on 4 cores (community ports exist:
  `leli14/laya_cpu_onnx`, `receptron/laya-onnx`; someone has run the 421M checkpoint
  in a browser tab). A `laya-mlx` port exists for Apple Silicon — ~140 ms for three
  questions once warm.
- whisper-tiny int8 in-loop.
- Combined ~300–480 ms per turn: **fits the turn-gap budget, but only just**, and a
  4-core box saturates at roughly 8–10 concurrent calls.

If you are CPU-pinched in dev, an embedding classifier (multilingual-e5-small,
118M, ~10–30 ms on CPU) is genuinely faster than Laya on CPU — but it costs the
240 multilingual exemplars and handles paraphrase worse. **Build for the GPU sidecar;
keep the CPU path as a dev fallback at reduced concurrency, not as a second
implementation.**

---

## Phase 0 measurement tasks this creates

1. Benchmark Laya multilingual on ~200 real agent transcripts per language; report
   per-language intent accuracy and the confidence distribution.
2. Fit a calibration map (temperature scaling or isotonic) per language; report
   expected calibration error before and after.
3. Measure end-to-end turn-gap latency of tier 1 + tier 2 on the target hardware,
   and confirm it stays under 400 ms at target concurrency.
4. Prototype the `noul` primitive for repair and silent-error detection; compare
   against hand-labelled turns.
