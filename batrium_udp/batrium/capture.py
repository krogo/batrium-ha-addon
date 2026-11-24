"""
Raw capture of WatchMon message types this add-on does not decode.

Purpose: reverse-engineer undocumented messages (0x3031, 0x3033, 0x5931,
0x5932, 0x5A31, 0x5A32, 0x5B31 on a WatchMon Core) by lining their bytes up
against values the add-on already decodes.

Off by default (option `raw_capture`). When on:

  * MQTT: every undecoded packet is published, hex-encoded, to
    batrium/<system>/raw/<TYPE> (not retained), so it can be watched live
    in HA's MQTT "Listen to a topic".

  * File: /share/batrium_raw/YYYY-MM-DD.jsonl (local date), one JSON object
    per line:
      {"t": "<iso time>", "kind": "raw",   "type": "5931", "len": 104, "hex": "3a3159…"}
      {"t": "<iso time>", "kind": "state", "state": {…decoded values…}}
    A raw line is written only when that type's bytes differ from the last
    one written, which keeps static messages to one line. A state snapshot
    is written at most once a minute, for correlation by time.

Rough size with seven unknown types: under ~15 MB/day, depending on how
many of them change every cycle.
"""

import json
import logging
import os
import time
from datetime import datetime

_LOGGER = logging.getLogger(__name__)

STATE_SNAPSHOT_INTERVAL_S = 60.0


class RawCapture:
    def __init__(self, publisher, system_name: str, directory: str = "/share/batrium_raw"):
        self._publisher = publisher
        self._topic_base = f"batrium/{system_name}/raw"
        self._dir = directory
        self._last_hex: dict[int, str] = {}
        self._last_state_at: float | None = None   # None: first snapshot is unconditional
        self._file_ok = True

        try:
            os.makedirs(self._dir, exist_ok=True)
        except OSError as exc:
            # Most likely /share isn't mapped. Keep the MQTT side working.
            self._file_ok = False
            _LOGGER.error("Raw capture: cannot create %s (%s) — MQTT only", self._dir, exc)
        else:
            _LOGGER.info("Raw capture ON: MQTT %s/<TYPE>, files in %s", self._topic_base, self._dir)

    def packet(self, msg_type: int, data: bytes) -> None:
        """Record one undecoded packet."""
        hx = data.hex()
        type_s = f"{msg_type:04X}"
        self._publisher.publish_raw(f"{self._topic_base}/{type_s}", hx)

        if self._last_hex.get(msg_type) == hx:
            return
        self._last_hex[msg_type] = hx
        self._write({"kind": "raw", "type": type_s, "len": len(data), "hex": hx})
        self._maybe_state()

    def _maybe_state(self) -> None:
        now = time.monotonic()
        if self._last_state_at is not None and now - self._last_state_at < STATE_SNAPSHOT_INTERVAL_S:
            return
        self._last_state_at = now
        self._write({"kind": "state", "state": self._publisher.snapshot()})

    def _write(self, record: dict) -> None:
        if not self._file_ok:
            return
        now = datetime.now().astimezone()
        record = {"t": now.isoformat(timespec="milliseconds"), **record}
        path = os.path.join(self._dir, f"{now:%Y-%m-%d}.jsonl")
        try:
            with open(path, "a") as f:
                f.write(json.dumps(record, separators=(",", ":")) + "\n")
        except OSError as exc:
            self._file_ok = False
            _LOGGER.error("Raw capture: writing %s failed (%s) — file capture stopped", path, exc)
