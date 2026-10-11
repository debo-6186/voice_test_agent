# Kickoff prompt for Claude Code

Open a terminal in this directory and run `claude`. It loads `CLAUDE.md`
automatically. Press **Shift+Tab** for plan mode and paste the prompt below.

---

## Prompt — Phase 1 (listening demo) and Phase 2 (one-shot SIP probe)

```
Read PLAN.md (especially section 11, the build order), then docs/stack.md,
docs/sip-harness.md and docs/vendors.md. Then read the existing code in vqlab/
and config/.

Before proposing anything, verify the existing degradation engine works:
  python -c "from vqlab.corpus import build; print(build('config/utterances.yaml','config/conditions.yaml','/tmp/out'))"

Then produce an implementation plan for Phase 1 and Phase 2 ONLY. Do not plan
later phases.

PHASE 1 — the listening demo. A static page, no backend, no accounts, no cost,
deployed to GitHub Pages. A visitor drops in a WAV or records from their mic,
and hears their own voice down the café SNR ladder with the ASR transcript
under each rung.

For this I want:
1. The file layout and build step. Critically: how config/conditions.yaml becomes
   the single source of truth for both the Python engine and the browser, so the
   two can never disagree about what "cafe at 10 dB" means.
2. How the ambience beds get from the Python generator into the page as
   hash-pinned assets, and how you keep the page small.
3. The WebAudio graph for each rung, and the G.711 mu-law implementation.
4. How whisper-tiny runs via transformers.js, what happens without WebGPU, and
   how the page stays responsive while it works.
5. The one screen of UI. Not a settings panel — the flow that makes someone
   understand in ten seconds.

PHASE 2 — the one-shot SIP probe. The smallest possible real call: dial a vendor's
SIP URI, play one WAV, record what comes back, hang up. No state machine, no
intent classification, no scenarios.

For this I want:
6. The PJSUA2 call lifecycle with the specific calls involved, including where
   setNullDev(), PJMEDIA_FILE_NO_LOOP and recorder finalisation go.
7. How PCMU-only is pinned in the SDP, and how you verify what was actually
   negotiated rather than assuming.
8. How the probe is tested with zero vendor spend — what can be checked against
   a local SIP endpoint before any credentials exist.
9. The Docker build, and how someone confirms their PJSIP has the raised
   conference-port limits rather than discovering it at 32 concurrent calls.

Finally:
10. An ordered task list with rough effort, and the three places you think this
    is most likely to go wrong.

Non-negotiable: PCMU-only SDP. Browser demo shares parameters with the Python
engine, never re-typed. Nothing uploaded from the demo page. Everything seeded
and hash-pinned.

Ask me questions before writing the plan if anything is ambiguous or contradictory.
```

---

## Then, when Phase 2 works

```
Read PLAN.md section 11, Phase 3. Implement the hallucination probe: play the
ctl_noise_only condition (30 s of cafe ambience with zero speech) to a vendor,
and count the turns the agent generates using VAD on the inbound leg. No
transcription, no scoring.

Output a per-vendor table and a per-call timeline suitable for cutting into a
short video. Keep it under 200 lines on top of the Phase 2 probe.
```

---

## Notes

- `/init` is not needed — `CLAUDE.md` exists and is tuned.
- `git init && git add -A && git commit` before Claude Code writes anything, so
  you can diff and revert.
- File the **Telnyx Level 2 verification** and **Bland SIP entitlement** requests
  on day one. Both are human reviews and will otherwise block Phase 7.
