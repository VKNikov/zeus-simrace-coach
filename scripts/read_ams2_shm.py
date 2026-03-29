#!/usr/bin/env python3
"""
AMS2 Shared Memory Reader.
Reads AMS2 telemetry directly from shared memory (more reliable than UDP).
Shared memory name: 'Local\\AMS2Telemetry'
"""

import mmap
import struct
import time
from pathlib import Path

# Shared memory
SHM_NAME = r'Local\AMS2Telemetry'
PAGE_SIZE = 4096


def read_string(raw, offset, max_len=50):
    """Read null-terminated ASCII string from buffer."""
    try:
        end = offset
        while end < len(raw) and end < offset + max_len and raw[end] != 0:
            end += 1
        return raw[offset:end].decode('ascii', errors='replace').strip()
    except Exception:
        return ""


def read_shared_memory() -> dict:
    """Read AMS2 shared memory, return telemetry dict or None."""
    try:
        shm = mmap.mmap(0, PAGE_SIZE * 10, SHM_NAME, access=mmap.ACCESS_READ)
        raw = shm.read(PAGE_SIZE * 3)
        shm.close()

        page_type = struct.unpack_from('<i', raw, 0)[0]

        result = {}

        # Page type 1 = telemetry (speed, gear, throttle, brake, steer, rpm)
        if page_type == 1:
            speed_ms = struct.unpack_from('<f', raw, 12)[0]
            gear = struct.unpack_from('<i', raw, 16)[0]
            rpm = struct.unpack_from('<f', raw, 20)[0]
            throttle = struct.unpack_from('<f', raw, 24)[0]
            brake = struct.unpack_from('<f', raw, 28)[0]
            steer = struct.unpack_from('<f', raw, 32)[0]

            result.update({
                "speed": round(speed_ms * 3.6, 1),
                "gear": gear,
                "rpm": int(rpm),
                "throttle": round(throttle, 3),
                "brake": round(brake, 3),
                "steer": round(steer, 3),
            })

        # Page type 0 = session info (track, car, lap, times)
        elif page_type == 0:
            track_name = read_string(raw, 200, 50)
            car_name = read_string(raw, 250, 50)
            lap = struct.unpack_from('<i', raw, 24)[0]
            lap_time_ms = struct.unpack_from('<i', raw, 28)[0]
            last_lap_ms = struct.unpack_from('<i', raw, 32)[0]
            sector_byte = raw[36] if len(raw) > 36 else 255
            current_sector = sector_byte if sector_byte in (0, 1, 2) else 255

            if track_name:
                result["current_track"] = track_name
            if car_name:
                result["current_car"] = car_name
            result["lap"] = lap
            result["lap_time_ms"] = max(lap_time_ms, 0)
            result["last_lap_time_ms"] = max(last_lap_ms, 0)
            result["current_sector"] = current_sector

        result["sim"] = "AMS2"
        result["page_type"] = page_type
        return result

    except Exception as e:
        return {"_error": str(e)}


if __name__ == "__main__":
    print("[AMS2 SHM Reader] Testing shared memory read...")
    for i in range(3):
        data = read_shared_memory()
        if data:
            print(f"[{i}] page_type={data.get('page_type')}, "
                  f"speed={data.get('speed', 0):.1f} km/h, "
                  f"gear={data.get('gear')}, "
                  f"brake={data.get('brake', 0):.3f}, "
                  f"throttle={data.get('throttle', 0):.3f}, "
                  f"track={data.get('current_track', '?')}, "
                  f"car={data.get('current_car', '?')}, "
                  f"lap={data.get('lap')}")
        else:
            print(f"[{i}] No data")
        time.sleep(1)
