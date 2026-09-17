"""
The two capabilities this project records discovery runs for. Both mirror
goal examples given directly in the assignment brief.

Keeping these declarations here (rather than inline in the CLI) is meant to
make the point that "which capability to discover" is itself a reviewable,
typed declaration -- the input/output contract exists before the LLM ever
touches the page.
"""
from __future__ import annotations

from src.agent.discovery import CapabilitySpec
from src.artifact.schema import ParamSpec, ParamType

LOOKUP_MEMBER_BALANCE = CapabilitySpec(
    capability_id="lookup_member_balance",
    name="Look up member and read savings balance",
    description=(
        "Search for a member by ID and read their current savings balance from the member detail page."
    ),
    goal_text="Look up member {member_id} and read their current savings balance.",
    entry_path="/search",
    input_params=[
        ParamSpec(name="member_id", type=ParamType.STRING, description="The member ID to look up.",
                   required=True, sensitive=False, example="12345"),
    ],
    output_params=[
        ParamSpec(name="savings_balance", type=ParamType.STRING,
                   description="The member's current savings balance as displayed on the page.",
                   required=True, sensitive=False),
    ],
)

OPEN_MEMBER_SUB_ACCOUNT = CapabilitySpec(
    capability_id="open_member_sub_account",
    name="Open a new sub-account for a member",
    description=(
        "Search for a member, open a new sub-account of the given type with the given initial deposit, "
        "confirm the irreversible submission, and capture the new account number."
    ),
    goal_text=(
        "For member {member_id}, open a new {account_type} sub-account with an initial deposit of "
        "{initial_deposit}. Complete the confirmation step so the account is actually created, and finish "
        "once you see the success screen showing the new account number."
    ),
    entry_path="/search",
    input_params=[
        ParamSpec(name="member_id", type=ParamType.STRING, description="The member ID to open a sub-account for.",
                   required=True, sensitive=False, example="12345"),
        ParamSpec(name="account_type", type=ParamType.STRING,
                   description="One of Savings, Checking, or CD.", required=True, sensitive=False, example="Savings"),
        ParamSpec(name="initial_deposit", type=ParamType.NUMBER,
                   description="Initial deposit amount, must be greater than 0.", required=True, sensitive=False,
                   example="500"),
    ],
    output_params=[
        ParamSpec(name="account_number", type=ParamType.STRING,
                   description="The newly created sub-account's account number.", required=True, sensitive=False),
    ],
)

CAPABILITIES = {
    LOOKUP_MEMBER_BALANCE.capability_id: LOOKUP_MEMBER_BALANCE,
    OPEN_MEMBER_SUB_ACCOUNT.capability_id: OPEN_MEMBER_SUB_ACCOUNT,
}
