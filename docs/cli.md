# CLI surface

The public interface. Build to this — it is what the README, QUICKSTART and
Docker image assume.

```
polycall doctor                    # what is installed, what keys resolve, what is missing
polycall list-conditions           # the 15 conditions with their full parameters
polycall list-vendors              # supported vendors, reachability, your key status

polycall degrade <audio>           # CPU, no accounts — the front door
    --ladder cafe|street|car|office|tv
    --condition <id>               # a single condition instead of a ladder
    --out ./out --seed 4471

polycall corpus build              # generate a session corpus
    --config config/utterances.yaml --conditions config/conditions.yaml
    --tts espeak|elevenlabs --languages en-US,id-ID --out ./corpus
polycall corpus verify <dir>       # re-hash, recompute delivered SNR

polycall demo                      # mock agent, zero accounts, zero cost
    --asr-error-rate 0.08 --silent-error-rate 0.03 --repair-threshold 0.4
    --conditions all

polycall call                      # Mode A: test YOUR agent
    --vendor retell --agent-id agent_abc123
    --scenario booking_3field --lang en-US
    --ladder cafe | --condition A_cafe_snr10
    --repeats 3 --concurrency 4
    --dry-run --yes --max-spend 25

polycall bench                     # many vendors / languages / conditions
    --vendors retell,vapi --agents retell=a,vapi=b
    --languages en-US,id-ID --ladder cafe --repeats 3
    --standard-agent --spec specs/booking_v3.yaml    # Mode B
    --all-conditions --concurrency 20 --force-concurrency

polycall score  --run <dir>        # referee ASR, entities, silent errors, turn metrics
polycall report --run <dir> --open # HTML: curves, RDP with CI, per-condition detail
polycall verify --against published/v1
```

## Rules the CLI must enforce

- **Mode is stamped on every artifact.** `mode: A` or `mode: B` in the run manifest,
  in every result row, and in the report header. `report` refuses to emit a
  leaderboard-comparable table from a Mode A run and says why.
- **Cost confirmation before dialling.** Print the matrix, the call count, the
  estimated spend, and wait. `--yes` to skip; `--max-spend` / `POLYCALL_MAX_SPEND`
  aborts mid-run when projected spend exceeds it.
- **Concurrency defaults to 4.** Read the vendor's published limit from
  `vendors/*.yaml` and refuse to exceed it without `--force-concurrency`.
- **Exponential backoff on 429 is always on** and cannot be disabled — four vendors
  publish no rate limits, so you will hit undocumented ceilings.
- **`doctor` runs before anything expensive** and fails loudly on: missing ffmpeg,
  missing PJSUA2, PJSIP built without the raised conference-port limits, a codec the
  ladder needs that ffmpeg cannot encode, key material found in committed config.
- **`--dry-run` places no calls** but walks the full matrix and prints the plan.
- **Results below the significance floor print `n.s.`**, never a rank.
- **Every run directory is self-contained**: config snapshot, seeds, corpus hashes,
  agent spec, per-call tapes, manifest, and the discarded turns with reasons.

## Exit codes

| | |
|---|---|
| 0 | success |
| 2 | environment problem — see `doctor` |
| 3 | cost ceiling would be exceeded; nothing dialled |
| 4 | vendor unreachable or credentials rejected |
| 5 | run completed but results fell below the significance floor |
