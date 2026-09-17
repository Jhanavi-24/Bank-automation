"""
Curated interrupt rules for the "sterling-core-member-services" app.

These are authored once from knowledge of the app's known runtime states
(the trigger conditions built into src/target_app), not re-derived from a
single discovery trajectory -- in the same way a real team would maintain a
shared library of "known error/interstitial pages for this vendor product"
that gets attached to every capability recorded against it, rather than
rediscovering "what does a session-timeout page look like" from scratch each
time an agent happens to wander into one. See REPORT.md section 3.

Every capability artifact for this app_key includes this same rule set.
"""
from __future__ import annotations

from typing import List

from src.artifact.schema import InterruptRule, Locator, LocatorStrategy, OutcomeClass, ParamSpec, ParamType


def default_interrupts_for_sterling_core() -> List[InterruptRule]:
    return [
        InterruptRule(
            id="session_expired",
            description="The core system ended the session due to inactivity or a forced timeout.",
            detect=Locator(
                strategy=LocatorStrategy.TEXT_CONTAINS,
                value="Your session has expired",
                notes="Session-expiry page text; stable across the app.",
            ),
            classification=OutcomeClass.HARD_FAILURE,
            outcome_code="SESSION_EXPIRED",
            max_occurrences=1,
        ),
        InterruptRule(
            id="member_not_found",
            description="The looked-up member ID does not exist in the core system.",
            detect=Locator(strategy=LocatorStrategy.TEXT_CONTAINS, value="No member found matching"),
            classification=OutcomeClass.BUSINESS_OUTCOME,
            outcome_code="MEMBER_NOT_FOUND",
            max_occurrences=1,
        ),
        InterruptRule(
            id="permission_denied",
            description="The authenticated operator is not authorized to view this member.",
            detect=Locator(strategy=LocatorStrategy.TEXT_CONTAINS, value="not authorized to view member"),
            classification=OutcomeClass.BUSINESS_OUTCOME,
            outcome_code="PERMISSION_DENIED",
            max_occurrences=1,
        ),
        InterruptRule(
            id="transient_system_busy",
            description="A transient core-system slowdown/error that resolves on retry.",
            detect=Locator(strategy=LocatorStrategy.TEXT_CONTAINS, value="temporarily busy"),
            classification=OutcomeClass.RECOVERABLE,
            outcome_code="TRANSIENT_RETRYABLE",
            dismiss_action=Locator(strategy=LocatorStrategy.TEXT_EXACT, value="Retry"),
            resume_current_step=True,
            max_occurrences=2,
        ),
        InterruptRule(
            id="verification_interstitial",
            description="An unexpected 'additional verification' interstitial before the member record is shown.",
            detect=Locator(strategy=LocatorStrategy.TEXT_CONTAINS, value="flagged for additional handling review"),
            classification=OutcomeClass.RECOVERABLE,
            outcome_code="VERIFICATION_INTERSTITIAL",
            dismiss_action=Locator(strategy=LocatorStrategy.ROLE_NAME, value="button|Continue"),
            resume_current_step=True,
            max_occurrences=1,
        ),
        InterruptRule(
            id="invalid_deposit_amount",
            description="The submitted initial deposit failed server-side validation.",
            detect=Locator(strategy=LocatorStrategy.TEXT_CONTAINS, value="Initial deposit must be"),
            classification=OutcomeClass.BUSINESS_OUTCOME,
            outcome_code="INVALID_DEPOSIT_AMOUNT",
            max_occurrences=1,
        ),
        InterruptRule(
            id="invalid_account_type",
            description="No (or an invalid) account type was selected for the new sub-account.",
            detect=Locator(strategy=LocatorStrategy.TEXT_CONTAINS, value="choose a valid account type"),
            classification=OutcomeClass.BUSINESS_OUTCOME,
            outcome_code="INVALID_ACCOUNT_TYPE",
            max_occurrences=1,
        ),
    ]
