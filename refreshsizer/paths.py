"""Filesystem locations (all relative to the project folder; user-space only)."""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
CONFIG_DIR = PACKAGE_DIR / "config"
CATALOG_DIR = PACKAGE_DIR / "catalog"
SEEDS_DIR = PACKAGE_DIR / "benchmarks" / "seeds"
TEMPLATES_DIR = PACKAGE_DIR / "report" / "templates"


def home_dir() -> Path:
    """Project home: $REFRESHSIZER_HOME, else the repository folder (parent of the package)."""
    env = os.environ.get("REFRESHSIZER_HOME")
    return Path(env).expanduser().resolve() if env else PACKAGE_DIR.parent


def data_dir() -> Path:
    d = home_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def bench_db_path() -> Path:
    env = os.environ.get("REFRESHSIZER_BENCH_DB")
    return Path(env) if env else data_dir() / "benchmarks.sqlite"


def mapping_profiles_dir() -> Path:
    d = data_dir() / "mapping_profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d
