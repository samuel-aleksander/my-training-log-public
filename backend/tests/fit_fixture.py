"""Minimal FIT encoder used to build synthetic test fixtures / demo activities.

Only what the app needs: file_id, event, record, lap and session messages.
Real Garmin files contain much more; those extra messages are ignored by the app.
"""

from __future__ import annotations

import struct
from datetime import datetime, timezone

FIT_EPOCH = 631065600  # 1989-12-31T00:00:00Z

CRC_TABLE = [
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
]


def crc16(data: bytes, crc: int = 0) -> int:
    for byte in data:
        for nibble in (byte & 0x0F, (byte >> 4) & 0x0F):
            tmp = CRC_TABLE[crc & 0xF]
            crc = (crc >> 4) & 0x0FFF
            crc = crc ^ tmp ^ CRC_TABLE[nibble]
    return crc & 0xFFFF


U8, U16, U32, ENUM = 0x02, 0x84, 0x86, 0x00
SIZES = {U8: 1, U16: 2, U32: 4, ENUM: 1}
INVALID = {U8: 0xFF, U16: 0xFFFF, U32: 0xFFFFFFFF, ENUM: 0xFF}
PACK = {U8: "B", U16: "<H", U32: "<I", ENUM: "B"}


def to_fit_time(dt: datetime) -> int:
    return int(dt.replace(tzinfo=dt.tzinfo or timezone.utc).timestamp()) - FIT_EPOCH


class FitBuilder:
    def __init__(self) -> None:
        self.body = bytearray()
        self.defs: dict[int, list[tuple[int, int]]] = {}

    def define(self, local: int, global_num: int, fields: list[tuple[int, int]]) -> None:
        self.defs[local] = fields
        out = bytearray([0x40 | local, 0, 0])
        out += struct.pack("<H", global_num)
        out.append(len(fields))
        for num, base in fields:
            out += bytes([num, SIZES[base], base])
        self.body += out

    def data(self, local: int, values: list[int | None]) -> None:
        out = bytearray([local])
        for (_num, base), val in zip(self.defs[local], values):
            v = INVALID[base] if val is None else int(val)
            out += struct.pack(PACK[base], v)
        self.body += out

    def build(self) -> bytes:
        header = bytearray([14, 0x20]) + struct.pack("<H", 2158) + struct.pack("<I", len(self.body)) + b".FIT"
        header += struct.pack("<H", crc16(bytes(header)))
        full = bytes(header) + bytes(self.body)
        return full + struct.pack("<H", crc16(bytes(self.body), crc16(bytes(header))))


def make_fit(
    start: datetime,
    samples: list[tuple[int, int | None, float, float]],
    *,
    pauses: list[tuple[int, int]] | None = None,
    laps: int = 1,
    include_session_timer: bool = True,
    altitudes: list[float | None] | None = None,
    session_elevation: tuple[int, int] | None = None,
) -> bytes:
    """samples: (offset_seconds, heart_rate|None, cumulative_metres, speed_mps).

    altitudes: one value in metres per sample (record field 2, scale 5, offset 500).
    session_elevation: (total_ascent, total_descent) written by the "watch".
    """
    b = FitBuilder()
    b.define(0, 0, [(0, ENUM), (1, U16), (4, U32)])
    b.data(0, [4, 255, to_fit_time(start)])

    b.define(1, 21, [(253, U32), (0, ENUM), (1, ENUM)])
    b.data(1, [to_fit_time(start), 0, 0])  # timer start
    for p_start, p_end in pauses or []:
        b.data(1, [to_fit_time(start) + p_start, 0, 4])  # stop_all
        b.data(1, [to_fit_time(start) + p_end, 0, 0])  # start

    record_fields = [(253, U32), (3, U8), (5, U32), (6, U16)]
    if altitudes is not None:
        record_fields.append((2, U16))
    b.define(2, 20, record_fields)
    for i, (offset, hr, metres, speed) in enumerate(samples):
        values = [to_fit_time(start) + offset, hr, round(metres * 100), round(speed * 1000)]
        if altitudes is not None:
            alt = altitudes[i]
            values.append(None if alt is None else round((alt + 500) * 5))
        b.data(2, values)

    last_offset = samples[-1][0]
    total_m = samples[-1][2]
    paused_total = sum(e - s for s, e in (pauses or []))
    timer = last_offset - paused_total
    hrs = [h for _, h, _, _ in samples if h]

    b.define(3, 19, [(253, U32), (2, U32), (7, U32), (8, U32), (9, U32), (15, U8), (16, U8)])
    per = max(1, len(samples) // laps)
    for i in range(laps):
        chunk = samples[i * per : (i + 1) * per] or samples[-1:]
        lap_hrs = [h for _, h, _, _ in chunk if h]
        b.data(3, [
            to_fit_time(start) + chunk[-1][0],
            to_fit_time(start) + chunk[0][0],
            (chunk[-1][0] - chunk[0][0]) * 1000,
            (chunk[-1][0] - chunk[0][0]) * 1000,
            round((chunk[-1][2] - chunk[0][2]) * 100),
            round(sum(lap_hrs) / len(lap_hrs)) if lap_hrs else None,
            max(lap_hrs) if lap_hrs else None,
        ])

    fields = [(253, U32), (2, U32), (5, ENUM), (7, U32), (9, U32), (16, U8), (17, U8)]
    values = [
        to_fit_time(start) + last_offset,
        to_fit_time(start),
        1,  # running
        last_offset * 1000,
        round(total_m * 100),
        round(sum(hrs) / len(hrs)) if hrs else None,
        max(hrs) if hrs else None,
    ]
    if include_session_timer:
        fields.insert(4, (8, U32))
        values.insert(4, timer * 1000)
    if session_elevation is not None:
        fields += [(22, U16), (23, U16)]
        values += list(session_elevation)
    b.define(4, 18, fields)
    b.data(4, values)
    return b.build()


def steady_run(start: datetime, km: float, pace_min_km: float, hr: int | None = 130, **kw) -> bytes:
    """A simple constant-pace run sampled once per second."""
    speed = 1000 / (pace_min_km * 60)
    total_s = int(km * 1000 / speed)
    samples = [(t, hr, min(speed * t, km * 1000), speed) for t in range(0, total_s + 1)]
    return make_fit(start, samples, **kw)


if __name__ == "__main__":  # write a labelled demo file
    import sys
    from pathlib import Path

    out = Path(sys.argv[1] if len(sys.argv) > 1 else "demo-activity.fit")
    out.write_bytes(steady_run(datetime(2026, 9, 22, 7, 0, tzinfo=timezone.utc), 6.0, 6.95))
    print(f"wrote {out}")
