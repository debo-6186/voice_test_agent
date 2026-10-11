# Quickstart

Four levels. Each one works on its own — stop wherever you have what you need.

---

## Level 0 — the corpus (no install)

The degraded audio is published as a dataset. Download it and play it into whatever
you already have.

```bash
huggingface-cli download polycall/corpus-v1 --repo-type dataset --local-dir ./corpus
python corpus/verify.py ./corpus     # re-hashes everything, recomputes delivered SNR
```

`manifest.jsonl` carries one row per file with the full condition parameters inlined,
the ground-truth text, typed entity labels, the seed and the SHA-256.

---

## Level 1 — degrade your own audio (pip, CPU, no accounts)

```bash
pip install polycall
polycall degrade myvoice.wav --ladder cafe --out ./out
polycall degrade myvoice.wav --condition C_loss5        # burst packet loss
polycall list-conditions                                 # all 15
```

Needs ffmpeg. No GPU, no keys, no network.

---

## Level 2 — call the mock agent (Docker, no accounts, no money)

```bash
docker run --rm -v "$PWD/out:/out" ghcr.io/polycall/bench demo
```

Starts a local SIP agent we ship, dials it with the real client, runs the full
15-condition matrix, and writes a report. Nothing leaves your machine.

Use this to:
- confirm your setup works before spending money
- develop against the harness without a vendor account
- sanity-check that the harness itself adds no bias (the mock's behaviour is known)

The mock is deliberately imperfect and configurable:

```bash
polycall demo --asr-error-rate 0.08 --silent-error-rate 0.03 --repair-threshold 0.4
```

---

## Level 3 — test your own agent

```bash
cp .env.example .env     # add the keys for the vendors you care about
polycall doctor          # checks ffmpeg, PJSIP, codecs, GPU, and which keys resolve
```

One condition, to check it connects:

```bash
polycall call --vendor vapi --agent-id asst_xyz \
              --scenario booking_3field --condition A_cafe_snr10 --lang en-US
```

The full ladder:

```bash
polycall call --vendor vapi --agent-id asst_xyz \
              --scenario booking_3field --ladder cafe --lang en-US --repeats 3
```

Then:

```bash
polycall score  --run runs/2026-09-24T10-22-vapi
polycall report --run runs/2026-09-24T10-22-vapi --open
```

Prints the estimated cost and asks before dialling. Roughly **$2–6** for one
18-call ladder.

### Several vendors at once

```bash
polycall bench --vendors retell,vapi,elevenlabs \
               --agents retell=agent_a,vapi=asst_b,elevenlabs=agt_c \
               --languages en-US,id-ID --ladder cafe --repeats 3
```

Vendor tracks run concurrently, so wall clock is set by your slowest vendor rather
than the sum.

---

## Level 4 — reproduce the published benchmark

This creates a **standardised agent** on each vendor from a pinned spec, so the
numbers are comparable to the leaderboard rather than to your own agent.

```bash
polycall bench --standard-agent --spec specs/booking_v3.yaml \
               --vendors retell,vapi,elevenlabs,bland,livekit,telnyx \
               --languages en-US,en-SG,hi-IN,es-ES,id-ID \
               --all-conditions --repeats 3
```

Needs agent-create permission on each vendor key, and a GPU is strongly recommended
(one T4 runs the whole thing at ~15% utilisation — see `docs/stack.md`).

Full matrix ≈ 6,300 calls, ≈ 2.3 h wall clock, ≈ $1,500–4,000 in vendor fees.
It will make you confirm that.

```bash
polycall verify --against published/v1     # reproduce a published figure
```

---

## Troubleshooting

`polycall doctor` first. Then:

| Symptom | Cause |
|---|---|
| Call sits at `registered`, then silence (Retell) | **Billing, not SIP.** Retell needs a card on file; free trial cannot dial the SIP domain |
| Second call to Retell fails | `call_id` is single-use — re-register per call |
| Codec negotiation fails (ElevenLabs) | They support G.711/G.722 only. We pin PCMU; check nothing overrode it |
| Stuck at exactly 32 concurrent | PJSIP conference ports. Use the Docker image, or rebuild with our `config_site.h` |
| Worker dies after ~30 calls | Missing `setNullDev()` — conference slots never freed. Use the Docker image |
| `403 User channel limit exceeded` (Telnyx) | Default is 2 concurrent; needs Level 2 verification |
| Caller pauses feel long, repairs inflated | In-loop ASR too slow. Use `--device cuda`, or lower concurrency |
