"""
Deterministic replay engine -- the production execution path.

No LLM in this file. Given a saved CapabilityArtifact and a dict of input
parameters, it drives Playwright directly using the ranked locator fallback
chain each step already carries, opportunistically checks the artifact's
interrupt rules whenever something doesn't go as expected, and returns a
structured ReplayResult that separates:

  - SUCCESS               goal reached, checkpoint verified, outputs returned
  - BUSINESS_OUTCOME      a legitimate answer the caller needs (not a crash)
  - HARD_FAILURE          stop, with enough detail to debug
  - CONFIRMATION_REQUIRED an irreversible step was reached without explicit
                           operator confirmation -- refused, not attempted

See REPORT.md section 3 for the reasoning behind treating interrupts as a
cross-cutting concern checked after every step rather than baked into each
step's own logic.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from playwright.sync_api import Page

from src.artifact.schema import ActionType, CapabilityArtifact, InterruptRule, OutcomeClass, Step
from src.common import locator_resolver
from src.common.evidence import EvidenceRecorder
from src.common.locator_resolver import LocatorResolutionError
from src.replay import templating
from src.replay.result import ReplayResult, ReplayStatus
from src.safety.allowlist import Allowlist, PolicyViolation
from src.handoff.controller import HandoffController, Resolution


def _settle(page: Page, timeout_ms: int = 3000) -> None:
    try:
        page.wait_for_load_state("load", timeout=timeout_ms)
    except Exception:
        pass


def _scan_interrupts(
    page: Page,
    artifact: CapabilityArtifact,
    occurrence_counts: Dict[str, int],
) -> Optional[InterruptRule]:
    for rule in artifact.interrupts:
        if locator_resolver.is_present(page, rule.detect):
            return rule
    return None


def _handle_interrupt(
    page: Page,
    rule: InterruptRule,
    evidence: EvidenceRecorder,
    occurrence_counts: Dict[str, int],
    interrupts_fired: List[str],
) -> Optional[str]:
    """
    Returns:
      "retry"      - caller should retry the step that triggered this scan
      "continue"   - caller should move on as if the step succeeded
      None         - not applicable (caller should treat as terminal; check .result on rule)
    Raises _Terminal to short-circuit with a final ReplayResult.
    """
    count = occurrence_counts.get(rule.id, 0)
    interrupts_fired.append(rule.id)
    if count >= rule.max_occurrences:
        evidence.log("interrupt_exceeded_max", rule=rule.id, count=count)
        raise _Terminal(ReplayResult(
            status=ReplayStatus.HARD_FAILURE,
            capability_id="",
            outcome_code=rule.outcome_code,
            message=f"Interrupt '{rule.id}' recurred beyond max_occurrences={rule.max_occurrences}.",
        ))
    occurrence_counts[rule.id] = count + 1
    evidence.log("interrupt_fired", rule=rule.id, classification=rule.classification.value,
                 outcome_code=rule.outcome_code, occurrence=count + 1)
    evidence.screenshot(page, f"interrupt_{rule.id}")

    if rule.classification == OutcomeClass.BUSINESS_OUTCOME:
        raise _Terminal(ReplayResult(
            status=ReplayStatus.BUSINESS_OUTCOME,
            capability_id="",
            outcome_code=rule.outcome_code,
            message=rule.description,
        ))

    if rule.classification == OutcomeClass.HARD_FAILURE:
        raise _Terminal(ReplayResult(
            status=ReplayStatus.HARD_FAILURE,
            capability_id="",
            outcome_code=rule.outcome_code,
            message=rule.description,
        ))

    # RECOVERABLE
    if rule.dismiss_action is not None:
        try:
            resolved = locator_resolver.resolve(page, [rule.dismiss_action], timeout_ms=3000)
            locator_resolver.click(page, resolved)
            _settle(page)
        except LocatorResolutionError as e:
            raise _Terminal(ReplayResult(
                status=ReplayStatus.HARD_FAILURE,
                capability_id="",
                message=f"Recoverable interrupt '{rule.id}' fired but its dismiss control was not found: {e}",
            ))
    return "retry" if rule.resume_current_step else "continue"


class _Terminal(Exception):
    def __init__(self, result: ReplayResult):
        self.result = result


class ConfirmationRequired(Exception):
    """
    Raised only once a risky step's target has actually been resolved on the
    live page -- i.e. we have positively confirmed we are at the point where
    the irreversible action is really about to happen, not merely that this
    step appears somewhere later in the artifact. This ordering matters: if
    we gated on `requires_confirmation` before attempting resolution, a step
    that would legitimately fail for an unrelated reason (e.g. the form
    never reached the confirm page because of a validation error) would be
    misreported as "blocked pending confirmation" instead of the real
    business outcome. See REPORT.md section 6.
    """
    def __init__(self, step: Step):
        self.step = step


def _execute_step(page: Page, step: Step, params: Dict[str, Any], allowlist: Allowlist,
                   outputs: Dict[str, Any], evidence: EvidenceRecorder,
                   confirm_irreversible: bool, approved_steps: set) -> None:
    allowlist.check_action_type(step.action)

    if step.action == ActionType.NAVIGATE:
        url = templating.render(step.value_template or "", params)
        allowlist.check_url(url)
        page.goto(url)
        _settle(page)

    elif step.action == ActionType.CLICK:
        # Irreversible steps must never resolve via raw coordinates: that
        # strategy carries no identity check, so a coordinate "match" is not
        # trustworthy evidence that we're really looking at the confirmation
        # control (see locator_resolver.resolve's allow_coordinates doc).
        resolved = locator_resolver.resolve(
            page, step.locators, timeout_ms=step.timeout_ms,
            allow_coordinates=not step.requires_confirmation,
        )
        if step.requires_confirmation and not confirm_irreversible and step.id not in approved_steps:
            raise ConfirmationRequired(step)
        locator_resolver.click(page, resolved)
        _settle(page)
        allowlist.check_url(page.url)

    elif step.action == ActionType.FILL:
        resolved = locator_resolver.resolve(page, step.locators, timeout_ms=step.timeout_ms)
        value = templating.render(step.value_template or "", params)
        locator_resolver.fill(page, resolved, value)

    elif step.action == ActionType.SELECT_OPTION:
        resolved = locator_resolver.resolve(page, step.locators, timeout_ms=step.timeout_ms)
        value = templating.render(step.value_template or "", params)
        locator_resolver.select_option(page, resolved, value)

    elif step.action == ActionType.WAIT_FOR:
        if step.locators:
            locator_resolver.resolve(page, step.locators, timeout_ms=step.timeout_ms)
        else:
            page.wait_for_timeout(step.timeout_ms)

    elif step.action == ActionType.EXTRACT:
        resolved = locator_resolver.resolve(page, step.locators, timeout_ms=step.timeout_ms)
        text = locator_resolver.read_text(page, resolved)
        if step.extract_as:
            outputs[step.extract_as] = text.strip()

    elif step.action == ActionType.ASSERT_CHECKPOINT:
        locator_resolver.resolve(page, step.locators, timeout_ms=step.timeout_ms)

    else:
        raise ValueError(f"Unhandled action type: {step.action}")


def replay(
    artifact: CapabilityArtifact,
    params: Dict[str, Any],
    page: Page,
    allowlist: Allowlist,
    evidence: EvidenceRecorder,
    confirm_irreversible: bool = False,
    handoff: Optional[HandoffController] = None,
) -> ReplayResult:
    evidence.log("replay_start", capability_id=artifact.capability_id, params=params)
    used_handoff = False

    missing = [p.name for p in artifact.input_params if p.required and p.name not in params]
    if missing:
        return ReplayResult(
            status=ReplayStatus.HARD_FAILURE,
            capability_id=artifact.capability_id,
            message=f"Missing required input parameter(s): {', '.join(missing)}",
        )

    outputs: Dict[str, Any] = {}
    interrupts_fired: List[str] = []
    occurrence_counts: Dict[str, int] = {}
    approved_steps: set = set()

    for step in artifact.steps:
        evidence.log("step_start", step_id=step.id, action=step.action.value, description=step.description)

        attempts = 0
        while True:
            attempts += 1
            try:
                _execute_step(page, step, params, allowlist, outputs, evidence,
                               confirm_irreversible, approved_steps)
                evidence.log("step_ok", step_id=step.id)
                break
            except _Terminal:
                raise
            except ConfirmationRequired:
                evidence.log("confirmation_required", step_id=step.id, risk=step.risk.value)
                if handoff is None:
                    return ReplayResult(
                        status=ReplayStatus.CONFIRMATION_REQUIRED,
                        capability_id=artifact.capability_id,
                        failed_step_id=step.id,
                        message=(
                            f"Step '{step.id}' is classified {step.risk.value} and requires explicit "
                            "confirmation (pass --confirm-irreversible) before replay will execute it "
                            "unattended."
                        ),
                        interrupts_fired=interrupts_fired,
                    )
                used_handoff = True
                outcome = handoff.request_intervention(
                    artifact.capability_id, step.id,
                    reason=f"Step '{step.id}' is {step.risk.value} ({step.description}) and needs human sign-off.",
                    context={"action": step.action.value, "description": step.description},
                )
                if outcome.resolution == Resolution.DENIED:
                    return ReplayResult(
                        status=ReplayStatus.DENIED_BY_OPERATOR,
                        capability_id=artifact.capability_id,
                        failed_step_id=step.id,
                        message=f"Operator denied step '{step.id}': {outcome.note}",
                        interrupts_fired=interrupts_fired,
                        human_intervention=True,
                    )
                if outcome.resolution == Resolution.APPROVED:
                    approved_steps.add(step.id)
                    continue  # retry now that this exact step is pre-approved for this run
                if outcome.resolution == Resolution.RESUMED:
                    # Operator performed this step (or more) manually via the same live session.
                    break
            except (LocatorResolutionError, PolicyViolation, Exception) as e:  # noqa: BLE001
                if isinstance(e, PolicyViolation):
                    evidence.log("policy_violation", step_id=step.id, error=str(e))
                    evidence.screenshot(page, f"policy_violation_{step.id}")
                    return ReplayResult(
                        status=ReplayStatus.HARD_FAILURE,
                        capability_id=artifact.capability_id,
                        failed_step_id=step.id,
                        message=f"Safety policy violation: {e}",
                        interrupts_fired=interrupts_fired,
                    )

                evidence.log("step_error", step_id=step.id, error=str(e), attempt=attempts)
                try:
                    rule = _scan_interrupts(page, artifact, occurrence_counts)
                except Exception:
                    rule = None

                if rule is None:
                    evidence.screenshot(page, f"hard_failure_{step.id}")
                    if handoff is not None:
                        used_handoff = True
                        outcome = handoff.request_intervention(
                            artifact.capability_id, step.id,
                            reason=(
                                f"Step '{step.id}' failed and no known interrupt matched the current page: {e}"
                            ),
                            context={"expected": step.description or step.id, "observed_url": page.url},
                        )
                        if outcome.resolution == Resolution.DENIED:
                            return ReplayResult(
                                status=ReplayStatus.DENIED_BY_OPERATOR,
                                capability_id=artifact.capability_id,
                                failed_step_id=step.id,
                                message=f"Operator denied continuing past step '{step.id}': {outcome.note}",
                                interrupts_fired=interrupts_fired,
                                human_intervention=True,
                            )
                        if outcome.resolution == Resolution.RESUMED and attempts <= 3:
                            continue  # retry the step now that the human has fixed the live session's state
                        if outcome.resolution == Resolution.APPROVED and attempts <= 3:
                            continue  # human says the original plan is fine -- try again as-is
                    return ReplayResult(
                        status=ReplayStatus.HARD_FAILURE,
                        capability_id=artifact.capability_id,
                        failed_step_id=step.id,
                        expected=step.description or step.id,
                        observed=f"url={page.url!r}; error={e}",
                        message=f"Step '{step.id}' failed and no known interrupt matched the current page.",
                        interrupts_fired=interrupts_fired,
                        human_intervention=used_handoff,
                    )

                try:
                    outcome = _handle_interrupt(page, rule, evidence, occurrence_counts, interrupts_fired)
                except _Terminal as term:
                    term.result.capability_id = artifact.capability_id
                    term.result.outputs = outputs
                    term.result.interrupts_fired = interrupts_fired
                    return term.result

                if outcome == "retry":
                    if attempts > 3:
                        return ReplayResult(
                            status=ReplayStatus.HARD_FAILURE,
                            capability_id=artifact.capability_id,
                            failed_step_id=step.id,
                            message=f"Step '{step.id}' still failing after {attempts} attempts and interrupt recovery.",
                            interrupts_fired=interrupts_fired,
                        )
                    continue  # retry the same step
                else:  # "continue" -- treat step as handled, move to next step
                    break

    # Also give the checkpoint the same interrupt-aware treatment: a
    # capability can complete every step and still not be in the expected
    # final state (e.g. it silently landed on a business-outcome page).
    if artifact.checkpoint is not None:
        try:
            locator_resolver.resolve(page, [artifact.checkpoint.detect], timeout_ms=4000)
        except LocatorResolutionError:
            try:
                rule = _scan_interrupts(page, artifact, occurrence_counts)
            except Exception:
                rule = None
            if rule is not None:
                try:
                    _handle_interrupt(page, rule, evidence, occurrence_counts, interrupts_fired)
                except _Terminal as term:
                    term.result.capability_id = artifact.capability_id
                    term.result.outputs = outputs
                    term.result.interrupts_fired = interrupts_fired
                    return term.result
            evidence.screenshot(page, "checkpoint_not_verified")
            return ReplayResult(
                status=ReplayStatus.HARD_FAILURE,
                capability_id=artifact.capability_id,
                message=f"Checkpoint not verified: {artifact.checkpoint.description}",
                observed=f"url={page.url!r}",
                interrupts_fired=interrupts_fired,
            )

    evidence.log("replay_success", outputs=outputs)
    return ReplayResult(
        status=ReplayStatus.SUCCESS,
        capability_id=artifact.capability_id,
        outputs=outputs,
        message="Goal reached and checkpoint verified.",
        interrupts_fired=interrupts_fired,
        human_intervention=used_handoff,
    )
