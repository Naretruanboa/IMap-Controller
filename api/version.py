from functools import lru_cache
from pathlib import Path
import re


@lru_cache(maxsize=1)
def get_app_version() -> str:
    """Read the app version from pyproject.toml so it has one source of truth."""
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    if not match:
        raise RuntimeError("Project version not found in pyproject.toml")
    return match.group(1)
