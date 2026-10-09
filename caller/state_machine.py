"""Canonical turn matching (PLAN.md §6): decides the caller's next action
from a classified intent - select the matching pre-rendered turn when the
agent follows the expected flow, adaptive fallback when nothing matches.
Pure, PJSIP-free, unit-testable on its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from caller.intent import Classification

FIELD_BY_INTENT = {"ask_name": "person_name", "ask_phone": "phone", "ask_date": "date"}


@dataclass
class Action:
    kind: str                          # "play_canonical" | "play_utility" | "play_adaptive" | "wait"
    turn: dict | None = None            # the scenario turn dict, for play_canonical
    utility_key: str | None = None      # caller/utility.py key, for play_utility
    divergence: bool = False
    divergence_reason: str | None = None


@dataclass
class CallerState:
    turns: list[dict]                          # this scenario's turns, config/scenarios.yaml order
    collected: dict = field(default_factory=dict)
    last_played_turn: dict | None = None

    def __post_init__(self):
        for t in self.turns:
            self.collected.setdefault(t["field"], False)

    def remaining_fields(self) -> list[str]:
        return [t["field"] for t in self.turns if not self.collected[t["field"]]]

    def turn_for_field(self, field_name: str) -> dict | None:
        for t in self.turns:
            if t["field"] == field_name:
                return t
        return None

    def next_action(self, classification: Classification) -> Action:
        intent = classification.intent

        if classification.needs_adaptive:
            return Action("play_adaptive", divergence=True,
                         divergence_reason=f"tier3_low_confidence({classification.confidence:.2f})")

        if intent == "repair":
            if self.last_played_turn is None:
                return Action("play_adaptive", divergence=True,
                             divergence_reason="repair_before_any_turn")
            return Action("play_canonical", turn=self.last_played_turn)

        if intent == "confirm":
            return Action("play_utility", utility_key="confirm_ack")

        if intent == "closing":
            # Closing is irreversible - it ends the call. Laya ships
            # uncalibrated and can be confidently WRONG, not just
            # unconfident (verified live: 0.854 on a garbled phrase that
            # was actually asking for the date, well above this classifier's
            # 0.75 tier-3 threshold). Trusting "closing" alone while fields
            # are still outstanding would silently abandon the scenario -
            # route it through adaptive fallback instead, so context (or a
            # human reading the divergence log) gets a say before the call
            # ends on an unverified guess.
            if self.remaining_fields():
                return Action("play_adaptive", divergence=True,
                             divergence_reason="closing_with_fields_remaining")
            return Action("wait")

        field_name = FIELD_BY_INTENT.get(intent)
        if field_name is None:
            return Action("play_adaptive", divergence=True,
                         divergence_reason=f"unmapped_intent:{intent}")
        if self.collected.get(field_name):
            return Action("play_adaptive", divergence=True,
                         divergence_reason=f"field_already_collected:{field_name}")

        turn = self.turn_for_field(field_name)
        if turn is None:
            return Action("play_adaptive", divergence=True,
                         divergence_reason=f"no_turn_for_field:{field_name}")

        self.collected[field_name] = True
        self.last_played_turn = turn
        return Action("play_canonical", turn=turn)
