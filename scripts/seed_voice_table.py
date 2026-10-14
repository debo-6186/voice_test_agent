#!/usr/bin/env python
"""Introspects a vendor's real, already-running agent and writes its native
voice into vendors/voices.yaml - seeds the voice-mapping table from ground
truth instead of a guess. Read-only against the live API (a GET, no create/
delete), idempotent - re-running just refreshes the value and date.

python scripts/seed_voice_table.py --vendor retell
python scripts/seed_voice_table.py --vendor all
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import yaml  # noqa: E402

from vendors.create import retell_get_agent, telnyx_get_agent  # noqa: E402
from vendors.loader import load_vendor_config  # noqa: E402

VOICES_PATH = os.path.join(REPO_ROOT, "vendors", "voices.yaml")

# Only vendors with (a) a live API key and (b) a native voice worth
# introspecting (not ElevenLabs, which reads config/utterances.yaml
# directly instead - see vendors/create.py's module docstring). Telnyx IS
# seedable despite also supporting BYOK ElevenLabs voices - its native
# voice is the current default until BYOK's api_key_ref requirement is
# confirmed (see vendors/create.py's telnyx_create_agent() docstring).
SEEDABLE = {
    "retell": lambda: retell_get_agent(os.environ["RETELL_AGENT_ID"]),
    "telnyx": lambda: telnyx_get_agent(load_vendor_config("telnyx")["dial"]["assistant_id"]),
}


def _field_at_path(obj: dict, path: str):
    cur = obj
    for part in path.split("."):
        cur = cur[part]
    return cur


def seed_one(vendor: str) -> None:
    if vendor not in SEEDABLE:
        print(f"{vendor}: not seedable from a live agent - skipping "
              f"(see vendors/create.py's module docstring for why)")
        return

    vcfg = load_vendor_config(vendor)
    voice_field_path = vcfg["create"]["voice_field_path"]
    agent = SEEDABLE[vendor]()
    voice_id = _field_at_path(agent, voice_field_path)

    # Confirm the row exists (fails loudly on a typo/missing row) without
    # needing to parse+re-serialize the whole file - yaml.safe_dump would
    # silently drop every hand-written comment in vendors/voices.yaml
    # (including the header explaining the two-namespace confusion risk),
    # so this patches only the three affected lines in place instead.
    table = yaml.safe_load(open(VOICES_PATH, encoding="utf-8"))
    if vendor not in table["voice_keys"]["default"]:
        raise KeyError(f"no vendors/voices.yaml row for vendor={vendor!r}")

    today = datetime.date.today().isoformat()
    text = open(VOICES_PATH, encoding="utf-8").read()

    def _patch_vendor_block(text: str, vendor: str) -> str:
        # Matches from "    <vendor>:" up to (not including) the next
        # same-indent "    <key>:" line or end of file.
        pattern = re.compile(
            rf"(^    {re.escape(vendor)}:\n)(.*?)(?=^    \S|\Z)", re.MULTILINE | re.DOTALL,
        )
        m = pattern.search(text)
        if not m:
            raise KeyError(f"vendors/voices.yaml: could not locate the {vendor}: block")
        block = m.group(2)
        block = re.sub(r"(voice_id:).*", lambda _: f"voice_id: {voice_id!r}", block, count=1)
        block = re.sub(r"(source:)\s*\S+", lambda _: "source: live_introspection", block, count=1)
        if "introspected_date:" in block:
            block = re.sub(r"(introspected_date:).*",
                          lambda _: f'introspected_date: "{today}"', block, count=1)
        else:
            block = block.rstrip("\n") + f'\n      introspected_date: "{today}"\n'
        return text[:m.start(2)] + block + text[m.end(2):]

    text = _patch_vendor_block(text, vendor)
    open(VOICES_PATH, "w", encoding="utf-8").write(text)
    print(f"{vendor}: seeded voice_id={voice_id!r} (from {voice_field_path}) "
         f"into vendors/voices.yaml")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--vendor", required=True, choices=sorted(SEEDABLE) + ["all"])
    args = p.parse_args()

    vendors = sorted(SEEDABLE) if args.vendor == "all" else [args.vendor]
    for v in vendors:
        seed_one(v)


if __name__ == "__main__":
    main()
