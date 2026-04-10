from __future__ import annotations

from pathlib import Path

from .utils import read_text


def load_prompt(path: Path) -> str:
    return read_text(path)


def render_prompt(path: Path, **kwargs: object) -> str:
    template = load_prompt(path)
    return template.format(**kwargs)
