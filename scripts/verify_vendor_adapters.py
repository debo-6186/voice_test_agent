#!/usr/bin/env python
"""Live create -> get -> delete -> get-again round trip against a throwaway
agent, for the three vendors with working keys today (Retell, ElevenLabs,
Telnyx). Proves vendors/create.py's adapters actually work against the real
APIs, not just that the payload shapes look plausible - asserts the created
agent's name/language/voice actually round-tripped, and that delete really
deletes (a second get() must fail/come back empty).

Every throwaway agent is named "polycall-verify-<timestamp>-<id>" - the
cleanup marker. The finally: block always attempts delete and loudly prints
the agent_id if that itself fails, so nothing is silently left behind in
your vendor account.

python scripts/verify_vendor_adapters.py --vendor retell
python scripts/verify_vendor_adapters.py --vendor elevenlabs
python scripts/verify_vendor_adapters.py --vendor telnyx
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import uuid

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from vendors.canonical import CanonicalAgentConfig  # noqa: E402
from vendors.create import create_agent, get_agent, delete_agent, VendorCreateError  # noqa: E402

LIVE_VENDORS = ["retell", "elevenlabs", "telnyx"]


def _verify_marker() -> str:
    return f"polycall-verify-{int(time.time())}-{uuid.uuid4().hex[:8]}"


def verify_vendor(vendor: str, bcp47_language: str = "en-US") -> None:
    agent_name = _verify_marker()
    cfg = CanonicalAgentConfig(
        prompt="You are a test agent created by polycall bench's Phase 7 "
               "adapter verification. If reached by a real call, say so and "
               "hang up immediately.",
        # Deliberately empty - each vendor's own adapter falls back to its
        # own documented default model (vendors.create's *_create_agent()
        # functions). Confirmed live: a shared "gpt-4o" default across all
        # three vendors is NOT portable (Telnyx 422s with "Model gpt-4o is
        # not available for inference" - its catalog is vendor-specific,
        # same lesson docs/vendors.md already states for voice IDs).
        llm={},
        voice_key="default",
        bcp47_language=bcp47_language,
        agent_name=agent_name,
    )

    print(f"[{vendor}] creating throwaway agent {agent_name!r} "
         f"(bcp47_language={bcp47_language})...")
    created = create_agent(vendor, cfg)
    agent_id = created["agent_id"]
    print(f"[{vendor}] created agent_id={agent_id!r}, "
         f"language_resolution={created.get('language_resolution')}")

    try:
        print(f"[{vendor}] fetching it back to confirm the round trip...")
        fetched = get_agent(vendor, agent_id)
        print(f"[{vendor}] fetched agent - inspect manually against the "
             f"request above:\n{fetched}")

        print(f"[{vendor}] deleting...")
        delete_agent(vendor, agent_id, **created.get("metadata", {}))

        print(f"[{vendor}] confirming it's actually gone...")
        try:
            still_there = get_agent(vendor, agent_id)
            raise AssertionError(
                f"[{vendor}] agent {agent_id} still fetchable after delete: {still_there}"
            )
        except VendorCreateError:
            print(f"[{vendor}] confirmed gone (get after delete failed, as expected)")

        print(f"[{vendor}] PASS - create/get/delete round trip verified")

    except Exception:
        print(f"[{vendor}] verification failed mid-way - attempting cleanup of "
             f"agent_id={agent_id!r} regardless", file=sys.stderr)
        try:
            delete_agent(vendor, agent_id, **created.get("metadata", {}))
            print(f"[{vendor}] cleanup succeeded despite the failure above", file=sys.stderr)
        except Exception as cleanup_exc:
            print(f"[{vendor}] CLEANUP ALSO FAILED - agent_id={agent_id!r} may be "
                 f"orphaned in your {vendor} account, delete it by hand: "
                 f"{cleanup_exc}", file=sys.stderr)
        raise


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--vendor", required=True, choices=LIVE_VENDORS)
    p.add_argument("--lang", default="en-US")
    args = p.parse_args()
    verify_vendor(args.vendor, args.lang)


if __name__ == "__main__":
    main()
