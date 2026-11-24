"""SmartThings refusals under Samsung's paid API plans (pysmartthings 4.1.0).

Home Assistant 2026.12 pins pysmartthings 4.1.0 for its SmartThings
integration, and this integration uses whichever version Home Assistant
installs. That release turns the API's plan-era answers into distinct
exceptions — 402 SmartThingsQuotaExceededError, 418 SmartThingsBlockedError,
429 SmartThingsRateLimitError, 5xx SmartThingsServerError — where before only
401/403 raised. A used-up quota was read as an ordinary failure and polled at
the normal cadence, every call counting against a quota already gone.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp import ClientResponseError
import pytest

from custom_components.samsungtv_smart import st_errors, token_notify
from custom_components.samsungtv_smart.media_player import SamsungTVDevice
from custom_components.samsungtv_smart.st_errors import (
    MANAGE_PLANS_URL,
    StErrorKind,
    classify_st_error,
    clear_st_backoff,
    set_st_backoff,
    st_backoff_active,
)


def _pysmartthings_error(name: str, message: str) -> Exception:
    """An exception shaped like pysmartthings 4.1.0's, without the library."""
    base = type("SmartThingsError", (Exception,), {})
    return type(name, (base,), {})(message)


def _http(status: int) -> ClientResponseError:
    return ClientResponseError(MagicMock(), (), status=status, message="x")


# --- classification ----------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "message", "kind"),
    [
        ("SmartThingsQuotaExceededError", "Quota exceeded", StErrorKind.QUOTA),
        (
            "SmartThingsBlockedError",
            "SmartThings has flagged this client as problematic",
            StErrorKind.BLOCKED,
        ),
        ("SmartThingsRateLimitError", "Rate limit exceeded", StErrorKind.RATE_LIMITED),
        (
            "SmartThingsServerError",
            "SmartThings server error (503)",
            StErrorKind.SERVER,
        ),
        ("SmartThingsForbiddenError", "Forbidden", StErrorKind.REFUSED),
        (
            "SmartThingsAuthenticationFailedError",
            "Authentication failed with SmartThings",
            StErrorKind.REFUSED,
        ),
        ("SmartThingsConnectionError", "Timeout occurred", StErrorKind.OTHER),
    ],
)
def test_pysmartthings_4_1_errors_are_classified(name, message, kind):
    assert classify_st_error(_pysmartthings_error(name, message)) is kind


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (401, StErrorKind.REFUSED),
        (402, StErrorKind.QUOTA),
        (403, StErrorKind.REFUSED),
        (418, StErrorKind.BLOCKED),
        (429, StErrorKind.RATE_LIMITED),
        (503, StErrorKind.SERVER),
        (404, StErrorKind.OTHER),
    ],
)
def test_raw_http_errors_are_classified_by_status(status, kind):
    assert classify_st_error(_http(status)) is kind


def test_an_older_pysmartthings_is_still_read_by_its_words():
    assert classify_st_error(RuntimeError("Forbidden")) is StErrorKind.REFUSED
    assert classify_st_error(RuntimeError("boom")) is StErrorKind.OTHER


# --- the shared hold ---------------------------------------------------------


def test_the_hold_is_per_tv_and_keeps_the_longer_one():
    hass = SimpleNamespace(data={})
    set_st_backoff(hass, "a", 300)
    set_st_backoff(hass, "a", 60)  # a rate limit does not shorten a quota hold
    assert st_backoff_active(hass, "a")
    assert not st_backoff_active(hass, "b")
    clear_st_backoff(hass, "a")
    assert not st_backoff_active(hass, "a")


# --- the media player --------------------------------------------------------


def _device(error):
    device = object.__new__(SamsungTVDevice)
    device._st = SimpleNamespace(async_device_update=AsyncMock(side_effect=error))
    device._use_channel_info = True
    device._st_last_exc = None
    device._st_auth_error_count = 0
    device._st_account_error = None
    device.hass = SimpleNamespace(data={})
    device._entry_id = "entry"
    device._name = "TV"
    device.entity_id = "media_player.tv"
    device._log = MagicMock()
    return device


async def _poll(device):
    with (
        patch.object(SamsungTVDevice, "_allow_st_refresh", return_value=True),
        patch(
            "custom_components.samsungtv_smart.media_player.report_smartthings_access"
        ),
        patch(
            "custom_components.samsungtv_smart.media_player.notify_token_problem"
        ) as notify,
        patch(
            "custom_components.samsungtv_smart.media_player.clear_token_problem"
        ) as clear,
    ):
        result = await device._async_st_update()
    return result, notify, clear


async def test_a_used_up_quota_holds_the_cloud_off_and_says_so_once():
    device = _device(_pysmartthings_error("SmartThingsQuotaExceededError", "Quota"))
    result, notify, _ = await _poll(device)
    assert result is False
    assert st_backoff_active(device.hass, "entry")
    notify.assert_called_once_with(
        device.hass, "entry", token_notify.METHOD_SMARTTHINGS_QUOTA, "TV"
    )
    assert device._log.warning.call_count == 1

    _, notify, _ = await _poll(device)
    notify.assert_not_called()  # once, not every poll
    assert device._log.warning.call_count == 1


async def test_the_poll_gate_stays_shut_while_the_cloud_is_held_off():
    device = _device(None)
    device._ws = SimpleNamespace(is_connected=True)
    device._ws_was_connected = False  # a wake edge would force a poll
    set_st_backoff(device.hass, "entry", 300)
    assert device._should_poll_st() is False


async def test_an_answer_lifts_the_hold_and_the_notice():
    device = _device(_pysmartthings_error("SmartThingsQuotaExceededError", "Quota"))
    await _poll(device)
    device._st.async_device_update.side_effect = None
    result, _, clear = await _poll(device)
    assert result is True
    assert not st_backoff_active(device.hass, "entry")
    cleared = {call.args[2] for call in clear.call_args_list}
    assert token_notify.METHOD_SMARTTHINGS_QUOTA in cleared
    assert device._st_account_error is None


async def test_a_blocked_client_gets_its_own_notice():
    device = _device(
        _pysmartthings_error("SmartThingsBlockedError", "flagged this client")
    )
    _, notify, _ = await _poll(device)
    assert notify.call_args.args[2] == token_notify.METHOD_SMARTTHINGS_BLOCKED


@pytest.mark.parametrize(
    "error",
    [
        _pysmartthings_error("SmartThingsServerError", "server error (502)"),
        _http(503),
    ],
)
async def test_a_server_error_is_transient_no_hold_no_warning(error):
    device = _device(error)
    _, notify, _ = await _poll(device)
    assert not st_backoff_active(device.hass, "entry")
    notify.assert_not_called()
    device._log.warning.assert_not_called()


async def test_a_rate_limit_holds_briefly_without_a_notice():
    device = _device(_http(429))
    _, notify, _ = await _poll(device)
    assert st_backoff_active(device.hass, "entry")
    notify.assert_not_called()
    device._log.warning.assert_not_called()


async def test_a_raw_403_now_counts_towards_the_refusal_threshold():
    device = _device(_http(403))
    await _poll(device)
    assert device._st_auth_error_count == 1


# --- the child sensors ------------------------------------------------------


def test_child_sensors_do_not_poll_while_held_off():
    from custom_components.samsungtv_smart.sensor import _st_child_gate

    hass = SimpleNamespace(data={})
    entity = SimpleNamespace(
        hass=hass, _entry=SimpleNamespace(entry_id="entry"), _st_last_poll=0.0
    )
    set_st_backoff(hass, "entry", 300)
    assert _st_child_gate(entity) is False


def test_child_sensor_errors_warn_only_when_there_is_nothing_to_hold():
    from custom_components.samsungtv_smart import sensor

    hass = SimpleNamespace(data={})
    entity = SimpleNamespace(hass=hass, _entry=SimpleNamespace(entry_id="entry"))
    with patch.object(sensor, "_LOGGER") as logger:
        sensor._log_st_child_error(entity, "illuminance sensor", _http(402))
        logger.warning.assert_not_called()
        assert st_backoff_active(hass, "entry")
        sensor._log_st_child_error(entity, "illuminance sensor", RuntimeError("x"))
        logger.warning.assert_called_once()


# --- the notices -------------------------------------------------------------


@pytest.mark.parametrize("lang", ["en", "fr"])
@pytest.mark.parametrize(
    "method",
    [
        token_notify.METHOD_SMARTTHINGS,
        token_notify.METHOD_SMARTTHINGS_PLAN,
        token_notify.METHOD_SMARTTHINGS_QUOTA,
        token_notify.METHOD_SMARTTHINGS_BLOCKED,
    ],
)
def test_every_smartthings_notice_links_the_plans_page(lang, method):
    text = token_notify._MESSAGES[lang][method].format(
        device_name="TV", plans_url=MANAGE_PLANS_URL
    )
    assert MANAGE_PLANS_URL in text
    assert "{" not in text
    assert method in token_notify._TITLES[lang]


def test_the_plans_url_is_samsungs():
    assert st_errors.MANAGE_PLANS_URL == (
        "https://developer.smartthings.com/console/service-integrations/manage-plans"
    )
