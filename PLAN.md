# PolyCall Bench — project plan v3.0 (full scope)

An open benchmark measuring how commercial voice-AI agents degrade under controlled
acoustic and network impairment, across multi-turn calls delivered over real SIP
telephony, in five languages — shipping the audio, the parameters, and the
instrument's own error bars so anyone can re-mark the results.

| | |
|---|---|
| Languages | 5 — `en-US`, `en-SG`, `hi-IN`, `es-ES`, `id-ID` |
| Vendors | 7 — Retell, Vapi, ElevenLabs, Bland, LiveKit, Telnyx, Pipecat |
| Matrix | 6,300 calls |
| Wall clock | ~2.3 h (parallel vendor tracks) |
| Telephony cost | $0 (direct SIP) |
| Build | ~17 weeks |
| Licence | MIT (code) + CC-BY-4.0 (corpus) |

---

## 1. The finding this exists to produce

"We built a benchmark" is not a story — a dozen exist. The reason to build this one
is a specific finding nobody currently has the apparatus to measure:

> **Voice AI agents degrade measurably harder in Hindi, Bahasa Indonesia and
> Singapore English than in English or Spanish under identical acoustic
> conditions** — same platform, same call, same noise, same codec, same scenario.

If true, that is a language-equity gap in a product class being sold into Southeast
Asia and India right now, and it is defensible because it is measurement rather than
opinion. If false, that is publishable too — just quieter.

The five languages are not five separate studies. They are **one comparison**, and
each is load-bearing:

- **Spanish** — control. Lowest referee error of the set; proves an effect is the
  vendor's rather than the instrument's.
- **English (US)** — anchor. The only language with comparable prior results;
  validates the method against published work.
- **Hindi, Bahasa Indonesia, Singapore English** — the finding.

## 2. Positioning

> No existing benchmark combines **multi-turn conversation** with a
> **parameter-published degradation ladder**, over **real SIP telephony**, against
> **commercial voice-agent platforms**, in **languages beyond English**, shipping a
> **downloadable checksummed corpus** and **error bars on its own instrument**.

### Prior art

| Project | Turns | Degradation | Real telephony | Agent platforms | Ships corpus | Non-English | Stats |
|---|---|---|---|---|---|---|---|
| openbenchmarks/voice-agent-latency | Multi | No | **Yes** | Yes | Yes | No | p50/p95 |
| τ-Voice (Sierra) | Multi | **Yes** | No | Models | Runtime | No | pass^k |
| τ-Elicitation | Multi | One level | No | Yes | Artifacts | No | **Bootstrap** |
| EVA-Bench | Multi | Partial | No | Models | Partial | FR accent | pass^k |
| RW-Voice-EQ | Multi | Yes | No | Models | No | EU/JA | No |
| Cekura Bench | Multi | Incidental | Yes | Yes | No | No | pass³ |
| Coval Benchmarks | Single | Partial | No | Models | Yes | No | No |
| call-asr-bench | Single | Yes | No | ASR only | BYO | No | No |
| VoiceAgentBench | Query | No | No | SpeechLMs | Yes | Indic | No |
| **PolyCall Bench** | **Multi** | **Ladder** | **Yes** | **Yes** | **Yes** | **5 langs** | **Full** |

**Read τ-Elicitation (arXiv 2609.13602) before writing code.** Published September
2026, it is the closest prior work and validates the metric design: 200 tasks,
10 entity types, exact-match entity scoring, linked multi-field workflows, noise at
10 dB with bursts and frame drops, 4,800 calls. Its headline number is the argument
for multi-turn in one line: **per-field accuracy 0.75, task pass rate 0.53 once
three fields are chained**; intermediate validation lifts task accuracy 0.64 → 0.82.

It is also the only voice-agent benchmark doing proper statistics — 10,000
task-level cluster bootstraps with conditions kept paired, sign-permutation tests
with Holm correction. **Copy that method.** What it did not do: English only, three
providers, simulated transport, one noise level, and it deliberately omits
caller-side ASR entirely — which is the confound §7 addresses.

Position the work as *"extends τ-Elicitation's task design to a degradation ladder,
real telephony and four additional languages."* A citable precedent is worth more
than a solo claim.

## 3. Five differentiators

1. **A degradation ladder over real telephony.** Telephony benchmarks measure
   latency only; degradation benchmarks run in simulation. Nobody has put a
   parameterised SNR ladder through a live SIP leg to a commercial agent.
2. **Five languages, three with zero prior coverage.** Bahasa Indonesia and
   Singapore English appear in no published agent benchmark.
3. **Published measurement uncertainty, and a truncation rule.** No benchmark
   reports the error of its own instrument, and none handles the referee ASR
   degrading with SNR alongside the agent.
4. **Silent-error rate as a first-class metric.** Not "did it get the number wrong"
   but "did it get it wrong *and not know*".
5. **Code-switching under degradation.** Singlish and Hinglish on a noisy line is
   how a very large number of real calls sound, and it is measured nowhere.

## 4. Transport

Dialling every vendor's SIP URI directly from one machine gives an identical
originating leg — the only property comparison requires — and it is free. A carrier
trunk is needed only when a vendor will not accept a bare softphone, or when the
carrier leg is deliberately wanted in the path.

> **The comparison transport is set by the most restrictive vendor in the set, not
> the most capable.** Mixing direct SIP for one vendor and a trunk for another
> reintroduces the harness as a variable and invalidates the comparison.

Full per-vendor SIP reachability, codec support, and the PJSIP build required for
50 concurrent calls: **`docs/sip-harness.md`** and **`docs/vendors.md`**.

Key constraint: **offer PCMU only in the SDP.** One codec, every vendor, every call.

## 5. Scale

The instinct that 6,300 calls is a months-long endeavour assumes serial dialling.
Concurrency is the lever; vendor tracks run in parallel; wall clock is set by the
slowest track, not the sum.

| Vendor track | Concurrency | 900 calls |
|---|---|---|
| Retell (burst) | 60 | 0.4 h |
| Bland Build | 50 | 0.5 h |
| ElevenLabs Scale | 30 | 0.8 h |
| Retell (default) | 20 | 1.1 h |
| LiveKit Ship | 20 | 1.1 h |
| Vapi + 10 lines | 10 | 2.3 h |
| Telnyx (L2 verified) | 10 | 2.3 h |

**Full matrix wall clock ≈ 2.3 hours**, bounded by the Vapi and Telnyx tracks.
Nightly CI is realistic.

### Cost

| Item | |
|---|---|
| Vendor fees | 9,450 min × $0.07–0.31 = **$660 – $2,930** |
| Telephony | **$0** — direct SIP |
| Referee ASR | 315 audio-h. Groq batch ≈ $13; self-hosted faster-whisper ≈ 6.3 GPU-h ≈ $9 |
| TTS corpus | one-off, < $100 |
| Plan tiers | Bland Build $299 + LiveKit Ship $50 + Vapi lines $100 + ElevenLabs Scale $299 |
| **Total** | **≈ $1,500 – $4,000 per full published run** |

Billing traps that silently break an automated run: `docs/vendors.md`.

## 6. The conversation model

A caller cannot play files in order — it does not know what the agent will ask.
The caller is a state machine driven by what it hears:

```
agent speaks
  → detect end of turn          (inbound silence > 700 ms)
  → transcribe with YOUR referee ASR
  → classify intent             (ask_name | ask_phone | confirm | repair | unknown)
  → select the matching pre-rendered turn
  → if nothing matches → ADAPTIVE FALLBACK (LLM caller, TTS + degrade at runtime)
  → play at real-time pace, session noise bed continuing underneath
```

### Two-tier caller

- **Canonical path** — deterministic scripted dialogue, fully pre-rendered and
  hash-pinned. This is the corpus and this is what gets compared; when the agent
  follows the expected flow every vendor hears byte-identical audio.
- **Adaptive fallback** — off-script turns generated by an LLM caller at
  **temperature 0**, TTS'd and degraded at runtime with that session's fixed noise
  bed and seed. Logged and published, excluded from the headline comparison.
- **Divergence rate** — how often each vendor left the canonical path. A metric in
  its own right, reported by neither τ-Voice nor τ-Elicitation.

### Why multi-turn (worked example)

`booking_3field`, clean:

```
[00:00] AGENT  "Thanks for calling Riverside Clinic. Can I take your name?"
[00:04] CALLER ▶ turn_name
[00:08] AGENT  "Thanks Wei Ming. And a contact number?"        ✓ name
[00:11] CALLER ▶ turn_phone
[00:16] AGENT  "Got it, 91234567. What date works?"            ✓ phone
[00:19] CALLER ▶ turn_date
[00:23] AGENT  "Booked for 15 October."                        ✓ date
→ 3/3 slots · 4 turns · 23 s · 0 repairs
```

Identical scenario, café at 5 dB:

```
[00:00] AGENT  "Thanks for calling Riverside Clinic. Can I take your name?"
[00:04] CALLER ▶ turn_name (5 dB)
[00:09] AGENT  "Sorry, I didn't catch that. Could you repeat?"   ⟳ REPAIR 1
[00:12] CALLER ▶ turn_name (5 dB, bed continues at new offset)
[00:18] AGENT  "Thanks Tan Wei Min. And a contact number?"       ⚠ name truncated
[00:21] CALLER ▶ turn_phone (5 dB)
[00:28] AGENT  "Got it, 9123456. What date works?"               ⚠ SILENT ERROR
                 7 digits stored, nothing flagged
[00:31] CALLER ▶ turn_correct        ← adaptive: no canonical turn matched
[00:38] AGENT  "Let me try again. What's your number?"           ⟳ REPAIR 2
→ 1/3 slots · 11 turns · 74 s · 3 repairs · 1 silent error · 3.2x cost
```

None of that is visible single-turn. A single-turn corpus records "phone: wrong" and
misses that the agent *did not know* it was wrong, that the call took three times as
long, and that the caller's correction was never accepted.

## 7. Corpus

The corpus unit is a **call**. One continuous ambience bed runs the whole session —
under the caller's speech, under the agent's speech, through every pause.

```
sessions/<lang>/<scenario>/<condition>/
  bed.wav          continuous ambience, full call duration
  turn_01.wav      caller turn + bed slice at its true offset
  turn_02.wav
  timeline.json    offsets, so the bed is provably contiguous
  manifest row     condition inlined, SHA-256 pinned, seed recorded
```

Every item carries 0.5 s head and 1.5 s tail padding. Endpointing is decided in the
silence that follows speech, and the tail is a pure-noise reference that makes
delivered SNR computable from the finished file — the calibration check that already
caught two real bugs in the first build (a jitter σ larger than its own playout
buffer, and Gilbert-Elliott sampling variance on short utterances).

### Languages

| Language | Tag | Role | Vendor support risk |
|---|---|---|---|
| Spanish | `es-ES` | Control — lowest referee error | Universal |
| English (US) | `en-US` | Anchor — comparable prior results | Universal |
| Hindi | `hi-IN` | Finding + hard case; Hinglish code-switch | Bland has `hi-Latn`; others `hi` only |
| Bahasa Indonesia | `id-ID` | Finding — zero coverage anywhere | Telnyx undocumented |
| English (Singapore) | `en-SG` | Finding — accent + Singlish | **7/8 vendors have no `en-SG`** |

### Scenarios

Entity chains mirroring τ-Elicitation's structure so the compounding curve is
directly comparable:

- **S1** single field — phone number only
- **S2** linked two-field — name then phone
- **S3** linked three-field — name, phone, date (the compounding case)
- **S4** staged — validate one field before revealing the next (tests 0.64 → 0.82)

Adversarial turns embedded within scenarios: self-correction (false start must not
be captured), three-second mid-sentence pause, barge-in over the greeting,
mid-sentence code-switch (en-SG, hi-IN), confusable pairs (fifteen/fifty, local
toponyms).

**Known limitation, stated up front.** TTS speech is cleaner and more uniform than
human speech, so absolute scores will be optimistic. Rankings and deltas remain
valid. Mitigate by recording ~20 human utterances per language and publishing the
synthetic-to-human offset as a measured correction — itself a contribution.

## 8. Degradation matrix

Speech held at −26 dBov active level (ITU-T P.56); noise scaled to target SNR.

**Block A — reference ladder, café babble (6 conditions)**
- ambience: café babble, 16 talkers, seeded
- SNR: 30 / 20 / 15 / 10 / 5 / 0 dB
- codec: G.711 µ-law 8 kHz, PCMU-pinned in SDP

**Block B — ambience sweep at fixed 10 dB (4)**
- street (traffic bed + passing-vehicle swells)
- car (engine harmonics, tyre roar, cabin wind)
- office (HVAC + keyboard + distant speech)
- tv (band-limited speech + music bleed)

**Block C — network (3)**
- loss: Gilbert-Elliott, 5% mean, burst length 3, PLC hold
- jitter: 30 ms σ, 60 ms playout buffer
- codec: Opus @ 8 kbps starved (where the vendor supports it)

**Block D — controls (2)**
- clean: baseline; every score is a delta from this, and it estimates the lapse rate
- **noise-only: 30 s café ambience, ZERO speech**

*The control nobody runs.* Block D's noise-only condition counts the turns an agent
generates against thirty seconds of ambience containing no speech at all. An agent
that answers noise will answer noise in production. Two lines of code, devastating,
absent from every published benchmark.

**15 conditions per language.**

## 9. Measurement

> **The core methodological threat.** The referee ASR degrades with SNR at the same
> time as the agent does. A measured accuracy drop from 15 dB to 0 dB is a confound
> of agent degradation and referee degradation — and published ASR-robustness work
> shows noise gaps costing **+119% error**, which can be the larger term.

This is why τ-Elicitation deliberately omits caller-side ASR entirely. Handling it
properly is this project's contribution. Four parts, detailed in `docs/referees.md`:

1. **Per-language referees.** Whisper large-v3-turbo scores 32.02 WER on Singapore
   English; MERaLiON-2-10B scores 4.62 on the same corpus. A 7× gap.
2. **The truncation rule.** Human-label a stratified sample (100–200 calls per
   language across all six SNR levels), characterise the referee's own error as a
   function of SNR, and **truncate the ladder where referee error stops being much
   smaller than agent error.** State it explicitly.
3. **Score from the clean side.** The agent's own speech is clean TTS. Score task
   success primarily from the agent's read-backs and stored values against known
   caller ground truth.
4. **Two referees, publish their agreement.** Inter-referee agreement as a function
   of SNR is a publishable proxy for referee reliability.

### Metrics

| Tier | Metric | Why |
|---|---|---|
| 1 | Task success (all slots) | Primary. Binary at call level keeps statistics binomial. |
| 1 | Per-field entity accuracy | Robust to referee error. Comparable to τ-Elicitation. |
| 1 | **Silent-error rate** | Wrong value stored, no flag raised. Least measured, most dangerous. |
| 2 | Repairs per call · duration multiplier | Repair loops × per-minute billing = cost under degradation. |
| 2 | Correction acceptance | Caller corrects a misheard digit. Does it take, under noise? |
| 2 | Endpoint latency | From your own tape, one clock, every vendor. |
| 2 | False barge-in · hallucinated turns | The latter from the noise-only control. |
| 2 | Divergence rate · code-switch retention | Both novel; neither reported anywhere. |
| 2 | Referee agreement · hallucination rate | Instrument health, published alongside results. |
| 3 | Drop rate · cost/min · language fallback used | Non-connecting calls stay in the denominator. |

**RDP — Robustness Degradation Point.** The SNR at which task success crosses 95% of
that vendor's own clean baseline, reported with a bootstrap confidence interval. One
number per vendor, per language, per ambience. Each vendor is its own control group,
so it survives residual harness differences.

## 10. Statistics

Full detail in `docs/statistics.md`. The essentials:

- **pass^k, not pass@k.** pass@k rises with k and hides degradation. Under
  independence pass^k = p^k, so it *amplifies* the signal being measured.
- **Pair scenarios across SNR levels** — 155 paired vs 398 unpaired for a 10-point
  effect at 80% power. Free 2.5× saving.
- **Cluster bootstrap at task level**, conditions kept paired, 10,000 resamples.
  Ignoring the clustering understates uncertainty by up to 3×.
- **Wilson or Clopper-Pearson**, never Wald.
- **Sign-permutation tests**, 100,000 permutations, **Holm correction**.
- **Psychometric function with guess AND lapse rates**: p = γ + (1−γ−λ)·F(SNR).
  Omitting the lapse rate biases both threshold and slope; the clean condition gives
  you a good lapse estimate for free.
- **Threshold = −β₀/β₁ with Fieller or bootstrap CIs**, not the delta method.
  Cross-check against non-parametric Spearman-Kärber.
- Fit with **psignifit** (handles the overdispersion from task-difficulty variation).
- Choose repeats by `E[σ²ᵢ]/K ≪ Var(x)`; past that, spend budget on more scenarios.
- **Pilot with an adaptive staircase (QUEST/ZEST)** to locate the threshold region,
  then place the fixed ladder to bracket it. The ladder must span ~0% to ~100% or
  the slope is unidentifiable and the threshold CI is unbounded.
- **Score at call level, not slot level.** Per-slot successes are correlated within
  a call and violate binomial independence.

Do not chase determinism: temperature 0 produced 80 unique outputs from 1,000
completions in published work, and no voice platform exposes a determinism control.

## 11. Build order — every phase ships something

The old plan built the whole harness before anything was visible. That is the wrong
order for a project whose value depends on people seeing it. **Each phase below
produces a shareable artifact**, and each one de-risks the next.

| Phase | Weeks | Ships | Needs |
|---|---|---|---|
| **0. Spike** | 1 | Go/no-go decision | Nothing new |
| **1. Listening demo** | 2 | A public web page, $0 to host | Browser only |
| **2. One-shot SIP probe** | 3–5 | First real call to a real vendor | PJSIP |
| **3. Hallucination reel** | 6 | **A video people share** | Phase 2 |
| **4. Session corpus** | 7–8 | Multi-turn corpus, 5 languages | — |
| **5. Mock agent + full harness** | 9–12 | `polycall demo` — whole loop, no accounts | Phase 2 |
| **6. Caller state machine** | 13–14 | Multi-turn conversations | Laya, in-loop ASR |
| **7. Vendor adapters** | 15–16 | All 7 vendors | Keys |
| **8. Referees + scoring + stats** | 17–18 | Numbers you can defend | GPU |
| **9. Pilot** | 19 | Range-finding, variance check | — |
| **10. Full run + publish** | 20–22 | Dataset, leaderboard, preprint | — |

### Phase 0 — Spike (week 1, KILL GATE)

**Day 1, before anything else:** file the Telnyx Level 2 verification, request Bland's
SIP entitlement, and request the Pipecat concurrency increase. All three are human
reviews with lead time and will otherwise become the critical path in week 15.

Then: native-speaker review of 20 ElevenLabs utterances per language; benchmark 3
referee ASRs per language, clean and at 10 dB; verify the ElevenLabs
non-routable-number SIP path; benchmark Laya intent accuracy on ~200 real agent
transcripts per language.

**STOP IF** TTS quality fails native review, or referee error exceeds the effect size
you need to measure.

### Phase 1 — Listening demo (week 2)

**A static page. No backend, no accounts, no keys, no cost.** The user drops in a
WAV (or records from their mic), and hears their own voice at 30/20/15/10/5/0 dB
café, with the transcript at each rung underneath.

This is the front door for everything else. It runs entirely in the browser — see
`docs/stack.md` for the WebAudio/WASM stack — so it costs nothing to host and
survives a front-page traffic spike. "Your audio never leaves your browser" is also
the right answer for the security-conscious audience that will not paste an API key
anywhere.

Deploy to GitHub Pages. This is the link that goes in every post.

### Phase 2 — One-shot SIP probe (weeks 3–5)

The smallest thing that makes a real call: **dial, play one file, record what comes
back, hang up.** No state machine, no intent classification, no scenarios.

This is deliberately the hardest infrastructure work done at its simplest. If PJSIP,
the codec pinning, the conference-port build and the vendor credentials all work for
one file, everything after is incremental. Doing it this way means SIP is debugged
before any conversational logic exists to confuse the picture.

### Phase 3 — Hallucination reel (week 6)

Play the `ctl_noise_only` condition — 30 seconds of café ambience containing **no
speech at all** — to every vendor you can reach, and count the turns each one
generates.

Nearly free once Phase 2 works: it needs dial, play, record, and a VAD to count
agent speech segments. No transcription, no scoring, no scenarios.

The output is a 30-second video with no narration required. It is the single most
shareable artifact in the project and it costs two lines of code on top of the probe.
Publish it alongside the Phase 1 demo.

### Phase 4 — Session corpus (weeks 7–8)

Convert the working utterance builder to multi-turn sessions: continuous ambience
beds sliced per turn at the correct offset, `timeline.json`, scenario schema. Four
scenarios x five languages x three voices. Entity labels, per-language normalisation,
NFC and number canonicalisation.

### Phase 5 — Mock agent + full harness (weeks 9–12)

The mock agent first: a local SIP endpoint you control, deliberately imperfect
(configurable ASR error rate, repair behaviour, silent-error rate). Then grow the
Phase 2 probe into the full client — process sharding, real-time 20 ms pacing,
stereo tape, VAD turn boundaries.

`polycall demo` ships here. It runs the entire loop with zero accounts and zero cost,
which is how contributors develop and how users verify their setup before spending.
It also doubles as the null test proving the harness adds no bias of its own.

### Phase 6 — Caller state machine (weeks 13–14)

Intent classification on degraded agent speech (Laya cascade, `docs/stack.md`),
canonical path matching, adaptive fallback at temperature 0, divergence logging.

### Phase 7 — Vendor adapters (weeks 15–16)

Canonical config to 7 adapters. Voice-mapping table, language-code translation,
per-vendor language-fallback recording. Exponential backoff and 429 instrumentation
from day one — four vendors publish no rate limits.

### Phase 8 — Referees, scoring, statistics (weeks 17–18)

Per-language ASR routing, dual-referee agreement, entity extraction, silent-error
detection. Cluster bootstrap, psignifit threshold fitting, pass^k, permutation tests
with Holm correction.

### Phase 9 — Pilot (week 19, KILL GATE)

Adaptive staircase on two vendors x two languages to locate the threshold region;
confirm the fixed ladder brackets 0–100%.

**STOP IF** run-to-run variance swamps the between-condition effect, or the ladder
cannot be positioned to bracket the range.

### Phase 10 — Full run and publish (weeks 20–22)

6,300 calls across parallel vendor tracks (~2.3 h wall clock). Human verification
sample, far-end SNR calibration, discarded turns logged with reasons. Hugging Face
dataset, repo with `verify.py`, static leaderboard, arXiv preprint, one blog post
built around one number.

## 12. Impact — what actually travels

Repos do not go viral. Findings and experiences do. Ranked:

| Asset | Reach | Why |
|---|---|---|
| **The hallucination reel** — 30 s of café noise with nobody speaking; agents reply | **Highest** | Funny, damning, needs no technical background, no narration. Two lines of code. |
| **The listening demo** — hear your own voice at 5 dB | **High** | Sensory and immediate. Nobody needs the concept explained after hearing it once. |
| **The language gap** — agents lose more in Hindi/Bahasa/Singlish at identical noise | **High** | Moral dimension; natural audience in SEA and India. Indicts the field, not one company. |
| **Silent errors** — wrong value stored, no flag raised | Medium | Visceral to any buyer, but needs more setup. |
| The corpus, the harness, the leaderboard | Low | Valuable and slow-burning. These are the citation assets, not the viral ones. |

### Launch sequence

1. **Ship the listening demo** (Phase 1). It is the link everything else points at.
2. **Cut the hallucination reel** (Phase 3) as soon as the probe reaches three vendors.
3. **Publish demo + reel together.** A finding without receipts gets dismissed; a tool
   without a finding gets ignored. Together they reinforce.
4. **Then the full benchmark and the language gap**, with the demo already carrying traffic.
5. **The Hugging Face dataset throughout** — the asset that still matters in two years.

### Two things to avoid

**Do not lead with a vendor leaderboard.** "Vendor X is worst" buys one news cycle and
permanent hostility from a company with more lawyers than you. The language-gap and
hallucination framings are more interesting *and* safer, because they indict the field
rather than an individual — which lets vendors engage instead of attack. Save named
rankings for the paper, with right of reply (`docs/responsible-use.md`).

**Do not confuse virality with impact.** A dataset with 5,000 downloads over two years
does more than one 100k-view post. The viral moment is distribution for the durable
artifact, not the goal.

### Artifacts, in order of durable value

1. **The Hugging Face dataset.** What people find, download and cite. Download counts
   survive a job change; a repo with no data is invisible by comparison.
2. **One blog post with one number.**
3. **The arXiv preprint.** The referee-degradation protocol in §9 is what reviewers
   engage with — write that section first and best.
4. **Upstream contributions** to an existing benchmark.

### The one-line version

> Built and published the first open benchmark measuring multi-turn voice-agent
> robustness under a controlled degradation ladder over real SIP telephony, across
> five languages including three with no prior coverage — releasing a hash-pinned
> corpus and the first published error bars on the measurement instrument itself.

## 13. Risks

| Risk | Assessment | Mitigation |
|---|---|---|
| Run-to-run variance swamps the effect | Biggest scientific risk | Paired design (2.5× power), 3 repeats, cluster bootstrap, pass^k amplification, Phase 6 gate |
| Referee degrades faster than the agent at low SNR | Likely at 0–5 dB | Truncation rule, dual referees, human-labelled sample, score from agent's clean side |
| Singapore English has no vendor support | **Confirmed — 7 of 8** | Record fallback per call; publish as a finding |
| PJSIP stops at 32 concurrent calls | Will happen if only MAX_CALLS is raised | Full `config_site.h`, process sharding, `setNullDev()` |
| Manual approval gates become critical path | Avoidable | File Telnyx L2, Bland SIP entitlement, Pipecat concurrency on day one |
| Synthflow unobtainable | **Confirmed** — $30k/yr floor, self-serve removed 2026-09-15 | Run seven vendors; state why the eighth is absent |
| Vendor SIP and pricing change | Certain | Versioned config with per-run verification |
| A vendor disputes the results | Certain, if the work lands | The good outcome — shipped corpus + `verify.py` make the argument about the result, not the method |

## 14. Definition of done

- A third party downloads the corpus, runs `verify.py`, and reproduces a published
  RDP figure within its stated confidence interval.
- Every condition is specified by published parameters, not by a name.
- Each language carries a referee error figure, a significance threshold, and a
  stated ladder truncation point.
- Seven commercial platforms measured over an identical PCMU-pinned SIP transport,
  with the language fallback recorded per call.
- All five languages reported, with Spanish and English demonstrating the instrument
  is sound.
- At least one finding no prior published work contains.
- Non-connecting calls and discarded turns appear in the denominator, with reasons.
- The mock agent demonstrates the harness adds no measurable bias of its own.
