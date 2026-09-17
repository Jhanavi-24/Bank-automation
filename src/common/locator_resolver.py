"""
Resolves a `Locator` (or ranked list of them) against a live Playwright page,
and performs the corresponding action. Shared by the discovery actor and the
replay engine so both exercise the exact same targeting logic -- what the
agent proved works during discovery is exactly what replay will try first.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Union

from playwright.sync_api import Page, TimeoutError as PWTimeoutError

from src.artifact.schema import Locator, LocatorStrategy


class LocatorResolutionError(Exception):
    """No candidate locator resolved to exactly one visible, actionable element."""


@dataclass
class ResolvedTarget:
    locator: Locator                 # which candidate succeeded
    attempt_index: int               # position in the candidate list (0 = first/most robust)
    playwright_locator: Optional[object]  # a Playwright Locator/FrameLocator target, or None for COORDINATES
    coordinates: Optional[tuple] = None


def _scope(page: Page, frame_selector: Optional[str]):
    return page.frame_locator(frame_selector) if frame_selector else page


def _try_one(page: Page, locator: Locator, timeout_ms: int):
    scope = _scope(page, locator.frame)

    if locator.strategy == LocatorStrategy.TEST_ID:
        candidate = scope.get_by_test_id(locator.value)
        candidate.first.wait_for(state="visible", timeout=timeout_ms)
        return candidate.first, None

    if locator.strategy == LocatorStrategy.ROLE_NAME:
        role, name = locator.value.split("|", 1)
        # exact=True matters here: table cells/containers in a nested-table
        # legacy layout can have accessible names that are substrings of an
        # ancestor container's flattened text (e.g. a wrapping <td> "contains"
        # every value on the page). Substring matching would silently
        # resolve to the wrong (outer) element; exact matching does not.
        candidate = scope.get_by_role(role, name=name, exact=True)
        candidate.first.wait_for(state="visible", timeout=timeout_ms)
        return candidate.first, None

    if locator.strategy == LocatorStrategy.LABEL_TEXT:
        safe = locator.value.replace('"', "")
        xpath = f'xpath=//tr[td[normalize-space()="{safe}"]]//*[self::input or self::select or self::textarea]'
        candidate = scope.locator(xpath)
        candidate.first.wait_for(state="visible", timeout=timeout_ms)
        return candidate.first, None

    if locator.strategy == LocatorStrategy.ROW_VALUE_TEXT:
        safe = locator.value.replace('"', "")
        # Direct-child td/th only (not //  descendant search) so this never
        # collides with LABEL_TEXT's control-lookup in the same row.
        xpath = f'xpath=//tr[td[normalize-space()="{safe}"]]/td[normalize-space()!="{safe}"]'
        candidate = scope.locator(xpath)
        candidate.first.wait_for(state="visible", timeout=timeout_ms)
        return candidate.first, None

    if locator.strategy == LocatorStrategy.TEXT_EXACT:
        candidate = scope.get_by_text(locator.value, exact=True)
        candidate.first.wait_for(state="visible", timeout=timeout_ms)
        return candidate.first, None

    if locator.strategy == LocatorStrategy.TEXT_CONTAINS:
        candidate = scope.get_by_text(locator.value, exact=False)
        candidate.first.wait_for(state="visible", timeout=timeout_ms)
        return candidate.first, None

    if locator.strategy == LocatorStrategy.CSS:
        candidate = scope.locator(locator.value)
        candidate.first.wait_for(state="visible", timeout=timeout_ms)
        return candidate.first, None

    if locator.strategy == LocatorStrategy.COORDINATES:
        x_str, y_str = locator.value.split(",")
        x, y = float(x_str), float(y_str)
        # Coordinates carry no identity check by construction (that's exactly why
        # they're ranked dead last), but resolving them unconditionally would
        # defeat every downstream consumer that treats "resolution succeeded" as
        # "the target is really here" -- confirmation gating and interrupt
        # detection both depend on that. A cheap elementFromPoint check at least
        # catches "nothing is even rendered there," e.g. after landing on a
        # differently-laid-out page instead of the one this point was recorded on.
        has_target = page.evaluate(
            "([px, py]) => !!document.elementFromPoint(px, py)", [x, y]
        )
        if not has_target:
            raise LocatorResolutionError(f"No element rendered at coordinates ({x}, {y}).")
        return None, (x, y)

    raise LocatorResolutionError(f"Unknown locator strategy: {locator.strategy}")


def resolve(page: Page, candidates: List[Locator], timeout_ms: int = 4000,
            allow_coordinates: bool = True) -> ResolvedTarget:
    """
    Try each candidate locator in order; return the first that resolves to a
    visible element.

    allow_coordinates=False excludes the COORDINATES strategy entirely. Use
    this for anything where "resolution succeeded" is being used as proof
    the target genuinely exists on the current page (confirmation-gating an
    irreversible step, or deciding whether an interrupt applies) --
    coordinates carry no identity check by construction (almost any point in
    a viewport has *some* element under it), so treating a coordinate match
    as that proof would silently defeat both of those safety checks.
    """
    last_error: Optional[Exception] = None
    for i, loc in enumerate(candidates):
        if not allow_coordinates and loc.strategy == LocatorStrategy.COORDINATES:
            continue
        try:
            pw_locator, coords = _try_one(page, loc, timeout_ms)
            return ResolvedTarget(locator=loc, attempt_index=i, playwright_locator=pw_locator, coordinates=coords)
        except (PWTimeoutError, Exception) as e:  # noqa: BLE001 - deliberately broad, we try the next candidate
            last_error = e
            continue
    raise LocatorResolutionError(
        f"None of {len(candidates)} candidate locators resolved. Last error: {last_error}"
    )


def click(page: Page, target: ResolvedTarget) -> None:
    if target.playwright_locator is not None:
        target.playwright_locator.click()
    else:
        page.mouse.click(*target.coordinates)


def fill(page: Page, target: ResolvedTarget, value: str) -> None:
    if target.playwright_locator is not None:
        target.playwright_locator.fill(value)
    else:
        raise LocatorResolutionError("FILL is not supported via coordinate-only locators.")


def select_option(page: Page, target: ResolvedTarget, value: str) -> None:
    if target.playwright_locator is not None:
        target.playwright_locator.select_option(value)
    else:
        raise LocatorResolutionError("SELECT_OPTION is not supported via coordinate-only locators.")


def read_text(page: Page, target: ResolvedTarget) -> str:
    if target.playwright_locator is not None:
        return target.playwright_locator.inner_text()
    raise LocatorResolutionError("EXTRACT is not supported via coordinate-only locators.")


def is_present(page: Page, locator: Locator, timeout_ms: int = 800) -> bool:
    """Cheap existence check used for interrupt scanning -- short timeout, expected to fail often."""
    try:
        scope = _scope(page, locator.frame)
        if locator.strategy == LocatorStrategy.ROLE_NAME:
            role, name = locator.value.split("|", 1)
            scope.get_by_role(role, name=name).first.wait_for(state="visible", timeout=timeout_ms)
        elif locator.strategy in (LocatorStrategy.TEXT_EXACT, LocatorStrategy.TEXT_CONTAINS):
            scope.get_by_text(locator.value, exact=(locator.strategy == LocatorStrategy.TEXT_EXACT)).first.wait_for(
                state="visible", timeout=timeout_ms
            )
        elif locator.strategy == LocatorStrategy.CSS:
            scope.locator(locator.value).first.wait_for(state="visible", timeout=timeout_ms)
        elif locator.strategy == LocatorStrategy.TEST_ID:
            scope.get_by_test_id(locator.value).first.wait_for(state="visible", timeout=timeout_ms)
        else:
            return False
        return True
    except Exception:
        return False
