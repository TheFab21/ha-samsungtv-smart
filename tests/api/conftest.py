"""Scoped fixtures for the vendored Samsung Art client unit tests."""
import sys
from pathlib import Path

import pytest

API_DIR = Path(__file__).resolve().parents[2] / "custom_components" / "samsungtv_smart" / "api"
sys.path.insert(0, str(API_DIR))


@pytest.fixture
def art_client():
    """A SamsungTVAsyncArt instance that never opens a real socket."""
    import art  # noqa: PLC0415

    return art.SamsungTVAsyncArt(host="192.0.2.10", port=8002, token="tok", name="pytest")
