"""
In-memory "core system" for the mock bank app.

This stands in for a legacy core banking database. Everything lives in
process memory and resets when the server restarts -- that's fine, this is
a proxy target for exercising the automation system, not a real ledger.
"""
from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class SubAccount:
    account_number: str
    account_type: str
    balance: float


@dataclass
class Member:
    member_id: str
    first_name: str
    last_name: str
    savings_balance: float
    checking_balance: float
    sub_accounts: List[SubAccount] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"


# Trigger IDs used to deterministically exercise runtime/error scenarios.
# Documented in README.md under "Demo member IDs".
TRIGGER_NOT_FOUND = "00000"
TRIGGER_PERMISSION_DENIED = "40403"
TRIGGER_TRANSIENT_ERROR = "50000"
TRIGGER_SESSION_EXPIRED = "77777"
TRIGGER_INTERSTITIAL = "90001"
TRIGGER_SUPERVISOR_OVERRIDE = "66666"  # deliberately NOT in the interrupt library -- needs a human

_lock = threading.Lock()
_account_number_seq = itertools.count(500001)

_MEMBERS: Dict[str, Member] = {
    "12345": Member("12345", "Jordan", "Blake", 8214.53, 1050.00),
    "12346": Member("12346", "Priya", "Shah", 530.10, 220.00),
    "12347": Member("12347", "Sam", "O'Connor", 15000.00, 2000.00),
    TRIGGER_TRANSIENT_ERROR: Member(TRIGGER_TRANSIENT_ERROR, "Dana", "Reyes", 3120.00, 440.00),
    TRIGGER_INTERSTITIAL: Member(TRIGGER_INTERSTITIAL, "Wei", "Chen", 92000.00, 5000.00),
    TRIGGER_SUPERVISOR_OVERRIDE: Member(TRIGGER_SUPERVISOR_OVERRIDE, "Marcus", "Ito", 47250.00, 6100.00),
}

# Real (non-trigger) IDs that exist but are outside the caller's authorized
# portfolio -- used to demonstrate the permission-denied business outcome
# without conflating it with "not found".
_RESTRICTED_IDS = {TRIGGER_PERMISSION_DENIED: Member(TRIGGER_PERMISSION_DENIED, "Restricted", "Account", 0, 0)}


def get_member(member_id: str) -> Optional[Member]:
    return _MEMBERS.get(member_id)


def is_restricted(member_id: str) -> bool:
    return member_id in _RESTRICTED_IDS


def open_sub_account(member_id: str, account_type: str, initial_deposit: float) -> SubAccount:
    with _lock:
        member = _MEMBERS[member_id]
        acct = SubAccount(
            account_number=f"SUB-{next(_account_number_seq)}",
            account_type=account_type,
            balance=initial_deposit,
        )
        member.sub_accounts.append(acct)
        return acct
