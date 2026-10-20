"""Tests for Lab /run endpoint stack gating."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_react_stack_may_use_run():
    from api_apps import _check_app_allows_run

    app = SimpleNamespace(tech_stack="react")
    _check_app_allows_run(app)  # must not raise


def test_python_stack_may_use_run():
    from api_apps import _check_app_allows_run

    app = SimpleNamespace(tech_stack="python")
    _check_app_allows_run(app)


def test_static_stack_rejected_for_run():
    from api_apps import _check_app_allows_run

    app = SimpleNamespace(tech_stack="static")
    with pytest.raises(HTTPException) as exc:
        _check_app_allows_run(app)
    assert exc.value.status_code == 400
    assert "Python backend" in exc.value.detail
