# Referee ASR — per-language choice, normalisation, and the truncation rule

> **The core methodological threat.** The referee ASR degrades with SNR at the same
> time as the agent does. A measured accuracy drop from 15 dB to 0 dB is a confound
> of agent degradation and referee degradation — and published ASR-robustness work
> (WildASR, arXiv 2603.25727) shows noise gaps costing **+118.9% (Japanese)** and
> **+121.0% (Korean)** error. That can be the larger term.

This is why τ-Elicitation deliberately omits caller-side ASR entirely. Handling it
properly is this project's contribution.

## 1. A uniform referee is indefensible

| Model | Singapore English WER (NSC corpus) |
|---|---|
| Whisper-large-v3-turbo | **32.02** |
| MERaLiON-2-10B-ASR | **4.62** |
| Polyglot-Lion-1.7B | 5.28 |

**A ~7× gap.** Using Whisper uniformly would make the en-SG results a measurement of
Whisper, not of any vendor.

| Language | Primary referee | Cross-check | Notes |
|---|---|---|---|
| Spanish | Whisper large-v3 (self-hosted) | ElevenLabs Scribe v2 | 2.3–4.4% WER; the easy one |
| English (US) | Whisper large-v3 / Parakeet-TDT-v3 | AssemblyAI Universal-3 | Well-characterised baseline |
| **English (SG)** | **MERaLiON-3-3B-ASR** | Polyglot-Lion-1.7B | Purpose-built for Singapore; IMDA NSC Part 4 is the code-switching split |
| **Hindi / Hinglish** | **Sarvam Saaras V3** | Gemini 3.5 Transcribe | ~19% WER on IndicVoices (spontaneous, harder than FLEURS); strongest code-mixing claim |
| Bahasa Indonesia | Gemini 3.5 or MERaLiON-3 | Whisper large-v3 | FLEURS 5.2% is optimistic — spontaneous speech is 2–3× worse |

**Benchmark all candidates on your own audio in Phase 0.** Vendor WER numbers come
from clean read speech; yours is telephony-band spontaneous speech.

## 2. The truncation rule

1. **Human-label a stratified sample** — 100–200 calls per language, stratified
   across all six SNR levels. Non-negotiable: it is the only way to know the
   referee's error *as a function of SNR*.
2. **Characterise the referee's SNR-dependent error curve** from that sample: WER,
   entity accuracy, and hallucination rate vs SNR, per language.
3. **Truncate the ladder** at the SNR where referee error stops being much smaller
   than agent error, and **say so explicitly**. If at 0 dB the referee's entity error
   is 25% and the agent's task failure is 30%, that point is uninterpretable and must
   not be reported as a ranking.

This is probably the single most important design decision in the study, and stating
it plainly is what makes the rest credible.

## 3. Score from the clean side

The agent's own speech is clean TTS — you did not add noise to it. So score task
success primarily from **the agent's read-backs and stored values**, transcribed with
high confidence, against known caller ground truth. That largely sidesteps the
confound. Reserve noisy caller-side transcription for metrics that genuinely need it,
and flag those separately.

## 4. Two referees, publish their agreement

Where two architecturally different ASRs agree, confidence is high; where they
disagree, escalate to human review or mark the call indeterminate. **Inter-referee
agreement as a function of SNR** is a publishable proxy for referee reliability at
scales where human labels are unaffordable.

Also track **hallucination rate separately from WER**. WildASR found Nova 2 on
Chinese code-switching at 33.7% character error but **68.4% hallucination rate** — a
referee that hallucinates fluently is worse than one that errors visibly, because
hallucinations look like valid transcripts and silently corrupt scoring.

## Throughput and cost

Self-hosted faster-whisper large-v2, 13 min of audio on one GPU:

| Config | Time | VRAM | RTF |
|---|---|---|---|
| openai/whisper fp16 | 2m23s | 4708 MB | 0.18 |
| faster-whisper fp16 | 1m03s | 4525 MB | 0.08 |
| **faster-whisper fp16, batch=8** | **17s** | 6090 MB | **0.02** |
| faster-whisper int8, batch=8 | 16s | 4500 MB | 0.02 |

~50× real time batched. Our 315 audio-hours ≈ **6.3 GPU-hours ≈ $9**.

Batch API alternatives per audio-hour: Groq Whisper-v3 **$0.04**, AssemblyAI async
$0.21, Deepgram Nova-3 batch ~$0.26, Gemini 3.5 batch $0.30.

**Use batch/async endpoints, never streaming** — streaming costs 2–10× more and is
*less* accurate (no bidirectional attention over the full waveform).

Cost is not the constraint. **Accuracy per language is.** Budget for 2–3 referees.

## Normalisation

Standard practice: **Whisper's `EnglishTextNormalizer`** for English and
`BasicTextNormalizer` for everything else, with **`jiwer`** computing the edit
distance. The `whisper_normalizer` package bundles both plus Indic normalizers
derived from indic-nlp-library.

### Pipeline

1. **Unicode NFC first, always.**
2. Language-routed normaliser:
   - English, Singapore English → `EnglishTextNormalizer`
   - Hindi → `whisper_normalizer` Indic normaliser with **`preserve_marks=True`**
   - Spanish, Indonesian → `BasicTextNormalizer`
3. **Canonicalise numbers to one spoken form on BOTH sides** before scoring.
4. For Hindi, **decide and document a script policy** for code-mixed tokens
   (romanised vs Devanagari) and apply it identically to both sides.
5. Compute with `jiwer`. Report **both** WER and entity/slot accuracy.
6. **Version-control and hash the normaliser.** The same audio and transcript can
   differ by several WER points across normalisers.

### Traps

- **`BasicTextNormalizer` strips combining marks by default.** On Devanagari this
  inflates WER in a way that looks like ASR error but is a normalisation bug.
- **Numbers have at least three valid written forms** — "पांच सौ" / "500" / "५००".
  Standard WER treats them as unrelated tokens. No off-the-shelf tool handles this
  consistently; write it and document it.
- **Colloquial variants**: Tamil "avunga" vs formal "avargal" scores 80% WER despite
  identical meaning. Morphologically rich languages penalise valid suffix
  alternatives heavily.
- **Normalisation for Hinglish and Singlish code-mixing is not established
  practice.** You will define your own policy and defend it. Treat it as a methods
  contribution, not an afterthought.

### Why entity accuracy beats WER here

WER treats a wrong digit and a dropped "the" identically. We care whether the agent
got the phone number, the date and the name right. **Entity Preservation Score is the
primary metric; WER is reported as a comparable secondary.**
