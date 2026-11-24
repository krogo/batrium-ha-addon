"""
Batrium WatchMon UDP → MQTT bridge for Home Assistant.

Binds UDP port 18542, parses Batrium broadcast packets, publishes to MQTT
with Home Assistant auto-discovery (no manual HA configuration needed).

Per-node voltage entities are dynamically created when new nodes are first seen:
  - CellMate-K/J (one unit per cell): node N = cell N; node_N_volt_min/max = cell voltage
  - CellMate K9 (all cells on one board): one node = pack aggregate min/max

Run as HA addon:  /app/run.sh (reads /data/options.json)
Run for local dev: python3 batrium/main.py  (uses env vars or defaults)
"""

import asyncio
import errno
import json
import logging
import os
import sys
import time

# Allow running as a script: `python3 batrium/main.py`
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from batrium.protocol import (  # noqa: E402
    parse_header,
    parse_4232,
    parse_415a,
    parse_3e33,
    parse_3f33,
    parse_3f34,
    parse_5732,
    parse_4932,
    parse_3233,
    parse_5432,
    parse_7832,
    MSG_CELL_NODE,
    MSG_CELL_NODE_STATUS,
    MSG_STATUS_RAPID_OLD,
    MSG_CELL_STATS,
    MSG_STATUS_FAST,
    MSG_STATUS_SHUNT,
    MSG_SYSTEM_DISCOVERY,
    MSG_REMOTE_STATUS,
    MSG_LIVE_DISPLAY,
    MSG_DAILY_SESSION,
    MSG_SHUNT_METRICS,
    MSG_NAMES,
    MIN_LEN,
    KNOWN_UNDECODED,
    NODE_STATUS_NAMES,
)
from batrium.discovery import (  # noqa: E402
    ADDON_URL,
    build_discovery_configs,
    build_node_discovery_configs,
    build_node_delete_configs,
    build_group_discovery_configs,
    build_unit_migration_deletes,
)
from batrium.publisher import BatriumPublisher  # noqa: E402
from batrium.capture import RawCapture  # noqa: E402

# Messages handled generically by _handle_group (added in this fork).
GROUP_PARSERS = {
    MSG_SYSTEM_DISCOVERY: parse_5732,
    MSG_REMOTE_STATUS:    parse_4932,
    MSG_LIVE_DISPLAY:     parse_3233,
    MSG_DAILY_SESSION:    parse_5432,
    MSG_SHUNT_METRICS:    parse_7832,
}

# Pack-aggregate keys. When a message reports these directly, the per-cell
# fallback in _derive_pack_from_cells stops writing them.
PACK_KEYS = {
    "volt_min_v", "volt_max_v", "volt_avg_v",
    "temp_min_c", "temp_max_c", "temp_avg_c",
    "cells_active", "bypass_count",
}


def load_config() -> dict:
    """
    Load addon options.

    Priority (highest first):
    1. Environment variables (upper-cased key names) — for local dev
    2. /data/options.json — provided by HA Supervisor
    3. Built-in defaults
    """
    cfg = {
        "mqtt_host":     "core-mosquitto",
        "mqtt_port":     1883,
        "mqtt_username": "",
        "mqtt_password": "",
        "udp_port":      18542,
        "system_name":   "Stanley",
        "log_level":     "info",
        "raw_capture":   False,
    }

    options_path = "/data/options.json"
    if os.path.exists(options_path):
        with open(options_path) as f:
            cfg.update(json.load(f))

    for key, default in cfg.items():
        env_val = os.environ.get(key.upper())
        if env_val is not None:
            if isinstance(default, bool):        # check before int: bool is an int
                cfg[key] = env_val.strip().lower() in ("1", "true", "yes", "on")
            elif isinstance(default, int):
                cfg[key] = int(env_val)
            else:
                cfg[key] = env_val

    return cfg


# Delivery watchdog timings (see BatriumUdpProtocol._check_delivery).
UNDELIVERED_GRACE_S  = 30.0    # let the broker connect race the first packets
UNDELIVERED_REPEAT_S = 300.0   # then keep saying it, so any log slice contains it


class BatriumUdpProtocol(asyncio.DatagramProtocol):
    """
    asyncio UDP protocol: parses incoming Batrium broadcast packets and pushes
    parsed values into the publisher's state dict.

    On first sight of a new node_id (from 0x4232), publishes per-node MQTT
    auto-discovery configs so HA immediately creates entities for that node.
    """

    def __init__(self, publisher: BatriumPublisher, system_name: str, sys_id: int,
                 capture: "RawCapture | None" = None):
        self._publisher    = publisher
        self._system_name  = system_name
        self._sys_id       = sys_id
        self._seen_nodes: set[int] = set()
        self._seen_msg_types: set[int] = set()
        self._capture = capture
        self._discovered_groups: set[int] = set()
        self._cells: dict[int, dict] = {}          # node_id -> {"v": volts, "t": °C}
        self._authoritative: set[str] = set()      # pack keys a pack message has supplied
        self._first_packet_at: float | None = None
        self._last_undelivered_warn: float | None = None

    def _check_delivery(self) -> None:
        """
        Warn while Batrium data is arriving but MQTT has never connected.

        This is the gap that made a broker rejecting our credentials look like a
        Batrium problem: the addon logs cheerfully about every node and message
        type it finds, while not one byte reaches Home Assistant. Read from the
        top, that log says the addon is working.

        Deliberately REPEATS rather than firing once. Whoever is debugging will
        grab some arbitrary slice of the log to paste into an issue, and the
        slice needs to contain the reason — a single line at startup scrolls away
        and is never seen again.
        """
        if self._publisher.ever_connected:
            return

        now = time.monotonic()
        if self._first_packet_at is None:
            self._first_packet_at = now
            return
        # Don't shout during normal startup: the broker connect and the first
        # packets race each other, and a warning at t=0 would be noise.
        if now - self._first_packet_at < UNDELIVERED_GRACE_S:
            return
        if (self._last_undelivered_warn is not None
                and now - self._last_undelivered_warn < UNDELIVERED_REPEAT_S):
            return

        self._last_undelivered_warn = now
        logging.getLogger(__name__).warning(
            "Receiving Batrium data, but MQTT has never connected — NOTHING has "
            "reached Home Assistant and no entities will appear. Any 'node "
            "discovered' lines above were queued, not delivered. See the 'MQTT "
            "connect ... failed' error for the reason; the Batrium side is fine."
        )

    def _report_first_sight(self, msg_type: int, size: int) -> None:
        """
        Announce each message type the first time it arrives.

        Which messages a WatchMon transmits varies by generation and firmware,
        and an entity that never populates looks identical whether the data was
        never sent, arrived malformed, or arrived in a type we don't decode.
        Logging one line per type at INFO makes the difference visible in the
        default log, so a report of "these entities are always unknown" carries
        its own diagnosis rather than needing a second round-trip.

        Once per type, not per packet: these arrive every 300ms.
        """
        logger = logging.getLogger(__name__)
        name = MSG_NAMES.get(msg_type)

        if name is None:
            known = KNOWN_UNDECODED.get(msg_type)
            if known:
                logger.debug("Ignoring 0x%04X %s (%d bytes) — documented, not decoded",
                             msg_type, known, size)
            else:
                # Not in Batrium's published payload list either (a WatchMon Core also
                # sends 0x3031, 0x3033, 0x5931, 0x5932, 0x5A31, 0x5A32, 0x5B31).
                # Info, not warning: there is no layout to decode them from.
                logger.info("Undocumented message type 0x%04X (%d bytes) — ignored",
                            msg_type, size)
            return

        need = MIN_LEN.get(msg_type)
        if need is not None and size < need:
            logger.warning(
                "%s arrived but is too short to decode: %d bytes, need %d. Its "
                "entities will stay unknown. Please report this at %s/issues, "
                "quoting your WatchMon model and firmware version.",
                name, size, need, ADDON_URL,
            )
            return

        logger.info("Receiving %s (%d bytes)", name, size)

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        if len(data) < 8:
            return
        header = parse_header(data)
        if not header:
            return
        msg_type, _sys_id = header

        if msg_type not in self._seen_msg_types:
            self._seen_msg_types.add(msg_type)
            self._report_first_sight(msg_type, len(data))

        # Data is arriving; is any of it actually getting out?
        self._check_delivery()

        if self._capture is not None and msg_type not in MSG_NAMES:
            self._capture.packet(msg_type, data)

        if msg_type == MSG_CELL_NODE_STATUS:
            # Preferred: all cells in one atomic snapshot
            self._handle_415a(data)

        elif msg_type == MSG_CELL_NODE:
            # Fallback / also triggers per-node discovery
            self._handle_4232(data)

        elif msg_type in (MSG_CELL_STATS, MSG_STATUS_RAPID_OLD):
            self._handle_3e33(data)

        elif msg_type in (MSG_STATUS_FAST, MSG_STATUS_SHUNT):
            self._handle_3f3x(data, msg_type)

        elif msg_type in GROUP_PARSERS:
            self._handle_group(data, msg_type)

    # ------------------------------------------------------------------
    # Pack aggregates

    def _set_pack(self, updates: dict) -> None:
        """
        Publish pack-level values from a message that reports them directly
        (0x3E33, 0x5732, 0x3233). Those keys are then never overwritten by
        the cell-derived fallback below.
        """
        self._authoritative.update(updates.keys())
        self._publisher.update_state(updates)

    def _derive_pack_from_cells(self) -> None:
        """
        Fill pack min/max/avg voltage and temperature from per-cell data, for
        WatchMons that send per-node messages but none of the pack summaries.
        This is what the Node-RED flow did by hand. Only keys no pack message
        has supplied are written.
        """
        if not self._cells:
            return
        volts = [c["v"] for c in self._cells.values()]
        temps = [c["t"] for c in self._cells.values()]
        derived = {
            "volt_min_v":  min(volts),
            "volt_max_v":  max(volts),
            "volt_avg_v":  round(sum(volts) / len(volts), 3),
            "temp_min_c":  min(temps),
            "temp_max_c":  max(temps),
        }
        updates = {k: v for k, v in derived.items() if k not in self._authoritative}
        if "volt_min_v" in updates or "volt_max_v" in updates:
            updates["volt_spread_v"] = round(max(volts) - min(volts), 3)
        if updates:
            self._publisher.update_state(updates)

    def _discover_node(self, n: int, via: str) -> None:
        if n in self._seen_nodes:
            return
        self._seen_nodes.add(n)
        node_configs = build_node_discovery_configs(n, self._system_name, self._sys_id)
        sent = self._publisher.publish_node_discovery(node_configs)
        logging.getLogger(__name__).info(
            "New node discovered via %s: id=%d — %s %d entities",
            via, n, "published" if sent else "QUEUED (MQTT not connected):",
            len(node_configs),
        )

    # ------------------------------------------------------------------
    # Handlers

    def _handle_415a(self, data: bytes) -> None:
        parsed = parse_415a(data)
        if not parsed:
            return

        state_updates = {}
        for node in parsed["nodes"]:
            n = node["node_id"]
            self._discover_node(n, "0x415A")
            self._cells[n] = {"v": node["volt_min_v"], "t": node["temp_min_c"]}
            state_updates.update({
                f"cell_{n}_volt":           node["volt_min_v"],   # volt_min == volt_max for single-cell nodes
                f"cell_{n}_temp_c":         node["temp_min_c"],
                f"cell_{n}_bypass_a":       node["bypass_a"],
                f"cell_{n}_op_status_name": node["op_status_name"],
                f"cell_{n}_in_bypass":      node["in_bypass"],
            })

        state_updates["nodes_online"] = len(self._seen_nodes)
        self._publisher.update_state(state_updates)
        self._derive_pack_from_cells()

    def _handle_4232(self, data: bytes) -> None:
        parsed = parse_4232(data)
        if not parsed:
            return

        n = parsed["node_id"]
        self._discover_node(n, "0x4232")
        self._cells[n] = {"v": parsed["volt_min_v"], "t": parsed["temp_c"]}

        # Flatten per-cell fields into state with cell_{id}_ prefix
        self._publisher.update_state({
            f"cell_{n}_volt":              parsed["volt_min_v"],   # volt_min == volt_max for single-cell nodes
            f"cell_{n}_temp_c":            parsed["temp_c"],
            f"cell_{n}_bypass_temp_c":     parsed["bypass_temp_c"],
            f"cell_{n}_bypass_a":          parsed["bypass_a"],
            f"cell_{n}_bypass_session_ah": parsed["bypass_session_ah"],
            f"cell_{n}_op_status_name":    parsed["op_status_name"],
            f"cell_{n}_in_bypass":         parsed["in_bypass"],
            f"cell_{n}_is_overdue":        parsed["is_overdue"],
            "nodes_online":                len(self._seen_nodes),
        })
        self._derive_pack_from_cells()

    def _handle_3e33(self, data: bytes) -> None:
        parsed = parse_3e33(data)
        if not parsed:
            return
        self._set_pack({
            "volt_min_v":            parsed["volt_min_v"],
            "volt_max_v":            parsed["volt_max_v"],
            "volt_avg_v":            parsed["volt_avg_v"],
            "volt_spread_v":         round(parsed["volt_max_v"] - parsed["volt_min_v"], 3),
            "temp_min_c":            parsed["temp_min_c"],
            "temp_max_c":            parsed["temp_max_c"],
            "temp_avg_c":            parsed["temp_avg_c"],
            "min_bypass_a":          parsed["min_bypass_a"],
            "max_bypass_a":          parsed["max_bypass_a"],
            "bypass_count":          parsed["bypass_count"],
            "cells_overdue":         parsed["cells_overdue"],
            "cells_active":          parsed["cells_active"],
            "cells_in_system":       parsed["cells_in_system"],
            "min_bypass_session_ah": parsed["min_bypass_session_ah"],
            "max_bypass_session_ah": parsed["max_bypass_session_ah"],
            "balancing_active":      parsed["bypass_count"] > 0,
        })

    def _handle_3f3x(self, data: bytes, msg_type: int) -> None:
        if msg_type == MSG_STATUS_SHUNT:
            parsed = parse_3f34(data)
            if not parsed:
                return
            # 0x3F34 = StatusShunt (FW ≥ 2.15): every decoded field goes to state
            self._publisher.update_state(parsed)
            return

        parsed = parse_3f33(data)
        if not parsed:
            return
        self._discover_group(MSG_STATUS_FAST)
        self._publisher.update_state({
            "op_status":            parsed["op_status"],
            "op_status_name":       parsed["op_status_name"],
            "soc_pct":              parsed["soc_pct"],
            "shunt_status":         parsed["shunt_status"],
            "shunt_status_name":    parsed["shunt_status_name"],
            "expansion_battery_on": parsed["expansion_battery_on"],
            "relay_1":              parsed["relay_1"],
            "relay_2":              parsed["relay_2"],
            "relay_3":              parsed["relay_3"],
            "relay_4":              parsed["relay_4"],
            "contactor_batt":       parsed["contactor_batt"],
            "load_contactor":       parsed["load_contactor"],
        })

    def _discover_group(self, msg_type: int) -> None:
        """Register a message group's entities with HA, once, on first receipt."""
        if msg_type in self._discovered_groups:
            return
        self._discovered_groups.add(msg_type)
        configs = build_group_discovery_configs(msg_type, self._system_name, self._sys_id)
        if configs:
            sent = self._publisher.publish_node_discovery(configs)
            logging.getLogger(__name__).info(
                "%s: %s %d entities", MSG_NAMES[msg_type],
                "published" if sent else "QUEUED (MQTT not connected):", len(configs),
            )

    def _handle_group(self, data: bytes, msg_type: int) -> None:
        """
        Messages added in this fork. Entities for each are registered with HA
        the first time that message decodes, so a WatchMon that never sends,
        say, 0x4932 doesn't get a page of permanently-unknown entities.
        """
        parsed = GROUP_PARSERS[msg_type](data)
        if not parsed:
            return

        self._discover_group(msg_type)

        pack_keys = PACK_KEYS & parsed.keys()
        if pack_keys:
            self._set_pack({k: parsed[k] for k in pack_keys})
            if {"volt_min_v", "volt_max_v"} <= pack_keys:
                self._set_pack({"volt_spread_v": round(parsed["volt_max_v"] - parsed["volt_min_v"], 3)})
        self._publisher.update_state({k: v for k, v in parsed.items() if k not in pack_keys})

    def error_received(self, exc: Exception) -> None:
        logging.getLogger(__name__).error("UDP error: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        logging.getLogger(__name__).warning("UDP connection lost: %s", exc)


async def main() -> None:
    cfg = load_config()

    log_level = getattr(logging, cfg["log_level"].upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(name)-30s %(levelname)-8s %(message)s",
    )
    logger = logging.getLogger(__name__)
    logger.info("Batrium UDP addon starting — system_name=%s", cfg["system_name"])

    sys_id = 0   # placeholder; unique_ids are stable via system_name + sys_id combo

    # Delete old 'node_N' entities (replaced by 'cell_N') — empty retained payload removes from HA
    delete_configs = []
    for n in range(1, 17):
        delete_configs.extend(build_node_delete_configs(n, sys_id))
    # Remove entities whose unit changed in this fork (mV/mA/W -> V/A/kW).
    # Their unique_ids changed too, so HA treats the new ones as fresh sensors
    # instead of mixing old mV history with new V readings.
    delete_configs.extend(build_unit_migration_deletes(sys_id))

    pack_discovery = build_discovery_configs(cfg["system_name"], sys_id)

    publisher = BatriumPublisher(
        host=cfg["mqtt_host"],
        port=cfg["mqtt_port"],
        username=cfg["mqtt_username"],
        password=cfg["mqtt_password"],
        system_name=cfg["system_name"],
        discovery_configs=delete_configs + pack_discovery,  # deletions published first
    )
    publisher.start()

    capture = RawCapture(publisher, cfg["system_name"]) if cfg["raw_capture"] else None

    loop = asyncio.get_event_loop()
    logger.info("Binding UDP on 0.0.0.0:%d", cfg["udp_port"])
    try:
        transport, _protocol = await loop.create_datagram_endpoint(
            lambda: BatriumUdpProtocol(publisher, cfg["system_name"], sys_id, capture),
            local_addr=("0.0.0.0", cfg["udp_port"]),
            allow_broadcast=True,
        )
    except OSError as exc:
        # Common cause: another Batrium UDP listener (e.g. a Node-RED flow)
        # still holds the port. The WatchMon always broadcasts to 18542
        # (fixed in firmware), so stop the other listener — do not change
        # udp_port to sidestep it.
        #
        # errno.EADDRINUSE rather than a literal: it is 98 on Linux (the addon
        # container) but 48 on macOS/BSD, and the strerror text differs too —
        # musl says "Address in use", glibc "Address already in use". Matching
        # on either literal silently misses the case when running the module
        # directly for local development.
        if exc.errno == errno.EADDRINUSE:
            logger.error(
                "UDP port %d is already in use — another program is listening "
                "on it. If you have a Batrium/WatchMon UDP listener running "
                "(Node-RED flow, another instance of this addon, etc.), stop "
                "it: this addon replaces it and both cannot bind the same "
                "port. Note the WatchMon broadcasts to a fixed port, so "
                "changing udp_port here will not help.",
                cfg["udp_port"],
            )
            sys.exit(1)
        raise

    logger.info("Listening for Batrium WatchMon broadcasts...")
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down")
        transport.close()
        publisher.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
