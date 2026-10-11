#!/usr/bin/env python
"""Proves the custom config_site.h actually raised PJSUA_MAX_PLAYERS above
its default of 32, by creating more conference ports than that with no call
involved. This is the concrete way to confirm the raised limits rather than
discovering you're still stuck at 32 once real calls are running
(docs/sip-harness.md).
"""
from __future__ import annotations

import os
import struct
import sys
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pjsua2 as pj  # noqa: E402

from sip_probe.account import make_endpoint  # noqa: E402

N_PLAYERS = 40  # comfortably above the stock PJSUA_MAX_PLAYERS default of 32


def _make_silent_wav(path: str, seconds: float = 1.0, sr: int = 8000) -> None:
    n = int(seconds * sr)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(struct.pack(f"<{n}h", *([0] * n)))


def main() -> None:
    wav_path = "/tmp/_capacity_check_silence.wav"
    _make_silent_wav(wav_path)

    ep = make_endpoint()
    ep.libRegisterThread("capacity-check")

    players = []
    try:
        for _ in range(N_PLAYERS):
            player = pj.AudioMediaPlayer()
            player.createPlayer(wav_path, pj.PJMEDIA_FILE_NO_LOOP)
            players.append(player)
        print(
            f"OK: created {len(players)} player conference ports "
            f"(stock PJSUA_MAX_PLAYERS default is 32 - config_site.h took effect)"
        )
    except pj.Error as e:
        print(f"FAILED at player #{len(players) + 1}: {e}")
        sys.exit(1)
    finally:
        # Tear down one port at a time with an event pump in between - the
        # conference bridge removes ports via its own worker thread
        # (conf_thread.c), and destroying all 40 wrappers in one burst
        # (e.g. players.clear()) races that queue and crashes the process.
        while players:
            player = players.pop()
            del player
            ep.libHandleEvents(10)
        ep.libDestroy()


if __name__ == "__main__":
    main()
