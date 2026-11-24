"""
Build Home Assistant MQTT auto-discovery config payloads.

All entities share one state topic and use value_template to extract their field.

Two types of discovery:
  build_discovery_configs()       — pack-level entities, published once on connect
  build_node_discovery_configs()  — per-node entities, published when a new node is first seen

With individual CellMate-K/J hardware (one unit per cell), each node = one cell, so
node_N_volt_min / node_N_volt_max are the individual cell voltages for cell N.
With a K9 board, each node covers all cells on the board (volt_min/max are pack aggregate).

Reference: https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery
"""

import json

DEVICE_MANUFACTURER = "Batrium"
DEVICE_MODEL        = "WatchMon Core"
DEVICE_NAME         = "Batrium"


ADDON_VERSION       = "1.2.0"
ADDON_URL           = "https://github.com/krogo/batrium-ha-addon"


def _device(system_name: str, sys_id: int) -> dict:
    return {
        "identifiers":       [f"batrium_{sys_id}"],
        "name":              DEVICE_NAME,
        "manufacturer":      DEVICE_MANUFACTURER,
        "model":             DEVICE_MODEL,
        "sw_version":        f"krogo/batrium-ha-addon {ADDON_VERSION}",
        "configuration_url": ADDON_URL,
    }


def _make_sensor(
    uid: str,
    name: str,
    state_topic: str,
    avail_topic: str,
    device: dict,
    field: str,
    unit: str | None = None,
    device_class: str | None = None,
    state_class: str | None = None,
    icon: str | None = None,
) -> tuple[str, str]:
    # value_json.get() keeps a missing field rendering as "unknown" instead of
    # emitting a J2 'dict object has no attribute' warning every publish cycle.
    # The state JSON only contains fields for message types the WatchMon
    # actually transmits, so older/partial hardware (e.g. WatchMon1 without
    # 0x3E33/0x3F34) must not log-spam HA. See issue #2.
    payload: dict = {
        "unique_id":          uid,
        "name":               name,
        "state_topic":        state_topic,
        "value_template":     f"{{{{ value_json.get('{field}') }}}}",
        "availability_topic": avail_topic,
        "device":             device,
    }
    if unit:
        payload["unit_of_measurement"] = unit
    if device_class:
        payload["device_class"] = device_class
    if state_class:
        payload["state_class"] = state_class
    if icon:
        payload["icon"] = icon
    return (f"homeassistant/sensor/{uid}/config", json.dumps(payload))


def _make_binary_sensor(
    uid: str,
    name: str,
    state_topic: str,
    avail_topic: str,
    device: dict,
    field: str,
    device_class: str | None = None,
) -> tuple[str, str]:
    # .get() so a missing field yields "" (falsy -> "OFF") without a warning.
    payload: dict = {
        "unique_id":          uid,
        "name":               name,
        "state_topic":        state_topic,
        "value_template":     f"{{% if value_json.get('{field}') %}}ON{{% else %}}OFF{{% endif %}}",
        "payload_on":         "ON",
        "payload_off":        "OFF",
        "availability_topic": avail_topic,
        "device":             device,
    }
    if device_class:
        payload["device_class"] = device_class
    return (f"homeassistant/binary_sensor/{uid}/config", json.dumps(payload))


def build_discovery_configs(system_name: str, sys_id: int) -> list[tuple[str, str]]:
    """
    Pack-level entities derived from 0x3E33 and 0x3F33.
    Published once on MQTT connect (and again on reconnect).
    """
    state_topic = f"batrium/{system_name}/state"
    avail_topic = f"batrium/{system_name}/availability"
    device      = _device(system_name, sys_id)
    configs: list[tuple[str, str]] = []

    def uid(s):
        return f"batrium_{sys_id}_{s}"

    def S(suffix, name, field, **kw):
        configs.append(_make_sensor(uid(suffix), name, state_topic, avail_topic, device, field, **kw))

    def B(suffix, name, field, **kw):
        configs.append(_make_binary_sensor(uid(suffix), name, state_topic, avail_topic, device, field, **kw))

    # --- Pack voltages (from 0x3E33) ---
    S("volt_min_v",    "Cell Voltage Min",    "volt_min_v",    unit="V", device_class="voltage", state_class="measurement", icon="mdi:lightning-bolt")
    S("volt_max_v",    "Cell Voltage Max",    "volt_max_v",    unit="V", device_class="voltage", state_class="measurement", icon="mdi:lightning-bolt")
    S("volt_avg_v",    "Cell Voltage Avg",    "volt_avg_v",    unit="V", device_class="voltage", state_class="measurement", icon="mdi:lightning-bolt")
    S("volt_spread_v", "Cell Voltage Spread", "volt_spread_v", unit="V", device_class="voltage", state_class="measurement", icon="mdi:arrow-expand-vertical")

    # --- Temperature (from 0x3E33) ---
    S("temp_min_c", "Cell Temp Min", "temp_min_c", unit="°C", device_class="temperature", icon="mdi:thermometer-low")
    S("temp_max_c", "Cell Temp Max", "temp_max_c", unit="°C", device_class="temperature", icon="mdi:thermometer-high")
    S("temp_avg_c", "Cell Temp Avg", "temp_avg_c", unit="°C", device_class="temperature", icon="mdi:thermometer")

    # --- Shunt telemetry (from 0x3F34 StatusShunt, 300ms) ---
    S("shunt_a",        "Shunt Current",   "shunt_a",        unit="A",  device_class="current",  state_class="measurement", icon="mdi:current-dc")
    S("shunt_kw",       "Pack Power",      "shunt_kw",       unit="kW", device_class="power",    state_class="measurement", icon="mdi:lightning-bolt")
    S("shunt_volt_v",   "Pack Voltage",    "shunt_volt_v",   unit="V",  device_class="voltage",  state_class="measurement", icon="mdi:battery")
    S("supply_volt_v",  "WatchMon Supply Voltage", "supply_volt_v", unit="V", device_class="voltage", state_class="measurement", icon="mdi:power-plug")
    S("ambient_temp_c", "Ambient Temp",    "ambient_temp_c", unit="°C", device_class="temperature", state_class="measurement", icon="mdi:thermometer")
    S("shunt_temp_c",   "Shunt Temp",      "shunt_temp_c",   unit="°C", device_class="temperature", state_class="measurement", icon="mdi:thermometer")
    S("capacity_to_full_ah",  "Capacity To Full",  "capacity_to_full_ah",  unit="Ah", state_class="measurement", icon="mdi:battery-arrow-up")
    S("capacity_to_empty_ah", "Capacity To Empty", "capacity_to_empty_ah", unit="Ah", state_class="measurement", icon="mdi:battery-arrow-down")
    S("est_mins_to_full",  "Estimated Time To Full",  "est_mins_to_full",  unit="min", device_class="duration", icon="mdi:timer-sand")
    S("est_mins_to_empty", "Estimated Time To Empty", "est_mins_to_empty", unit="min", device_class="duration", icon="mdi:timer-sand")
    S("avg_charge_a",    "Average Charge Current",    "avg_charge_a",    unit="A", device_class="current", state_class="measurement", icon="mdi:current-dc")
    S("avg_discharge_a", "Average Discharge Current", "avg_discharge_a", unit="A", device_class="current", state_class="measurement", icon="mdi:current-dc")
    S("avg_net_a",       "Average Net Current",       "avg_net_a",       unit="A", device_class="current", state_class="measurement", icon="mdi:current-dc")
    S("shunt_soc_pct",  "SOC (Coulomb)",   "shunt_soc_pct",  unit="%",  device_class="battery",  icon="mdi:battery-high", state_class="measurement")

    # --- Bypass current (from 0x3E33) ---
    S("min_bypass_a", "Min Bypass Current",  "min_bypass_a", unit="A", device_class="current", icon="mdi:lightning-bolt-circle")
    S("max_bypass_a", "Peak Bypass Current", "max_bypass_a", unit="A", device_class="current", icon="mdi:lightning-bolt-circle")

    # --- SOC (from 0x3F34) ---
    S("soc_pct", "State of Charge", "soc_pct", unit="%", device_class="battery", icon="mdi:battery", state_class="measurement")

    # --- Balancing / cell counts (from 0x3E33) ---
    S("bypass_count",          "Cells Balancing",       "bypass_count",          icon="mdi:scale-balance")
    S("cells_in_system",       "Cells in System",       "cells_in_system",       state_class=None, icon="mdi:counter")
    S("cells_active",          "Cells Active",          "cells_active",          state_class=None, icon="mdi:check-circle-outline")
    S("cells_overdue",         "Cells Overdue",         "cells_overdue",         icon="mdi:alert-circle-outline")
    S("min_bypass_session_ah", "Min Bypass Session",    "min_bypass_session_ah", unit="Ah", icon="mdi:battery-charging")
    S("max_bypass_session_ah", "Max Bypass Session",    "max_bypass_session_ah", unit="Ah", icon="mdi:battery-charging")

    # --- Node count (from 0x4232) ---
    S("nodes_online", "Nodes Online", "nodes_online", icon="mdi:network")

    # --- System status (text, from 0x3F33) ---
    S("op_status",    "System Status",  "op_status_name",    state_class=None, icon="mdi:information-outline")
    S("shunt_status", "Shunt Status",   "shunt_status_name", state_class=None, icon="mdi:current-dc")

    # --- Binary: balancing / contactors / relays ---
    B("balancing_active",  "Balancing Active",  "balancing_active")
    B("soc_recal_needed",  "SOC Recalibration Needed", "soc_recal_needed")

    return configs


def build_node_discovery_configs(
    node_id: int, system_name: str, sys_id: int
) -> list[tuple[str, str]]:
    """
    Per-cell entities, published when a cell/node is first seen.

    With CellMate-K/J or K9 on newer firmware (≥ 2.15), each node = one cell.
    volt_min == volt_max so a single 'cell_N_volt' entity is sufficient.
    State field names use cell_ prefix (e.g. cell_3_volt).
    """
    state_topic = f"batrium/{system_name}/state"
    avail_topic = f"batrium/{system_name}/availability"
    device      = _device(system_name, sys_id)
    configs: list[tuple[str, str]] = []
    n = node_id

    def uid(s):
        return f"batrium_{sys_id}_cell{n}_{s}"

    def S(suffix, name, field, **kw):
        configs.append(_make_sensor(uid(suffix), name, state_topic, avail_topic, device, field, **kw))

    def B(suffix, name, field, **kw):
        configs.append(_make_binary_sensor(uid(suffix), name, state_topic, avail_topic, device, field, **kw))

    label = f"Cell {n}"

    # Single voltage (volt_min == volt_max for single-cell nodes)
    S("volt_v",        f"{label} Voltage",       f"cell_{n}_volt",          unit="V",  device_class="voltage",     state_class="measurement", icon="mdi:lightning-bolt")

    # Temperature
    S("temp_c",        f"{label} Temp",           f"cell_{n}_temp_c",        unit="°C", device_class="temperature", icon="mdi:thermometer")
    S("bypass_temp_c", f"{label} Bypass Temp",    f"cell_{n}_bypass_temp_c", unit="°C", device_class="temperature", icon="mdi:thermometer-alert")

    # Bypass / balancing
    S("bypass_a",          f"{label} Bypass Current", f"cell_{n}_bypass_a",          unit="A",  device_class="current", icon="mdi:lightning-bolt-circle")
    S("bypass_session_ah", f"{label} Bypass Session", f"cell_{n}_bypass_session_ah", unit="Ah", icon="mdi:battery-charging")

    # Status
    S("op_status", f"{label} Status", f"cell_{n}_op_status_name", state_class=None, icon="mdi:information-outline")

    B("in_bypass",  f"{label} In Bypass", f"cell_{n}_in_bypass")
    B("is_overdue", f"{label} Overdue",   f"cell_{n}_is_overdue")

    return configs


def build_node_delete_configs(node_id: int, sys_id: int) -> list[tuple[str, str]]:
    """
    Return (topic, '') pairs to delete old 'node_N' entities from HA.
    Publishing empty retained payload removes the entity from auto-discovery.
    Call on startup to clean up entities created before the cell_ rename.
    """
    n = node_id
    old = [
        ("sensor",        "volt_min"),
        ("sensor",        "volt_max"),
        ("sensor",        "temp_c"),
        ("sensor",        "bypass_temp_c"),
        ("sensor",        "bypass_ma"),
        ("sensor",        "bypass_mah"),
        ("sensor",        "op_status"),
        ("binary_sensor", "in_bypass"),
        ("binary_sensor", "is_overdue"),
    ]
    return [
        (f"homeassistant/{domain}/batrium_{sys_id}_node{n}_{field}/config", "")
        for domain, field in old
    ]


# ---------------------------------------------------------------------------
# Fork additions


def build_unit_migration_deletes(sys_id: int) -> list[tuple[str, str]]:
    """
    Empty retained configs for entities whose unit changed in this fork, so a
    user upgrading from upstream doesn't keep orphaned mV/mA/W sensors.
    """
    pack = ["volt_min", "volt_max", "volt_avg", "volt_spread",
            "shunt_ma", "shunt_watt", "shunt_volt_mv",
            "min_bypass_ma", "max_bypass_ma"]
    cell = ["volt", "bypass_ma", "bypass_mah"]
    out = [(f"homeassistant/sensor/batrium_{sys_id}_{f}/config", "") for f in pack]
    # 3233's kWh fields turned out to be daily, not lifetime (1.1.1).
    out += [(f"homeassistant/sensor/batrium_{sys_id}_{f}/config", "")
            for f in ("lifetime_kwh_charge", "lifetime_kwh_discharge")]
    # 0x3F33-only entities; re-registered on receipt of 0x3F33 (1.1.1).
    out += [(f"homeassistant/binary_sensor/batrium_{sys_id}_{f}/config", "")
            for f in ("contactor_battery", "load_contactor", "expansion_battery",
                      "relay_1", "relay_2", "relay_3", "relay_4")]
    for n in range(1, 33):
        out += [(f"homeassistant/sensor/batrium_{sys_id}_cell{n}_{f}/config", "") for f in cell]
    return out


def build_group_discovery_configs(
    msg_type: int, system_name: str, sys_id: int
) -> list[tuple[str, str]]:
    """Entities for one of the fork's message groups; published on first receipt."""
    from batrium.protocol import (
        MSG_SYSTEM_DISCOVERY, MSG_REMOTE_STATUS, MSG_LIVE_DISPLAY,
        MSG_DAILY_SESSION, MSG_SHUNT_METRICS, MSG_STATUS_FAST,
    )

    state_topic = f"batrium/{system_name}/state"
    avail_topic = f"batrium/{system_name}/availability"
    device      = _device(system_name, sys_id)
    configs: list[tuple[str, str]] = []

    def S(suffix, name, field, **kw):
        configs.append(_make_sensor(f"batrium_{sys_id}_{suffix}", name, state_topic, avail_topic, device, field, **kw))

    def B(suffix, name, field, **kw):
        configs.append(_make_binary_sensor(f"batrium_{sys_id}_{suffix}", name, state_topic, avail_topic, device, field, **kw))

    def status_entities():
        S("system_op_status", "WatchMon Status", "system_op_status_name", icon="mdi:information-outline")
        B("critical_batt_ok", "Critical Battery OK", "critical_batt_ok")
        B("heat_on", "Heating On", "heat_on")
        B("cool_on", "Cooling On", "cool_on")
        S("system_time", "WatchMon Clock", "system_time", device_class="timestamp", icon="mdi:clock-outline")

    if msg_type == MSG_SYSTEM_DISCOVERY:
        status_entities()
        S("charge_rate",    "Charge Power State",    "charge_rate_name",    icon="mdi:battery-charging")
        S("discharge_rate", "Discharge Power State", "discharge_rate_name", icon="mdi:battery-minus")
        S("auth_mode",      "Auth Mode",             "auth_mode_name",      icon="mdi:account-key")
        S("cmu_poller",     "Cell Poller Mode",      "cmu_poller_mode_name", icon="mdi:access-point-network")
        S("firmware",       "Firmware Version",      "firmware_version",    icon="mdi:chip")

    elif msg_type == MSG_LIVE_DISPLAY:
        status_entities()
        B("charge_on",         "Charging Allowed",    "charge_on")
        B("charge_limited",    "Charge Limited",      "charge_limited")
        B("discharge_on",      "Discharging Allowed", "discharge_on")
        B("discharge_limited", "Discharge Limited",   "discharge_limited")
        S("critical_events", "Critical Events", "critical_events", icon="mdi:alert")

    elif msg_type == MSG_REMOTE_STATUS:
        for side, label in (("remote_charge", "Charger"), ("remote_discharge", "Inverter")):
            S(f"{side}_target_v",  f"{label} Target Voltage", f"{side}_target_v",  unit="V", device_class="voltage", icon="mdi:target")
            S(f"{side}_target_a",  f"{label} Target Current", f"{side}_target_a",  unit="A", device_class="current", icon="mdi:target")
            S(f"{side}_actual_v",  f"{label} Actual Voltage", f"{side}_actual_v",  unit="V", device_class="voltage", state_class="measurement")
            S(f"{side}_actual_a",  f"{label} Actual Current", f"{side}_actual_a",  unit="A", device_class="current", state_class="measurement")
            S(f"{side}_temp_c",    f"{label} Temp",           f"{side}_temp_c",    unit="°C", device_class="temperature", state_class="measurement")
            S(f"{side}_last_rx",   f"{label} Last Message",   f"{side}_last_rx",   device_class="timestamp")

    elif msg_type == MSG_DAILY_SESSION:
        S("daily_kwh_charge",    "Today Energy In",  "daily_kwh_charge",
          unit="kWh", device_class="energy", state_class="total_increasing", icon="mdi:battery-arrow-up")
        S("daily_kwh_discharge", "Today Energy Out", "daily_kwh_discharge",
          unit="kWh", device_class="energy", state_class="total_increasing", icon="mdi:battery-arrow-down")
        S("daily_ah_charge",    "Today Charge",    "daily_ah_charge",    unit="Ah", state_class="total_increasing")
        S("daily_ah_discharge", "Today Discharge", "daily_ah_discharge", unit="Ah", state_class="total_increasing")
        S("daily_peak_charge_a",    "Today Peak Charge Current",    "daily_peak_charge_a",    unit="A", device_class="current")
        S("daily_peak_discharge_a", "Today Peak Discharge Current", "daily_peak_discharge_a", unit="A", device_class="current")
        S("daily_volt_min_v", "Today Min Cell Voltage", "daily_volt_min_v", unit="V", device_class="voltage")
        S("daily_volt_max_v", "Today Max Cell Voltage", "daily_volt_max_v", unit="V", device_class="voltage")
        S("daily_temp_min_c", "Today Min Temp", "daily_temp_min_c", unit="°C", device_class="temperature")
        S("daily_temp_max_c", "Today Max Temp", "daily_temp_max_c", unit="°C", device_class="temperature")
        S("daily_soc_min_pct", "Today Min SOC", "daily_soc_min_pct", unit="%", device_class="battery")
        S("daily_soc_max_pct", "Today Max SOC", "daily_soc_max_pct", unit="%", device_class="battery")

    elif msg_type == MSG_STATUS_FAST:
        # Real relay/contactor states. Only registered if the WatchMon sends
        # 0x3F33; a WatchMon Core on current firmware does not.
        B("contactor_battery", "Battery Contactor", "contactor_batt")
        B("load_contactor",    "Load Contactor",    "load_contactor")
        B("expansion_battery", "Expansion Battery", "expansion_battery_on")
        for i in range(1, 5):
            B(f"relay_{i}", f"Relay {i}", f"relay_{i}")

    elif msg_type == MSG_SHUNT_METRICS:
        S("shunt_soc_cycles",    "SOC Cycles",            "shunt_soc_cycles", state_class="total_increasing", icon="mdi:counter")
        S("shunt_last_lo_recal", "Last Low SOC Recal",    "shunt_last_lo_recal", device_class="timestamp")
        S("shunt_last_hi_recal", "Last High SOC Recal",   "shunt_last_hi_recal", device_class="timestamp")

    return configs
