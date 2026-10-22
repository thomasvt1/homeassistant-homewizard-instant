# HomeWizard Instant

This fork supports P1 meters through API v2 WebSockets. Keep the `homewizard_instant` domain and existing DOMAIN-prefixed entry, device and sensor identifiers stable.

- Centralize snapshots in `HWEnergyDeviceUpdateCoordinator`; entities consume its data without polling.
- Keep one authenticated WebSocket per entry and use the Home Assistant-managed aiohttp session.
- Verify TLS against the HomeWizard CA and the expected device identity. Build SSL contexts in an executor.
- Pair through the config flow using a distinct local user name. Token rejection starts reauthentication; network failures mark data unavailable and retry with bounded backoff.
- Await reader cancellation and socket closure on unload, failed setup and shutdown. Never close the shared HTTP session.
- Use the official library models so existing sensor and external-meter mappings remain compatible.
- Keep strings.json and translations/en.json identical; redact tokens and identifying data from diagnostics.
- Run pytest, Ruff and strict mypy using requirements_test.txt after changes. Mock devices; physical hardware testing is a separate installation check.
