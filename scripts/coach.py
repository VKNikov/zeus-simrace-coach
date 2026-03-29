#!/usr/bin/env python3
"""
SimRacing AI Coach
Reads telemetry, generates coaching phrases, speaks them via TTS.
Uses community pace data for absolute reference comparison.
"""

import json
import time
import os
import subprocess
import sys
import statistics
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

# State files
STATE_DIR = Path.home() / ".openclaw" / "var"
STATE_FILE = STATE_DIR / "simrace_telemetry.json"
LAPS_FILE = STATE_DIR / "simrace_laps.json"
PB_FILE = STATE_DIR / "simrace_personal_best.json"

# TTS
SPEAK_PS1 = Path.home() / ".openclaw" / "tools" / "sherpa-onnx-tts" / "speak.ps1"

# Pace data
SCRIPT_DIR = Path(__file__).parent.parent / "references"
PACE_DATA_FILE = SCRIPT_DIR / "pace_data.json"


@dataclass
class CoachingState:
    """Tracks coaching state across laps."""
    level: str = "intermediate"  # beginner, intermediate, advanced
    last_sector_time: int = 0
    last_lap_time: int = 0
    last_sector: int = 0
    lap_count: int = 0
    sector_times: list = field(default_factory=list)
    lap_times: list = field(default_factory=list)
    consistency_scores: list = field(default_factory=list)
    personal_best: dict = field(default_factory=dict)
    last_coaching_time: float = 0
    cooldown_seconds: float = 3.0  # Minimum seconds between coaching calls
    last_brake_callout: str = ""
    last_throttle_callout: str = ""
    # Current reference data
    current_ref: dict = field(default_factory=dict)
    pace_data: dict = field(default_factory=dict)


def load_telemetry():
    """Load latest telemetry from JSON."""
    try:
        if STATE_FILE.exists():
            with open(STATE_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def load_laps():
    """Load lap history."""
    try:
        if LAPS_FILE.exists():
            with open(LAPS_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return []


def load_pb():
    """Load personal best."""
    try:
        if PB_FILE.exists():
            with open(PB_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def load_pace_data():
    """Load community pace reference data."""
    try:
        if PACE_DATA_FILE.exists():
            with open(PACE_DATA_FILE) as f:
                return json.load(f)
    except Exception as e:
        print(f"[coach] Warning: Could not load pace data: {e}", flush=True)
    return {}


def save_pb(pb):
    """Save personal best."""
    try:
        with open(PB_FILE, "w") as f:
            json.dump(pb, f)
    except Exception:
        pass


def get_current_ref(state: CoachingState, telemetry: dict) -> Optional[dict]:
    """Get reference pace data for the current track/car combo."""
    track = telemetry.get("current_track", "")
    car = telemetry.get("current_car", "")

    if not track or not car:
        return None

    # Normalize keys
    track_lower = track.lower()
    car_lower = car.lower().replace(" ", "_").replace("-", "_")

    pace = state.pace_data

    # Try sim-specific lookup
    for sim_key, sim_data in pace.items():
        if not isinstance(sim_data, dict):
            continue
        for track_key, track_data in sim_data.items():
            if track_key.lower() in track_lower or track_lower in track_key.lower():
                cars = track_data.get("cars", {})
                for car_key, car_data in cars.items():
                    if car_key.lower() in car_lower or car_lower in car_key.lower():
                        return {
                            "track_name": track_key,
                            "car_name": car_data.get("_name", car_key),
                            "ref_lap": car_data.get("ref_lap_time_s", 0) * 1000,  # ms
                            "sectors": car_data.get("sectors", {}),
                            "braking_zones": car_data.get("braking_zones", {}),
                            "sim": sim_key
                        }
    return None


def speak(text: str, voice: str = "libritts_r-male", async_: bool = True):
    """Speak text via sherpa-onnx TTS."""
    try:
        cmd = [
            "powershell", "-ExecutionPolicy", "Bypass", "-File",
            str(SPEAK_PS1),
            "-Text", text,
            "-Voice", voice
        ]
        if async_:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[coach] TTS error: {e}", flush=True)


def delta_description(delta_ms: float) -> str:
    """Convert delta milliseconds to spoken description."""
    if abs(delta_ms) < 50:
        return "on target"
    elif delta_ms > 0:
        secs = delta_ms / 1000
        if secs < 0.5:
            return "plus one tenth"
        elif secs < 1.0:
            return f"plus {int(secs * 10) * 2} hundredths"
        else:
            return f"plus {secs:.1f} seconds"
    else:
        secs = abs(delta_ms) / 1000
        if secs < 0.5:
            return "minus one tenth"
        elif secs < 1.0:
            return f"minus {int(secs * 10) * 2} hundredths"
        else:
            return f"minus {secs:.1f} seconds"


def delta_description_abs(delta_ms: float) -> str:
    """Absolute delta (reference) in spoken form."""
    return delta_description(delta_ms)


def brake_coaching(speed: float, brake: float, throttle: float, gear: int,
                   current_ref: dict, telemetry: dict, sector: int) -> Optional[str]:
    """Generate brake point coaching comparing speed to reference."""
    if brake < 0.1:
        return None

    # Detect heavy braking zones (speed > 80 km/h, brake > 0.7)
    if speed > 80 and brake > 0.7:
        if throttle > 0.3:
            return "brake and throttle together. Lift off the throttle first."
        elif brake > 0.9:
            return "maximum brake. Trail brake into the corner."
        else:
            return "good brake pressure. Smooth and progressive."

    return None


def speed_at_braking_zone(speed: float, current_ref: dict, sector: int) -> Optional[str]:
    """Compare current speed at a braking zone vs reference, return coaching."""
    if not current_ref or "braking_zones" not in current_ref:
        return None

    bz = current_ref.get("braking_zones", {})
    # Map sector number to braking zone
    sector_bz_map = {
        0: ["T1_Descida", "T1", "T3_Laranja"],
        1: ["T4_Ferradura", "T4", "Juncao"],
        2: ["T10_Cotovelo", "T10", "T8_T9", "T7"]
    }

    relevant_bz = sector_bz_map.get(sector, [])
    for bz_name in relevant_bz:
        if bz_name in bz:
            info = bz[bz_name]
            advice = info.get("_advice", "")
            # No hard speed reference in pace data yet, just advisory
            if advice:
                return f"at {int(speed)} kilometres. {advice}"
    return None


def throttle_coaching(throttle: float, brake: float, speed: float, gear: int) -> Optional[str]:
    """Generate throttle application coaching."""
    if brake > 0.2:
        return None  # Don't coach throttle during braking

    if throttle > 0.95 and brake < 0.05:
        if speed < 60:
            return "smooth throttle. Build speed gradually."
        elif speed > 150:
            return "full throttle. Hold it flat."

    if 0.3 < throttle < 0.7:
        return "intermediate throttle. Commit or lift."

    return None


def sector_coaching_vs_ref(sector: int, sector_time_ms: int, current_ref: dict,
                           personal_best_ms: int) -> Optional[str]:
    """Generate sector coaching comparing against reference AND personal best."""
    if not current_ref:
        # Fallback to PB-based coaching
        if personal_best_ms <= 0:
            return None
        delta = sector_time_ms - personal_best_ms
        delta_str = delta_description(delta)
        sector_names = {0: "sector one", 1: "sector two", 2: "sector three"}
        name = sector_names.get(sector, f"sector {sector + 1}")
        if delta > 0:
            return f"{name} {delta_str} vs your best."
        return None

    ref_sectors = current_ref.get("sectors", {})
    sector_key = f"S{sector + 1}"
    ref_s = ref_sectors.get(sector_key, 0)
    if ref_s <= 0:
        return None

    ref_ms = ref_s * 1000
    delta_vs_ref = sector_time_ms - ref_ms
    delta_vs_ref_str = delta_description(delta_vs_ref)

    sector_names = {0: "sector one", 1: "sector two", 2: "sector three"}
    name = sector_names.get(sector, f"sector {sector + 1}")
    car = current_ref.get("car_name", "reference")

    if delta_vs_ref > 500:
        return f"{name} {delta_vs_ref_str} versus {car} pace. Look for more."
    elif delta_vs_ref > 100:
        return f"{name} {delta_vs_ref_str} off {car} pace."
    elif delta_vs_ref < -200:
        return f"{name} faster than reference pace by {abs(delta_vs_ref_str)}!"
    elif delta_vs_ref > 0:
        return f"{name} {delta_vs_ref_str} versus reference."
    return None


def sector_coaching_pb(sector: int, sector_time_ms: int, pb_sector_ms: int) -> Optional[str]:
    """Generate sector-by-sector coaching vs personal best only."""
    if pb_sector_ms <= 0:
        return None

    delta = sector_time_ms - pb_sector_ms
    delta_str = delta_description(delta)

    sector_names = {0: "sector one", 1: "sector two", 2: "sector three"}
    name = sector_names.get(sector, f"sector {sector + 1}")

    if delta > 200:
        return f"{name} {delta_str}. Look for more exit speed."
    elif delta > 0:
        return f"{name} {delta_str}."
    elif delta < -100:
        return f"{name} new personal best, {delta_str}!"
    else:
        return f"{name} {delta_str}."


def consistency_coaching(lap_times: list, pb_ms: int) -> Optional[str]:
    """Check consistency and generate advice."""
    if len(lap_times) < 3:
        return None

    recent = lap_times[-5:]
    avg = statistics.mean(recent)
    stdev = statistics.stdev(recent) if len(recent) > 1 else 0

    if stdev > 500:
        return "laps are inconsistent. Focus on entry speed and hold your line."
    elif stdev > 200:
        return "reasonably consistent. Look for small improvements in each corner."
    elif stdev > 50:
        if avg > pb_ms + 500:
            return "consistent but slow. Try a different driving line."
        else:
            return "very consistent. Push harder in the fast corners."

    return None


def lap_complete_coaching(lap_time_ms: int, pb_ms: int, lap_count: int,
                          current_ref: dict, personal_best: dict) -> Optional[str]:
    """Generate lap completion coaching with absolute reference comparison."""
    lap_time_s = lap_time_ms / 1000

    if current_ref and current_ref.get("ref_lap"):
        ref_ms = current_ref["ref_lap"]
        delta_vs_ref = lap_time_ms - ref_ms
        delta_vs_ref_str = delta_description(delta_vs_ref)
        car = current_ref.get("car_name", "reference")

        if delta_vs_ref < 0:
            return f"new reference! {delta_vs_ref_str} under {car} pace. Lap saved."
        elif delta_vs_ref < 300:
            return f"good lap, {delta_vs_ref_str} off {car} pace. {lap_time_s:.1f} seconds."
        else:
            return f"lap complete, {lap_time_s:.1f}. {delta_vs_ref_str} off {car} pace."
    else:
        # No reference, use PB only
        if pb_ms <= 0:
            return f"lap complete, {lap_time_s:.1f} seconds."
        delta = lap_time_ms - pb_ms
        delta_str = delta_description(delta)
        if delta < 0:
            return f"new personal best! {delta_str}. Lap saved."
        elif delta < 200:
            return f"close to personal best, {delta_str}. One more try."
        else:
            return f"lap complete, {lap_time_s:.1f} seconds. {delta_str} off pace."


def generate_coaching(state: CoachingState, telemetry: dict) -> Optional[str]:
    """Main coaching generation logic. Returns a coaching phrase or None."""

    now = time.time()
    if now - state.last_coaching_time < state.cooldown_seconds:
        return None

    speed = telemetry.get("speed", 0)
    brake = telemetry.get("brake", 0)
    throttle = telemetry.get("throttle", 0)
    gear = telemetry.get("gear", 0)
    sector = telemetry.get("sector", 255)
    lap_time_ms = telemetry.get("lap_time_ms", 0)
    lap = telemetry.get("lap", 0)
    rpm = telemetry.get("rpm", 0)
    current_track = telemetry.get("current_track", "")
    current_car = telemetry.get("current_car", "")

    # Update reference pace data when track/car changes
    if current_track or current_car:
        ref = get_current_ref(state, telemetry)
        if ref and ref != state.current_ref:
            state.current_ref = ref
            print(f"[coach] Reference pace loaded: {ref['car_name']} @ {ref['track_name']}", flush=True)
            # Announce reference pace at session start
            if ref.get("ref_lap"):
                ref_s = ref["ref_lap"] / 1000
                speak(f"Reference pace loaded. {ref['car_name']} target lap: {ref_s:.1f} seconds.", async_=False)

    # Lap completion detection
    if lap > state.lap_count and lap_time_ms > 1000:
        coaching = lap_complete_coaching(
            lap_time_ms,
            state.personal_best.get("lap_ms", 0),
            lap,
            state.current_ref,
            state.personal_best
        )
        state.lap_count = lap
        state.last_coaching_time = now
        state.lap_times.append(lap_time_ms)

        # Update PB
        if state.personal_best.get("lap_ms", 0) == 0 or lap_time_ms < state.personal_best["lap_ms"]:
            state.personal_best["lap_ms"] = lap_time_ms
            save_pb(state.personal_best)

        if coaching and len(coaching) < 200:
            return coaching

    # Brake coaching
    brake_call = brake_coaching(speed, brake, throttle, gear,
                                 state.current_ref, telemetry, sector)
    if brake_call and brake_call != state.last_brake_callout:
        state.last_brake_callout = brake_call
        state.last_coaching_time = now
        return brake_call

    # Speed at braking zone (only if we have reference data)
    if state.current_ref and "braking_zones" in state.current_ref and brake > 0.5:
        speed_call = speed_at_braking_zone(speed, state.current_ref, sector)
        if speed_call and speed_call != state.last_brake_callout:
            # Don't spam - throttle cooldown
            if now - state.last_coaching_time > 8:
                state.last_brake_callout = speed_call
                state.last_coaching_time = now
                return speed_call

    # Throttle coaching
    throttle_call = throttle_coaching(throttle, brake, speed, gear)
    if throttle_call and throttle_call != state.last_throttle_callout:
        state.last_throttle_callout = throttle_call
        state.last_coaching_time = now
        return throttle_call

    # Consistency check every 3 laps
    if len(state.lap_times) >= 3 and len(state.lap_times) % 3 == 0:
        cons_call = consistency_coaching(state.lap_times, state.personal_best.get("lap_ms", 0))
        if cons_call:
            state.last_coaching_time = now
            return cons_call

    return None


def main():
    print("[coach] Zeus SimRace Coach v0.2", flush=True)
    print(f"[coach] Reading from: {STATE_FILE}", flush=True)

    state = CoachingState()
    state.personal_best = load_pb()
    state.pace_data = load_pace_data()
    print(f"[coach] Pace data loaded: {list(state.pace_data.keys())}", flush=True)
    print(f"[coach] Personal best: {state.personal_best}", flush=True)

    last_telemetry = {}

    print("[coach] Monitoring telemetry... Press Ctrl+C to stop.", flush=True)

    try:
        while True:
            telemetry = load_telemetry()

            if telemetry and telemetry != last_telemetry:
                coaching = generate_coaching(state, telemetry)
                if coaching:
                    print(f"[coach] >>> {coaching}", flush=True)
                    speak(coaching)

            last_telemetry = telemetry
            time.sleep(0.5)  # Poll at ~2Hz

    except KeyboardInterrupt:
        print("\n[coach] Shutdown.", flush=True)
        sys.exit(0)


if __name__ == "__main__":
    main()
