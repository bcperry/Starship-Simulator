"""GUI regression tests (headless via streamlit.testing.AppTest).

These only boot the app and check it renders without exceptions; the full
interactive smoke test is scripts/gui_smoke.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

st = pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest

_GUI = Path(__file__).resolve().parents[1] / "simulator" / "engine" / "gui.py"


def test_gui_boots() -> None:
    at = AppTest.from_file(str(_GUI), default_timeout=60)
    at.run()
    assert not at.exception


def test_gui_runs_a_short_burn() -> None:
    at = AppTest.from_file(str(_GUI), default_timeout=60)
    at.run()
    at.number_input[6].set_value(3.0)   # 3 s max duration
    at.button[0].click().run()
    assert not at.exception
