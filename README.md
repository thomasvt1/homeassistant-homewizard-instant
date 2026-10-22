# HomeWizard Instant — API v2 WebSocket fork

A fork of [taurgis/homeassistant-homewizard-instant](https://github.com/taurgis/homeassistant-homewizard-instant) for HomeWizard **P1 meters**, using authenticated API v2 WebSocket push updates. Requires Home Assistant 2025.10 or newer and P1 firmware supporting `/api/ws`.

## Install

[![Open your Home Assistant instance and add a custom repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=thomasvt1&repository=homeassistant-homewizard-instant&category=integration)

1. Add `https://github.com/thomasvt1/homeassistant-homewizard-instant` as a HACS custom repository (Integration), or copy `custom_components/homewizard_instant` into your Home Assistant config directory.
2. Restart Home Assistant, then add **HomeWizard Instant** under Settings → Devices & services.
3. Enter the P1 meter's local IP address. When prompted, press its button and submit within 30 seconds. If the window expires, submit to reopen it, then press and submit again.

## Upgrading from the original

Back up your configuration, replace the integration files and restart. Existing entries prompt for API v2 pairing once. Complete that prompt instead of deleting the entry: the domain and entity IDs remain unchanged, preserving history and dashboard references. This fork replaces the original custom integration; both cannot be installed simultaneously under the same domain. The official `homewizard` integration uses a different domain. This fork creates a separate device user so it does not replace the official integration's token.

API v1 and its local-API toggle are not used. Devices without WebSocket support need a firmware update; there is no polling fallback.

## Updates and recovery

A single TLS-verified `wss://<host>/api/ws` connection authenticates, then subscribes to `device`, `measurement` and `system`. Setup waits for all three initial snapshots. Measurement updates arrive as the meter produces them; WebSockets cannot make the physical smart meter report faster.

A dropped connection marks entities unavailable and reconnects with backoff from 1 to 60 seconds. A revoked token prompts for pairing again. Unloading cancels the reader and closes its socket. System diagnostics follow device pushes: HomeWizard only guarantees real-time updates for certain system settings, so Wi-Fi and uptime may not refresh continuously.

The existing sensor descriptions and official Python client models preserve power, energy, voltage/current and external gas/water/heat meter mappings. No device-control services are added.

## Troubleshooting

- Pairing fails: reopen the window and press the physical button before submitting.
- Unavailable: check firmware WebSocket support and local HTTPS/WSS reachability on port 443.
- IP changed: discovery updates known devices, or use Reconfigure to enter the new address.
- Token revoked: complete the reauthentication prompt.

## Development

On Linux with Python 3.13:

```sh
python -m pip install -r requirements_test.txt
python -m ruff check custom_components/homewizard_instant
python -m mypy --strict custom_components/homewizard_instant
pytest tests -v
```

Automated tests use mocked devices; physical P1 validation is still required before relying on this fork for live automations.

## References

- [HomeWizard API v2](https://api-documentation.homewizard.com/docs/category/api-v2/)
- [WebSocket protocol](https://api-documentation.homewizard.com/docs/v2/websocket/)
- [Official Home Assistant integration](https://github.com/home-assistant/core/tree/dev/homeassistant/components/homewizard)
- [hprax WebSocket fork](https://github.com/hprax/homeassistant-homewizard-instant), reviewed as an additional reference
