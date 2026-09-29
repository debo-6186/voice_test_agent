"""Zero-spend test fixture: a local SIP endpoint that auto-answers and plays
a known reference WAV to the caller. Lets the whole dial/negotiate/play/
record/hang-up path in probe.py be verified end-to-end against something we
control, before any vendor account is involved (KICKOFF.md item 8).
"""
from __future__ import annotations

import time

import pjsua2 as pj

from .account import make_endpoint


class EchoCall(pj.Call):
    def __init__(self, acc: pj.Account, call_id: int, reference_wav: str):
        super().__init__(acc, call_id)
        self.reference_wav = reference_wav
        self.player: pj.AudioMediaPlayer | None = None

    def onCallState(self, prm):  # noqa: N802 - PJSUA2 callback naming
        ci = self.getInfo()
        if ci.state == pj.PJSIP_INV_STATE_DISCONNECTED:
            self.player = None

    def onCallMediaState(self, prm):  # noqa: N802 - PJSUA2 callback naming
        ci = self.getInfo()
        for mi in ci.media:
            if mi.type == pj.PJMEDIA_TYPE_AUDIO and mi.status == pj.PJSUA_CALL_MEDIA_ACTIVE:
                if self.player is None:
                    aud_med = self.getAudioMedia(mi.index)
                    self.player = pj.AudioMediaPlayer()
                    self.player.createPlayer(self.reference_wav, pj.PJMEDIA_FILE_NO_LOOP)
                    self.player.startTransmit(aud_med)


class EchoAccount(pj.Account):
    def __init__(self, reference_wav: str):
        super().__init__()
        self.reference_wav = reference_wav
        self.calls: list[EchoCall] = []

    def onRegState(self, prm):  # noqa: N802 - PJSUA2 callback naming
        pass

    def onIncomingCall(self, prm):  # noqa: N802 - PJSUA2 callback naming
        call = EchoCall(self, prm.callId, self.reference_wav)
        self.calls.append(call)
        answer_prm = pj.CallOpParam()
        answer_prm.statusCode = pj.PJSIP_SC_OK
        call.answer(answer_prm)


def run_echo_server(port: int, reference_wav: str) -> None:
    """Blocks, serving auto-answered calls, until interrupted."""
    ep = make_endpoint(port=port)
    ep.libRegisterThread("local-echo-main")
    acc_cfg = pj.AccountConfig()
    acc_cfg.idUri = "sip:echo@127.0.0.1"
    acc_cfg.regConfig.registerOnAdd = False
    acc = EchoAccount(reference_wav)
    acc.create(acc_cfg)

    print(f"local echo UAS listening on UDP {port}, playing {reference_wav} to any caller")
    try:
        while True:
            ep.libHandleEvents(100)
            time.sleep(0.01)
    except KeyboardInterrupt:
        pass
    finally:
        # Wrapper objects must be destroyed while the library is still
        # alive, same reasoning as probe.py - acc.calls holds every
        # EchoCall, and acc itself, so drop both before libDestroy().
        acc.calls.clear()
        del acc
        ep.libDestroy()
