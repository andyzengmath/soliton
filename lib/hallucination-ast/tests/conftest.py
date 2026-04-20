"""Shared pytest fixtures for hallucination_ast tests."""
from pathlib import Path

import pytest


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"
