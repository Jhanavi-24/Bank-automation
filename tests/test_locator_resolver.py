"""
Regression test for the LocatorStrategy.TABLE_CELL fix (see REPORT.md
section 3, bug #3).

ROW_VALUE_TEXT ("the tr's other <td>, whichever text isn't the label") is
only unambiguous for exactly two columns. Applied to a real 3+-column data
table (Recent Transactions: Date | Description | Amount), it silently
resolved to the wrong column -- e.g. extracting "Dividend Payment" when the
target was the Amount cell "+$340.12" -- which is exactly the failure a real
`get_recent_transactions` discovery run hit live. TABLE_CELL (row position +
<th> column header) fixes this by construction; this test locks it in
against the live app rather than relying on it only being exercised
incidentally by a capability's own discovery run.

Requires: `python -m src.target_app.app` running on BASE_URL (skipped
otherwise, same convention as test_integration.py).
"""
import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from playwright.sync_api import sync_playwright

from src.agent.discovery import login_operator
from src.artifact.schema import Locator, LocatorStrategy
from src.common import browser_surface, locator_resolver

BASE_URL = "http://127.0.0.1:5055"


def _app_reachable() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 5055), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(
    not _app_reachable(), reason="mock bank app is not running on 127.0.0.1:5055 (see README 'Setup')"
)


@pytest.fixture
def member_page():
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_context().new_page()
        login_operator(page, BASE_URL)
        # Member 12348's transactions (src/target_app/data.py): most recent first --
        # Dividend Payment +340.12, Home Insurance -210.00, Payroll Deposit +4800.00.
        page.goto(f"{BASE_URL}/members/12348")
        page.wait_for_load_state("load")
        yield page
        browser.close()


@pytest.mark.parametrize("row,column,expected", [
    ("1", "Amount", "+$340.12"),
    ("1", "Description", "Dividend Payment"),
    ("2", "Amount", "-$210.00"),
    ("2", "Description", "Home Insurance - Autopay"),
    ("3", "Amount", "+$4800.00"),
    ("3", "Description", "Payroll Deposit"),
])
def test_table_cell_disambiguates_columns_correctly(member_page, row, column, expected):
    loc = Locator(strategy=LocatorStrategy.TABLE_CELL, value=f"{row}|{column}")
    target = locator_resolver.resolve(member_page, [loc])
    assert locator_resolver.read_text(member_page, target).strip() == expected


def test_snapshot_annotates_table_cells_with_row_and_column(member_page):
    elements = browser_surface.snapshot(member_page)
    amount_row1 = next(
        e for e in elements if e.table_column_header == "Amount" and e.table_row_index == 1
    )
    assert amount_row1.text == "+$340.12"


def test_candidate_locators_prefer_table_cell_over_row_value_text(member_page):
    elements = browser_surface.snapshot(member_page)
    amount_row1 = next(
        e for e in elements if e.table_column_header == "Amount" and e.table_row_index == 1
    )
    strategies = [c.strategy for c in amount_row1.candidate_locators()]
    assert LocatorStrategy.TABLE_CELL in strategies
    assert LocatorStrategy.ROW_VALUE_TEXT not in strategies
