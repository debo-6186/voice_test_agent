"""PJSUA2 endpoint and account setup - PCMU-only SDP.

No SIP registration is needed to dial a bare URI directly (most vendors
accept a direct SIP URI with no registration - see docs/vendors.md). This
module also restricts codec priorities so only PCMU is offered in the SDP,
per docs/sip-harness.md's "offer PCMU only" rule - free negotiation would
mean the benchmark silently measures codec choice instead of agent quality.
"""
from __future__ import annotations

import pjsua2 as pj


def restrict_to_pcmu(ep: pj.Endpoint) -> None:
    """Zero out every codec's priority except PCMU, so PJSIP offers only
    PCMU in the SDP regardless of what else the build supports."""
    for codec in ep.codecEnum2():
        priority = 255 if codec.codecId.startswith("PCMU") else 0
        ep.codecSetPriority(codec.codecId, priority)


def make_endpoint(port: int = 0, stun_server: str | None = None) -> pj.Endpoint:
    """Create and start a PJSUA2 endpoint with no sound device.

    `port` is the local UDP SIP port; 0 lets the OS pick a free one (fine
    for a same-host test like the local echo UAS), or pass a fixed port for
    something that needs to be dialled into from outside, or reached through
    a NAT/Docker port publish.

    `stun_server` (host:port) makes PJSIP discover and advertise our real
    public IP/port in the SDP instead of a local interface address - without
    it, a container's internal bridge IP (e.g. 172.17.0.3) ends up in the
    SDP `c=` line, which is unreachable from a real vendor's media server:
    SIP signaling still completes (responses use `rport`, which is
    self-correcting), but RTP has no such correction, so audio never
    arrives and a call can show CONFIRMED while recording pure silence.
    """
    ep = pj.Endpoint()
    ep.libCreate()

    ep_cfg = pj.EpConfig()
    ep_cfg.uaConfig.maxCalls = 8
    ep_cfg.medConfig.noVad = True
    if stun_server:
        ep_cfg.uaConfig.stunServer = pj.StringVector()
        ep_cfg.uaConfig.stunServer.append(stun_server)
        ep_cfg.uaConfig.stunIgnoreFailure = False
    ep.libInit(ep_cfg)

    transport_cfg = pj.TransportConfig()
    transport_cfg.port = port
    ep.transportCreate(pj.PJSIP_TRANSPORT_UDP, transport_cfg)

    ep.libStart()
    # With no sound device and no master port, nothing pumps the conference
    # bridge, async ops never complete, and conference slots leak until the
    # process dies (docs/sip-harness.md trap #1).
    ep.audDevManager().setNullDev()
    restrict_to_pcmu(ep)
    return ep


class SimpleAccount(pj.Account):
    """A minimal account - no registration, just enough identity to
    originate a call to (or receive a call at) a bare SIP URI."""

    def onRegState(self, prm):  # noqa: N802 - PJSUA2 callback naming
        pass


def make_account(ep: pj.Endpoint, local_uri: str = "sip:probe@127.0.0.1",
                 auth_username: str | None = None, auth_password: str | None = None) -> SimpleAccount:
    """auth_username/auth_password let the account answer a 401/407 digest
    challenge on the INVITE itself (no REGISTER involved) - needed for a
    LiveKit SIP trunk gated on username/password rather than allowed_addresses
    (the latter is a support-enabled feature per-project, not on by default)."""
    acc_cfg = pj.AccountConfig()
    acc_cfg.idUri = local_uri
    acc_cfg.regConfig.registerOnAdd = False
    if auth_username and auth_password:
        cred = pj.AuthCredInfo("digest", "*", auth_username, 0, auth_password)
        acc_cfg.sipConfig.authCreds.append(cred)
    acc = SimpleAccount()
    acc.create(acc_cfg)
    return acc
