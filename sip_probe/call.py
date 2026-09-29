"""Outgoing-call lifecycle: dial, wire player -> call -> recorder on answer,
finalize the recording on hang-up.

Deliberately avoids the five traps in docs/sip-harness.md:
  1. setNullDev() lives in account.make_endpoint(), called before any call.
  2. self.player / self.recorder are held for the call's full lifetime -
     GC'ing them while PJSIP still holds the port is a segfault.
  3. (thread registration is the caller's job, once per long-lived thread)
  4. PJMEDIA_FILE_NO_LOOP on the player; the recorder is explicitly deleted
     on disconnect, since stopTransmit() alone does not finalize the WAV.
  5. Built against pjproject 2.16+ per the project's Dockerfile.
"""
from __future__ import annotations

from typing import Callable, Optional

import pjsua2 as pj


class ProbeCall(pj.Call):
    def __init__(
        self,
        acc: pj.Account,
        wav_in: str,
        wav_out: str,
        on_media_active: Optional[Callable[["ProbeCall", int], None]] = None,
        on_disconnect: Optional[Callable[["ProbeCall"], None]] = None,
        call_id: int = pj.PJSUA_INVALID_ID,
    ):
        super().__init__(acc, call_id)
        self.wav_in = wav_in
        self.wav_out = wav_out
        self.on_media_active = on_media_active
        self.on_disconnect = on_disconnect

        self.player: Optional[pj.AudioMediaPlayer] = None
        self.recorder: Optional[pj.AudioMediaRecorder] = None
        self.last_state: Optional[str] = None
        self.disconnect_reason: Optional[str] = None

    def onCallState(self, prm):  # noqa: N802 - PJSUA2 callback naming
        ci = self.getInfo()
        self.last_state = ci.stateText
        if ci.state == pj.PJSIP_INV_STATE_DISCONNECTED:
            self.disconnect_reason = ci.lastReason
            if self.recorder is not None:
                # stopTransmit() does NOT close/finalize the WAV - deleting
                # the recorder object does.
                self.recorder = None
            if self.on_disconnect:
                self.on_disconnect(self)

    def onCallMediaState(self, prm):  # noqa: N802 - PJSUA2 callback naming
        ci = self.getInfo()
        for mi in ci.media:
            if mi.type == pj.PJMEDIA_TYPE_AUDIO and mi.status == pj.PJSUA_CALL_MEDIA_ACTIVE:
                aud_med = self.getAudioMedia(mi.index)

                if self.player is None:
                    self.player = pj.AudioMediaPlayer()
                    self.player.createPlayer(self.wav_in, pj.PJMEDIA_FILE_NO_LOOP)
                    self.player.startTransmit(aud_med)

                if self.recorder is None:
                    self.recorder = pj.AudioMediaRecorder()
                    self.recorder.createRecorder(self.wav_out)
                    aud_med.startTransmit(self.recorder)

                if self.on_media_active:
                    self.on_media_active(self, mi.index)
