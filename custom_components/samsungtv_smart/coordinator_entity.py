"""Coordinator entities that skip unchanged state updates."""

from copy import deepcopy

from homeassistant.core import callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity


class ChangedCoordinatorEntity(CoordinatorEntity):
    _published_coordinator_value: tuple | None = None

    @callback
    def _handle_coordinator_update(self) -> None:
        if self.hass is None or self.entity_id is None:
            super()._handle_coordinator_update()
            return
        value = self.native_value
        # bool and int compare equal in Python but publish different states.
        visible = (
            self.available,
            (type(value), value),
            self.extra_state_attributes,
            self.icon,
            self.entity_picture,
            self.name,
            getattr(self, "native_unit_of_measurement", None),
            self.assumed_state,
            getattr(self, "device_class", None),
            getattr(self, "state_class", None),
            getattr(self, "last_reset", None),
            getattr(self, "options", None),
            getattr(self, "suggested_display_precision", None),
            getattr(self, "native_min_value", None),
            getattr(self, "native_max_value", None),
            getattr(self, "native_step", None),
            getattr(self, "mode", None),
        )
        if self.force_update or visible != self._published_coordinator_value:
            self._published_coordinator_value = deepcopy(visible)
            super()._handle_coordinator_update()
