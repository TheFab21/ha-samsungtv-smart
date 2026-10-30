"""Global fixtures for integration_blueprint integration."""

# Fixtures allow you to replace functions with a Mock object. You can perform
# many options via the Mock to reflect a particular behavior from the original
# function that you want to see without going through the function's actual logic.
# Fixtures can either be passed into tests as parameters, or if autouse=True, they
# will automatically be used across all tests.
#
# Fixtures that are defined in conftest.py are available across all tests. You can also
# define fixtures within a particular test file to scope them locally.
#
# pytest_homeassistant_custom_component provides some fixtures that are provided by
# Home Assistant core. You can find those fixture definitions here:
# https://github.com/MatthewFlamm/pytest-homeassistant-custom-component/blob/master/pytest_homeassistant_custom_component/common.py
#
# See here for more info: https://docs.pytest.org/en/latest/fixture.html (note that
# pytest includes fixtures OOB which you can use as defined on this page)
from importlib.util import find_spec
import sys
from types import ModuleType
from unittest.mock import patch

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


# This fixture enables loading custom integrations in all tests.
# Remove to enable selective use of this fixture
@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


# This fixture is used to prevent HomeAssistant from attempting to create and dismiss persistent
# notifications. These calls would fail without this fixture since the persistent_notification
# integration is never loaded during a test.
@pytest.fixture(name="skip_notifications", autouse=True)
def skip_notifications_fixture():
    """Skip notification calls."""
    with patch("homeassistant.components.persistent_notification.async_create"), patch(
        "homeassistant.components.persistent_notification.async_dismiss"
    ):
        yield


# pysmartthings is not in requirements_test, and the integration imports it at
# module level, so each test file that reaches integration code stubs it. Every
# stub is guarded by `if "pysmartthings" not in sys.modules`, which means the
# FIRST one collected serves the whole session — and they did not all carry the
# same names. A run of the whole directory therefore died at collection on
# `from pysmartthings import Capability, Command` in switch.py, because the
# first stub to land happened to define no Command. One stub here, installed
# before any test module is imported, settles it for every run; the per-file
# guards then find it already there and leave it alone.
if "pysmartthings" not in sys.modules and find_spec("pysmartthings") is None:
    _pysmartthings = ModuleType("pysmartthings")

    class _Names:
        """Any attribute is its own name, which is all the integration uses."""

        def __getattr__(self, name):
            return name

    _pysmartthings.Attribute = _Names()
    _pysmartthings.Capability = _Names()
    _pysmartthings.Command = _Names()
    _pysmartthings.SmartThings = object
    sys.modules["pysmartthings"] = _pysmartthings
