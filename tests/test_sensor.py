"""Tests for sensor platform."""

from __future__ import annotations

from unittest.mock import AsyncMock

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfVolume

from custom_components.homewizard_instant.coordinator import (
    HWEnergyDeviceUpdateCoordinator,
)
from custom_components.homewizard_instant.sensor import (
    HomeWizardExternalSensorEntity,
    HomeWizardSensorEntity,
    async_setup_entry,
)
from custom_components.homewizard_instant.sensor import (
    SENSORS,
    to_percentage,
    uptime_to_datetime,
)


async def test_async_setup_entry_adds_entities(
    hass, mock_config_entry, mock_combined_data
):
    """Test async_setup_entry creates sensors."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass, mock_config_entry, api=AsyncMock()
    )
    coordinator.data = mock_combined_data

    mock_config_entry.runtime_data = coordinator

    added = []

    def _add_entities(entities):
        added.extend(entities)

    await async_setup_entry(hass, mock_config_entry, _add_entities)

    assert added
    assert any(isinstance(entity, HomeWizardSensorEntity) for entity in added)
    assert any(isinstance(entity, HomeWizardExternalSensorEntity) for entity in added)


async def test_sensor_entity_enabled_default(
    hass, mock_config_entry, mock_combined_data
):
    """Test sensor enabled default respects enabled_fn."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass, mock_config_entry, api=AsyncMock()
    )
    coordinator.data = mock_combined_data

    description = next(d for d in SENSORS if d.key == "total_power_export_kwh")

    entity = HomeWizardSensorEntity(coordinator, description)

    assert entity.entity_registry_enabled_default is False


async def test_sensor_entity_available(hass, mock_config_entry, mock_combined_data):
    """Test sensor availability depends on value."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass, mock_config_entry, api=AsyncMock()
    )
    coordinator.data = mock_combined_data
    coordinator.data.measurement.power_w = None

    description = next(d for d in SENSORS if d.key == "active_power_w")
    entity = HomeWizardSensorEntity(coordinator, description)

    assert entity.native_value is None
    assert entity.available is False


async def test_external_sensor_unit_and_device_class(
    hass, mock_config_entry, mock_combined_data
):
    """Test external sensor unit normalization and device class handling."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass, mock_config_entry, api=AsyncMock()
    )
    coordinator.data = mock_combined_data

    from custom_components.homewizard_instant.sensor import EXTERNAL_SENSORS

    description = EXTERNAL_SENSORS[
        coordinator.data.measurement.external_devices["gas123"].type
    ]

    external = HomeWizardExternalSensorEntity(coordinator, description, "gas123")

    assert external.native_unit_of_measurement == UnitOfVolume.CUBIC_METERS
    assert external.device_class == SensorDeviceClass.GAS

    coordinator.data.measurement.external_devices["gas123"].unit = "invalid"
    assert external.device_class is None


def test_to_percentage_and_uptime() -> None:
    """Test sensor helpers."""
    assert to_percentage(None) is None
    assert to_percentage(0.5) == 50

    dt = uptime_to_datetime(10)
    assert dt.tzinfo is not None


async def test_external_sensor_no_device(hass, mock_config_entry, mock_combined_data):
    """Test external sensor handles missing device gracefully."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass, mock_config_entry, api=AsyncMock()
    )
    coordinator.data = mock_combined_data
    coordinator.data.measurement.external_devices = None

    from custom_components.homewizard_instant.sensor import EXTERNAL_SENSORS

    description = EXTERNAL_SENSORS[list(EXTERNAL_SENSORS.keys())[0]]

    external = HomeWizardExternalSensorEntity(coordinator, description, "missing")

    assert external.device is None
    assert external.native_value is None
    assert external.available is False
    assert external.native_unit_of_measurement is None


async def test_external_sensor_missing_device_key(
    hass, mock_config_entry, mock_combined_data
):
    """Test external sensor handles missing device key gracefully."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass, mock_config_entry, api=AsyncMock()
    )
    coordinator.data = mock_combined_data

    from custom_components.homewizard_instant.sensor import EXTERNAL_SENSORS

    description = EXTERNAL_SENSORS[
        coordinator.data.measurement.external_devices["gas123"].type
    ]

    external = HomeWizardExternalSensorEntity(coordinator, description, "missing")

    assert external.device is None
    assert external.native_value is None
    assert external.available is False
    assert external.native_unit_of_measurement is None
    assert external.device_class is None


async def test_import_tariff_t1_sensor_does_not_depend_on_export_t2(
    hass, mock_config_entry, mock_combined_data
):
    """Test import tariff 1 sensor creation depends on import data only."""
    mock_config_entry.add_to_hass(hass)

    coordinator = HWEnergyDeviceUpdateCoordinator(
        hass, mock_config_entry, api=AsyncMock()
    )
    coordinator.data = mock_combined_data
    coordinator.data.measurement.energy_import_t1_kwh = 2.0
    coordinator.data.measurement.energy_import_t2_kwh = 1.0
    coordinator.data.measurement.energy_export_t2_kwh = None
    mock_config_entry.runtime_data = coordinator

    added = []

    def _add_entities(entities):
        added.extend(entities)

    await async_setup_entry(hass, mock_config_entry, _add_entities)

    assert any(
        isinstance(entity, HomeWizardSensorEntity)
        and entity.entity_description.key == "total_power_import_t1_kwh"
        for entity in added
    )


async def test_uptime_does_not_drift_on_measurement_push(
    hass, mock_config_entry, mock_combined_data, freezer
):
    """The same system snapshot must not move the estimated boot time."""
    from dataclasses import replace
    from datetime import timedelta
    from custom_components.homewizard_instant.coordinator import (
        HWEnergyDeviceUpdateCoordinator,
    )
    from custom_components.homewizard_instant.sensor import (
        HomeWizardSensorEntity,
        SENSORS,
    )

    coordinator = HWEnergyDeviceUpdateCoordinator(hass, mock_config_entry, AsyncMock())
    coordinator.data = mock_combined_data
    sensor = HomeWizardSensorEntity(
        coordinator, next(item for item in SENSORS if item.key == "uptime")
    )
    initial = sensor.native_value
    freezer.tick(timedelta(minutes=10))
    assert sensor.native_value == initial
    coordinator.data = replace(
        mock_combined_data, system=replace(mock_combined_data.system, uptime_s=1)
    )
    assert sensor.native_value > initial


async def test_kwh_meter_sensors(hass):
    """A three-phase kWh meter exposes phase sensors and no P1-only sensors."""
    from homewizard_energy.models import CombinedModels, Device, Measurement, System
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.homewizard_instant.const import DOMAIN

    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=f"{DOMAIN}_HWE-KWH3_aabbccddeeff", title="kWh"
    )
    entry.add_to_hass(hass)
    coordinator = HWEnergyDeviceUpdateCoordinator(hass, entry, api=AsyncMock())
    coordinator.data = CombinedModels(
        device=Device.from_dict(
            {
                "product_type": "HWE-KWH3",
                "product_name": "kWh meter",
                "serial": "aabbccddeeff",
                "api_version": "2.0.0",
                "firmware_version": "4.19",
            }
        ),
        measurement=Measurement.from_dict(
            {
                "energy_import_kwh": 12.5,
                "energy_export_kwh": 1.5,
                "power_w": 600,
                "power_l1_w": 100,
                "power_l2_w": 200,
                "power_l3_w": 300,
                "voltage_l1_v": 230.1,
                "voltage_l2_v": 230.2,
                "voltage_l3_v": 230.3,
                "current_a": 2.6,
                "current_l1_a": 0.5,
                "current_l2_a": 0.9,
                "current_l3_a": 1.2,
                "apparent_power_va": 650,
                "reactive_power_var": 30,
                "power_factor_l1": 0.9,
                "power_factor_l2": 0.95,
                "power_factor_l3": 0.99,
                "frequency_hz": 50,
            }
        ),
        system=System.from_dict({"wifi_rssi_db": -60, "uptime_s": 10}),
        state=None,
    )
    entry.runtime_data = coordinator
    added = []
    await async_setup_entry(hass, entry, added.extend)

    keys = {entity.entity_description.key for entity in added}
    assert {
        "total_power_import_kwh",
        "total_power_export_kwh",
        "active_power_l3_w",
        "active_voltage_l2_v",
        "active_current_l1_a",
        "active_power_factor_l3",
        "active_frequency_hz",
    } <= keys
    assert not keys & {
        "active_tariff",
        "smr_version",
        "total_power_import_t1_kwh",
        "active_voltage_v",
        "monthly_power_peak_w",
    }
    assert not any(isinstance(e, HomeWizardExternalSensorEntity) for e in added)
