"""
Hand-built packets with known values at the offsets given in Batrium's
WatchMonUdpListener layouts; checks the fork decodes them to the expected
published units. Run: python3 -I tests/test_decoders.py <path-to-batrium_udp>
"""
import os
import struct
import sys

# WatchMon timestamps are local wall-clock; pin the zone so results are fixed.
os.environ["TZ"] = "America/New_York"
sys.path.insert(0, sys.argv[1])
from batrium import protocol as p  # noqa: E402
from batrium import main as m      # noqa: E402


def pkt(mt: int, n: int) -> bytearray:
    b = bytearray(n)
    b[0] = 0x3A
    struct.pack_into("<H", b, 1, mt)
    b[3] = 0x2C
    return b


def eq(got, want, label):
    assert got == want, f"{label}: got {got!r}, want {want!r}"


# --- 0x5732 SystemDiscovery -------------------------------------------------
b = pkt(0x5732, 50)
b[8:16] = b"SYS7518\x00"
struct.pack_into("<hh", b, 16, 215, 5)
struct.pack_into("<i", b, 20, 1_700_000_000)
b[24] = 4; b[25] = 1; b[26] = 1; b[27] = 4; b[28] = 2; b[29] = 0; b[30] = 1
struct.pack_into("<hhh", b, 31, 3301, 3347, 3322)
b[37] = 40 + 18; b[38] = 14; b[40] = 1; b[41] = 2 * 80 + 10
struct.pack_into("<h", b, 42, 4650)
struct.pack_into("<f", b, 44, -12_340.0)
b[48] = 4
r = p.parse_5732(bytes(b))
eq(r["system_code"], "SYS7518", "5732 code")
eq(r["firmware_version"], 215, "5732 fw")
eq(r["system_op_status_name"], "Charging", "5732 op")
eq(r["auth_mode_name"], "Technician", "5732 auth")
eq(r["charge_rate_name"], "Normal Power", "5732 chg")
eq(r["discharge_rate_name"], "Limited Power", "5732 dis")
eq((r["heat_on"], r["cool_on"]), (False, True), "5732 heat/cool")
eq((r["volt_min_v"], r["volt_max_v"], r["volt_avg_v"]), (3.301, 3.347, 3.322), "5732 volts")
eq(r["temp_min_c"], 18, "5732 temp")
eq(r["cells_active"], 14, "5732 cells")
eq(r["cmu_poller_mode_name"], "Normal", "5732 poller")
eq(r["shunt_status_name"], "Charging", "5732 shunt status")
# 1_700_000_000 decodes as wall-clock 2023-11-14 22:13:20 local (EST, -05:00)
eq(r["system_time"], "2023-11-14T22:13:20-05:00", "5732 time (local wall clock)")

# --- 0x4932 RemoteStatus ----------------------------------------------------
b = pkt(0x4932, 62)
b[8:11] = bytes([5, 6, 7]); b[11] = 40 + 25
struct.pack_into("<hhhhhh", b, 12, 574, 3000, 5000, 558, 987, 4900)
struct.pack_into("<I", b, 32, 1_700_000_000)
b[37] = 40 + 30
struct.pack_into("<hhhhhh", b, 38, 4800, 2000, 9000, 4810, -1500, 7000)
struct.pack_into("<I", b, 58, 0)
r = p.parse_4932(bytes(b))
eq(r["remote_charge_temp_c"], 25, "4932 chg temp")
eq(r["remote_charge_target_v"], 57.4, "4932 chg tv (0.1 V/bit, live-confirmed)")
eq(r["remote_charge_actual_v"], 55.8, "4932 chg av")
eq(r["remote_charge_target_a"], 300, "4932 chg ta (Node-RED x/10, integer)")
eq(r["remote_charge_actual_a"], 98.7, "4932 chg aa")
eq(r["remote_charge_last_rx"], "2023-11-14T22:13:20-05:00", "4932 chg rx")
eq(r["remote_discharge_temp_c"], 30, "4932 dis temp")
eq(r["remote_discharge_target_a"], 200, "4932 dis ta")
eq(r["remote_discharge_actual_a"], -150.0, "4932 dis aa")
eq(r["remote_discharge_last_rx"], None, "4932 dis rx (0 = never)")

# --- 0x3F34 ShuntStatus -----------------------------------------------------
b = pkt(0x3F34, 50)
struct.pack_into("<H", b, 8, 1250); b[10] = 40 + 21; b[11] = 40 + 27
struct.pack_into("<H", b, 12, 5320)
struct.pack_into("<f", b, 14, -25_678.0)
struct.pack_into("<f", b, 18, -1_366.0)
struct.pack_into("<h", b, 22, 8734); b[24] = 2 * 87 + 10; b[25] = 0b0100
struct.pack_into("<ff", b, 26, 35_500.0, 244_250.0)
struct.pack_into("<hh", b, 34, 120, 570)
struct.pack_into("<fff", b, 38, 10_120.0, 15_330.0, -5_210.0)
r = p.parse_3f34(bytes(b))
eq(r["supply_volt_v"], 12.5, "3f34 supply")
eq((r["ambient_temp_c"], r["shunt_temp_c"]), (21, 27), "3f34 temps")
eq(r["shunt_volt_v"], 53.2, "3f34 V")
eq(r["shunt_a"], -25.68, "3f34 A (Node-RED x/1000, 2dp)")
eq(r["shunt_kw"], -1.37, "3f34 kW (Node-RED x/1000, 2dp)")
eq(r["shunt_soc_pct"], 87.34, "3f34 SOC hi-res")
eq(r["soc_pct"], 87.0, "3f34 SOC lo-res")
eq(r["soc_recal_needed"], True, "3f34 recal flag")
eq((r["capacity_to_full_ah"], r["capacity_to_empty_ah"]), (35.5, 244.25), "3f34 Ah")
eq((r["est_mins_to_full"], r["est_mins_to_empty"]), (120, 570), "3f34 mins")
eq((r["avg_charge_a"], r["avg_discharge_a"], r["avg_net_a"]), (10.12, 15.33, -5.21), "3f34 avgs")
eq(r["op_status_name"], "Discharging", "3f34 derived op")
assert not any(k.startswith("relay_") for k in r), "3f34 must not publish fake relays"

# --- 0x3233 LiveDisplay -----------------------------------------------------
b = pkt(0x3233, 57)
b[8] = 2; b[10] = 0b0000_1001; b[11] = 0b0000_0101
struct.pack_into("<hhh", b, 12, 3280, 3310, 3295)
b[18:22] = bytes([40 + 15, 40 + 22, 40 + 19, 3])
struct.pack_into("<f", b, 38, 1_234_567.0)
struct.pack_into("<f", b, 42, 1_111_111.0)
b[46] = 2
struct.pack_into("<i", b, 47, 1_700_000_000)
r = p.parse_3233(bytes(b))
eq(r["system_op_status_name"], "Discharging", "3233 op")
eq((r["critical_batt_ok"], r["heat_on"], r["cool_on"]), (True, True, False), "3233 b10")
eq((r["charge_on"], r["charge_limited"], r["discharge_on"]), (True, False, True), "3233 b11")
eq((r["temp_min_c"], r["temp_max_c"], r["temp_avg_c"], r["bypass_count"]), (15, 22, 19, 3), "3233 temps")
assert not any("lifetime" in k for k in r), "3233 kWh is daily, not lifetime; not published"
eq(r["critical_events"], 2, "3233 crit")

# --- 0x5432 DailySession ----------------------------------------------------
b = pkt(0x5432, 69)
struct.pack_into("<hh", b, 8, 3250, 3420)
b[16] = 40 + 12; b[17] = 40 + 31
struct.pack_into("<HH", b, 18, 5100, 5560)
b[22] = 2 * 40 + 10; b[23] = 2 * 99 + 10
struct.pack_into("<hh", b, 40, 9550, 12034)
b[44] = 1
struct.pack_into("<ffff", b, 53, 85_000.0, 60_250.0, 4_400.0, 3_125.5)
r = p.parse_5432(bytes(b))
eq((r["daily_volt_min_v"], r["daily_volt_max_v"]), (3.25, 3.42), "5432 cell V")
eq((r["daily_temp_min_c"], r["daily_temp_max_c"]), (12, 31), "5432 temps")
eq((r["daily_pack_volt_min_v"], r["daily_pack_volt_max_v"]), (51.0, 55.6), "5432 pack V")
eq((r["daily_soc_min_pct"], r["daily_soc_max_pct"]), (40.0, 99.0), "5432 SOC")
eq((r["daily_peak_charge_a"], r["daily_peak_discharge_a"]), (95.5, 120.34), "5432 peaks")
eq((r["daily_ah_charge"], r["daily_ah_discharge"]), (85.0, 60.25), "5432 Ah")
eq((r["daily_kwh_charge"], r["daily_kwh_discharge"]), (4.4, 3.126), "5432 kWh")

# --- 0x7832 ShuntMetrics ----------------------------------------------------
b = pkt(0x7832, 32)
struct.pack_into("<h", b, 10, 412)
struct.pack_into("<II", b, 16, 1_700_000_000, 0)
r = p.parse_7832(bytes(b))
eq(r["shunt_soc_cycles"], 412, "7832 cycles")
eq((r["shunt_last_lo_recal"], r["shunt_last_hi_recal"]), ("2023-11-14T22:13:20-05:00", None), "7832 recal")

# --- 0x4232 CellNode (existing, units changed) -----------------------------
b = pkt(0x4232, 52)
b[8] = 3
struct.pack_into("<hh", b, 10, 3312, 3312)
b[14] = 40 + 20; b[15] = 40 + 35
struct.pack_into("<h", b, 16, 450)
b[20] = 7
struct.pack_into("<f", b, 47, 1_234.5)
r = p.parse_4232(bytes(b))
eq((r["volt_min_v"], r["bypass_a"], r["bypass_session_ah"]), (3.312, 0.45, 1.2345), "4232 (session Ah at full float32 precision)")
eq(r["op_status_name"], "InBypass", "4232 status")
cell_pkt = bytes(b)

# --- 0x3E33 (existing; bypass session now Ah) -------------------------------
b = pkt(0x3E33, 48)
struct.pack_into("<hh", b, 8, 3301, 3340)
struct.pack_into("<hh", b, 18, 100, 900)
struct.pack_into("<h", b, 28, 3320)
struct.pack_into("<ff", b, 38, 250.0, 1_500.0)
r = p.parse_3e33(bytes(b))
eq((r["volt_min_v"], r["volt_max_v"], r["volt_avg_v"]), (3.301, 3.34, 3.32), "3e33 V")
eq((r["min_bypass_a"], r["max_bypass_a"]), (0.1, 0.9), "3e33 bypass A")
eq((r["min_bypass_session_ah"], r["max_bypass_session_ah"]), (0.25, 1.5), "3e33 session Ah")


# --- End-to-end through the UDP handler with a fake publisher ---------------
class FakePub:
    def __init__(self):
        self.state, self.disc = {}, []
        self.ever_connected = True

    def update_state(self, u):
        self.state.update(u)

    def publish_node_discovery(self, c):
        self.disc.extend(c)
        return True


pub = FakePub()
proto = m.BatriumUdpProtocol(pub, "bms", 0)

# Two cells via 0x4232 only: pack min/max should be derived from them.
b2 = bytearray(cell_pkt); b2[8] = 4
struct.pack_into("<hh", b2, 10, 3290, 3290); b2[14] = 40 + 24
proto.datagram_received(cell_pkt, ("x", 0))
proto.datagram_received(bytes(b2), ("x", 0))
eq((pub.state["volt_min_v"], pub.state["volt_max_v"]), (3.29, 3.312), "derived V")
eq((pub.state["temp_min_c"], pub.state["temp_max_c"]), (20, 24), "derived T")
eq(pub.state["volt_spread_v"], 0.022, "derived spread")

# 0x5732 then supplies volts directly; later cell packets must not overwrite.
b = pkt(0x5732, 50)
struct.pack_into("<hhh", b, 31, 3000, 3500, 3250); b[37] = 40 + 10
proto.datagram_received(bytes(b), ("x", 0))
proto.datagram_received(cell_pkt, ("x", 0))
eq((pub.state["volt_min_v"], pub.state["volt_max_v"]), (3.0, 3.5), "authoritative V kept")
eq(pub.state["temp_min_c"], 10, "authoritative Tmin kept")
eq(pub.state["temp_max_c"], 24, "Tmax still derived (5732 lacks it)")
uids = [t for t, _ in pub.disc]
assert not any("relay_" in t for t in uids), "relay entities only after 0x3F33"
b = pkt(0x3F33, 80); b[50] = 1
proto.datagram_received(bytes(b), ("x", 0))
assert any("relay_1" in t for t, _ in pub.disc), "0x3F33 registers relay entities"
eq(pub.state["relay_1"], True, "3f33 relay")
assert any("system_op_status" in t for t in uids), "5732 group discovered"
assert not any("charge_on" in t for t in uids), "3233 not discovered before receipt"
n_before = len(pub.disc)
proto.datagram_received(bytes(b), ("x", 0))
eq(len(pub.disc), n_before, "group discovery published once")

# Every discovery payload must be valid JSON with a unique unique_id.
import json  # noqa: E402
from batrium import discovery as d  # noqa: E402
allc = d.build_discovery_configs("bms", 0) + d.build_node_discovery_configs(1, "bms", 0)
for mt in m.GROUP_PARSERS:
    allc += d.build_group_discovery_configs(mt, "bms", 0)
seen = {}
for topic, pl in allc:
    uid = json.loads(pl)["unique_id"]
    # 0x5732 and 0x3233 both carry WatchMon status; same entity, same config.
    assert seen.setdefault(uid, pl) == pl, f"conflicting configs for {uid}"
allc = list(dict(allc).items())
units = {json.loads(pl).get("unit_of_measurement") for _, pl in allc}
assert not units & {"mV", "mA", "W", "mAh"}, f"stray old units: {units}"

print(f"all checks passed ({len(allc)} entity configs, units: {sorted(u for u in units if u)})")
