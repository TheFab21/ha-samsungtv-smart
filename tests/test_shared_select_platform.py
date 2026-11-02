import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.samsungtv_smart import select as integration_select
from custom_components.samsungtv_smart.const import (
    CONF_IP_CONTROL_TOKEN,
    DATA_IP_CONTROL_STATE_COORDINATOR,
    DOMAIN,
)
from homeassistant.components import select as ha_select
from homeassistant.setup import async_setup_component


async def test_real_platform_registration_manual_refresh_and_removal(hass, monkeypatch):
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "select", {})
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_IP_CONTROL_TOKEN: "synthetic"})
    entry.add_to_hass(hass)
    listeners = []

    def subscribe(callback):
        listeners.append(callback)
        return lambda: listeners.remove(callback)

    coordinator = SimpleNamespace(
        data={"tv": {"speakerSelect": "internal"}, "polled_at": time.monotonic()},
        last_update_success=True,
        async_add_listener=subscribe,
    )
    hass.data[DOMAIN] = {
        entry.entry_id: {DATA_IP_CONTROL_STATE_COORDINATOR: coordinator}
    }
    monkeypatch.setattr(integration_select, "_tv_normal_viewing", lambda *_: True)
    monkeypatch.setattr(integration_select, "clear_token_problem", Mock())
    entity = integration_select.SamsungTVIPControlSpeakerSelect(
        hass, entry, "192.0.2.10", "Example", "example-tv"
    )
    client = AsyncMock()
    client.async_get_speaker_select.return_value = "Internal"
    client.async_get_external_speakers.return_value = [
        {"deviceName": "Receiver", "deviceId": "receiver-1"}
    ]
    entity._get_ip_control = Mock(return_value=client)
    component = hass.data[ha_select.DATA_COMPONENT]
    await component.async_add_entities([entity], True)
    await hass.async_block_till_done()
    assert hass.states.get(entity.entity_id).state == "Internal"
    assert not entity.should_poll
    assert len(listeners) == 1
    coordinator.data["tv"]["speakerSelect"] = "external"
    coordinator.data["polled_at"] = time.monotonic()
    for callback in tuple(listeners):
        callback()
    await hass.async_block_till_done()
    assert hass.states.get(entity.entity_id).state == "Receiver"
    assert client.async_get_speaker_select.await_count == 1
    await hass.services.async_call(
        "homeassistant", "update_entity", {"entity_id": entity.entity_id}, blocking=True
    )
    assert client.async_get_speaker_select.await_count == 2
    assert client.async_get_external_speakers.await_count == 2
    await entity.async_remove()
    assert not listeners
    assert entity._shared_read_window is None
    assert entity._discovery_unsub is None
