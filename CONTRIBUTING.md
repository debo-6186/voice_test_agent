# Contributing

## Develop without spending money

Everything except the vendor adapters can be built and tested against the mock
agent:

```bash
make dev            # installs deps, builds the PJSIP image
polycall demo       # full end-to-end loop, no accounts, no cost
pytest              # unit tests, no SIP at all
pytest -m sip       # against the mock agent
pytest -m vendor    # needs real keys; skipped by default
```

The mock agent is a real SIP endpoint with controllable imperfection, so most bugs
reproduce without touching a vendor.

## Adding a vendor

Most people never need to — the seven supported vendors cover the field. If you do:

1. Add `vendors/<name>.yaml`: SIP URI template, auth style, codecs offered,
   concurrency limits per plan, agent-create endpoint, language codes, known traps.
2. Implement `adapters/<name>.py` against the `VendorAdapter` protocol —
   `create_agent`, `start_call`, `end_call`, `fetch_artifacts`. Four methods.
3. Record what the vendor does with each of our five language codes, **including
   which fallback it uses when the code is unsupported.** This is data, not a
   footnote — `en-SG` falls back on seven of eight vendors.
4. Add a `tests/vendors/test_<name>.py` marked `@pytest.mark.vendor` with one
   condition, so CI skips it and a human can run it with a key.

Please include, in the PR, the date you verified the SIP and concurrency facts.
They go stale fast.

## What we will not merge

- A web UI, a SaaS layer, or a test-authoring interface. This is a benchmark:
  a library, a CLI, a dataset and a static leaderboard.
- An agent framework dependency. The caller is a deterministic state machine; see
  `docs/stack.md` for why.
- Anything that makes the vendor's own transcript the scoring source.
- Codec auto-negotiation. We pin PCMU deliberately.
- Results without seeds, hashes and the mode stamp.

## Adding a language

Harder than it looks, and genuinely valuable. You need:

- Utterances with typed entity labels and a normalised form
- A referee ASR choice with a WER figure measured on **telephony-band spontaneous
  speech**, not FLEURS read speech
- A normalisation policy (NFC, script handling, spoken-vs-digit numerals) —
  and for code-switched languages you are defining new practice, so document it
- A native speaker who has listened to the generated TTS and signed off

See `docs/referees.md` before starting.

## Style

- Python 3.11+, `ruff` and `mypy` clean.
- Dependencies are expensive. `vqlab` uses stdlib `wave` rather than `soundfile` on
  purpose — justify anything new.
- Every generated artifact carries its seed and a SHA-256.
- Manifest rows are self-describing: full condition parameters inlined, so a row
  never depends on a config file the reader may not have.
