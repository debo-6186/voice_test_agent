"""Phase 5: run the mock agent - a local SIP endpoint that answers, listens,
transcribes, injects configurable errors, and speaks back, one call at a
time (process sharding / 50-concurrent growth is explicitly deferred - see
the Phase 5 plan). This is `polycall demo`'s backend per docs/cli.md.
"""
from __future__ import annotations

import json
import os
import time

import numpy as np
import pjsua2 as pj
import yaml

from sip_probe.account import make_endpoint
from mock_agent.account import make_account

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_mock_agent(port: int, scenario_id: str, lang: str, tts: str = "espeak",
                   asr_error_rate: float = 0.0, repair_threshold: float = 0.0,
                   silent_error_rate: float = 0.0, max_turn_listen_s: float = 15.0,
                   asr_model: str = "tiny", seed: int = 0,
                   scratch_dir: str | None = None,
                   log_path: str | None = None, max_calls: int | None = None) -> None:
    from mock_agent.call import run_conversation

    ucfg = yaml.safe_load(open(os.path.join(REPO_ROOT, "config", "utterances.yaml"), encoding="utf-8"))
    lang_meta = ucfg["languages"][lang]

    scratch_dir = scratch_dir or os.path.join(REPO_ROOT, "out", "mock_agent_scratch")
    os.makedirs(scratch_dir, exist_ok=True)
    rng = np.random.default_rng(seed)

    ep = make_endpoint(port=port)
    ep.libRegisterThread("mock-agent-main")
    acc = make_account(ep)

    print(f"mock agent listening on UDP {port}, scenario={scenario_id} lang={lang} "
         f"tts={tts} asr_error_rate={asr_error_rate} repair_threshold={repair_threshold} "
         f"silent_error_rate={silent_error_rate}")

    served = []
    n_calls = 0
    try:
        while max_calls is None or n_calls < max_calls:
            # Wait for the next call to arrive and go media-active.
            call = None
            while call is None:
                ep.libHandleEvents(100)
                time.sleep(0.01)
                for c in acc.calls:
                    if c.media_active and c not in served:
                        call = c
                        break
            served.append(call)
            n_calls += 1

            turn_log = run_conversation(
                ep, call, scenario_id, lang, lang_meta, tts, rng,
                asr_error_rate, repair_threshold, silent_error_rate,
                scratch_dir=scratch_dir, max_turn_listen_s=max_turn_listen_s,
                asr_model=asr_model,
            )
            print(f"call {n_calls} finished: {json.dumps(turn_log, ensure_ascii=False)}")
            if log_path:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"call": n_calls, "turns": turn_log}, ensure_ascii=False) + "\n")
    except KeyboardInterrupt:
        pass
    finally:
        acc.calls.clear()
        del acc
        ep.libDestroy()
