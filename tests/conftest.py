from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("REFRESHSIZER_OFFLINE", "1")
os.environ.pop("ANTHROPIC_API_KEY", None)


@pytest.fixture(scope="session")
def fixtures_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from fixtures_gen import generate_all

    d = tmp_path_factory.mktemp("fixtures")
    generate_all(d)
    return d


@pytest.fixture(scope="session", autouse=True)
def isolated_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    home = tmp_path_factory.mktemp("home")
    os.environ["REFRESHSIZER_HOME"] = str(home)
    return home
