from types import SimpleNamespace
from unittest.mock import Mock

from custom_components.samsungtv_smart.coordinator_entity import (
    ChangedCoordinatorEntity,
)
from homeassistant.components.number import NumberEntity
from homeassistant.components.sensor import SensorEntity


class ExampleSensor(ChangedCoordinatorEntity, SensorEntity):
    _attr_name = "Example"
    _attr_icon = "mdi:television"

    def __init__(self):
        coordinator = SimpleNamespace(
            data={"value": 7, "metadata": {"title": "First"}},
            last_update_success=True,
        )
        super().__init__(coordinator)
        self.hass = SimpleNamespace()
        self.entity_id = "sensor.example"
        self.async_write_ha_state = Mock()

    @property
    def native_value(self):
        return self.coordinator.data["value"]

    @property
    def extra_state_attributes(self):
        return self.coordinator.data["metadata"]


def test_one_hundred_identical_updates_publish_once():
    entity = ExampleSensor()
    for _ in range(100):
        entity._handle_coordinator_update()
    assert entity.async_write_ha_state.call_count == 1


def test_values_nested_attributes_images_and_availability_still_publish():
    entity = ExampleSensor()
    entity._handle_coordinator_update()
    entity.coordinator.data["value"] = 8
    entity._handle_coordinator_update()
    entity.coordinator.data["metadata"]["title"] = "Second"
    entity._handle_coordinator_update()
    entity._attr_entity_picture = "https://example.test/art/updated.jpg"
    entity._handle_coordinator_update()
    entity.coordinator.last_update_success = False
    entity._handle_coordinator_update()
    entity._handle_coordinator_update()
    entity.coordinator.last_update_success = True
    entity._handle_coordinator_update()
    assert entity.async_write_ha_state.call_count == 6


def test_explicit_force_update_is_preserved():
    entity = ExampleSensor()
    entity._attr_force_update = True
    for _ in range(100):
        entity._handle_coordinator_update()
    assert entity.async_write_ha_state.call_count == 100


def test_native_value_type_changes_are_not_hidden_by_numeric_equality():
    entity = ExampleSensor()
    entity._handle_coordinator_update()
    entity.coordinator.data["value"] = 7.0
    entity._handle_coordinator_update()
    entity.coordinator.data["value"] = 1
    entity._handle_coordinator_update()
    entity.coordinator.data["value"] = True
    entity._handle_coordinator_update()
    assert entity.async_write_ha_state.call_count == 4


def test_sensor_metadata_changes_are_not_hidden_by_an_unchanged_value():
    entity = ExampleSensor()
    entity._handle_coordinator_update()
    for key, value in [
        ("_attr_name", "Renamed"),
        ("_attr_icon", "mdi:power"),
        ("_attr_native_unit_of_measurement", "W"),
        ("_attr_suggested_display_precision", 2),
        ("_attr_assumed_state", True),
    ]:
        setattr(entity, key, value)
        entity._handle_coordinator_update()
    assert entity.async_write_ha_state.call_count == 6


def test_number_limits_and_step_changes_are_published():
    class ExampleNumber(ChangedCoordinatorEntity, NumberEntity):
        @property
        def native_value(self):
            return 7

    coordinator = SimpleNamespace(data={}, last_update_success=True)
    entity = ExampleNumber(coordinator)
    entity.hass = SimpleNamespace()
    entity.entity_id = "number.example"
    entity.async_write_ha_state = Mock()
    entity._handle_coordinator_update()
    entity._attr_native_max_value = 50
    entity._handle_coordinator_update()
    entity._attr_native_min_value = 5
    entity._handle_coordinator_update()
    entity._attr_native_step = 2
    entity._handle_coordinator_update()
    assert entity.async_write_ha_state.call_count == 4
