"""Constants for the Homewizard integration."""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homewizard_energy.const import Model

DOMAIN = "homewizard_instant"
# Devices exposing API v2 WebSocket push: the P1 meter and the DIN-rail kWh meters.
SUPPORTED_MODELS: frozenset[str] = frozenset(
    {
        Model.P1_METER,
        Model.ENERGY_METER_1_PHASE,
        Model.ENERGY_METER_3_PHASE,
        Model.ENERGY_METER_EASTRON_SDM230,
        Model.ENERGY_METER_EASTRON_SDM630,
    }
)
PLATFORMS = [
    Platform.SENSOR,
]

LOGGER = logging.getLogger(__package__)

# Platform config.
CONF_PRODUCT_NAME = "product_name"
CONF_PRODUCT_TYPE = "product_type"
CONF_SERIAL = "serial"
