#!/usr/bin/env python3
"""
SimRacing AI Coach
Reads telemetry, generates coaching phrases, speaks them via TTS.
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
        return {}


def save_pb(pb):
    """Save personal best."""
    try:
        with open(PB_FILE, "w") as f:
            json.dump(pb, f)
    except Exception:
        pass


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
            return f"plus one tenth"
        elif secs < 1.0:
            return f"plus {int(secs * 10) * 2} hundredths"
        else:
            return f"plus {secs:.1f} seconds"
    else:
        secs = abs(delta_ms) / 1000
        if secs < 0.5:
            return f"minus one tenth"
        elif secs < 1.0:
            return f"minus {int(secs * 10) * 2} hundredths"
        else:
            return f"minus {secs:.1f} seconds"


def brake_coaching(speed: float, brake: float, throttle: float, gear: int) -> Optional[str]:
    """Generate brake point coaching."""
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


def sector_coaching(sector: int, sector_time_ms: int, pb_sector_ms: int) -> Optional[str]:
    """Generate sector-by-sector coaching."""
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


def lap_complete_coaching(lap_time_ms: int, pb_ms: int, lap_count: int) -> Optional[str]:
    """Generate lap completion coaching."""
    if pb_ms <= 0:
        return f"lap complete, {lap_time_ms / 1000:.1f} seconds."

    delta = lap_time_ms - pb_ms
    delta_str = delta_description(delta)

    if delta < 0:
        return f"new personal best! {delta_str}. Lap saved."
    elif delta < 200:
        return f"close to personal best, {delta_str}. One more try."
    else:
        return f"lap complete, {lap_time_ms / 1000:.1f} seconds. {delta_str} off pace."


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

    # Lap completion detection
    if lap > state.lap_count and lap_time_ms > 1000:
        coaching = lap_complete_coaching(lap_time_ms, state.personal_best.get("lap_ms", 0), lap)
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
    brake_call = brake_coaching(speed, brake, throttle, gear)
    if brake_call and brake_call != state.last_brake_callout:
        state.last_brake_callout = brake_call
        state.last_coaching_time = now
        return brake_call

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
    print("[coach] Zeus SimRace Coach v0.1", flush=True)
    print(f"[coach] Reading from: {STATE_FILE}", flush=True)

    state = CoachingState()
    state.personal_best = load_pb()
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
