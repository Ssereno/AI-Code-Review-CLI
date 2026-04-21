"""Shared pytest fixtures for AI review tests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from src.config import ReviewConfig


@pytest.fixture
def review_config() -> ReviewConfig:
    """Return a valid baseline review configuration for CLI workflow tests."""
    return ReviewConfig(
        llm_provider="openai",
        api_key="secret",
        model="gpt-4o-mini",
        color_output=False,
        output_format="terminal",
        review_scope="diff_only",
        verbosity="detailed",
        max_diff_files=10,
        max_diff_lines=200,
        tfs_base_url="https://example.invalid/tfs",
        tfs_project="ExampleProject",
        tfs_pat="pat",
    )


@pytest.fixture
def review_config_factory(review_config: ReviewConfig) -> Callable[..., ReviewConfig]:
    """Build variant configurations for targeted scenarios."""

    def _factory(**changes: object) -> ReviewConfig:
        return replace(review_config, **changes)

    return _factory


@pytest.fixture
def sample_diff() -> str:
    """Return a reusable two-file unified diff sample."""
    return "\n".join([
        "diff --git a/src/app.py b/src/app.py",
        "--- a/src/app.py",
        "+++ b/src/app.py",
        "@@ -1,2 +1,3 @@",
        " import os",
        "-print('old')",
        "+print('new')",
        "+print('extra')",
        "diff --git a/docs/readme.md b/docs/readme.md",
        "--- a/docs/readme.md",
        "+++ b/docs/readme.md",
        "@@ -1 +1,2 @@",
        "-old",
        "+new",
        "+more",
    ])


@pytest.fixture
def temp_config_file(tmp_path: Path) -> Callable[[str], str]:
    """Create temporary configuration files and return their paths."""

    def _factory(content: str) -> str:
        path = tmp_path / "config.yaml"
        path.write_text(content, encoding="utf-8")
        return str(path)

    return _factory