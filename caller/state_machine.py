"""Canonical turn matching (PLAN.md §6): decides the caller's next action
from a classified intent - select the matching pre-rendered turn when the
agent follows the expected flow, adaptive fallback when nothing matches.
Pure, PJSIP-free, unit-testable on its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from caller.intent import Classification, looks_like_confirmation

FIELD_BY_INTENT = {"ask_name": "person_name", "ask_phone": "phone", "ask_date": "date"}


@dataclass
class Action:
    kind: str                          # "play_canonical" | "play_utility" | "play_adaptive" | "wait"
    turn: dict | None = None            # the scenario turn dict, for play_canonical
    utility_key: str | None = None      # caller/utility.py key, for play_utility
    divergence: bool = False
    divergence_reason: str | None = None
    field: str | None = None            # which scenario field this turn addressed - for
                                        # play_canonical, referee scoring (Phase 8) needs to
                                        # know which entity a turn was about; also set on a
                                        # play_adaptive Action when it's restating an already-
                                        # collected field (see CallerState.next_action's
                                        # field_already_collected handling) so caller/call.py's
                                        # _react() knows which known value to hand the LLM


@dataclass
class CallerState:
    turns: list[dict]                          # this scenario's turns, config/scenarios.yaml order
    collected: dict = field(default_factory=dict)
    last_played_turn: dict | None = None
    # {field_name: how many times we've replayed its canonical turn after
    # it was already marked collected} - caps the "just replay the exact
    # same audio again" response (see next_action's field_already_collected
    # handling) at one safe retry before switching strategy.
    reask_count: dict = field(default_factory=dict)
    # {field_name: how many times the AGENT has asked us to repeat that
    # field's value} - caps the "repair" response (see next_action's repair
    # handling) at one replay before giving up on that field entirely.
    repair_count: dict = field(default_factory=dict)

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

    def remaining_field_values(self, lang: str) -> dict[str, str]:
        """{field_name: the scenario's own canonical scripted sentence for
        that field, t["text"][lang]} for fields not yet collected - gives
        caller/adaptive.py's LLM fallback a natural-language REFERENCE to
        draw the correct fact from if it volunteers that field, instead of
        inventing one. Confirmed live this is a real bug, not theoretical:
        told only the field NAME "date" with no reference at all, Gemini
        fabricated "Friday, the 24th" when the scenario's actual scripted
        date was October 15th - the vendor agent then correctly heard and
        read back exactly what our own caller incorrectly said, which
        looked like a vendor failure in the score but was entirely ours.

        Deliberately the canonical TEXT, not entities[lang]'s raw value:
        the date field's raw value is machine-formatted ("--10-15") for
        scoring comparisons, not something a caller would ever say out
        loud, while every turn's own scripted text is already guaranteed
        both correct and naturally phrased - it's the literal sentence
        this caller speaks when playing that turn as a canonical reply."""
        values = {}
        for t in self.turns:
            if not self.collected[t["field"]] and lang in t.get("text", {}):
                values[t["field"]] = t["text"][lang]
        return values

    def next_action(self, classification: Classification, agent_text: str = "",
                    confirmed_by_agent: list[str] | None = None) -> Action:
        intent = classification.intent

        # Checked BEFORE needs_adaptive, not after: "closing" with nothing
        # left to collect is safe to act on regardless of confidence - the
        # one thing this confidence gate exists to protect against (ending
        # the call while a field is still outstanding) cannot happen here,
        # since nothing is outstanding. Confirmed live on hi-IN: Laya's
        # Hindi confidence on unambiguous vendor goodbye/closing lines sat
        # well under tier3_threshold almost every time (0.33-0.67 across a
        # 16-turn call), so the needs_adaptive check below used to catch
        # every one of them first and send the call to adaptive fallback
        # instead - which, with nothing left to ask about, invented a new
        # off-script question (e.g. appointment time, never a real field)
        # rather than ending the call. That gave the vendor something new
        # to respond to and dragged an already-complete call out by 10+
        # extra turns. The "closing_with_fields_remaining" guardrail further
        # down still applies unchanged for the case this IS meant to catch -
        # fields genuinely still outstanding.
        if intent == "closing" and not self.remaining_fields():
            return Action("wait")

        # Also checked BEFORE needs_adaptive, same reasoning as "closing"
        # above: the agent asking us to repeat is safe to act on regardless
        # of Laya's confidence, because the action is just "say the exact
        # same known-correct WAV again" - there is no risk of inventing
        # content or ending the call wrongly. Confirmed live: under real
        # acoustic noise, the vendor's own "I didn't catch that, can you
        # repeat?" sometimes classified at LOW confidence (noisy audio
        # confuses Laya same as it confuses the vendor), which used to fall
        # through to needs_adaptive below - generating a FRESH adaptive
        # reply through synth_and_degrade(), which applies the SAME noise
        # condition to the new audio. That doesn't fix anything: the vendor
        # mishearing it once under this session's noise has no reason to
        # hear a freshly-synthesized version any better, and a new LLM
        # reply varies in wording each time instead of reliably saying the
        # one thing already known to be correct. Replaying the identical
        # canonical WAV is both cheaper (no LLM call) and the only sensible
        # response to "I couldn't hear you" - the content was never wrong,
        # the delivery was.
        #
        # Capped at 2 total repair replays per field (user's explicit
        # choice): the original give counts as attempt 1, each repeat
        # requested afterward is one more attempt - a THIRD request to
        # repeat (i.e. the vendor still hasn't picked it up after 2 replays)
        # means repeating the identical audio a third time isn't going to
        # succeed either, so give up on this field rather than loop forever.
        # Deliberately does NOT mark the field collected when giving up -
        # "considered as not successful for that field" needs it to stay
        # unresolved so scoring reflects the real outcome, not a false pass.
        if intent == "repair":
            if self.last_played_turn is None:
                return Action("play_adaptive", divergence=True,
                             divergence_reason="repair_before_any_turn")
            field_name = self.last_played_turn.get("field")
            self.repair_count[field_name] = self.repair_count.get(field_name, 0) + 1
            if self.repair_count[field_name] <= 1:   # attempt 1 was the original give; this is attempt 2
                return Action("play_canonical", turn=self.last_played_turn)
            return Action("play_utility", utility_key="repair_exhausted_ack", divergence=True,
                         divergence_reason=f"repair_exhausted:{field_name}")

        if classification.needs_adaptive:
            return Action("play_adaptive", divergence=True,
                         divergence_reason=f"tier3_low_confidence({classification.confidence:.2f})")

        if intent == "check_in":
            # The agent is asking whether we're still on the line - almost
            # always a symptom of OUR OWN tier-3 latency (we don't listen
            # at all while generating an adaptive reply, so a vendor's
            # "are you still there?" lands in that blind spot). Answer it
            # directly and cheaply; don't touch any field or treat it as
            # the thing we were actually asked.
            return Action("play_utility", utility_key="check_in_ack")

        if intent == "confirm":
            # A confirm step means the agent now has enough to read back -
            # including anything conveyed earlier through adaptive fallback,
            # which the canonical-only tracking below never credited.
            # Verified live: a Retell call settled an off-script date via
            # Gemini, had the agent CONFIRM it, then spent ~8 extra confused
            # turns because "date" still showed as outstanding - the agent
            # kept closing, the guardrail kept blocking it, nobody's wrong
            # but the call loops. Treat confirm as resolving every field
            # still open, same as a real caller would take "is that right?"
            # to mean the whole booking is now settled, not just one field.
            #
            # BUT only if looks_like_confirmation(agent_text) - gated after
            # a real failure, not a hypothetical one: confirmed live, hi-IN/
            # C_jitter30, that Laya can classify "confirm" at 0.992
            # confidence (well above tier3_threshold, so the needs_adaptive
            # check above never catches it either) on a jitter-clipped
            # fragment with its subject missing entirely ("को confirm करवाना
            # चाहते हैं?" - "...do you want to confirm?") - no digit, no
            # date term, no affirmation word, nothing. Trusting the bare
            # intent label there marked phone AND date collected with
            # neither ever given. looks_like_confirmation is deliberately
            # loose (any digit/date-term/affirmation-word, not exact entity
            # matching) so a genuine bare "yes, that's right" (the real case
            # this branch exists for) still passes - it just filters out
            # content-free fragments that got mislabelled, same class of
            # bug as caller/call.py's now-removed low-confidence twin of
            # this same shortcut.
            #
            # STILL not field-specific enough on its own, though - confirmed
            # live, hi-IN/C_jitter30: the agent confirmed the PHONE number
            # ("...987-654-3210, is that correct?" - a real digit, so
            # looks_like_confirmation correctly says True), and this blanket
            # "mark every remaining field" then ALSO marked "date" collected,
            # even though nothing in that turn said anything about date. One
            # turn later, asked about date again (confidence too low to hit
            # this branch at all), the adaptive fallback had no reference
            # left for date (it had been wrongly marked done) and invented
            # "Delhi" instead of restating October 15th. confirmed_by_agent
            # is caller/call.py's OWN field-specific pre-check (match_entity
            # against each remaining field's real value, run every turn
            # BEFORE this function is even called) - when it already found
            # and credited something concrete this turn, trust THAT precise
            # result and don't also guess about every OTHER remaining field
            # just because the intent label is "confirm". Only fall through
            # to the blanket mark when the pre-check found NOTHING concrete
            # at all - the genuine bare-affirmation case this branch exists
            # for, where there is no field-specific content to check against.
            if confirmed_by_agent:
                return Action("play_utility", utility_key="confirm_ack")
            if not looks_like_confirmation(agent_text):
                return Action("play_utility", utility_key="confirm_ack")
            for field_name in self.remaining_fields():
                self.collected[field_name] = True
            return Action("play_utility", utility_key="confirm_ack")

        if intent == "closing":
            # The no-fields-remaining case already returned above - every
            # "closing" that reaches here has at least one field still
            # outstanding. Closing is irreversible - it ends the call. Laya
            # ships uncalibrated and can be confidently WRONG, not just
            # unconfident (verified live: 0.854 on a garbled phrase that
            # was actually asking for the date, well above this classifier's
            # 0.75 tier-3 threshold). Trusting "closing" alone while fields
            # are still outstanding would silently abandon the scenario -
            # route it through adaptive fallback instead, so context (or a
            # human reading the divergence log) gets a say before the call
            # ends on an unverified guess.
            return Action("play_adaptive", divergence=True,
                         divergence_reason="closing_with_fields_remaining")

        field_name = FIELD_BY_INTENT.get(intent)
        if field_name is None:
            return Action("play_adaptive", divergence=True,
                         divergence_reason=f"unmapped_intent:{intent}")

        turn = self.turn_for_field(field_name)
        if turn is None:
            return Action("play_adaptive", divergence=True,
                         divergence_reason=f"no_turn_for_field:{field_name}")

        if self.collected.get(field_name):
            # The vendor is asking for a field we already gave - almost
            # always because IT failed to understand/extract it the first
            # time (confirmed live, hi-IN/C_jitter30: the vendor misheard
            # "appointment" as "a payment appointment" and asked "what date
            # would you like?" again, despite the caller's canonical line
            # already stating October 15th correctly). The known-correct
            # answer is sitting right here in `turn` - replaying the SAME
            # canonical audio/text is strictly safer than routing to the
            # tier-3 LLM fallback, which has no access to that known value
            # and, confirmed live, can just invent a new one instead of
            # restating it ("Mujhe kal ki date de dijiye" - "give me
            # tomorrow's date" - fed back to the vendor as if it were the
            # real answer, which then closed the call on the wrong date).
            #
            # Capped at ONE such replay, not unlimited: this project's
            # corpus bakes noise/jitter into the WAV once at build time, so
            # replaying `turn` plays back byte-identical audio every time -
            # no human-like variation the way a real caller rephrasing
            # would have. Confirmed live, same call: capped only by
            # max_turns, it replayed the identical clip 9 times in a row,
            # the vendor failed to parse it every single time (unsurprising
            # - it's hearing the exact same signal, not a new attempt), and
            # the call ran to 21 turns/236s without ever ending. Past the
            # cap, hand this to the adaptive path instead (still flagged as
            # a field to restate accurately, not invent - see
            # caller/call.py's _react(), which feeds generate_reply_text
            # this field's real reference value) - varied phrasing and
            # fresh TTS synthesis at least has a chance where identical
            # replay provably does not.
            self.reask_count[field_name] = self.reask_count.get(field_name, 0) + 1
            if self.reask_count[field_name] <= 1:
                return Action("play_canonical", turn=turn, field=field_name, divergence=True,
                             divergence_reason=f"field_already_collected:{field_name}")
            return Action("play_adaptive", field=field_name, divergence=True,
                         divergence_reason=f"field_already_collected_restate:{field_name}")

        self.collected[field_name] = True
        self.last_played_turn = turn
        return Action("play_canonical", turn=turn, field=field_name)
