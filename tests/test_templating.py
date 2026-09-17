import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.replay.templating import MissingParamError, render


def test_render_simple():
    assert render("{{member_id}}", {"member_id": "12345"}) == "12345"


def test_render_literal_passthrough():
    assert render("Savings", {}) == "Savings"


def test_render_missing_param_raises():
    with pytest.raises(MissingParamError):
        render("{{member_id}}", {})


def test_render_multiple_and_mixed():
    out = render("id={{member_id}} type={{account_type}}", {"member_id": "1", "account_type": "Savings"})
    assert out == "id=1 type=Savings"
