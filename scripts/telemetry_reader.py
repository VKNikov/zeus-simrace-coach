#!/usr/bin/env python3
"""
SimRacing UDP Telemetry Reader
Listens to ACC (9000), AC (9996), AMS2 (5606) and writes JSON state.
Extracts: speed, brake, throttle, gear, rpm, steer, track, car,
          current_sector, lap, lap_time_ms, sector times.
AMS2 uses Project Cars 1 protocol on port 5606 (not 9900).
"""

import socket
import json
import struct
import os
import sys
import threading
import time
from pathlib import Path

# Config
# AMS2 uses Project Cars 1 protocol on port 5606 (UDP broadcast)
# (NOT 9900 — that's an incorrect assumption. PCars2 uses 5606.)
PORTS = {
    "ACC": 9000,
    "AC": 9996,
    "AMS2": 5606,
}
STATE_DIR = Path.home() / ".openclaw" / "var"
STATE_FILE = STATE_DIR / "simrace_telemetry.json"
LAPS_FILE = STATE_DIR / "simrace_laps.json"
PERSONAL_BEST_FILE = STATE_DIR / "simrace_personal_best.json"

# Ensure state dir exists
STATE_DIR.mkdir(parents=True, exist_ok=True)

# Global state
current_telemetry = {}
lap_history = []
personal_best = {}
active_sim = None

# Session state tracking (for lap/sector detection)
_last_lap_number = 0
_last_sector_number = 255
_last_lap_time_ms = 0


def _read_string_safe(data, offset, max_len=50):
    """Read a null-terminated ASCII string from buffer."""
    try:
        end = offset
        while end < len(data) and end < offset + max_len and data[end] != 0:
            end += 1
        return data[offset:end].decode("ascii", errors="replace").strip()
    except Exception:
        return ""


def parse_acc_packet(data):
    """Parse ACC UDP packet. Returns dict or None.
    
    ACC packet types (from community docs / CrewChiefV4):
      0  = CarInfo (static: car model, skin, team)
      1  = CarUpdate (live: speed, brake, throttle, gear, rpm, etc.)
      2  = LapInfo (lap times, sector times, positions)
      3  = Entrylist
      4  = Weather (temp, cloud, rain)
      5  = TyreCumulative (tyre wear)
      6  = PenaltyInfo
      7  = P2P_Count
      8  = P2P_Count (??)
      9  = LapData (split times)
      10 = GameState
      11 = ACCGameState
    
    This parser handles type 1 (CarUpdate) and type 2 (LapInfo).
    """
    if len(data) < 4:
        return None

    packet_type = data[0]

    # --- CarUpdate (type 1) ---
    if packet_type == 1 and len(data) >= 80:
        speed = struct.unpack_from("<f", data, 2)[0] * 3.6  # m/s -> km/h
        gear = struct.unpack_from("<h", data, 6)[0]
        rpm = struct.unpack_from("<H", data, 8)[0]
        brake = struct.unpack_from("<H", data, 22)[0] / 32767.0
        throttle = struct.unpack_from("<H", data, 24)[0] / 32767.0
        steer = struct.unpack_from("<f", data, 26)[0]

        result = {
            "speed": round(speed, 1),
            "brake": round(brake, 3),
            "throttle": round(throttle, 3),
            "steer": round(steer, 3),
            "gear": gear,
            "rpm": rpm,
        }
        return result

    # --- LapInfo (type 2) ---
    if packet_type == 2 and len(data) >= 200:
        # ACC LapInfo structure (from community docs):
        # Offset 0: packetType (1 byte) = 2
        # Offset 1: AC_PacketType (1 byte) = 2
        # Offset 2: playerCarIndex (1 byte)
        # Offset 3: otherCarIndex (1 byte) -- focus on player
        # For player (index = data[2]):
        # Per-car offset = 56 bytes, so player offset = 56 * playerCarIndex
        player_idx = data[2] if len(data) > 2 else 0
        base = 4 + (player_idx * 56)  # CarInfo offset

        # lapTimeMs: 4 bytes signed int at offset 4 within car block
        lap_time_ms = struct.unpack_from("<i", data, base + 4)[0] if len(data) >= base + 8 else 0

        # currentLapRS (lap number) at offset 8
        lap_number = struct.unpack_from("<i", data, base + 8)[0] if len(data) >= base + 12 else 0

        # lastLapTimeMS at offset 12
        last_lap_time_ms = struct.unpack_from("<i", data, base + 12)[0] if len(data) >= base + 16 else 0

        # currentSector (0, 1, 2) at offset 16
        current_sector = data[base + 16] if len(data) >= base + 17 else 255

        # sectorTimes[3] as int32 at offsets 20, 24, 28
        s1_ms = struct.unpack_from("<i", data, base + 20)[0] if len(data) >= base + 24 else 0
        s2_ms = struct.unpack_from("<i", data, base + 24)[0] if len(data) >= base + 28 else 0
        # s3 is not stored separately as it's derived from lapTime - s1 - s2

        result = {
            "lap": lap_number,
            "lap_time_ms": max(lap_time_ms, 0),
            "last_lap_time_ms": max(last_lap_time_ms, 0),
            "current_sector": current_sector if current_sector in (0, 1, 2) else 255,
            "sector1_time_ms": max(s1_ms, 0),
            "sector2_time_ms": max(s2_ms, 0),
        }
        return result

    # --- CarInfo (type 0) ---  contains track + car names
    if packet_type == 0 and len(data) >= 300:
        # Track name: offset ~240 (null-terminated string, ~50 bytes)
        track_name = _read_string_safe(data, 240, 50)
        # Car model: offset ~290 (null-terminated string, ~50 bytes)
        car_name = _read_string_safe(data, 290, 50)
        if track_name or car_name:
            return {
                "current_track": track_name,
                "current_car": car_name,
            }

    return None


def parse_ac_packet(data):
    """Parse Assetto Corsa UDP packet. Returns dict or None.
    
    AC sends packets with "ACSH" signature at offset 0.
    """
    if len(data) < 8:
        return None

    sig = data[:4]
    if sig != b"ACSH":
        return None

    # Packet type at offset 6 (DWORD)
    packet_type = struct.unpack_from("<I", data, 6)[0]

    # Car update packet (type 1)
    if packet_type == 1 and len(data) >= 80:
        speed = struct.unpack_from("<f", data, 26)[0] * 3.6  # m/s -> km/h
        gear = struct.unpack_from("<i", data, 42)[0]
        throttle = struct.unpack_from("<f", data, 50)[0]
        brake = struct.unpack_from("<f", data, 52)[0]
        steer = struct.unpack_from("<f", data, 48)[0]
        rpm = struct.unpack_from("<H", data, 46)[0]

        return {
            "speed": round(speed, 1),
            "brake": round(brake, 3),
            "throttle": round(throttle, 3),
            "steer": round(steer, 3),
            "gear": gear,
            "rpm": rpm,
        }

    return None


def parse_ams2_packet(data):
    """Parse AMS2 UDP packet using Project Cars 1 protocol on port 5606.

    AMS2 in "Project Cars 1" UDP mode sends PCars1-format telemetry.
    Packet size: ~1367 bytes per packet.

    Key offsets (PCars1 sTelemetryCarP2 structure, confirmed working):
      Offset   0: float  speed (m/s) -> multiply by 3.6 for km/h
      Offset   5: int8   gear (0=neutral, 1-8=gears, -1=invalid)
      Offset   6: uint16 rpm
      Offset   8: uint16 throttle [0-65535] -> normalize to [0-1]
      Offset  10: uint16 brake   [0-65535] -> normalize to [0-1]
      Offset  12: float  steerInput [-1 to 1]
      Offset  16: float  steerAngle [-1 to 1]
      Offset  20: float  clutch [0-1]
      (Track/car names are NOT transmitted via PC1 UDP — use shared memory or manual --track/--car)

    Lap/session data: scan uint32 values near offset 328-380 for valid lap/sector data.
    Offsets 332, 336, 340, 344 are used for lap time / sector data.
    """
    if len(data) < 4:
        return None

    result = {}

    if len(data) >= 1367:
        # Speed (confirmed: offset 0, float m/s)
        try:
            speed = struct.unpack_from('<f', data, 0)[0] * 3.6
        except Exception:
            speed = 0.0

        # Gear (confirmed: offset 5, int8)
        try:
            gear_raw = struct.unpack_from('b', data, 5)[0]
            gear = gear_raw if -1 <= gear_raw <= 8 else 0
        except Exception:
            gear = 0

        # RPM (offset 6, uint16 — confirmed non-zero when engine running)
        try:
            rpm = struct.unpack_from('<H', data, 6)[0]
        except Exception:
            rpm = 0

        # Throttle (offset 8, uint16)
        try:
            throttle = struct.unpack_from('<H', data, 8)[0] / 65535.0
        except Exception:
            throttle = 0.0

        # Brake (offset 10, uint16)
        try:
            brake = struct.unpack_from('<H', data, 10)[0] / 65535.0
        except Exception:
            brake = 0.0

        # Steer (offset 12, float)
        try:
            steer = struct.unpack_from('<f', data, 12)[0]
        except Exception:
            steer = 0.0

        result.update({
            "speed": round(speed, 1),
            "gear": gear,
            "rpm": rpm,
            "throttle": round(throttle, 3),
            "brake": round(brake, 3),
            "steer": round(steer, 3),
        })

        # Lap data: try offsets 328-344 (standard PCars1 positions)
        # These are unverified for AMS2 — use best effort
        try:
            lap = struct.unpack_from('<i', data, 328)[0]
            if 0 < lap < 1000:
                result["lap"] = lap
        except Exception:
            pass

        try:
            lap_time_ms = struct.unpack_from('<i', data, 332)[0]
            if 0 < lap_time_ms < 10000000:
                result["lap_time_ms"] = lap_time_ms
        except Exception:
            pass

        try:
            last_lap_ms = struct.unpack_from('<i', data, 336)[0]
            if 0 < last_lap_ms < 600000:
                result["last_lap_time_ms"] = last_lap_ms
        except Exception:
            pass

        try:
            sector = data[340] if len(data) > 340 else 255
            if sector in (0, 1, 2):
                result["current_sector"] = sector
        except Exception:
            pass

        result["sim"] = "AMS2"
        return result

    return None


def detect_sim(data, addr, port):
    """Detect which sim based on port and content signature."""
    if port == PORTS["ACC"]:
        return "ACC"
    if port == PORTS["AMS2"]:
        return "AMS2"
    if port == PORTS["AC"]:
        return "AC"
    if len(data) >= 4 and data[:4] == b"ACSH":
        return "AC"
    return None


def write_state(telemetry, laps, pb):
    """Write telemetry state to JSON files."""
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(telemetry, f)
    except Exception as e:
        print(f"[telemetry_reader] Warning: failed to write state: {e}", flush=True)

    try:
        with open(LAPS_FILE, "w") as f:
            json.dump(laps[-100:], f)
    except Exception:
        pass

    if pb:
        try:
            with open(PERSONAL_BEST_FILE, "w") as f:
                json.dump(pb, f)
        except Exception:
            pass


def merge_telemetry(base, update):
    """Merge update dict into base telemetry. Preserves existing keys."""
    if not update:
        return base
    result = dict(base)
    result.update(update)
    return result


def listener_loop(sim_name, port, parser):
    """Listen for UDP packets from a specific sim."""
    global current_telemetry, lap_history, personal_best, active_sim

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(1.0)

    try:
        sock.bind(("0.0.0.0", port))
        print(f"[telemetry_reader] Listening on {port} ({sim_name})", flush=True)

        while True:
            try:
                data, addr = sock.recvfrom(4096)
                parsed = parser(data)

                if parsed:
                    # Preserve existing data (track/car names persist across packets)
                    current_telemetry = merge_telemetry(current_telemetry, parsed)
                    current_telemetry["sim"] = sim_name
                    current_telemetry["port"] = port
                    current_telemetry["timestamp"] = time.time()

                    # Detect lap completion
                    _detect_lap_completion(current_telemetry)

                    active_sim = sim_name
                    write_state(current_telemetry, lap_history, personal_best)

            except socket.timeout:
                if current_telemetry:
                    current_telemetry["_last_update"] = time.time()
                    write_state(current_telemetry, lap_history, personal_best)
            except Exception as e:
                print(f"[telemetry_reader] Error on {sim_name}: {e}", flush=True)

    except Exception as e:
        print(f"[telemetry_reader] Fatal on port {port}: {e}", flush=True)
    finally:
        sock.close()


def _detect_lap_completion(telemetry):
    """Detect lap completion and record it."""
    global _last_lap_number, _last_lap_time_ms, lap_history, personal_best

    lap_num = telemetry.get("lap", 0)
    lap_time = telemetry.get("lap_time_ms", 0)
    last_lap = telemetry.get("last_lap_time_ms", 0)

    # Lap completed when lap number increases and we have a valid last lap time
    if lap_num > _last_lap_number and last_lap > 0:
        lap_record = {
            "lap": _last_lap_number,
            "time_ms": last_lap,
            "time_str": _format_time(last_lap),
            "track": telemetry.get("current_track", ""),
            "car": telemetry.get("current_car", ""),
            "sim": telemetry.get("sim", ""),
        }
        lap_history.append(lap_record)
        print(f"[telemetry_reader] Lap completed: {_format_time(last_lap)} "
              f"({telemetry.get('current_track', '')} / {telemetry.get('current_car', '')})", flush=True)

        # Update personal best
        if not personal_best or last_lap < personal_best.get("lap_ms", 999999999):
            personal_best = {
                "lap_ms": last_lap,
                "lap": _last_lap_number,
                "track": telemetry.get("current_track", ""),
                "car": telemetry.get("current_car", ""),
            }
            print(f"[telemetry_reader] New personal best: {_format_time(last_lap)}", flush=True)

        _last_lap_number = lap_num

    # If lap number just started (new session reset), update counter
    if lap_num > 0 and _last_lap_number == 0:
        _last_lap_number = lap_num


def _format_time(ms):
    """Format milliseconds as M:SS.mmm"""
    if ms <= 0:
        return "0:00.000"
    secs = ms / 1000
    return f"{int(secs // 60)}:{secs % 60:06.3f}"


def load_personal_best():
    """Load personal best from file."""
    global personal_best
    try:
        if PERSONAL_BEST_FILE.exists():
            with open(PERSONAL_BEST_FILE) as f:
                personal_best = json.load(f)
    except Exception:
        pass


def main():
    global active_sim, current_telemetry

    print("[telemetry_reader] Zeus SimRace Telemetry Reader v0.3", flush=True)
    print(f"[telemetry_reader] State dir: {STATE_DIR}", flush=True)

    # Load saved PB
    load_personal_best()
    if personal_best:
        print(f"[telemetry_reader] Loaded PB: {_format_time(personal_best.get('lap_ms', 0))} "
              f"@ {personal_best.get('track', '?')}", flush=True)

    threads = []

    for sim_name, port in PORTS.items():
        parser = {
            "ACC": parse_acc_packet,
            "AC": parse_ac_packet,
            "AMS2": parse_ams2_packet,
        }.get(sim_name)

        if parser:
            t = threading.Thread(target=listener_loop, args=(sim_name, port, parser), daemon=True)
            t.start()
            threads.append(t)
        else:
            print(f"[telemetry_reader] No parser for {sim_name}, skipping.", flush=True)

    if not threads:
        print("[telemetry_reader] Error: No listeners started. Check your sims.", flush=True)
        sys.exit(1)

    print("[telemetry_reader] All listeners started. Press Ctrl+C to stop.", flush=True)

    try:
        while True:
            time.sleep(5)
            if active_sim:
                print(f"[telemetry_reader] Active: {active_sim} | "
                      f"Last track: {current_telemetry.get('current_track', '?')} | "
                      f"Car: {current_telemetry.get('current_car', '?')} | "
                      f"Speed: {current_telemetry.get('speed', 0):.0f} km/h", flush=True)
    except KeyboardInterrupt:
        print("\n[telemetry_reader] Shutdown.", flush=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
