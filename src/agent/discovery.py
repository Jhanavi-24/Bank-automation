"""
Goal-driven discovery loop: observe -> decide -> act, against a live
Playwright page, with Claude making every decision as a structured tool
call. On success, the recorded trajectory is assembled into a
CapabilityArtifact -- see src/artifact/schema.py for why steps carry ranked
fallback locators and templated (not literal) values.

No shortcuts: every action the agent takes goes through the same
src.common.locator_resolver primitives the replay engine uses, so an
artifact's recorded locators are proven to work at the moment they're
recorded, not just plausible-looking guesses.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from playwright.sync_api import Page, sync_playwright

from src.agent.llm_client import AgentLLM, image_block, text_block
from src.agent.prompts import build_system_prompt
from src.artifact.interrupts_library import default_interrupts_for_sterling_core
from src.artifact.schema import (
    ActionType, CapabilityArtifact, Checkpoint, DiscoveryMetadata, Locator, LocatorStrategy,
    ParamSpec, RiskLevel, Step, TargetFingerprint, TargetSpec,
)
from src.common import browser_surface
from src.common.evidence import EvidenceRecorder
from src.common.locator_resolver import LocatorResolutionError, click, fill, select_option, resolve
from src.safety.allowlist import Allowlist, PolicyViolation
from src.target_app.app import DEMO_PASSWORD, DEMO_USERNAME


@dataclass
class CapabilitySpec:
    capability_id: str
    name: str
    description: str
    goal_text: str
    entry_path: str
    input_params: List[ParamSpec]
    output_params: List[ParamSpec]
    app_key: str = "sterling-core-member-services"


@dataclass
class DiscoveryOutcome:
    success: bool
    artifact: Optional[CapabilityArtifact]
    reason: str = ""
    evidence_dir: str = ""


def login_operator(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/login")
    page.locator('input[name="username"]').fill(DEMO_USERNAME)
    page.locator('input[name="password"]').fill(DEMO_PASSWORD)
    page.locator('input[type="submit"]').click()
    page.wait_for_load_state("load")


def run_discovery(
    spec: CapabilitySpec,
    base_url: str,
    evidence_root: str,
    param_values: Dict[str, str],
    headless: bool = True,
    max_steps: int = 16,
    run_id: Optional[str] = None,
) -> DiscoveryOutcome:
    allowlist = Allowlist.load(_default_allowlist_path())
    # run_id is normally auto-generated (see EvidenceRecorder) -- exposed here so a
    # caller that needs to know the evidence dir before the run finishes (e.g. to
    # tail its log live) can pre-assign it.
    evidence = EvidenceRecorder(evidence_root, "discovery", run_id=run_id, secret_values=[DEMO_PASSWORD])
    evidence.log("discovery_start", capability_id=spec.capability_id, goal=spec.goal_text, params=param_values)

    llm = AgentLLM()
    system_prompt = build_system_prompt(
        goal_text=render_goal(spec.goal_text, param_values),
        input_params=[_with_example(p, param_values) for p in spec.input_params],
        output_params=spec.output_params,
        base_url=base_url,
    )

    steps: List[Step] = []
    outputs_captured: Dict[str, str] = {}
    messages: List[Dict[str, Any]] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()

        login_operator(page, base_url)  # infrastructure, not part of the recorded capability -- see REPORT.md

        entry_url = f"{base_url}{spec.entry_path}"
        allowlist.check_url(entry_url)
        page.goto(entry_url)
        page.wait_for_load_state("load")

        checkpoint: Optional[Checkpoint] = None
        terminal_reason = ""
        success = False
        pending_tool_result: Optional[Dict[str, Any]] = None

        for step_num in range(max_steps):
            elements = browser_surface.snapshot(page)
            rendered = browser_surface.render_for_llm(elements)
            evidence.screenshot(page, f"observe_{step_num:02d}")
            observation_text = (
                f"Current URL: {page.url}\nTurn {step_num + 1}/{max_steps}\n\nInteractive elements:\n{rendered}"
            )

            user_content: List[Dict[str, Any]] = []
            if pending_tool_result is not None:
                user_content.append(pending_tool_result)
            user_content.append(text_block(observation_text))
            try:
                user_content.append(image_block(page.screenshot()))
            except Exception:
                pass
            messages.append({"role": "user", "content": user_content})

            response = llm.decide(system_prompt, messages)
            messages.append({"role": "assistant", "content": response.content})

            tool_use = next((b for b in response.content if b.type == "tool_use"), None)
            if tool_use is None:
                evidence.log("no_tool_call", raw=[str(b) for b in response.content])
                terminal_reason = "Model did not call a tool."
                break

            evidence.log("llm_decision", tool=tool_use.name, input=tool_use.input)

            result_text, terminal, outcome_data = _handle_tool_call(
                page=page, elements=elements, tool_name=tool_use.name, tool_input=tool_use.input,
                allowlist=allowlist, steps=steps, outputs_captured=outputs_captured,
                spec=spec, evidence=evidence,
            )
            pending_tool_result = {"type": "tool_result", "tool_use_id": tool_use.id, "content": result_text}

            if terminal == "success":
                checkpoint = outcome_data["checkpoint"]
                success = True
                terminal_reason = outcome_data.get("summary", "")
                break
            if terminal == "failure":
                terminal_reason = outcome_data.get("reason", "")
                break
            if terminal == "escalate":
                terminal_reason = f"Escalated to human: {outcome_data.get('reason', '')}"
                break
            if terminal == "policy_violation":
                terminal_reason = outcome_data.get("reason", "")
                break
        else:
            terminal_reason = f"Max steps ({max_steps}) exceeded without reaching the goal."

        evidence.log("discovery_end", success=success, reason=terminal_reason, steps_recorded=len(steps))

        if not success:
            context.close()
            browser.close()
            return DiscoveryOutcome(success=False, artifact=None, reason=terminal_reason, evidence_dir=str(evidence.dir))

        fingerprint = TargetFingerprint(
            app_key=spec.app_key, vendor_version="mock-1.0",
            landmark_text="Sterling Core — Member Services Console",
        )
        artifact = CapabilityArtifact(
            capability_id=spec.capability_id,
            name=spec.name,
            description=spec.description,
            target=TargetSpec(entry_url=f"{base_url}{spec.entry_path}", fingerprint=fingerprint),
            discovery=DiscoveryMetadata(
                discovery_run_id=evidence.run_id, goal_text=spec.goal_text, model=llm.model,
            ),
            input_params=spec.input_params,
            output_params=spec.output_params,
            steps=steps,
            interrupts=default_interrupts_for_sterling_core(),
            checkpoint=checkpoint,
        )

        artifact_path = evidence.copy_artifact(artifact.to_json())
        evidence.write_result({
            "success": True, "capability_id": spec.capability_id, "artifact_path": artifact_path,
            "outputs_captured": outputs_captured, "steps_recorded": len(steps),
        })

        context.close()
        browser.close()
        return DiscoveryOutcome(success=True, artifact=artifact, reason=terminal_reason, evidence_dir=str(evidence.dir))


def render_goal(goal_text: str, param_values: Dict[str, str]) -> str:
    out = goal_text
    for k, v in param_values.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def _with_example(p: ParamSpec, param_values: Dict[str, str]) -> ParamSpec:
    if p.name in param_values:
        return p.model_copy(update={"example": str(param_values[p.name])})
    return p


def _default_allowlist_path() -> str:
    from src.safety.allowlist import DEFAULT_ALLOWLIST_PATH
    return DEFAULT_ALLOWLIST_PATH


def _handle_tool_call(page, elements, tool_name, tool_input, allowlist: Allowlist, steps: List[Step],
                       outputs_captured: Dict[str, str], spec: CapabilitySpec, evidence: EvidenceRecorder):
    """Returns (result_text, terminal, data)."""
    try:
        if tool_name == "click_element":
            return _do_click(page, elements, tool_input, allowlist, steps, evidence)
        if tool_name == "fill_field":
            return _do_fill(page, elements, tool_input, allowlist, steps, evidence)
        if tool_name == "select_option":
            return _do_select(page, elements, tool_input, allowlist, steps, evidence)
        if tool_name == "navigate":
            return _do_navigate(page, tool_input, allowlist, steps, evidence)
        if tool_name == "extract_output":
            return _do_extract(page, elements, tool_input, steps, outputs_captured, spec, evidence)
        if tool_name == "finish_success":
            return _do_finish_success(elements, tool_input, spec, outputs_captured, evidence)
        if tool_name == "finish_failure":
            return f"Acknowledged: {tool_input.get('reason', '')}", "failure", {"reason": tool_input.get("reason", "")}
        if tool_name == "escalate_to_human":
            reason = tool_input.get("reason", "")
            evidence.log("escalate_to_human", reason=reason)
            return f"Escalation logged: {reason}", "escalate", {"reason": reason}
        return f"Unknown tool '{tool_name}'.", None, {}
    except PolicyViolation as e:
        evidence.log("policy_violation", tool=tool_name, error=str(e))
        return f"BLOCKED by safety policy: {e}", "policy_violation", {"reason": str(e)}


def _do_click(page, elements, tool_input, allowlist, steps, evidence):
    allowlist.check_action_type(ActionType.CLICK)
    idx = tool_input["index"]
    if idx < 0 or idx >= len(elements):
        return f"Invalid index {idx}. Choose a valid index from the current observation.", None, {}
    elem = elements[idx]
    candidates = elem.candidate_locators()
    try:
        target = resolve(page, candidates)
    except LocatorResolutionError as e:
        return f"Could not click element {idx}: {e}", None, {}

    url_before = page.url
    click(page, target)
    try:
        page.wait_for_load_state("load", timeout=4000)
    except Exception:
        pass
    url_after = page.url
    allowlist.check_url(url_after)

    step = Step(
        id=f"step_{len(steps) + 1}", action=ActionType.CLICK,
        description=tool_input.get("reason", f"Click '{elem.name or elem.text}'"),
        locators=candidates, timeout_ms=5000,
    )
    if allowlist.is_irreversible_route(url_after):
        step.risk = RiskLevel.IRREVERSIBLE
        step.requires_confirmation = True
    elif url_after != url_before:
        step.risk = RiskLevel.REVERSIBLE
    steps.append(step)
    evidence.log("step_recorded", step_id=step.id, action="click", url_after=url_after, risk=step.risk.value)
    return f"Clicked. New URL: {url_after}", None, {}


def _do_fill(page, elements, tool_input, allowlist, steps, evidence):
    allowlist.check_action_type(ActionType.FILL)
    idx = tool_input["index"]
    if idx < 0 or idx >= len(elements):
        return f"Invalid index {idx}.", None, {}
    elem = elements[idx]
    candidates = elem.candidate_locators()
    try:
        target = resolve(page, candidates)
    except LocatorResolutionError as e:
        return f"Could not fill element {idx}: {e}", None, {}

    value = tool_input["value"]
    fill(page, target, value)
    param_ref = tool_input.get("param_ref")
    value_template = "{{" + param_ref + "}}" if param_ref else value

    step = Step(
        id=f"step_{len(steps) + 1}", action=ActionType.FILL,
        description=tool_input.get("reason", f"Fill '{elem.name or elem.row_label}'"),
        locators=candidates, value_template=value_template, risk=RiskLevel.REVERSIBLE,
    )
    steps.append(step)
    evidence.log("step_recorded", step_id=step.id, action="fill", param_ref=param_ref)
    return "Filled.", None, {}


def _do_select(page, elements, tool_input, allowlist, steps, evidence):
    allowlist.check_action_type(ActionType.SELECT_OPTION)
    idx = tool_input["index"]
    if idx < 0 or idx >= len(elements):
        return f"Invalid index {idx}.", None, {}
    elem = elements[idx]
    candidates = elem.candidate_locators()
    try:
        target = resolve(page, candidates)
    except LocatorResolutionError as e:
        return f"Could not select on element {idx}: {e}", None, {}

    value = tool_input["value"]
    select_option(page, target, value)
    param_ref = tool_input.get("param_ref")
    value_template = "{{" + param_ref + "}}" if param_ref else value

    step = Step(
        id=f"step_{len(steps) + 1}", action=ActionType.SELECT_OPTION,
        description=tool_input.get("reason", f"Select on '{elem.name or elem.row_label}'"),
        locators=candidates, value_template=value_template, risk=RiskLevel.REVERSIBLE,
    )
    steps.append(step)
    evidence.log("step_recorded", step_id=step.id, action="select_option", param_ref=param_ref)
    return "Selected.", None, {}


def _do_navigate(page, tool_input, allowlist, steps, evidence):
    allowlist.check_action_type(ActionType.NAVIGATE)
    url = tool_input["url"]
    allowlist.check_url(url)
    page.goto(url)
    page.wait_for_load_state("load")
    step = Step(id=f"step_{len(steps) + 1}", action=ActionType.NAVIGATE,
                description=tool_input.get("reason", "Navigate"), value_template=url)
    steps.append(step)
    evidence.log("step_recorded", step_id=step.id, action="navigate", url=url)
    return f"Navigated. New URL: {page.url}", None, {}


def _do_extract(page, elements, tool_input, steps, outputs_captured, spec, evidence):
    idx = tool_input["index"]
    if idx < 0 or idx >= len(elements):
        return f"Invalid index {idx}.", None, {}
    output_name = tool_input["output_name"]
    declared = {p.name for p in spec.output_params}
    if output_name not in declared:
        return f"'{output_name}' is not a declared output ({sorted(declared)}). Use a declared name.", None, {}

    elem = elements[idx]
    candidates = elem.candidate_locators()
    try:
        target = resolve(page, candidates)
    except LocatorResolutionError as e:
        return f"Could not read element {idx}: {e}", None, {}

    from src.common.locator_resolver import read_text
    text = read_text(page, target).strip()
    outputs_captured[output_name] = text

    step = Step(
        id=f"step_{len(steps) + 1}", action=ActionType.EXTRACT,
        description=tool_input.get("reason", f"Extract {output_name}"),
        locators=candidates, extract_as=output_name,
    )
    steps.append(step)
    evidence.log("step_recorded", step_id=step.id, action="extract", output_name=output_name, value=text)
    return f"Captured {output_name} = {text!r}", None, {}


def _do_finish_success(elements, tool_input, spec, outputs_captured, evidence):
    declared = {p.name for p in spec.output_params}
    missing = declared - set(outputs_captured.keys())
    if missing:
        return f"Cannot finish yet -- missing declared outputs: {sorted(missing)}. Extract them first.", None, {}

    idx = tool_input["checkpoint_index"]
    if idx < 0 or idx >= len(elements):
        return f"Invalid checkpoint_index {idx}.", None, {}
    elem = elements[idx]
    candidates = elem.candidate_locators()
    checkpoint = Checkpoint(description=tool_input.get("summary", "Goal reached."), detect=candidates[0])
    evidence.log("finish_success", checkpoint_locator=candidates[0].model_dump(), outputs=outputs_captured)
    return "Goal complete.", "success", {"checkpoint": checkpoint, "summary": tool_input.get("summary", "")}
