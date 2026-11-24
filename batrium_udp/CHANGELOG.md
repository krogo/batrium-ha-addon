## 1.1.1 (fork)

Corrections from the first run against a live WatchMon Core + CellMate K9:

- Fix: charger/inverter (0x4932) voltages are 0.1 V/bit, not 0.01 — targets read 5.74 / 4.62 V instead of 57.4 / 46.2 V.
- Fix: WatchMon timestamps are local wall-clock time, not UTC; they were shown shifted by the UTC offset. Now given Home Assistant's timezone. Adds `tzdata`.
- Fix: "Lifetime Energy In/Out" (0x3233) were the same daily counters as 0x5432's "Today Energy In/Out". Removed; the daily sensors remain and work in the Energy dashboard.
- Fix: Relay 1–3 are no longer read from 0x3F34, where those bytes belong to a current value (all read "on"). Relay, contactor and expansion-battery entities are now registered only when 0x3F33 — the message that actually carries them — arrives. Existing ones are removed on start.

## 1.1.0 (fork)

- Add: decoders for 0x5732 SystemDiscovery, 0x4932 RemoteStatus, 0x3233 LiveDisplay, 0x5432 DailySession and 0x7832 ShuntMetrics, from Batrium's published layouts. Their entities are registered the first time each message arrives, so message types your WatchMon never sends don't leave permanently-unknown entities.
- Add: the rest of 0x3F34 — supply voltage, ambient and shunt temperature, capacity to full/empty, estimated minutes to full/empty, average charge/discharge/net current, SOC-recalibration flag.
- Add: lifetime and daily kWh in/out as `energy` / `total_increasing` sensors, usable in the Energy dashboard.
- Add: pack min/max/avg cell voltage and min/max temperature are derived from the per-cell messages when the WatchMon sends no pack summary (0x3E33/0x5732/0x3233). A pack message always wins over the derived value.
- Change: units now V, A, kW, Ah (were mV, mA, W, mAh), matching the Node-RED flow this replaces. Unique IDs changed with the units and the old entities are removed on start, so HA history isn't mixed across scales.
- Fix: bypass session values from 0x3E33 were raw mAh labelled Ah. Now divided by 1000, per Batrium's reference.
- Fix: cell status names for codes 4, 6, 10, 11, 12 (Timeout, Disabled, MissingSetup, NoConfig, CellOutLimits) were reported as Unknown.
- Change: message types Batrium documents but this add-on doesn't decode (setup echoes, lifetime/history metrics, comms status) are logged once at debug instead of as a warning. Types not in Batrium's published list are logged once at info. A live WatchMon Core sends about 20 of these, which previously filled the log with warnings.
- Change: `image:` removed from config.yaml, so Home Assistant builds the add-on locally from this repository.
- Note: the 0x3F34 "relay" fields read bytes 39–41, which Batrium's layout puts inside the average-charge-current float. Left in place so existing entities don't vanish, but they are almost certainly not relays.

## 1.0.6

- Fix: an MQTT connection the broker refuses is now explained instead of numbered. `MQTT connect failed (rc=5)` becomes "the broker refused the connection as not authorised", plus what to check — `rc=4` and `rc=5` mean the credentials were rejected, and with the Mosquitto addon that is almost always a blank or non-existent `mqtt_username`/`mqtt_password`.
- Fix: the addon no longer claims to have published entities it has not published. Discovery configs for a newly-seen node are queued when MQTT is down (correct, and unchanged) but were logged as "published ... entities" regardless. An addon that had never once reached the broker still produced a log full of apparent success, so the visible symptom pointed at the Batrium side while the real fault was the broker.
- Add: while data is arriving and MQTT has never connected, the addon says so — "Receiving Batrium data, but MQTT has never connected — NOTHING has reached Home Assistant". It repeats every 5 minutes rather than firing once, because whoever is debugging pastes an arbitrary slice of the log and the reason needs to be in it.
- Internal: a refused connect logged twice per retry (once from the connect callback, once from the disconnect callback that follows it). The second is now debug-level; the first says everything useful.

## 1.0.5

- Add: the log now names each Batrium message type the first time it arrives — `Receiving 0x3E33 PackStats (48 bytes)`. Which messages a WatchMon transmits varies by generation and firmware, and until now an entity that never populated looked the same whether the data was never sent, arrived too short to decode, or arrived in a message type this addon does not recognise. One line per type at startup, at the default log level, tells you which.
- Add: a message that arrives too short to decode, or in an unrecognised type, is now reported as a warning asking you to raise an issue. Previously both were discarded at debug level, so a WatchMon whose data was being dropped was indistinguishable from one that sent nothing.
- Internal: minimum packet lengths moved to `protocol.MIN_LEN`, so the parsers and the log report against the same numbers.

## 1.0.4

- Fix: missing state fields no longer log-spam HA. Auto-discovery templates now use `value_json.get()`, so fields absent from the state JSON (older WatchMon generations that don't transmit 0x3E33/0x3F34, e.g. WatchMon1) render as "unknown" instead of raising a "dict object has no attribute" warning every second. Closes #2.
- Fix: a port conflict at startup (e.g. a Node-RED UDP flow still bound to 18542) now produces an actionable error message explaining how to resolve it, instead of an unhandled `OSError: Address in use` traceback and restart loop. Addresses #3.
- Docs: clarified `udp_port` (the WatchMon transmits to a fixed port; the listener is the only configurable side) and added troubleshooting entries for port conflicts and template warnings.

## 1.0.3

- Fix Pack Voltage reading negative on high-voltage systems (e.g. 48V+ packs). The shunt voltage field was decoded as a signed integer, causing overflow above ~327V. Now correctly decoded as unsigned.

## 1.0.2

- Initial public release.
