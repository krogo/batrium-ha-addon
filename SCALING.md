# Field scaling

Every value this add-on publishes, how the WatchMon encodes it, and how it is scaled. "Ref" is Batrium's WatchMonUdpListener decoder; "Node-RED" is the flow this fork replaced. Offsets are bytes from the start of the packet, including the 8-byte header.

Confidence: **ref** = matches Batrium's reference decoder; **ref+NR** = also matches the Node-RED flow that ran on real hardware; **guess** = no reference scaling exists, so it is inferred; **unverified** = bit order or meaning taken from comments, not checked on hardware.

## Cells and pack

| Published field | Msg | Offset / type | Raw unit | Scaling | Unit | Confidence |
|---|---|---|---|---|---|---|
| `cell_N_volt`, `volt_min_v/max_v/avg_v` | 4232, 415A, 3E33, 5732, 3233 | int16 | mV | ÷1000 | V | ref+NR |
| `cell_N_temp_c`, `temp_*_c`, all temps | all | uint8 | °C+40 | −40 | °C | ref+NR |
| `cell_N_bypass_a`, `min/max_bypass_a` | 4232, 415A, 3E33 | int16 | mA | ÷1000 | A | ref+NR |
| `cell_N_bypass_session_ah` | 4232 @47 | float32 | mAh | ÷1000 | Ah | ref+NR — **decide: Ah or mAh?** |
| `min/max_bypass_session_ah` | 3E33 @38/42 | float32 | mAh | ÷1000 | Ah | ref (upstream published raw labelled Ah) |

## Shunt (0x3F34)

| Published field | Offset / type | Raw unit | Scaling | Unit | Confidence |
|---|---|---|---|---|---|
| `shunt_volt_v` | 12 uint16 | 10 mV | ÷100 | V | ref+NR |
| `shunt_a` | 14 float32 | mA | ÷1000, 2 dp | A | ref+NR |
| `shunt_kw` | 18 float32 | W | ÷1000, 2 dp | kW | ref+NR |
| `shunt_soc_pct` | 22 int16 | 0.01 % | ÷100 | % | ref+NR |
| `soc_pct` | 24 uint8 | 0.5 % | ×0.5 − 5 | % | ref |
| `supply_volt_v` | 8 uint16 | 10 mV | ÷100 | V | ref |
| `capacity_to_full_ah` / `_empty_ah` | 26 / 30 float32 | mAh | ÷1000 | Ah | ref+NR |
| `est_mins_to_full` / `_empty` | 34 / 36 int16 | min | — | min | ref+NR |
| `avg_charge_a` / `avg_discharge_a` / `avg_net_a` | 38 / 42 / 46 float32 | mA | ÷1000 | A | ref |
| `soc_recal_needed` | 25 bits 2–3 | flag | — | — | unverified |

## Remote charger / inverter (0x4932)

The reference applies no scaling to any of these.

| Published field | Offset / type | Scaling | Unit | Confidence |
|---|---|---|---|---|
| `remote_*_target_a` | 14 / 40 int16 | ÷10, integer | A | NR (Node-RED did ×100÷1000) |
| `remote_*_actual_a` | 20 / 46 int16 | ÷10, 1 dp | A | guess (same as target) |
| `remote_*_target_v`, `_actual_v` | 12, 18 / 38, 44 int16 | ÷100 | V | guess |
| `remote_*_target_va`, `_actual_va` | 16, 22 / 42, 48 int16 | none | VA | guess |
| `remote_*_last_rx` | 32 / 58 uint32 | epoch | timestamp | ref |

## Energy and sessions

| Published field | Msg | Offset / type | Raw unit | Scaling | Unit | Confidence |
|---|---|---|---|---|---|---|
| `lifetime_kwh_charge` / `_discharge` | 3233 | 38 / 42 float32 | Wh | ÷1000 | kWh | ref |
| `daily_kwh_charge` / `_discharge` | 5432 | 61 / 65 float32 | Wh | ÷1000 | kWh | ref |
| `daily_ah_charge` / `_discharge` | 5432 | 53 / 57 float32 | mAh | ÷1000 | Ah | ref |
| `daily_peak_charge_a` / `_discharge_a` | 5432 | 40 / 42 int16 | 10 mA | ÷100 | A | ref |
| `daily_pack_volt_min_v` / `_max_v` | 5432 | 18 / 20 int16 | 10 mV | ÷100 | V | ref |
| `daily_soc_min_pct` / `_max_pct` | 5432 | 22 / 23 uint8 | 0.5 % | ×0.5 − 5 | % | ref uses (x−5)/2 here; ×0.5−5 matches every other SOC field |
| `charge_on`, `discharge_on`, `*_limited`, `heat_on`, … | 3233 | 10–11 bits | flag | — | — | unverified bit order |
