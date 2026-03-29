#!/usr/bin/env python3
"""
SimRacing UDP Telemetry Reader
Listens to ACC (9000), AC (9996), AMS2 (9900) and writes JSON state.
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
PORTS = {
    "ACC": 9000,
    "AC": 9996,
    "AMS2": 9900,
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


def parse_acc_packet(data):
    """Parse ACC UDP packet. Returns dict or None."""
    if len(data) < 4:
        return None

    packet_type = data[0]

    if packet_type == 2:  # Car update
        speed = struct.unpack_from("<f", data, 2)[0] * 3.6  # m/s -> km/h
        gear = struct.unpack_from("<h", data, 6)[0]
        rpm = struct.unpack_from("<H", data, 8)[0]
        brake = struct.unpack_from("<H", data, 22)[0] / 32767.0
        throttle = struct.unpack_from("<H", data, 24)[0] / 32767.0
        steer = struct.unpack_from("<f", data, 26)[0]

        return {
            "speed": round(speed, 1),
            "brake": round(brake, 3),
            "throttle": round(throttle, 3),
            "steer": round(steer, 3),
            "gear": gear,
            "rpm": rpm,
        }

    return None


def parse_ac_packet(data):
    """Parse Assetto Corsa UDP packet. Returns dict or None."""
    if len(data) < 4:
        return None

    sig = data[:4]
    if sig != b"ACSH":
        return None

    packet_type = struct.unpack_from("<I", data, 6)[0]

    if packet_type == 1 and len(data) >= 60:  # Car update
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
    """Parse AMS2 UDP packet. Returns dict or None."""
    if len(data) < 4:
        return None

    packet_type = struct.unpack_from("<I", data, 0)[0]

    if packet_type == 1 and len(data) >= 100:  # Car update
        speed = struct.unpack_from("<f", data, 16)[0] * 3.6  # m/s -> km/h
        gear = struct.unpack_from("<i", data, 36)[0]
        throttle = struct.unpack_from("<f", data, 60)[0]
        brake = struct.unpack_from("<f", data, 64)[0]
        steer = struct.unpack_from("<f", data, 72)[0]
        rpm = struct.unpack_from("<f", data, 12)[0]

        return {
            "speed": round(speed, 1),
            "brake": round(brake, 3),
            "throttle": round(throttle, 3),
            "steer": round(steer, 3),
            "gear": gear,
            "rpm": int(rpm),
        }

    return None


def detect_sim(data, addr):
    """Detect which sim sent data based on port and content."""
    if len(data) >= 4:
        sig = data[:4]
        if sig == b"ACSH":
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
            json.dump(laps[-100:], f)  # Keep last 100 laps
    except Exception:
        pass

    if pb:
        try:
            with open(PERSONAL_BEST_FILE, "w") as f:
                json.dump(pb, f)
        except Exception:
            pass


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
                    parsed["sim"] = sim_name
                    parsed["port"] = port
                    parsed["timestamp"] = time.time()
                    current_telemetry = parsed
                    active_sim = sim_name
                    write_state(current_telemetry, lap_history, personal_best)

            except socket.timeout:
                # Send keepalive state periodically
                if current_telemetry:
                    current_telemetry["_last_update"] = time.time()
                    write_state(current_telemetry, lap_history, personal_best)
            except Exception as e:
                print(f"[telemetry_reader] Error on {sim_name}: {e}", flush=True)

    except Exception as e:
        print(f"[telemetry_reader] Fatal on port {port}: {e}", flush=True)
    finally:
        sock.close()


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
    global active_sim

    print("[telemetry_reader] Zeus SimRace Telemetry Reader v0.1", flush=True)
    print(f"[telemetry_reader] State dir: {STATE_DIR}", flush=True)

    # Load personal best
    load_personal_best()
    print(f"[telemetry_reader] Personal best loaded: {personal_best}", flush=True)

    # Start listener threads for all ports
    parsers = {
        "ACC": parse_acc_packet,
        "AC": parse_ac_packet,
        "AMS2": parse_ams2_packet,
    }

    threads = []
    for sim_name, port in PORTS.items():
        t = threading.Thread(target=listener_loop, args=(sim_name, port, parsers[sim_name]), daemon=True)
        t.start()
        threads.append(t)

    print("[telemetry_reader] All listeners started. Press Ctrl+C to stop.", flush=True)

    try:
        while True:
            time.sleep(5)
            if active_sim:
                print(f"[telemetry_reader] Active sim: {active_sim} | Speed: {current_telemetry.get('speed', 0):.0f} km/h | Gear: {current_telemetry.get('gear', 0)}", flush=True)
    except KeyboardInterrupt:
        print("[telemetry_reader] Shutting down...", flush=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
