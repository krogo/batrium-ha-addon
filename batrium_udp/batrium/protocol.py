"""
Batrium WatchMon UDP protocol parser.

Source: watchmon-wifi-udp-protocol-v0.5.pdf + WatchMonUdpListener /payload/*.js
All multi-byte integers are little-endian.

Hardware note on cell voltages via UDP:
  - CellMate-K / CellMate-J (one unit per cell): each presents as a separate node.
    With 8 cells you get 8 x 0x4232 messages. node.volt_min == node.volt_max == cell voltage.
  - CellMate K9 (one board for all 8 cells): presents as ONE node.
    volt_min/volt_max in 0x4232 are the pack min/max, not individual cells.
  Individual cell voltages are only available via Batrium's MQTT feature (not yet released).
"""

import struct
import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


# --- Units -------------------------------------------------------------------
# This fork publishes in the units the original Node-RED flow used, so existing
# dashboards/automations keep their scaling: volts (V), amps (A), kilowatts (kW),
# amp-hours (Ah), kWh, °C, minutes, percent. Raw WatchMon units are converted
# here, in one place, so main.py and discovery.py never see mV/mA/W.

def _mv_to_v(mv: float) -> float:
    return round(mv / 1000.0, 3)


def _ma_to_a(ma: float) -> float:
    return round(ma / 1000.0, 2)


def _w_to_kw(w: float) -> float:
    return round(w / 1000.0, 2)


def _f32_div(raw: float, div: float) -> float:
    """
    Divide a float32 field without rounding to a fixed number of decimals:
    keep the 7 significant digits a float32 actually carries, and drop the
    conversion noise (1.2345 not 1.2345000505447388).
    """
    return float(f"{raw / div:.7g}")


def _local_tz():
    """
    Home Assistant's timezone. The Supervisor passes it to add-ons as TZ;
    fall back to the container's local zone, then UTC.
    """
    name = os.environ.get("TZ")
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            _LOGGER.warning("Unknown TZ %r; WatchMon timestamps will assume UTC", name)
            return timezone.utc
    return datetime.now().astimezone().tzinfo or timezone.utc


_LOCAL_TZ = _local_tz()


def _epoch(ts: int) -> str | None:
    """
    WatchMon "epoch" seconds -> ISO-8601 for HA timestamp sensors; 0 = never.

    The WatchMon counts in LOCAL wall-clock time, not UTC: on a live WM5 in
    America/New_York the clock field read 12:43 when it was 12:43 EST. So
    the seconds are decoded as a wall-clock time and given HA's timezone.
    """
    if ts <= 0:
        return None
    wall = datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)
    return wall.replace(tzinfo=_LOCAL_TZ).isoformat()

_LOGGER = logging.getLogger(__name__)

HEADER_START = 0x3A   # ':'
HEADER_SEP   = 0x2C   # ','

# Message types
MSG_CELL_NODE        = 0x4232  # Per-node full info; 300ms
MSG_CELL_NODE_STATUS = 0x415A  # All nodes in one packet (array); 300ms — preferred source
MSG_STATUS_RAPID_OLD = 0x3E5A  # Pack stats, SW ≤ 1.0.29; 300ms
MSG_CELL_STATS       = 0x3E33  # Pack stats, SW ≥ 2.15;   300ms
MSG_STATUS_FAST      = 0x3F33  # SOC, relays, contactors (older FW)
MSG_STATUS_SHUNT     = 0x3F34  # ShuntCurrent/Voltage/Power/SOC (FW ≥ 2.15); 300ms
MSG_SYSTEM_DISCOVERY = 0x5732  # System status, cell summary, shunt summary; 2s
MSG_REMOTE_STATUS    = 0x4932  # Charger/inverter remote (CAN) target & actual; 2s
MSG_LIVE_DISPLAY     = 0x3233  # Live display: status flags, cell summary (FW ≥ 2.15); 2s
MSG_DAILY_SESSION    = 0x5432  # Today's session: kWh/Ah in/out, peaks; 20s
MSG_SHUNT_METRICS    = 0x7832  # Shunt SOC cycles and recalibration times; 30s

MSG_NAMES = {
    MSG_CELL_NODE:        "0x4232 CellNode",
    MSG_CELL_NODE_STATUS: "0x415A CellNodeStatus",
    MSG_STATUS_RAPID_OLD: "0x3E5A PackStats (legacy)",
    MSG_CELL_STATS:       "0x3E33 PackStats",
    MSG_STATUS_FAST:      "0x3F33 SystemStatus",
    MSG_STATUS_SHUNT:     "0x3F34 ShuntStatus",
    MSG_SYSTEM_DISCOVERY: "0x5732 SystemDiscovery",
    MSG_REMOTE_STATUS:    "0x4932 RemoteStatus",
    MSG_LIVE_DISPLAY:     "0x3233 LiveDisplay",
    MSG_DAILY_SESSION:    "0x5432 DailySession",
    MSG_SHUNT_METRICS:    "0x7832 ShuntMetrics",
}

# Message types Batrium documents (WatchMonUdpListener/payload) that this add-on
# deliberately does not decode: mostly setup/configuration echoes that only
# change when the WatchMon is reconfigured. Seen on a live WM5 broadcasting
# alongside the telemetry. Logged once at debug, not as a warning.
KNOWN_UNDECODED = {
    0x4033: "StatusSlow",
    0x4733: "StatusControlLogic",
    0x4A36: "HwSystemSetup",
    0x4B36: "HwCellGroupSetup",
    0x4C34: "HwShuntSetup",
    0x4D34: "HwExpansionSetup",
    0x4E33: "ControlRemoteSetup",
    0x4F33: "ControlCriticalSetup",
    0x5033: "ControlChargeSetup",
    0x5158: "ControlDischargeSetup",
    0x5233: "ControlThermalSetup",
    0x5335: "HwIntegrationSetup",
    0x5634: "LifetimeMetricsB",
    0x5635: "LifetimeMetricsA",
    0x6133: "StatusComms",
    0x6831: "QuickSessionHist",
}

# Smallest packet each parser can decode. Single source of truth: the parsers
# guard on these, and main.py reports the first packet of each type against
# them — so a message that arrives too short to decode is announced in the log
# rather than dropped where nobody can see it.
MIN_LEN = {
    MSG_CELL_NODE:        52,
    MSG_CELL_NODE_STATUS: 12,   # header + array header; per-node extent checked in parse_415a
    MSG_STATUS_RAPID_OLD: 48,
    MSG_CELL_STATS:       48,
    MSG_STATUS_FAST:      62,
    MSG_STATUS_SHUNT:     50,
    MSG_SYSTEM_DISCOVERY: 50,
    MSG_REMOTE_STATUS:    62,
    MSG_LIVE_DISPLAY:     51,   # fields used end at SystemTime (47..50)
    MSG_DAILY_SESSION:    69,
    MSG_SHUNT_METRICS:    32,
}

# WatchMon system operating status, as carried by 0x5732 and 0x3233.
# Source: WatchMonUdpListener/payload/Msg_5732_SystemDiscovery.js
SYSTEM_OP_STATUS_NAMES = {
    0:  "Simulator",
    1:  "Idle",
    2:  "Discharging",
    3:  "SoC Empty",
    4:  "Charging",
    5:  "Full",
    6:  "Timeout",
    7:  "Critical Pending",
    8:  "Critical Offline",
    9:  "MQTT Offline",
    10: "Auth Setup",
    11: "Shunt Timeout",
}

AUTH_MODE_NAMES = {0: "Default", 1: "Technician", 2: "Factory"}

POWER_RATE_NAMES = {0: "Off", 2: "Limited Power", 4: "Normal Power"}

CMU_POLLER_MODE_NAMES = {
    0:  "Idle",
    1:  "Normal",
    2:  "Collection Start",
    3:  "Collection Running",
    4:  "Sync Start",
    5:  "Sync Running",
    6:  "Network Test Start",
    7:  "Bypass Test Start",
    8:  "Bypass Test Running",
    9:  "Network Test Running",
    10: "Reboot All Start",
    11: "Rebooting All Devices",
    12: "Simulator Start",
    13: "Simulator Running",
}

OP_STATUS_NAMES = {
    0: "Timeout",
    1: "Idle",
    2: "Charging",
    3: "Discharging",
    4: "Full",
    5: "Empty",
    7: "CriticalPending",
    8: "CriticalOffline",
}

NODE_STATUS_NAMES = {
    0:  "None",
    1:  "HighVolt",
    2:  "HighTemp",
    3:  "Ok",
    4:  "Timeout",
    5:  "LowVolt",
    6:  "Disabled",
    7:  "InBypass",
    8:  "InitialBypass",
    9:  "FinalBypass",
    10: "MissingSetup",
    11: "NoConfig",
    12: "CellOutLimits",
}

SHUNT_STATUS_NAMES = {
    0: "Timeout",
    1: "Discharging",
    2: "Idle",
    4: "Charging",
}


def parse_header(data: bytes):
    """
    Parse the 8-byte packet header.
    Returns (msg_type: int, sys_id: int) or None if invalid.
    """
    if len(data) < 8:
        return None
    if data[0] != HEADER_START or data[3] != HEADER_SEP:
        return None
    msg_type = struct.unpack_from("<H", data, 1)[0]
    sys_id   = struct.unpack_from("<H", data, 4)[0]
    return msg_type, sys_id


def parse_4232(data: bytes) -> dict | None:
    """
    Parse Cell Node Full Info (0x4232, 52 bytes, 300ms).

    With individual CellMate-K/J units (one per cell), there is one 0x4232 message
    per cell per broadcast cycle; volt_min == volt_max == that cell's voltage.
    With a K9 board, there is one message covering all cells on the board.
    """
    if len(data) < MIN_LEN[MSG_CELL_NODE]:
        _LOGGER.debug("0x4232 too short: %d bytes", len(data))
        return None
    op = data[20]
    return {
        "node_id":       data[8],
        "volt_min_v":    _mv_to_v(struct.unpack_from("<h", data, 10)[0]),
        "volt_max_v":    _mv_to_v(struct.unpack_from("<h", data, 12)[0]),
        "temp_c":        data[14] - 40,                             # °C (min cell temp on node)
        "bypass_temp_c": data[15] - 40,                             # °C (bypass resistor temp)
        "bypass_a":      _ma_to_a(struct.unpack_from("<h", data, 16)[0]),  # 0 = not balancing
        "op_status":     op,
        "op_status_name": NODE_STATUS_NAMES.get(op, f"Unknown({op})"),
        "in_bypass":     op in (7, 8, 9),
        "is_overdue":    bool(data[21]),
        "bypass_session_ah": _f32_div(struct.unpack_from("<f", data, 47)[0], 1000.0),
    }


def parse_3e33(data: bytes) -> dict | None:
    """
    Parse Cell Stats / Combined Status Rapid (0x3E33 or 0x3E5A, 48 bytes, 300ms).
    0x3E5A is SW ≤ 1.0.29; 0x3E33 is SW ≥ 2.15 — identical field layout.

    Pack-level aggregate across all nodes.
    Source: WatchMonUdpListener/payload/Msg_3e33_StatusCellStats.js

    NOTE: shunt/pack current is NOT in this packet.  It lives in 0x3233 (LiveDisplay)
    or equivalent on newer firmware.  Offset 38 = MinBypassSession (Ah), 42 = MaxBypassSession.
    """
    if len(data) < MIN_LEN[MSG_CELL_STATS]:
        _LOGGER.debug("0x3E33/3E5A too short: %d bytes", len(data))
        return None
    return {
        "volt_min_v":            _mv_to_v(struct.unpack_from("<h", data,  8)[0]),
        "volt_max_v":            _mv_to_v(struct.unpack_from("<h", data, 10)[0]),
        "temp_min_c":            data[14] - 40,                            # °C
        "temp_max_c":            data[15] - 40,                            # °C
        "temp_avg_c":            data[30] - 40,                            # °C
        "min_bypass_a":          _ma_to_a(struct.unpack_from("<h", data, 18)[0]),
        "max_bypass_a":          _ma_to_a(struct.unpack_from("<h", data, 20)[0]),  # peak balancing
        "volt_avg_v":            _mv_to_v(struct.unpack_from("<h", data, 28)[0]),
        "bypass_count":          data[33],                                  # cells actively balancing
        "cells_overdue":         data[34],                                  # CMU comms failures
        "cells_active":          data[35],                                  # cells responding
        "cells_in_system":       data[36],                                  # total cell count
        "min_bypass_session_ah": _f32_div(struct.unpack_from("<f", data, 38)[0], 1000.0),
        "max_bypass_session_ah": _f32_div(struct.unpack_from("<f", data, 42)[0], 1000.0),
    }


# Same field layout — alias so callers can reference the old message type by name
parse_3e5a = parse_3e33


def parse_415a(data: bytes) -> dict | None:
    """
    Parse Cell Node Status array (0x415A, 100 bytes, 300ms).

    All nodes in a single packet — the preferred source for per-cell voltages.
    With K9 hardware on firmware ≥ ~2.15, the K9 exposes each cell as a
    separate node, so 8S = 8 entries with individual cell voltages.

    Payload layout (after 8-byte packet header):
      [0]     USN / sequence counter
      [1]     cells_in_system
      [2]     unknown (0x01)
      [3]     node_count (number of entries that follow)
      [4 + n*11 + 0]   node_id
      [4 + n*11 + 1]   per-node USN
      [4 + n*11 + 2:4] volt_min  (int16le, mV)
      [4 + n*11 + 4:6] volt_max  (int16le, mV)
      [4 + n*11 + 6]   temp_min  (raw, -40 = °C)
      [4 + n*11 + 7]   temp_max  (raw)
      [4 + n*11 + 8:10] bypass current (int16le, mA → A)
      [4 + n*11 + 10]  op_status
    """
    min_len = MIN_LEN[MSG_CELL_NODE_STATUS]  # header + 4-byte array header
    if len(data) < min_len:
        _LOGGER.debug("0x415A too short: %d bytes", len(data))
        return None

    payload      = data[8:]
    cells_in_sys = payload[1]
    node_count   = payload[3]

    if len(payload) < 4 + node_count * 11:
        _LOGGER.debug("0x415A truncated: expected %d bytes payload, got %d",
                      4 + node_count * 11, len(payload))
        return None

    nodes = []
    for i in range(node_count):
        base = 4 + i * 11
        op   = payload[base + 10]
        nodes.append({
            "node_id":    payload[base],
            "volt_min_v": _mv_to_v(struct.unpack_from("<h", payload, base + 2)[0]),
            "volt_max_v": _mv_to_v(struct.unpack_from("<h", payload, base + 4)[0]),
            "temp_min_c": payload[base + 6] - 40,                           # °C
            "temp_max_c": payload[base + 7] - 40,
            "bypass_a":   _ma_to_a(struct.unpack_from("<h", payload, base + 8)[0]),
            "op_status":  op,
            "op_status_name": NODE_STATUS_NAMES.get(op, f"Unknown({op})"),
            "in_bypass":  op in (7, 8, 9),
        })

    return {
        "cells_in_system": cells_in_sys,
        "node_count":      node_count,
        "nodes":           nodes,
    }


def parse_3f34(data: bytes) -> dict | None:
    """
    Parse Status Shunt (0x3F34, 50 bytes, 300ms).
    Used by firmware ≥ 2.15 instead of 0x3F33.

    Source: WatchMonUdpListener/payload/Msg_3f34_StatusShunt.js
    All offsets from packet start (including 8-byte header).

      data[12:14]  ShuntVoltage  int16le  /100 = V  (×10 → mV for HA)
      data[14:18]  ShuntCurrent  float32  raw = mA  (+charge, -discharge)
      data[18:22]  ShuntPowerVA  float32  raw = W   (+charge, -discharge)
      data[22:24]  ShuntSOC      int16le  /100 = %  (Coulomb-counted)
      data[24]     WatchMon SOC raw  (raw×0.5)−5 = %
      data[25]     op_status
      (no relays here — see note in the return dict)
    """
    if len(data) < MIN_LEN[MSG_STATUS_SHUNT]:
        _LOGGER.debug("0x3F34 too short: %d bytes", len(data))
        return None
    soc_raw   = data[24]
    shunt_ma  = round(struct.unpack_from("<f", data, 14)[0], 1)

    # Derive op_status from shunt current direction (confirmed reliable).
    # data[25] was tentatively mapped to op_status but is unconfirmed — don't use it.
    if shunt_ma < -50:
        op, op_name = 3, "Discharging"
    elif shunt_ma > 50:
        op, op_name = 2, "Charging"
    else:
        op, op_name = 1, "Idle"

    flags = data[25]
    return {
        "op_status":       op,
        "op_status_name":  op_name,
        "soc_pct":         round((soc_raw * 0.5) - 5, 1),   # WatchMon estimate
        "shunt_soc_pct":   round(struct.unpack_from("<h", data, 22)[0] / 100.0, 2),  # Coulomb counted
        "shunt_volt_v":    round(struct.unpack_from("<H", data, 12)[0] / 100.0, 2),  # uint16 (see 1.0.3)
        "shunt_a":         _ma_to_a(shunt_ma),                # negative=discharge positive=charge
        "shunt_kw":        _w_to_kw(struct.unpack_from("<f", data, 18)[0]),  # same sign
        # Previously undecoded fields, per Batrium's Msg_3f34_StatusShunt.js:
        "supply_volt_v":         round(struct.unpack_from("<H", data, 8)[0] / 100.0, 2),  # WatchMon supply
        "ambient_temp_c":        data[10] - 40,
        "shunt_temp_c":          data[11] - 40,
        "capacity_to_full_ah":   round(struct.unpack_from("<f", data, 26)[0] / 1000.0, 2),
        "capacity_to_empty_ah":  round(struct.unpack_from("<f", data, 30)[0] / 1000.0, 2),
        "est_mins_to_full":      struct.unpack_from("<h", data, 34)[0],
        "est_mins_to_empty":     struct.unpack_from("<h", data, 36)[0],
        "avg_charge_a":          round(struct.unpack_from("<f", data, 38)[0] / 1000.0, 2),
        "avg_discharge_a":       round(struct.unpack_from("<f", data, 42)[0] / 1000.0, 2),
        "avg_net_a":             round(struct.unpack_from("<f", data, 46)[0] / 1000.0, 2),
        # Bit order taken from Batrium's comments (bit0 first); unverified on hardware.
        "soc_recal_needed":      bool(flags & 0x0C),
        # Upstream read relay_1..3 from data[39:42]. Those bytes are inside the
        # avg_charge_a float (38..41); on a live WM5 all three read "on" with no
        # relays wired. Relays are only real in 0x3F33.
    }


def parse_3f33(data: bytes) -> dict | None:
    """
    Parse Combined Status Fast (0x3F33, 80 bytes, 1.55s).
    SOC, system op status, shunt status, relay and contactor states.
    """
    if len(data) < MIN_LEN[MSG_STATUS_FAST]:
        _LOGGER.debug("0x3F33 too short: %d bytes", len(data))
        return None
    soc_raw     = data[32]
    shunt_st    = data[43]
    op          = data[23]
    return {
        "op_status":           op,
        "op_status_name":      OP_STATUS_NAMES.get(op, f"Unknown({op})"),
        "soc_pct":             round((soc_raw * 0.5) - 5, 1),   # raw 10=0%, 200=95%
        "shunt_status":        shunt_st,
        "shunt_status_name":   SHUNT_STATUS_NAMES.get(shunt_st, f"Unknown({shunt_st})"),
        "expansion_battery_on": bool(data[46]),
        "relay_1":             bool(data[50]),
        "relay_2":             bool(data[51]),
        "relay_3":             bool(data[52]),
        "relay_4":             bool(data[53]),
        "contactor_batt":      bool(data[60]),
        "load_contactor":      bool(data[61]),
    }


# ---------------------------------------------------------------------------
# Messages added in this fork. Layouts from Batrium's WatchMonUdpListener
# (github.com/Batrium/WatchMonUdpListener/tree/master/payload); offsets below
# are from the start of the packet, including the 8-byte header.


def parse_5732(data: bytes) -> dict | None:
    """
    System Discovery (0x5732, 50 bytes, 2s).

    The WatchMon's own system status (not inferred from shunt current), the
    power-rate states it is commanding, and a cell summary. Useful on units
    that do not transmit 0x3E33.
    """
    if len(data) < MIN_LEN[MSG_SYSTEM_DISCOVERY]:
        return None
    op     = data[24]
    auth   = data[25]
    chg    = data[27]
    dis    = data[28]
    poller = data[40]
    return {
        "system_code":            data[8:16].split(b"\x00", 1)[0].decode("ascii", "replace"),
        "firmware_version":       struct.unpack_from("<h", data, 16)[0],
        "hardware_version":       struct.unpack_from("<h", data, 18)[0],
        "system_time":            _epoch(struct.unpack_from("<i", data, 20)[0]),
        "system_op_status":       op,
        "system_op_status_name":  SYSTEM_OP_STATUS_NAMES.get(op, f"Unknown({op})"),
        "auth_mode_name":         AUTH_MODE_NAMES.get(auth, f"Unknown({auth})"),
        "critical_batt_ok":       bool(data[26]),
        "charge_rate_name":       POWER_RATE_NAMES.get(chg, f"Unknown({chg})"),
        "discharge_rate_name":    POWER_RATE_NAMES.get(dis, f"Unknown({dis})"),
        "heat_on":                bool(data[29]),
        "cool_on":                bool(data[30]),
        "volt_min_v":             _mv_to_v(struct.unpack_from("<h", data, 31)[0]),
        "volt_max_v":             _mv_to_v(struct.unpack_from("<h", data, 33)[0]),
        "volt_avg_v":             _mv_to_v(struct.unpack_from("<h", data, 35)[0]),
        "temp_min_c":             data[37] - 40,
        "cells_active":           data[38],
        "cmu_poller_mode_name":   CMU_POLLER_MODE_NAMES.get(poller, f"Unknown({poller})"),
        "shunt_status":           data[48],
        "shunt_status_name":      SHUNT_STATUS_NAMES.get(data[48], f"Unknown({data[48]})"),
    }


def parse_4932(data: bytes) -> dict | None:
    """
    Control Remote Status (0x4932, 62 bytes, 2s).

    What the WatchMon is asking a CAN-connected charger/inverter for (target)
    and what that device reports back (actual).

    Scaling: Batrium's reference applies none. Amps ÷10 (0.1 A/bit), matching
    the Node-RED flow this fork replaces. Volts ÷10 (0.1 V/bit), confirmed on
    a live 14S pack: targets of 574 / 462 = 57.4 V / 46.2 V = 4.1 / 3.3 V per
    cell. VA is passed raw; unverified.
    """
    if len(data) < MIN_LEN[MSG_REMOTE_STATUS]:
        return None

    def side(base: int, temp_off: int, prefix: str) -> dict:
        tv, ta, tva, av, aa, ava = struct.unpack_from("<hhhhhh", data, base)
        return {
            f"{prefix}_temp_c":         data[temp_off] - 40,
            f"{prefix}_target_v":       round(tv / 10.0, 1),
            f"{prefix}_target_a":       round(ta / 10.0),
            f"{prefix}_target_va":      tva,
            f"{prefix}_actual_v":       round(av / 10.0, 1),
            f"{prefix}_actual_a":       round(aa / 10.0, 1),
            f"{prefix}_actual_va":      ava,
            f"{prefix}_last_rx":        _epoch(struct.unpack_from("<I", data, base + 20)[0]),
        }

    out = {
        "can_rx_status_ticks":  data[8],
        "can_rx_unknown_ticks": data[9],
        "can_tx_cmd_ticks":     data[10],
    }
    out.update(side(12, 11, "remote_charge"))
    out.update(side(38, 37, "remote_discharge"))
    return out


def parse_3233(data: bytes) -> dict | None:
    """
    Live Display (0x3233, 57 bytes, 2s, FW ≥ 2.15).

    Its kWh in/out fields (38/42) are NOT lifetime: on a live WM5 they equal
    0x5432's daily kWh to within the 20 s between the two messages, so they are
    not published (the daily sensors cover them).
    """
    if len(data) < MIN_LEN[MSG_LIVE_DISPLAY]:
        return None
    op     = data[8]
    b10    = data[10]
    b11    = data[11]
    return {
        "system_op_status":       op,
        "system_op_status_name":  SYSTEM_OP_STATUS_NAMES.get(op, f"Unknown({op})"),
        # Bit order per Batrium's comments (bit0 = first listed). Unverified.
        "critical_batt_ok":       bool(b10 & 0x01),
        "heat_on":                bool(b10 & 0x08),
        "cool_on":                bool(b10 & 0x10),
        "charge_on":              bool(b11 & 0x01),
        "charge_limited":         bool(b11 & 0x02),
        "discharge_on":           bool(b11 & 0x04),
        "discharge_limited":      bool(b11 & 0x08),
        "volt_min_v":             _mv_to_v(struct.unpack_from("<h", data, 12)[0]),
        "volt_max_v":             _mv_to_v(struct.unpack_from("<h", data, 14)[0]),
        "volt_avg_v":             _mv_to_v(struct.unpack_from("<h", data, 16)[0]),
        "temp_min_c":             data[18] - 40,
        "temp_max_c":             data[19] - 40,
        "temp_avg_c":             data[20] - 40,
        "bypass_count":           data[21],
        "critical_events":        data[46],
        "system_time":            _epoch(struct.unpack_from("<i", data, 47)[0]),
    }


def parse_5432(data: bytes) -> dict | None:
    """Daily Session (0x5432, 69 bytes, 20s): today's totals and extremes."""
    if len(data) < MIN_LEN[MSG_DAILY_SESSION]:
        return None
    return {
        "daily_volt_min_v":        _mv_to_v(struct.unpack_from("<h", data, 8)[0]),
        "daily_volt_max_v":        _mv_to_v(struct.unpack_from("<h", data, 10)[0]),
        "daily_temp_min_c":        data[16] - 40,
        "daily_temp_max_c":        data[17] - 40,
        "daily_pack_volt_min_v":   round(struct.unpack_from("<H", data, 18)[0] / 100.0, 2),
        "daily_pack_volt_max_v":   round(struct.unpack_from("<H", data, 20)[0] / 100.0, 2),
        # Batrium's JS uses (x-5)/2 here but x/2-5 everywhere else; the latter
        # matches the SOC field it is a min/max of, so use it.
        "daily_soc_min_pct":       round(data[22] * 0.5 - 5, 1),
        "daily_soc_max_pct":       round(data[23] * 0.5 - 5, 1),
        "daily_peak_charge_a":     round(struct.unpack_from("<h", data, 40)[0] / 100.0, 2),
        "daily_peak_discharge_a":  round(struct.unpack_from("<h", data, 42)[0] / 100.0, 2),
        "daily_critical_events":   data[44],
        "daily_ah_charge":         round(struct.unpack_from("<f", data, 53)[0] / 1000.0, 2),
        "daily_ah_discharge":      round(struct.unpack_from("<f", data, 57)[0] / 1000.0, 2),
        "daily_kwh_charge":        round(struct.unpack_from("<f", data, 61)[0] / 1000.0, 3),
        "daily_kwh_discharge":     round(struct.unpack_from("<f", data, 65)[0] / 1000.0, 3),
    }


def parse_7832(data: bytes) -> dict | None:
    """Hardware Shunt Metrics (0x7832, 32 bytes, 30s): SOC cycles, recal times."""
    if len(data) < MIN_LEN[MSG_SHUNT_METRICS]:
        return None
    return {
        "shunt_soc_cycles":      struct.unpack_from("<h", data, 10)[0],
        "shunt_last_lo_recal":   _epoch(struct.unpack_from("<I", data, 16)[0]),
        "shunt_last_hi_recal":   _epoch(struct.unpack_from("<I", data, 20)[0]),
    }
