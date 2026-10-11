# PolyCall Bench — project context

An open benchmark measuring how commercial voice-AI agents degrade under controlled
acoustic and network impairment, across multi-turn calls over real SIP telephony,
in five languages.

**Read `PLAN.md` first.** It is the specification. `docs/` holds the researched
detail for each subsystem — including **`docs/stack.md`**, which decides the tech
stack, the intent classifier and the hardware, and overrides any default instinct
about frameworks. Everything below is the short list of things that will
waste a day if you get them wrong.

---

## Build order — every phase must ship something

The value of this project depends on people seeing it, so nothing is built in a
drawer. Full detail in `PLAN.md` §11. The order and the reason:

1. **Listening demo** (browser, no backend, week 2) — the public link everything
   else points at. Different stack from the harness, on purpose; see `docs/stack.md`.
2. **One-shot SIP probe** (weeks 3–5) — dial, play one file, record, hang up.
   Nothing else. This debugs PJSIP, codec pinning and credentials at the simplest
   possible complexity, before any conversational logic exists to confuse it.
3. **Hallucination reel** (week 6) — play 30 s of speechless café noise to every
   vendor, count the turns they invent. Nearly free once step 2 works, and it is the
   single most shareable artifact in the project.
4. Then the corpus, the mock agent, the full harness, the benchmark.

**Do not build the full harness before the probe works.** And do not skip the demo
or the reel to "get to the real work" — they are the distribution for the real work.

## Scope — do not reduce it

Five languages: `en-US`, `en-SG`, `hi-IN`, `es-ES`, `id-ID`.
Seven vendors: Retell, Vapi, ElevenLabs, Bland, LiveKit, Telnyx, Pipecat.
(Synthflow is enterprise-only at a $30k/yr floor as of 2026-09-15 — excluded, with
the reason stated in the paper, not silently dropped.)

Full matrix = 5 langs x 7 vendors x 15 conditions x 4 scenarios x 3 repeats
= **6,300 calls**. This runs in ~2.3 hours of wall clock because vendor tracks run
concurrently. Calls are not the bottleneck; engineering is. Do not propose cutting
languages or vendors on feasibility grounds.

## Already built and tested — build on it, do not rewrite

`vqlab/` is a working, verified degradation engine:

- `vqlab/audio.py` — WAV I/O, resampling, ITU-T P.56 active speech level,
  energy VAD, VAD-aware SNR mixing, padding/fades.
- `vqlab/fx/noise.py` — 22 synthetic ambiences (cafe, street, car, office, wind,
  tv, construction, ...) plus a loader for real corpora (MUSAN/DEMAND).
- `vqlab/fx/acoustic.py` — synthetic RIR, reverb, mic distance, device responses.
- `vqlab/fx/channel.py` — AGC, clipping, hum, echo, half-duplex ducking, dropouts,
  over-aggressive noise suppression, gating.
- `vqlab/fx/codec.py` — verified ffmpeg round-trips: g711u, g711a, g722, g726_16,
  g726_32, gsm, speex_nb, opus_6k/8k/16k/24k. Note gsm and g726 need raw container
  formats with explicit decode hints — already handled, do not "fix" it.
- `vqlab/fx/network.py` — Gilbert-Elliott burst loss with PLC, jitter buffer
  simulation, frame repeat, clock skew. All at 20 ms RTP frame grain.
- `vqlab/corpus.py` — builder: TTS -> level normalise -> pad -> noise -> network
  -> codec -> manifest with SHA-256 and re-measured delivered SNR.

Run `python -c "from vqlab.corpus import build; build('config/utterances.yaml','config/conditions.yaml','out')"`
to confirm it works before changing anything.

## The corpus is SESSIONS, not utterances

The current `corpus.py` builds single utterances. **It must be converted to
multi-turn sessions.** One continuous ambience bed per call, sliced per turn at the
correct offset, running under the caller's speech, under the agent's speech, and
through every pause. Independently-generated per-turn noise is a bug: the caller
appears to teleport between cafes and the endpointing measurement is destroyed.

Keep the 0.5 s head / 1.5 s tail padding. Endpointing is decided in that silence,
and the tail is the pure-noise reference that makes delivered SNR measurable from
the finished file.

## Hard-won gotchas

### PJSIP / PJSUA2
- `PJSUA_MAX_CALLS` defaults to 4 and **cannot be raised at runtime** — the library
  must be recompiled with a custom `config_site.h`.
- **The real ceiling is conference ports, not calls.** Each scripted call needs
  ~3 slots (call + player + recorder). `PJSUA_MAX_PLAYERS` and
  `PJSUA_MAX_RECORDERS` default to **32 each** — raising only `MAX_CALLS` leaves
  you mysteriously stuck at 32. See `docs/sip-harness.md` for the full header.
- **Shard across processes, not threads.** PJSIP issue #3125 (GIL deadlock between
  `libHandleEvents` and `Call::getInfo`) is closed upstream as "not planned".
- **Call `setNullDev()` in every worker.** With no sound device and no master port,
  nothing pumps the bridge, async ops never complete, conference slots fill and are
  never freed, and the process dies after N calls.
- Hold explicit Python references to every Call/Player/Recorder for its lifetime;
  GC of a wrapper while PJSIP holds the port is a segfault.
- `libRegisterThread()` leaks thread-description memory until `libDestroy()` —
  register long-lived threads once, never per task.
- Use pjproject **2.16+**. Raise `ulimit -n` to 4x the ioqueue handle count.
- **Never use SIPp.** No WAV support, no codec negotiation; it replays RTP blindly
  and will send the wrong payload type.

### SIP transport
- **Offer PCMU only in the SDP.** Codec support differs per vendor; free
  negotiation means one vendor lands on Opus and another on G.711, and the
  benchmark silently measures codec choice instead of agent quality.
- The comparison transport is set by the **most restrictive vendor**, not the most
  capable. Never mix direct SIP for one vendor and a trunk for another.
- Retell: `call_id` is **single-use** — re-register per call. A free trial without a
  card on file leaves the call stuck at `call_status: registered` with
  `disconnection_reason: null` and silence. It looks like a SIP bug. It is billing.
- LiveKit will not negotiate Opus on SIP — force G.711 via `onlyListedCodecs`.
- Vapi's US media IPs are dynamic; open 40000-60000/UDP rather than pinning.

### Scoring
- **Never use the vendor's transcript.** They are post-processed differently and
  some come from a second offline ASR pass the live call never used. Record the
  agent's spoken reply from your own leg and transcribe it with your own referee,
  identically for every vendor.
- **Per-language referees are mandatory.** Whisper large-v3-turbo scores 32.02 WER
  on Singapore English; MERaLiON-2-10B scores 4.62 on the same corpus. A uniform
  referee makes en-SG a measurement of Whisper, not of any vendor.
- **The referee degrades with SNR too.** Characterise its error vs SNR on a
  human-labelled sample and truncate the ladder where referee error stops being
  much smaller than agent error. Score from the agent's clean TTS side wherever
  possible.
- `BasicTextNormalizer` strips combining marks — on Devanagari this inflates WER in
  a way that looks like ASR error but is a normalisation bug. Use
  `preserve_marks=True`.

### Statistics
- **pass^k, never pass@k.** pass@k rises with k and hides degradation. Under
  independence pass^k = p^k, which amplifies the signal being measured.
- **Pair scenarios across SNR levels** — 155 paired vs 398 unpaired for a 10-point
  effect. Free 2.5x saving.
- Cluster bootstrap at task level with conditions kept paired; ignoring the
  clustering understates uncertainty by up to 3x.
- Wilson or Clopper-Pearson intervals, never Wald.
- Score task success at **call level** (binary). Per-slot successes are correlated
  within a call and violate binomial independence.
- Do not chase determinism. Temperature 0 is not deterministic (80 unique outputs
  from 1,000 completions in published work) and no voice platform exposes a
  determinism control.

### Stack (full detail in `docs/stack.md`)
- **No agent framework.** No LangChain, LangGraph or CrewAI. The caller is a
  deterministic state machine; a framework injects non-determinism exactly where we
  are removing it. An LLM appears only in the adaptive fallback.
- **Two separate ASR roles that must never share a model.** In-loop ASR (small,
  fast, accuracy-tolerant — its errors become logged divergence) transcribes the
  agent's turn during the call. Referee ASR (large, per-language, accuracy-critical)
  scores the tapes offline with no latency budget.
- **Intent classification is Laya** (`convaiinnovations/laya-multilingual`), not an
  embedding model: its `choice` primitive takes natural-language criteria, so we
  write 8 descriptions once instead of 240 multilingual exemplars. It is also
  deterministic, which an LLM classifier would not be.
- **Laya ships uncalibrated** despite the marketing claim — mean confidence
  0.75–0.83, systematically over-confident. The tier-2 → tier-3 escalation depends
  on a confidence threshold, so calibrate it per language on held-out transcripts
  before trusting it. This is a real task.
- **One GPU sidecar, not a model per worker.** One T4 runs the whole harness at
  ~15% utilisation at 50 concurrent calls. Loading models per worker process wastes
  VRAM and loses the batching win (7 ms/question batched vs 33 ms single).
- CPU-only is a dev fallback at ~8–10 concurrent, not a second implementation.

## Conventions

- Python 3.11+. Dependencies: numpy, scipy, pyyaml, ffmpeg. Keep it minimal —
  `vqlab` deliberately uses stdlib `wave` rather than soundfile.
- Every generated artifact carries a SHA-256 and the seed that produced it.
- Manifest rows are **self-describing**: the full condition parameters are inlined,
  so a row never depends on a config file the reader may not have.
- Every condition is specified by published parameters, never by a name.
  "cafe noise" is not a condition; the full seeded parameter set is.
- Config that ages fast (vendor SIP URIs, concurrency limits, pricing) lives in
  versioned YAML with a per-run verification step. Never hardcode it.

## This is an open-source tool, not just our benchmark

Most users will bring their own API keys and test **their own agent** on a vendor.
That is the default path and it must be frictionless. Two consequences:

- **Two modes, never blurred.** Mode A (default) tests the user's existing agent —
  their prompt, their config — and is NOT comparable to the leaderboard or to
  another user's run. Mode B (`--standard-agent`) creates a standardised agent from
  a pinned spec and is comparable. **Stamp the mode on every artifact**, and make
  `report` refuse to emit a leaderboard-comparable table from a Mode A run. If these
  get confused, someone posts a bad Mode A number as a vendor score and the
  project's credibility is gone.
- **Progressive onboarding.** `pip install` + `polycall degrade` must work with no
  accounts, no GPU and no SIP. The Docker image ships PJSIP prebuilt because the
  custom `config_site.h` is the biggest barrier to a first live call. A GPU is
  optional — `--device cpu` degrades gracefully at reduced concurrency, never a
  hard fail.
- **The mock agent is a shipped feature**, not a test fixture. `polycall demo` runs
  the whole loop with zero accounts and zero cost. It is how contributors develop
  without spending money and how users verify their setup before they do.
- **Cost guards are not optional.** Estimated spend printed and confirmed before
  dialling; `--max-spend` enforced mid-run; concurrency defaults to 4 and cannot
  exceed the vendor's published limit without `--force-concurrency`; backoff on 429
  always on. See `docs/responsible-use.md`.
- **Vendor config is data.** `vendors/*.yaml` holds SIP URIs, codecs, concurrency
  and language fallbacks, with the date verified. Never hardcode it — it goes stale
  within months.

See `docs/cli.md` for the command surface and the rules it must enforce.

## Do not

- Do not build a web UI, SaaS, or test-authoring interface. This is a benchmark:
  a library, a CLI, a dataset, and a static leaderboard.
- Do not add a sixth language or a new vendor without being asked.
- Do not reduce the matrix to make something fit.
- Do not make a GPU, a phone number, or a vendor account required for the first
  useful result.
