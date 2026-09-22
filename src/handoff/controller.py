"""
Human-in-the-loop escalation and handoff.

The core design constraint from the brief: a human must take control of the
SAME live session the automation was using, not a fresh one, then hand
control back so the run can resume or complete. Concretely, that means the
`HandoffController` never opens a new browser/page -- it holds a reference to
the exact `Page` object the discovery/replay loop was already driving, and
every operator command it executes runs against that same object. When
control is handed back, the calling loop simply keeps going against that
same `page`, which now reflects whatever the human changed.

The control-transfer model is a simple two-state machine (AUTOMATION / HUMAN)
plus a logged, reasoned "intervention request" that carries the context a
person needs to act (which capability/step, why, current URL, a screenshot).
That request is also actually routed to a human, not just logged: see
src/handoff/notifier.py -- an email (or, via a carrier's email-to-SMS
gateway, a text) is sent, or written to evidence/ if no SMTP is configured,
so "route an intervention request to a human operator" means something more
than "print to the terminal the operator happens to already be watching."
A full real-time co-browsing console is out of scope (see the brief's scope
note); this is a bare-but-real operator surface: a command REPL that acts on
the live page. Swapping it for a web-based operator console later means
replacing `_operator_repl`'s input source and the command vocabulary stays
the same -- the state machine and the "same session" guarantee do not change.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from playwright.sync_api import Page

from src.common import locator_resolver
from src.common.evidence import EvidenceRecorder
from src.handoff.notifier import NotificationConfig, send_intervention_notice
from src.safety.allowlist import Allowlist


class ControlState(str, Enum):
    AUTOMATION = "automation"
    HUMAN = "human"


class Resolution(str, Enum):
    APPROVED = "approved"   # human says: go ahead and run the automated step as planned
    RESUMED = "resumed"     # human performed the needed action(s) manually; continue from here
    DENIED = "denied"       # human says: do not proceed


@dataclass
class InterventionOutcome:
    resolution: Resolution
    note: str = ""


@dataclass
class HandoffController:
    page: Page
    evidence: EvidenceRecorder
    allowlist: Allowlist
    mode: str = "interactive"           # "interactive" | "scripted" | "auto_approve" | "auto_deny"
    operator_script: List[str] = field(default_factory=list)
    state: ControlState = ControlState.AUTOMATION
    human_actions: List[str] = field(default_factory=list)
    # Real notification channel, not just a print statement -- see
    # src/handoff/notifier.py. Defaults to reading ESCALATION_* env vars;
    # unset (the default) means the demo runs fully offline via the
    # notifier's dry-run fallback. Overridable per-instance for tests.
    notification_config: NotificationConfig = field(default_factory=NotificationConfig.from_env)

    def request_intervention(self, capability_id: str, step_id: str, reason: str,
                              context: Optional[dict] = None) -> InterventionOutcome:
        context = context or {}
        screenshot = self.evidence.screenshot(self.page, f"escalation_{step_id}")
        self.evidence.log(
            "intervention_requested",
            capability_id=capability_id,
            step_id=step_id,
            reason=reason,
            current_url=self.page.url,
            screenshot=screenshot,
            **context,
        )
        self.state = ControlState.HUMAN

        notice_result = send_intervention_notice(
            self.notification_config, self.evidence, capability_id=capability_id, step_id=step_id,
            reason=reason, current_url=self.page.url, screenshot=screenshot,
        )

        print("\n" + "=" * 72)
        print("HUMAN INTERVENTION REQUESTED")
        print(f"  capability : {capability_id}")
        print(f"  step       : {step_id}")
        print(f"  reason     : {reason}")
        print(f"  current URL: {self.page.url}")
        print(f"  screenshot : {screenshot}")
        print(f"  notified   : {notice_result}")
        print("=" * 72 + "\n")

        if self.mode == "auto_approve":
            self.state = ControlState.AUTOMATION
            outcome = InterventionOutcome(Resolution.APPROVED, "auto_approve mode (no human attached)")
        elif self.mode == "auto_deny":
            self.state = ControlState.AUTOMATION
            outcome = InterventionOutcome(Resolution.DENIED, "auto_deny mode (no human attached)")
        else:
            outcome = self._operator_repl(capability_id, step_id)

        self.evidence.log(
            "intervention_resolved",
            capability_id=capability_id,
            step_id=step_id,
            resolution=outcome.resolution.value,
            note=outcome.note,
            human_actions=list(self.human_actions),
        )
        self.state = ControlState.AUTOMATION
        return outcome

    # -- operator surface --------------------------------------------------

    def _operator_repl(self, capability_id: str, step_id: str) -> InterventionOutcome:
        print("Operator console -- you are now in control of the LIVE session.")
        print("Commands:")
        print("  click <exact visible text>")
        print("  fill <row label>|<value>")
        print("  goto <url>")
        print("  screenshot")
        print("  approve            (let automation run the planned step now)")
        print("  resume             (you handled it manually; continue from current state)")
        print("  deny <reason>      (stop the run)")

        if self.mode == "scripted":
            source = iter(self.operator_script)

            def get_command() -> Optional[str]:
                return next(source, None)
        else:
            def get_command() -> Optional[str]:
                try:
                    return input("operator> ")
                except EOFError:
                    return None

        while True:
            raw = get_command()
            if raw is None:
                return InterventionOutcome(Resolution.DENIED, "operator input exhausted without a decision")
            raw = raw.strip()
            if not raw:
                continue
            print(f"operator> {raw}")
            self.human_actions.append(raw)
            self.evidence.log("human_action", capability_id=capability_id, step_id=step_id, command=raw)

            try:
                verb, _, rest = raw.partition(" ")
                verb = verb.lower()

                if verb == "approve":
                    return InterventionOutcome(Resolution.APPROVED, "operator approved the planned step")
                if verb == "resume":
                    return InterventionOutcome(Resolution.RESUMED, "operator completed the needed action(s) manually")
                if verb == "deny":
                    return InterventionOutcome(Resolution.DENIED, rest or "operator denied")
                if verb == "screenshot":
                    path = self.evidence.screenshot(self.page, f"human_{step_id}_{int(time.time())}")
                    print(f"  saved: {path}")
                    continue
                if verb == "goto":
                    self.allowlist.check_url(rest)
                    self.page.goto(rest)
                    print(f"  now at: {self.page.url}")
                    continue
                if verb == "click":
                    self.page.get_by_text(rest, exact=False).first.click(timeout=4000)
                    print(f"  clicked '{rest}', now at: {self.page.url}")
                    continue
                if verb == "fill":
                    label, _, value = rest.partition("|")
                    from src.artifact.schema import Locator, LocatorStrategy
                    target = locator_resolver.resolve(
                        self.page, [Locator(strategy=LocatorStrategy.LABEL_TEXT, value=label.strip())]
                    )
                    locator_resolver.fill(self.page, target, value)
                    print(f"  filled '{label.strip()}'")
                    continue

                print(f"  (unrecognized command: {raw!r})")
            except Exception as e:  # noqa: BLE001 -- an operator typo should not crash the console
                print(f"  command failed: {e}")
                self.evidence.log("human_action_failed", command=raw, error=str(e))
