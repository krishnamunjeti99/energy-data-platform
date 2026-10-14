"""Smoke test: the dashboard renders against a real gold table without errors."""

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parent.parent / "dashboard" / "app.py"


def test_dashboard_renders(db_available):
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    labels = [m.label for m in at.metric]
    assert labels == ["Generation", "Net export", "Auxiliary consumption", "Data completeness"]
