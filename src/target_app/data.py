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
class Transaction:
    date: str
    description: str
    amount: float  # positive = credit/deposit, negative = debit/withdrawal


@dataclass
class Card:
    last4: str
    network: str  # Visa, Mastercard
    card_type: str  # Debit, Credit
    status: str  # Active, Frozen, Expired


@dataclass
class Beneficiary:
    name: str
    relationship: str
    account_last4: str


@dataclass
class AlertPreferences:
    low_balance_threshold: Optional[float] = None  # None = disabled
    paperless_statements: bool = True
    large_transaction_alerts: bool = True


@dataclass
class Member:
    member_id: str
    first_name: str
    last_name: str
    savings_balance: float
    checking_balance: float
    sub_accounts: List[SubAccount] = field(default_factory=list)
    # Added for a richer, more realistic-looking roster -- both default so
    # every existing positional Member(...) construction above stays valid
    # unchanged (see README/REPORT: existing member records and their exact
    # balances are load-bearing for already-committed evidence and must
    # never change).
    member_since: str = "2015-01-01"
    tier: str = "Standard"
    transactions: List[Transaction] = field(default_factory=list)
    cards: List[Card] = field(default_factory=list)
    beneficiaries: List[Beneficiary] = field(default_factory=list)
    alert_preferences: AlertPreferences = field(default_factory=AlertPreferences)

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


def _txns(*rows: tuple) -> List[Transaction]:
    """Small factory to keep the roster below readable -- each row is
    (date, description, amount)."""
    return [Transaction(date=d, description=desc, amount=amt) for d, desc, amt in rows]


def _cards(*rows: tuple) -> List[Card]:
    """Each row is (last4, network, card_type, status)."""
    return [Card(last4=l4, network=n, card_type=ct, status=s) for l4, n, ct, s in rows]


def _benes(*rows: tuple) -> List[Beneficiary]:
    """Each row is (name, relationship, account_last4)."""
    return [Beneficiary(name=n, relationship=r, account_last4=a) for n, r, a in rows]


_MEMBERS: Dict[str, Member] = {
    # Original three -- exact values kept byte-for-byte unchanged: they're
    # baked into already-committed evidence/screenshots (e.g. member 12345's
    # "$8214.53" balance appears verbatim in evidence/discovery_.../result.json).
    # Transaction history is new and purely additive, so it's safe to attach.
    "12345": Member("12345", "Jordan", "Blake", 8214.53, 1050.00, transactions=_txns(
        ("2026-09-18", "Payroll Deposit", 2400.00),
        ("2026-09-15", "Grocery Store - Debit Card", -86.42),
        ("2026-09-10", "Transfer to Savings", -300.00),
        ("2026-09-02", "ATM Withdrawal", -100.00),
    ), cards=_cards(("4471", "Visa", "Debit", "Active")),
       alert_preferences=AlertPreferences(low_balance_threshold=200.0)),
    "12346": Member("12346", "Priya", "Shah", 530.10, 220.00, transactions=_txns(
        ("2026-09-19", "Mobile Deposit", 150.00),
        ("2026-09-12", "Electric Co-op - Autopay", -64.20),
        ("2026-09-05", "Coffee Shop - Debit Card", -5.75),
    ), cards=_cards(("2290", "Mastercard", "Debit", "Active")),
       beneficiaries=_benes(("Raj Shah", "Spouse", "7734")),
       alert_preferences=AlertPreferences(low_balance_threshold=100.0, paperless_statements=False)),
    "12347": Member("12347", "Sam", "O'Connor", 15000.00, 2000.00, transactions=_txns(
        ("2026-09-17", "Interest Payment", 22.14),
        ("2026-09-08", "Transfer from Checking", 500.00),
        ("2026-08-30", "Wire Transfer Received", 5000.00),
    ), cards=_cards(("5581", "Visa", "Credit", "Active"))),
    TRIGGER_TRANSIENT_ERROR: Member(TRIGGER_TRANSIENT_ERROR, "Dana", "Reyes", 3120.00, 440.00),
    TRIGGER_INTERSTITIAL: Member(TRIGGER_INTERSTITIAL, "Wei", "Chen", 92000.00, 5000.00),
    TRIGGER_SUPERVISOR_OVERRIDE: Member(TRIGGER_SUPERVISOR_OVERRIDE, "Marcus", "Ito", 47250.00, 6100.00),
    # Additional synthetic roster -- richer data depth only (see REPORT.md /
    # conversation history for why this stops here rather than adding new
    # auth mechanics): varied names, balances, tiers, membership tenures,
    # and now transaction history, purely so the app reads like a real
    # member roster rather than six special-cased records.
    "12348": Member("12348", "Elena", "Petrova", 45230.18, 3200.50,
                     member_since="2011-03-14", tier="Premier", transactions=_txns(
                         ("2026-09-20", "Dividend Payment", 340.12),
                         ("2026-09-14", "Home Insurance - Autopay", -210.00),
                         ("2026-09-01", "Payroll Deposit", 4800.00),
                     ), cards=_cards(("3312", "Visa", "Debit", "Active"), ("7788", "Mastercard", "Credit", "Active")),
                        beneficiaries=_benes(("Ivan Petrov", "Sibling", "4420")),
                        alert_preferences=AlertPreferences(low_balance_threshold=5000.0)),
    "12349": Member("12349", "Malik", "Johnson", 128.42, 75.10,
                     member_since="2023-08-02", tier="Standard", transactions=_txns(
                         ("2026-09-19", "Mobile Deposit", 60.00),
                         ("2026-09-13", "Ride Share", -18.50),
                         ("2026-09-06", "Campus Bookstore", -42.00),
                     ), cards=_cards(("6640", "Mastercard", "Debit", "Active")),
                        alert_preferences=AlertPreferences(low_balance_threshold=50.0)),
    "12350": Member("12350", "Fatima", "Al-Sayed", 92150.00, 12400.33,
                     member_since="2008-11-30", tier="Premier",
                     sub_accounts=[SubAccount("SUB-441207", "Certificate of Deposit", 25000.00)],
                     transactions=_txns(
                         ("2026-09-16", "Wire Transfer Received", 15000.00),
                         ("2026-09-09", "Property Tax - Autopay", -3400.00),
                         ("2026-08-28", "Interest Payment", 118.60),
                     ), cards=_cards(("9012", "Visa", "Debit", "Active"), ("4456", "Visa", "Credit", "Frozen")),
                        beneficiaries=_benes(("Youssef Al-Sayed", "Child", "2201"), ("Layla Al-Sayed", "Child", "2202")),
                        alert_preferences=AlertPreferences(low_balance_threshold=10000.0, paperless_statements=False)),
    "12351": Member("12351", "Liam", "O'Brien", 3450.75, 890.20,
                     member_since="2019-06-01", tier="Standard", transactions=_txns(
                         ("2026-09-18", "Payroll Deposit", 1900.00),
                         ("2026-09-11", "Auto Loan Payment", -410.00),
                         ("2026-09-04", "Grocery Store - Debit Card", -95.30),
                     ), cards=_cards(("1123", "Mastercard", "Debit", "Active"))),
    "12352": Member("12352", "Grace", "Nakamura", 267000.00, 45000.00,
                     member_since="2003-01-15", tier="Private", transactions=_txns(
                         ("2026-09-15", "Brokerage Transfer In", 50000.00),
                         ("2026-09-05", "Charitable Donation", -10000.00),
                         ("2026-08-25", "Dividend Payment", 1240.55),
                     ), cards=_cards(("8890", "Visa", "Debit", "Active"), ("3345", "Visa", "Credit", "Active")),
                        beneficiaries=_benes(("Kenji Nakamura", "Spouse", "5567"))),
    "12353": Member("12353", "Carlos", "Mendoza", 610.00, 220.15,
                     member_since="2021-02-20", tier="Standard", transactions=_txns(
                         ("2026-09-17", "Mobile Deposit", 200.00),
                         ("2026-09-10", "Phone Bill - Autopay", -55.00),
                         ("2026-09-03", "ATM Withdrawal", -60.00),
                     ), cards=_cards(("2201", "Mastercard", "Debit", "Active")),
                        alert_preferences=AlertPreferences(low_balance_threshold=75.0)),
    "12354": Member("12354", "Aisha", "Bello", 15420.60, 2100.00,
                     member_since="2016-09-09", tier="Standard", transactions=_txns(
                         ("2026-09-19", "Payroll Deposit", 3100.00),
                         ("2026-09-12", "Transfer to Savings", -1000.00),
                         ("2026-09-02", "Pharmacy - Debit Card", -34.90),
                     ), cards=_cards(("6612", "Visa", "Debit", "Active")),
                        beneficiaries=_benes(("Tunde Bello", "Sibling", "3390")),
                        alert_preferences=AlertPreferences(low_balance_threshold=500.0)),
    "12355": Member("12355", "Noah", "Kowalski", 78.33, 12.00,
                     member_since="2024-01-05", tier="Standard", transactions=_txns(
                         ("2026-09-14", "Mobile Deposit", 40.00),
                         ("2026-09-07", "Overdraft Fee", -30.00),
                         ("2026-08-29", "Fast Food - Debit Card", -11.25),
                     ), cards=_cards(("4478", "Mastercard", "Debit", "Frozen")),
                        alert_preferences=AlertPreferences(low_balance_threshold=25.0, paperless_statements=False)),
    "12356": Member("12356", "Yuki", "Tanaka", 54300.90, 6700.40,
                     member_since="2013-07-22", tier="Premier", transactions=_txns(
                         ("2026-09-18", "Payroll Deposit", 5200.00),
                         ("2026-09-09", "Mortgage Payment", -2100.00),
                         ("2026-08-31", "Interest Payment", 89.40),
                     ), cards=_cards(("7723", "Visa", "Debit", "Active"), ("9981", "Visa", "Credit", "Active")),
                        beneficiaries=_benes(("Haruto Tanaka", "Child", "6654")),
                        alert_preferences=AlertPreferences(low_balance_threshold=2000.0)),
    "12357": Member("12357", "Isabella", "Rossi", 9800.00, 1500.00,
                     member_since="2018-04-11", tier="Standard", transactions=_txns(
                         ("2026-09-16", "Payroll Deposit", 2200.00),
                         ("2026-09-08", "Student Loan Payment", -280.00),
                         ("2026-09-01", "Streaming Subscription", -15.99),
                     ), cards=_cards(("3391", "Mastercard", "Debit", "Active"))),
    "12358": Member("12358", "Tobias", "Reinhardt", 189500.00, 22000.00,
                     member_since="2005-12-01", tier="Private",
                     sub_accounts=[
                         SubAccount("SUB-388014", "Savings", 60000.00),
                         SubAccount("SUB-402951", "Certificate of Deposit", 100000.00),
                     ],
                     transactions=_txns(
                         ("2026-09-15", "Wire Transfer Received", 40000.00),
                         ("2026-09-06", "Private Banking Fee", -250.00),
                         ("2026-08-27", "Dividend Payment", 2100.75),
                     ), cards=_cards(("5567", "Visa", "Debit", "Active"), ("8834", "Visa", "Credit", "Active")),
                        beneficiaries=_benes(("Helga Reinhardt", "Spouse", "1123"), ("Klaus Reinhardt", "Child", "1124")),
                        alert_preferences=AlertPreferences(low_balance_threshold=20000.0, paperless_statements=False)),
    "12359": Member("12359", "Amara", "Okafor", 2200.45, 430.10,
                     member_since="2022-10-18", tier="Standard", transactions=_txns(
                         ("2026-09-17", "Payroll Deposit", 1450.00),
                         ("2026-09-10", "Rent - Autopay", -1100.00),
                         ("2026-09-03", "Grocery Store - Debit Card", -72.60),
                     ), cards=_cards(("2245", "Mastercard", "Debit", "Active")),
                        alert_preferences=AlertPreferences(low_balance_threshold=100.0)),
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
