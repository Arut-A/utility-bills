"""
Shared test fixtures for the bills pipeline.
"""
import sys
from pathlib import Path

import pytest
import yaml

# Add source directories to path so we can import modules
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "bill-parser"))
sys.path.insert(0, str(PROJECT_ROOT / "gmail-scraper"))
sys.path.insert(0, str(PROJECT_ROOT / "api-server"))


@pytest.fixture(scope="session")
def vendors_yaml_path() -> Path:
    return PROJECT_ROOT / "config" / "vendors.yaml"


@pytest.fixture(scope="session")
def vendors_config(vendors_yaml_path) -> dict:
    with open(vendors_yaml_path) as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="session")
def vendors(vendors_config) -> dict:
    return vendors_config.get("vendors", {})
