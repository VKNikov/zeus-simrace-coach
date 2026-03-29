#!/usr/bin/env python3
"""
SimRacing AI Coach
Reads telemetry, generates coaching phrases, speaks them via TTS.
Uses community pace data for absolute reference comparison.
Speed trace analyzer provides per-corner coaching (brake 10m early,
too wide, trail brake deeper, etc.).
"""

import json
import time
import os
import subprocess
import sys
import statistics
import re
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

# Speed trace analyzer
sys.path.insert(0, str(Path(__file__).parent))
from speed_trace_analyzer import (
    SpeedTraceAnalyzer, build_corner_profiles, CornerSnapshot
)


@dataclass
class CoachingState:
    """Tracks coaching state across laps."""
    level: str = "intermediate"   # beginner, intermediate, advanced
    trace_mode: str = "absolute"  # absolute | self_calibrating
    last_sector_time: int = 0
    last_lap_time: int = 0
    last_sector: int = 0
    lap_count: int = 0
    sector_times: list = field(default_factory=list)
    lap_times: list = field(default_factory=list)
    consistency_scores: list = field(default_factory=list)
    personal_best: dict = field(default_factory=dict)
    last_coaching_time: float = 0
    cooldown_seconds: float = 3.0   # Minimum seconds between coaching calls
    last_brake_callout: str = ""
    last_throttle_callout: str = ""
    last_corner_callout: str = ""
    corner_callout_cooldown: float = 12.0  # seconds between corner callouts
    # Current reference data
    current_ref: dict = field(default_factory=dict)
    pace_data: dict = field(default_factory=dict)
    # Speed trace analyzer
    trace_analyzer: Optional[SpeedTraceAnalyzer] = None
    # Last known corner key (for change detection)
    last_corner_key: str = ""
    # Corner profiles for current track
    corner_profiles: dict = field(default_factory=dict)
    # Track corners list for sequential corner detection
    track_corners: list = field(default_factory=list)
    corner_index: int = 0


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
    """Get reference pace data for the current track/car combo.
    
    pace_data.json structure:
      ams2:
        car: "Formula V10 Gen2"
        tracks:
          imola_2001: { track_name, pro_pace, good_pace, avg_pace, braking_zones[], acceleration_zones[] }
          monza_gp: { ... }
          spa_1993: { ... }
          silverstone_2001: { ... }
          catalunya_gp: { ... }
          interlagos: { ... (includes stock_cruze_22 with user data) }
      acc:
        car: "BMW M4 GT3 2022"
        tracks:
          spa: { track_name, pro_pace_s1/s2/s3, pro_pace_lap, good_pace, avg_pace, ... }
          monza: { ... }
          ... (24 tracks)
    Also builds corner profiles for the speed trace analyzer.
    """
    track = telemetry.get("current_track", "")
    car = telemetry.get("current_car", "")
    sim = telemetry.get("sim", "")  # "ams2", "acc", "ac", or similar

    if not track:
        return None

    track_lower = track.lower()
    car_lower = car.lower() if car else ""

    pace = state.pace_data

    # Determine which sim section to use
    sim_section = None
    sim_key = ""
    if sim == "ams2" and "ams2" in pace:
        sim_section = pace["ams2"]
        sim_key = "ams2"
    elif sim == "acc" and "acc" in pace:
        sim_section = pace["acc"]
        sim_key = "acc"
    else:
        # Fallback: search both sections
        for key in ["ams2", "acc"]:
            if key in pace:
                tracks = pace[key].get("tracks", {})
                for tk, td in tracks.items():
                    if tk.lower() in track_lower or track_lower in tk.lower():
                        sim_section = pace[key]
                        sim_key = key
                        break
                if sim_section:
                    break

    if not sim_section:
        return None

    tracks = sim_section.get("tracks", {})

    # Find matching track
    matched_track = None
    for track_key, track_data in tracks.items():
        if track_key.lower() in track_lower or track_lower in track_key.lower():
            matched_track = (track_key, track_data)
            break

    if not matched_track:
        return None

    track_key, track_data = matched_track
    ref_car = sim_section.get("car", car or "reference")

    # Build reference dict
    ref = {
        "track_key": track_key,
        "track_name": track_data.get("track_name", track_key),
        "car_name": ref_car,
        "sim": sim_key,
    }

    # AMS2 format: pro_pace, good_pace, avg_pace (flat seconds)
    if sim_key == "ams2":
        pro = track_data.get("pro_pace", 0)
        good = track_data.get("good_pace", 0)
        avg = track_data.get("avg_pace", 0)
        ref["pro_pace_ms"] = pro * 1000
        ref["good_pace_ms"] = good * 1000
        ref["avg_pace_ms"] = avg * 1000
        ref["ref_lap"] = pro * 1000
        ref["braking_zones"] = track_data.get("braking_zones", [])
        ref["acceleration_zones"] = track_data.get("acceleration_zones", [])

        # Special case: Stock Car with user data
        if "stock_cruze_22" in track_data:
            sc = track_data["stock_cruze_22"]
            ref["user_best_lap"] = sc.get("user_best_lap", 0) * 1000
            ref["community_top"] = sc.get("community_top", 0) * 1000

    # ACC format: pro_pace_s1/s2/s3, pro_pace_lap (seconds)
    else:
        ref["pro_pace_s1_ms"] = track_data.get("pro_pace_s1", 0) * 1000
        ref["pro_pace_s2_ms"] = track_data.get("pro_pace_s2", 0) * 1000
        ref["pro_pace_s3_ms"] = track_data.get("pro_pace_s3", 0) * 1000
        ref["pro_pace_lap_ms"] = track_data.get("pro_pace_lap", 0) * 1000
        ref["good_pace_ms"] = track_data.get("good_pace", 0) * 1000
        ref["avg_pace_ms"] = track_data.get("avg_pace", 0) * 1000
        ref["ref_lap"] = track_data.get("pro_pace_lap", 0) * 1000
        ref["braking_zones"] = track_data.get("braking_zones", [])
        ref["acceleration_zones"] = track_data.get("acceleration_zones", [])

    # ── Build corner profiles for speed trace analyzer ──────────────────────
    profiles = build_corner_profiles(pace, track_key, sim_key)
    if profiles:
        state.corner_profiles = profiles
        state.track_corners = list(profiles.keys())
        # Init or reinit the trace analyzer with the new profiles
        state.trace_analyzer = SpeedTraceAnalyzer(mode=state.trace_mode)
        state.trace_analyzer.set_corner_profiles(profiles)
        print(f"[coach] Corner profiles loaded: {len(profiles)} corners — {list(profiles.keys())}", flush=True)

    return ref


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
    """Compare current speed at a braking zone vs reference, return coaching.
    
    Braking zones in pace_data are a list of {zone, severity, tip} objects.
    Zone names include the turn name. We match against the zone string.
    """
    if not current_ref or not current_ref.get("braking_zones"):
        return None

    bz_list = current_ref["braking_zones"]
    if not isinstance(bz_list, list):
        return None

    speed_kmh = int(speed)

    # Match braking zone by severity in the current sector area
    # Zone names contain turn identifiers — we use severity + tip for coaching
    for bz in bz_list:
        severity = bz.get("severity", "")
        tip = bz.get("tip", "")
        zone_name = bz.get("zone", "")

        # Hard/very hard braking zones get coaching
        if severity in ("hard", "very hard") and tip and speed_kmh > 60:
            # Don't coach every frame — only when speed is relevant
            return f"{zone_name}. {tip}"

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
    """Generate sector coaching comparing against reference AND personal best.

    ACC refs have pro_pace_s1_ms, pro_pace_s2_ms, pro_pace_s3_ms.
    AMS2 refs have a single pro_pace_ms (no per-sector reference).
    """
    if not current_ref:
        if personal_best_ms <= 0:
            return None
        delta = sector_time_ms - personal_best_ms
        delta_str = delta_description(delta)
        sector_names = {0: "sector one", 1: "sector two", 2: "sector three"}
        name = sector_names.get(sector, f"sector {sector + 1}")
        if delta > 0:
            return f"{name} {delta_str} vs your best."
        return None

    sector_names = {0: "sector one", 1: "sector two", 2: "sector three"}
    name = sector_names.get(sector, f"sector {sector + 1}")
    car = current_ref.get("car_name", "reference")

    # ACC: per-sector reference
    if current_ref["sim"] == "acc":
        ref_s_keys = ["pro_pace_s1_ms", "pro_pace_s2_ms", "pro_pace_s3_ms"]
        ref_key = ref_s_keys[sector] if sector < 3 else None
        ref_ms = current_ref.get(ref_key, 0) if ref_key else 0

        if ref_ms > 0:
            delta_vs_ref = sector_time_ms - ref_ms
            delta_vs_ref_str = delta_description(delta_vs_ref)
            if delta_vs_ref > 500:
                return f"{name} {delta_vs_ref_str} versus {car} pace. Look for more."
            elif delta_vs_ref > 100:
                return f"{name} {delta_vs_ref_str} off {car} pace."
            elif delta_vs_ref < -200:
                return f"{name} faster than reference pace! Excellent."
            elif delta_vs_ref > 0:
                return f"{name} {delta_vs_ref_str} versus reference."
            return None

    # AMS2: single lap reference, no per-sector breakdown
    # Fall back to PB comparison
    if personal_best_ms > 0:
        delta = sector_time_ms - personal_best_ms
        if delta > 200:
            return f"{name} {delta_description(delta)} vs your PB."
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
    sector = telemetry.get("current_sector", 255)
    lap_time_ms = telemetry.get("lap_time_ms", 0)
    lap = telemetry.get("lap", 0)
    rpm = telemetry.get("rpm", 0)
    steer = telemetry.get("steer", 0)
    current_track = telemetry.get("current_track", "")
    current_car = telemetry.get("current_car", "")
    ts = telemetry.get("timestamp", now)

    # ── Update reference pace data when track/car changes ───────────────────
    if current_track or current_car:
        ref = get_current_ref(state, telemetry)
        if ref and ref != state.current_ref:
            state.current_ref = ref
            track_n = ref.get("track_name", "?")
            car_n = ref.get("car_name", "?")
            ref_ms = ref.get("ref_lap", 0)
            print(f"[coach] Reference pace loaded: {car_n} @ {track_n}", flush=True)
            if ref_ms > 0:
                ref_s = ref_ms / 1000
                speak(f"Reference pace loaded. {car_n} at {track_n}. Target: {ref_s:.1f} seconds.", async_=False)
            # Reset corner index on track change
            state.corner_index = 0

    # ── Speed trace analyzer: feed telemetry ────────────────────────────────
    if state.trace_analyzer is not None:
        state.trace_analyzer.update(
            speed=speed,
            brake=brake,
            throttle=throttle,
            steer=steer,
            gear=gear,
            rpm=rpm,
            timestamp=ts,
        )

        # Detect corner entry via sector change
        prev_sector = state.last_sector
        if sector != prev_sector and prev_sector != 255:
            # Sector changed — evaluate the corner we just finished
            if state.trace_analyzer is not None and state.last_corner_key:
                phrases = state.trace_analyzer.evaluate()
                if phrases:
                    for ph in phrases:
                        if (now - state.last_coaching_time >= state.cooldown_seconds
                                and ph != state.last_corner_callout):
                            state.last_corner_callout = ph
                            state.last_coaching_time = now
                            corner_anounce = f"corner check. {ph}"
                            print(f"[coach] [corner] >>> {corner_anounce}", flush=True)
                            return corner_anounce

            # Advance to next corner in sequence
            if state.track_corners and state.corner_index < len(state.track_corners) - 1:
                state.corner_index += 1

        # Also detect corner entry via steering spike (independent of sector)
        # Only trigger if speed is in corner range and we're not braking hard on a straight
        if (speed > 40 and speed < 260
                and abs(steer) > 0.4   # strong steering input
                and brake < 0.6        # not ABS-fixing a lock-up
                and state.trace_analyzer is not None):
            corner_key = (state.track_corners[state.corner_index]
                          if state.track_corners and state.corner_index < len(state.track_corners)
                          else f"S{sector}_corner")
            entered = state.trace_analyzer.detect_corner_entry(sector, corner_key)
            if entered:
                state.last_corner_key = corner_key
                state.last_sector = sector
                return None  # No phrase yet — wait for corner to complete

        state.last_sector = sector

    # ── Lap completion detection ────────────────────────────────────────────
    if lap > state.lap_count and lap_time_ms > 1000:
        # Evaluate any remaining corner
        if state.trace_analyzer is not None and state.last_corner_key:
            phrases = state.trace_analyzer.evaluate()
            for ph in phrases:
                if ph != state.last_corner_callout:
                    state.last_corner_callout = ph
                    corner_anounce = f"corner check. {ph}"
                    print(f"[coach] [corner] >>> {corner_anounce}", flush=True)
                    speak(corner_anounce)

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
        state.corner_index = 0   # reset at lap start

        # Update PB
        if state.personal_best.get("lap_ms", 0) == 0 or lap_time_ms < state.personal_best["lap_ms"]:
            state.personal_best["lap_ms"] = lap_time_ms
            save_pb(state.personal_best)

        if coaching and len(coaching) < 200:
            return coaching

    # ── Brake coaching (high-level) ──────────────────────────────────────
    brake_call = brake_coaching(speed, brake, throttle, gear,
                                 state.current_ref, telemetry, sector)
    if brake_call and brake_call != state.last_brake_callout:
        state.last_brake_callout = brake_call
        state.last_coaching_time = now
        return brake_call

    # ── Speed at braking zone (only if we have reference data) ─────────────
    if (state.current_ref and state.current_ref.get("braking_zones")
            and brake > 0.5 and speed > 50):
        speed_call = speed_at_braking_zone(speed, state.current_ref, sector)
        if speed_call and speed_call != state.last_brake_callout:
            if now - state.last_coaching_time > 8:
                state.last_brake_callout = speed_call
                state.last_coaching_time = now
                return speed_call

    # ── Throttle coaching ─────────────────────────────────────────────────
    throttle_call = throttle_coaching(throttle, brake, speed, gear)
    if throttle_call and throttle_call != state.last_throttle_callout:
        state.last_throttle_callout = throttle_call
        state.last_coaching_time = now
        return throttle_call

    # ── Consistency check every 3 laps ────────────────────────────────────
    if len(state.lap_times) >= 3 and len(state.lap_times) % 3 == 0:
        cons_call = consistency_coaching(state.lap_times, state.personal_best.get("lap_ms", 0))
        if cons_call:
            state.last_coaching_time = now
            return cons_call

    return None


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Zeus SimRace Coach")
    parser.add_argument(
        "--mode", "-m",
        choices=["absolute", "self_calibrating"],
        default="absolute",
        help="absolute: compare vs community reference. "
             "self_calibrating: compare vs your own rolling average.",
    )
    args = parser.parse_args()

    print("[coach] Zeus SimRace Coach v0.3", flush=True)
    print(f"[coach] Mode: {args.mode}", flush=True)
    print(f"[coach] Reading from: {STATE_FILE}", flush=True)

    state = CoachingState()
    state.pace_mode = args.mode
    state.trace_mode = args.mode
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
