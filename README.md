# PolyCall Bench

**Find out where your voice AI agent breaks.**

Replays realistic degraded audio — café noise, a car, a bad line, packet loss — into
your agent over a real SIP call, across a controlled ladder of conditions, and tells
you the SNR at which it stops working.

```
                     clean   30dB   20dB   15dB   10dB    5dB    0dB
 task success         100%    98%    97%    94%    88%    61%    12%
 phone captured       100%   100%    98%    96%    91%    58%     9%
 silent errors           0      0      0      1      3      9     14
 repairs / call        0.0    0.0    0.1    0.4    1.2    3.1    5.7
                                                    ^
                                          RDP = 9.4 dB  [8.1, 10.9]
```

Works with Retell, Vapi, ElevenLabs Agents, Bland, LiveKit, Telnyx and Pipecat.
Five languages: English (US), English (Singapore), Hindi, Spanish, Bahasa Indonesia.

---

## 60 seconds, no accounts, no GPU

```bash
pip install polycall
polycall degrade myvoice.wav --ladder cafe
```

Writes the full SNR ladder of your own audio to `./out/`. Play `cafe_05dB.wav` and
you will understand the rest of this README.

## 5 minutes, still no accounts

```bash
docker run --rm -v "$PWD/out:/out" ghcr.io/polycall/bench demo
```

Runs a **mock agent** — a local SIP endpoint we ship — and calls it across the full
15-condition matrix. No API keys, no phone number, no money. This is the end-to-end
loop, and it is also how you check your machine is set up correctly before spending
anything.

## Testing your own agent

```bash
export RETELL_API_KEY=...
polycall call --vendor retell --agent-id agent_abc123 \
              --scenario booking_3field --ladder cafe --lang en-US
```

No phone number required — we dial the vendor's SIP URI directly. Telephony cost is
$0 for five of the seven supported vendors. You pay only the vendor's own per-minute
fee, which you would pay testing by hand anyway.

`polycall doctor` tells you exactly what is missing from your setup.

---

## Two modes — read this before you quote a number

| | **Mode A — test my agent** (default) | **Mode B — reproduce the benchmark** |
|---|---|---|
| Agent under test | **Yours.** Your prompt, LLM, voice, language config | A standardised agent the tool creates from a pinned spec |
| Flag | *(default)* | `--standard-agent` |
| Valid for | Your own regression testing and vendor selection **for your use case** | Public, cross-vendor comparison |
| Comparable to the published leaderboard? | **No** | Yes |

Mode A measures *your agent on that vendor*. If your prompt is weak, the number is
about your prompt. Every result file stamps the mode, and `polycall report` refuses
to emit a leaderboard-comparable table from a Mode A run.

**Please do not post Mode A numbers as "vendor X scores N".** They are yours, not
the vendor's.

---

## What makes the numbers trustworthy

- **Your own referee.** We never use the vendor's transcript for scoring — those are
  post-processed differently by every vendor. We record the agent's spoken reply from
  our own leg and transcribe it ourselves, identically for everyone.
- **Every condition is published parameters, not a name.** "Café noise" is not a
  condition; a seeded 16-talker babble mixed to 10 dB SNR against a −26 dBov active
  speech level through G.711 µ-law is a condition.
- **One codec for everyone.** We offer PCMU only in the SDP. Left to negotiate freely,
  one vendor lands on Opus and another on G.711 and you are measuring codec choice.
- **We publish our instrument's error.** Referee ASR degrades under noise too. We
  measure how much, per language, and truncate the ladder where our own measurement
  stops being trustworthy. See `docs/referees.md`.
- **Every artifact is hash-pinned and seeded.** `polycall verify` re-downloads the
  published corpus, re-checks every SHA-256, and reproduces a published figure.

## Install

```bash
pip install polycall                  # degrade, corpus, score, report
docker pull ghcr.io/polycall/bench    # + SIP (PJSIP prebuilt), demo, call, bench
```

The Docker image exists because live calling needs a custom PJSIP build. If you only
want to generate degraded audio, pip is enough. A GPU is optional — `--device cpu`
works at reduced concurrency; see `docs/stack.md`.

## Costs, and the guard rails

`polycall bench --all` could spend thousands of dollars. It will not let you:
every run prints an estimated cost and requires confirmation, concurrency defaults
to conservative values, and `--yes` must be explicit in CI. See
`docs/responsible-use.md` — also for vendor terms of service, right of reply before
publishing comparisons, and why you should not point this at someone else's agent.

## Running a real test today

The `polycall` CLI above is the project's target shape - not built yet (see
`docs/cli.md`'s own note that it's aspirational). Every capability that exists today
is a standalone script under `scripts/`, run inside the `polycall-bench` Docker image
(PJSIP is only prebuilt there). Concretely, for Telnyx in Hindi:

```bash
# 1. One-time: build the degraded-audio session corpus for this (language, scenario)
#    pair, if out/sessions/sessions/hi-IN/S3_name_phone_date/ doesn't exist yet.
python scripts/build_session_corpus.py --languages hi-IN --scenarios S3_name_phone_date

# 2. Place the calls - all 15 conditions, one vendor, one language, one scenario.
#    This dials a real vendor and costs real money - see docs/responsible-use.md.
scripts/run_pilot.sh --vendor telnyx --lang hi-IN --out-suffix my_run

# 3. Score what came back. --log scopes scoring to exactly this run's calls
#    (never the whole out/calls/ directory, which would also pull in every
#    other call anyone has ever placed on this machine).
docker run --rm -v "$PWD:/app" --env-file .env --entrypoint python3 \
  polycall-bench:latest scripts/score_run.py \
  --log out/caller_pilot_telnyx_hi_IN_my_run.jsonl --out out/scores_my_run
```

Step 3 prints per-call slot accuracy, repairs, silent errors, and the tier1/tier2/
tier3/adaptive-fallback breakdown per call, plus an aggregate summary - see
"What makes the numbers trustworthy" above for why that breakdown matters (the
adaptive fallback plays freshly-synthesized audio, not the condition's real
degraded recording, so a high adaptive rate means the test got quietly easier for
that vendor on some turns).

Swap `--vendor telnyx` for `--vendor retell` or `--vendor elevenlabs`, and `--lang
hi-IN` for any of `en-US` / `en-SG` / `es-ES` / `id-ID`, once that language's session
corpus is built. `scripts/run_pilot.sh` always runs the full 15-condition ladder in
one shot; to retry a single condition, call `scripts/run_caller.py` directly with
`--session-dir out/sessions/sessions/<lang>/<scenario>/<condition>` (see the script's
own `--help` for every flag, including `--silence-s` and `--connect-timeout-s`,
which `run_pilot.sh` overrides from their library defaults - confirmed live these
values matter: too-short silence detection makes the caller interrupt the vendor
mid-sentence, and Telnyx specifically needs more than the default connect timeout).

## Docs

| | |
|---|---|
| `QUICKSTART.md` | The progressive path, from zero install to a full run |
| `PLAN.md` | The specification, and the build order (§11) |
| `docs/stack.md` | Architecture, the browser demo stack, intent classification, hardware sizing |
| `docs/vendors.md` | Per-vendor SIP, codecs, concurrency, APIs, billing traps |
| `docs/sip-harness.md` | PJSIP build and the traps |
| `docs/referees.md` | Per-language ASR, normalisation, the truncation rule |
| `docs/statistics.md` | pass^k, paired design, bootstrap, threshold fitting |
| `docs/responsible-use.md` | ToS, cost guards, publication ethics |
| `CONTRIBUTING.md` | Including how to add a vendor |

## Licence

Code **MIT**. Corpus and results **CC-BY-4.0**.
