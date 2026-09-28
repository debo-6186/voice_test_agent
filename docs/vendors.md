# Vendor reference

Verified against official vendor documentation, September 2026. **This file ages
faster than anything else in the repo.** Keep the live values in versioned YAML with
a per-run verification step; treat this as the researched starting point, not truth.

## SIP reachability — can a laptop softphone dial in with no number, no carrier?

| Vendor | Dial-in | URI / mechanism | Auth |
|---|---|---|---|
| **Vapi** | Yes | `sip:{name}@sip.vapi.ai` (EU: `sip.eu.vapi.ai`) | **None** — docs state no auth or SIP registration required |
| **Retell** | Yes | `POST /v2/register-phone-call` → `call_id` → `sip:{call_id}@sip.retellai.com` | The one-time `call_id` *is* the credential |
| **Telnyx** | Yes | SIP URI Calling: `user@sip.telnyx.com`; or SIP Attach: `<ext>@<appFQDN>.sip.telnyx.com` | "From Anyone (unrestricted)" setting; no number needed |
| **LiveKit** | Yes | `sip:{subdomain}.sip.livekit.cloud`, inbound trunk with `numbers: []` | Laptop public IP in `allowed_addresses` (or username/password). Self-hostable |
| **ElevenLabs** | Probably | `sip:+1999...@sip.rtc.elevenlabs.io:5060` | Requires an imported E.164 number — but their own doc example (`+19991234567`) is non-routable, suggesting arbitrary well-formed strings work. **Verify empirically in Phase 0.** |
| **Bland** | Gated | `us2.sip.bland.ai:5061;transport=tls` (+ `ca2.`, `asia2.`, `eu2.`) | Requires a SIP entitlement enabled by support — **request in week 1** |
| **Pipecat** | Indirect | No SIP stack at all; terminate via Daily/Twilio/Telnyx | Inherits the provider's |
| **Synthflow** | **Blocked** | `sip.synthflow.ai:32682;transport=tls` | Enterprise-only, **$30k/yr floor**, self-serve reselling removed 2026-09-15 |

**Jambonz** is a free self-hosted bridge for gated vendors — its `dial` verb takes
arbitrary SIP URIs with optional auth. Retell's own support recommends it, and there
is an official `jambonz/retell-sip-integration-example` repo.

## Codecs on the SIP leg

| Vendor | Codecs |
|---|---|
| Retell | PCMU, PCMA, G.722 |
| ElevenLabs | **G.711, G.722 only** |
| LiveKit | PCMU, PCMA, G.722 — **no Opus**; force via `onlyListedCodecs` |
| Bland | PCMU, PCMA, Opus, G.722, G.729 |
| Synthflow | G.711 µ-law/A-law, Opus |
| Vapi, Telnyx | **Not documented** — determine empirically |

→ **Offer PCMU only in the SDP.** It is the intersection of all vendors' support and
what a real PSTN leg delivers anyway. Free negotiation means the benchmark silently
measures codec choice instead of agent quality.

## Concurrency — the lever that makes the matrix feasible

| Vendor | Default / tiers | Raise by | Burst |
|---|---|---|---|
| **Retell** | 20 (pay-as-you-go) | $8/concurrency/month; enterprise uncapped | min(3× limit, limit+300) at +$0.10/min |
| **Vapi** | Usage-only 4; Core ($29) 10; Pro ($999 min) 30 | $10/line/month add-on | None |
| **ElevenLabs** | Free 4, Starter 6, Creator 10, Pro 20, Scale 30, Business 40 | Plan tier | Up to 3×/300, billed **2×** and **lower STT/TTS priority** — skews latency, avoid |
| **Bland** | Start (free) 10; Agent Phone ($29.99) **1**; Build ($299) 50; Scale ($499) 100 | Plan tier | None |
| **LiveKit** | Build (free) 5 sessions; Ship ($50) 20; Scale ($500) up to 600 | Plan tier | None |
| **Telnyx** | **2** outbound; **10** after Level 2 verification | Verification, then support request | None. Over-limit → SIP `403 User channel limit exceeded D1` |
| **Pipecat** | 50 per deployment (docs contradict a "unlimited" pricing claim) | Concurrency-increase request form | — |
| **Synthflow** | Not published, sales-gated | — | — |

Vapi note: concurrency is **per-subscription, shared across all organizations** —
you cannot multiply capacity by creating orgs.

## Programmatic agent creation

| Vendor | Create endpoint | ASR language control |
|---|---|---|
| Retell | `POST /create-agent` | `language` (string or **array** for multilingual) + `custom_stt_config.provider` |
| Vapi | `POST /assistant` | `transcriber.language` — **tied to transcriber choice**; auto-detect needs Deepgram `multi`, Gladia, or AssemblyAI |
| ElevenLabs | `POST /v1/convai/agents/create` | Splits `agent.language` (output) from `asr.provider/quality/keywords` (input) |
| Telnyx | `POST /v2/ai/assistants` | **Cleanest:** `transcription.model` (14 options) + `transcription.language` |
| Bland | `POST /agents` (name only) or per-call `POST /v1/calls` | `language`; uniquely includes **`hi-Latn`** (romanised Hinglish) |
| Synthflow | `POST /v2/assistants` | `agent.language`; no separate ASR field documented |
| LiveKit | **None** — worker code + dispatch metadata (512 KiB) | `inference.STT(language=)`; normalises ISO-639/BCP-47 automatically |
| Pipecat | **None** — Python, config set at **deploy** time | `Language` enum, 281 codes |

Only the prompt string ports cleanly. Voice IDs live in per-vendor namespaces and
language semantics differ fundamentally. **Define one canonical internal config**
(`{prompt, llm, voice_key, bcp47_language}`) and write thin adapters. The voice-
mapping table and language-code translation are the real work, not the HTTP calls.

### Language support for our five

| Vendor | hi-IN | id-ID | es-ES | en-SG |
|---|---|---|---|---|
| Retell | ✓ `hi-IN` | ✓ `id-ID` | ✓ `es-ES`, `es-419` | ✗ (has en-US/IN/GB/AU/NZ) |
| Vapi | ✓ | ✓ | ✓ | ✗ |
| ElevenLabs | ✓ | ✓ | ✓ | ✗ |
| Bland | ✓ `hi`, **`hi-Latn`** | ✓ `id` | ✓ | ✗ (has en-US/GB/AU/NZ/IN) |
| LiveKit | ✓ | ✓ | ✓ | provider-dependent |
| Telnyx | ✓ (Deepgram Flux) | **undocumented** | ✓ | ✗ |
| Synthflow | ✓ | ✓ | ✓ | ✗ |
| Pipecat | ✓ `HI_IN` | ✓ `ID_ID` | ✓ | **✓ `EN_SG`** (only one) |

**Seven of eight vendors do not document `en-SG` at all.** Singapore English will
fall back to `en-US`, `en-GB` or `en-IN` on every hosted vendor. **Record which
fallback each vendor used, per call, in the manifest** — without it the en-SG
results are not comparable, and "no vendor supports Singapore English" is itself a
publishable finding.

## Post-call data

| Vendor | Transcript + timestamps | Recording | Per-turn latency | Retention |
|---|---|---|---|---|
| **Retell** | Best in class — `transcript_object` with word-level start/end | `recording_url`, `recording_multi_channel_url` | Best in class — `e2e`/`asr`/`llm`/`tts` with p50/p90/p95/p99 | Configurable auto-delete; no default stated |
| **Vapi** | `artifact.messages[]` with `secondsFromStart`, `duration` | 7 endpoints incl. stereo, PCAP; 302 → signed URL | `artifact.performanceMetrics` per turn | Not stated |
| **ElevenLabs** | `GET /v1/convai/conversations/{id}`, `time_in_call_secs` | separate get-audio endpoint | No explicit per-turn fields; OTLP trace export | **Default 2 years**, configurable |
| **Bland** | `transcripts[]` with timestamps | `recording_url` (needs `record: true`) | `latency` webhook event only | `recording_expiration` returned in webhook |
| **LiveKit** | `session.history`, `make_session_report()` | via egress (file egress max 3 h) | Excellent code-level: `EOUMetrics`, `ttft`, `ttfb`, `e2e_latency` | 30-day observability window |
| **Telnyx** | Aggregates confirmed; per-conversation retrieval not confirmed | via Voice API recordings | Not documented | Not stated |

**Remember: we do not use vendor transcripts for scoring.** These fields are for
cross-checking, latency corroboration and debugging only.

## Rate limits

Only **Bland, LiveKit and Telnyx** publish numbers.

- **Bland** — Start: 100/day, 100/hr. Agent Phone: 50/day, 20/hr. Build: **2,000/day, 1,000/hr**. Scale: 5,000/day, 1,000/hr.
- **LiveKit** — Server API 1,000 req/min. Free plan also caps STT/TTS at 5 connections each.
- **Telnyx** — CPS: first 5 free, then $12–30/CPS. Standard SIP trunking capped at 20 CPS per source IP.
- **Retell, Vapi, ElevenLabs, Synthflow** — **not published.** Discover empirically; build exponential backoff and instrument 429s from day one.

## Billing traps that silently break an automated run

- **Retell blocks calls the instant balance hits $0.** Set auto-recharge before the
  run or it dies mid-sweep. Burst mode silently adds $0.10/min.
- **Retell free trial without a card**: call stuck at `call_status: registered`,
  `disconnection_reason: null`, silence. Looks like a SIP bug. Error string is
  `"Trial over quota, please add payment."`
- **Bland free tier is 100 calls/day AND 100/hour** — 900 calls would be a 9-day
  run. Build ($299) → 2,000/day. **Never select Agent Phone: it drops you to 1
  concurrent call.**
- **Telnyx trial can only dial your own verified number**, 2 concurrent, 100/day,
  10 min max per call. Ordered numbers reclaimed within 30 days if you don't
  upgrade. Level 2 verification is a manual review — **file it week 1**.
- **ElevenLabs burst bills 2× and drops STT/TTS priority** — that will skew your
  latency numbers. Buy the tier instead of bursting.
- **Pipecat Cloud has ~10 s cold start** polluting latency unless `--min-agents ≥ 1`
  (which bills 24/7).
- **Vapi free tier is 4 concurrent** — buy $10/line add-ons rather than the $999/mo
  Pro tier.
