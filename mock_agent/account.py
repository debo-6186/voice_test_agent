"""The mock agent's SIP account - auto-answers inbound calls (same shape as
sip_probe/local_echo.py's EchoAccount). Does no blocking work itself; the
server's top-level loop detects media-active calls and drives the actual
conversation (mock_agent/call.py:run_conversation).
"""
from __future__ import annotations

import pjsua2 as pj

from mock_agent.call import MockAgentCall


class MockAgentAccount(pj.Account):
    def __init__(self):
        super().__init__()
        self.calls: list[MockAgentCall] = []

    def onRegState(self, prm):  # noqa: N802 - PJSUA2 callback naming
        pass

    def onIncomingCall(self, prm):  # noqa: N802 - PJSUA2 callback naming
        call = MockAgentCall(self, prm.callId)
        self.calls.append(call)
        answer_prm = pj.CallOpParam()
        answer_prm.statusCode = pj.PJSIP_SC_OK
        call.answer(answer_prm)


def make_account(ep: pj.Endpoint, local_uri: str = "sip:mockagent@127.0.0.1") -> MockAgentAccount:
    acc_cfg = pj.AccountConfig()
    acc_cfg.idUri = local_uri
    acc_cfg.regConfig.registerOnAdd = False
    acc = MockAgentAccount()
    acc.create(acc_cfg)
    return acc
