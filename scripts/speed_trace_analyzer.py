#!/usr/bin/env python3
"""
Speed Trace Analyzer for SimRace Coach.
Compares real-time speed/brake/throttle traces against reference profiles
to generate precise corner-by-corner coaching.

Modes:
  - absolute: vs community reference pace
  - self_calibrating: vs your own average (learns as you drive)
"""

import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Callable

# ─── Coach phrase builders ───────────────────────────────────────────────────


def brake_too_early(speed_at_marker: float, ref_speed: float, dist_delta: float) -> str:
    """You're braking earlier than the reference -> coach to brake later.
    
    Only fires for significant deviations (>= 20 km/h difference).
    """
    delta_kmh = speed_at_marker - ref_speed
    if delta_kmh < -20:
        # Only call out if braking MUCH earlier than reference
        meters_early = round(abs(delta_kmh) / 2)
        if meters_early < 5:
            return None
        return f"brake {meters_early} meters later"
    return None


def brake_too_late(speed_at_marker: float, ref_speed: float) -> str:
    """You're braking later than reference (or overspeed)."""
    delta_kmh = speed_at_marker - ref_speed
    if delta_kmh > 20:
        return "you need to brake earlier, you're late"
    return None


def too_wide(entry_speed: float, ref_entry: float) -> str:
    """Carrying too much speed into the corner (wide line = poor apex).
    
    Only fires for >= 25 km/h excess entry speed.
    """
    if entry_speed > ref_entry + 25:
        return "you're too wide, apex later"
    return None


def too_tight(entry_speed: float, ref_entry: float) -> str:
    """Not enough entry speed (tight line = compromised exit).
    
    Only fires for >= 30 km/h deficit.
    """
    if entry_speed < ref_entry - 30:
        return "you're too tight, apex later for better exit"
    return None


def slow_apex(speed_at_apex: float, ref_apex: float) -> str:
    """Apex speed too low vs reference."""
    if ref_apex > 0 and speed_at_apex < ref_apex - 20:
        return "get back on throttle sooner, you're crawling"
    return None


def early_throttle_lift(speed: float, ref_exit: float, throttle: float,
                        corner_name: str) -> Optional[str]:
    """Lifting throttle early mid-corner or at exit."""
    # Only fire if nearly off throttle AND well below reference exit speed
    if throttle < 0.05 and speed > ref_exit * 0.7 and ref_exit > 0:
        return f"don't lift in {corner_name}, commitment through the corner"
    return None


def trail_brake_not_deep_enough(brake_pressure: float, ref_min_pressure: float,
                                 corner_name: str) -> Optional[str]:
    """Brake pressure dropping too early (not trail-braking deep enough)."""
    # Only call out if actively braking mid-corner (major technique error)
    if 0.15 < brake_pressure < ref_min_pressure * 0.5 and ref_min_pressure > 0:
        return f"trail brake deeper into {corner_name}"
    return None


def wheelspin_on_exit(throttle: float, speed: float, steer: float) -> Optional[str]:
    """High throttle + low speed + straightening = wheelspin waste."""
    # Only fire if clear wheelspin (not just heavy acceleration)
    if throttle > 0.9 and speed < 50 and abs(steer) < 0.15:
        return "ease off the throttle carefully, wheelspin is wasting energy"
    return None


def exit_too_slow(speed_at_exit: float, ref_exit: float, throttle: float) -> str:
    """Exit speed below reference despite throttle applied."""
    if ref_exit > 0 and speed_at_exit < ref_exit - 25 and throttle > 0.85:
        return "earlier throttle application, you're slow on exit"
    return None


def driving_line_too_early(steer_rate: float, ref_steer_rate: float) -> str:
    """Steering input too early = early turn-in = tight line."""
    # Disabled in absolute mode — reference is too rough for this check
    return None


# ─── Speed Trace Buffer ─────────────────────────────────────────────────────


@dataclass
class CornerSnapshot:
    """A captured corner entry/ exit sequence."""
    corner_key: str           # e.g. "T1", "T3_EauRouge"
    lap_start_time: float
    entry_times: list = field(default_factory=list)   # [time, ...]
    entry_speeds: list = field(default_factory=list)  # [speed_kmh, ...]
    entry_brakes: list = field(default_factory=list)  # [brake_0to1, ...]
    entry_throttles: list = field(default_factory=list)
    entry_steers: list = field(default_factory=list)
    steer_rate_profile: list = field(default_factory=list)  # steering rate over time
    apex_speed: float = 0.0
    apex_idx: int = -1
    exit_speed: float = 0.0
    exit_idx: int = -1
    # absolute reference (copied from pace_data at capture time)
    ref_entry_speed: float = 0.0
    ref_apex_speed: float = 0.0
    ref_exit_speed: float = 0.0
    ref_brake_start_speed: float = 0.0


class SpeedTraceAnalyzer:
    """
    Analyzes speed traces per corner.
    
    Call `update(speed, brake, throttle, steer, gear, rpm)` every telemetry tick.
    Call `detect_corner_entry()` after each update to check for corner transitions.
    Call `evaluate()` to get coaching strings for the last completed corner.
    
    Modes:
      absolute  – compare against reference profiles from pace_data
      self_calibrating – compare against rolling average of your own laps
    """

    # How many seconds of history to keep in the rolling buffer
    BUFFER_SECONDS = 5.0
    # Minimum time between two corner entries (avoids double-triggering)
    CORNER_DEBOUNCE_SEC = 12.0

    def __init__(self, mode: str = "absolute"):
        assert mode in ("absolute", "self_calibrating"), f"Unknown mode: {mode}"
        self.mode = mode

        # Rolling sample buffer: list of (timestamp, speed, brake, throttle, steer)
        self._buffer: deque = deque(maxlen=500)

        # Corner snapshot being built
        self._snapshot: Optional[CornerSnapshot] = None

        # Last corner key + time (debounce)
        self._last_corner_key: str = ""
        self._last_corner_time: float = 0.0

        # Completed snapshots (for calibration)
        self._completed: list[CornerSnapshot] = []

        # Per-corner rolling statistics (for self_calibrating mode)
        # structure: { corner_key: { "entries": [speed_at_entry, ...], ... } }
        self._cal_data: dict = {}

        # Current corner being evaluated (filled by evaluate())
        self._last_coaching: list[str] = []
        self._last_corner_key_done: str = ""

        # Corner detection thresholds
        self._steer_threshold = 0.25   # steering magnitude to trigger entry detection
        self._speed_threshold = 30.0   # minimum speed to consider a corner
        self._brake_threshold = 0.05   # brake > this = braking zone

        # Reference data (injected from outside)
        self._corner_profiles: dict = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def set_corner_profiles(self, profiles: dict):
        """Inject per-track per-corner speed profiles from pace_data."""
        self._corner_profiles = profiles

    def update(self, speed: float, brake: float, throttle: float,
               steer: float, gear: int, rpm: int, timestamp: float):
        """Feed a telemetry sample. Call every tick (~10-20Hz)."""
        self._buffer.append((timestamp, speed, brake, throttle, steer))

    def detect_corner_entry(self, current_sector: int, current_corner_key: str):
        """
        Check if we've just entered a new corner.
        Called from outside with the current corner key derived from sector/track position.
        Returns True if a new corner entry was just detected.
        """
        now = time.time()
        if (current_corner_key == self._last_corner_key
                or now - self._last_corner_time < self.CORNER_DEBOUNCE_SEC):
            return False

        # If we were building a snapshot, finalize it as "completed"
        if self._snapshot is not None:
            self._finalize_snapshot()
            self._last_corner_key_done = self._snapshot.corner_key

        # Start new snapshot
        ref = self._corner_profiles.get(current_corner_key, {})
        self._snapshot = CornerSnapshot(
            corner_key=current_corner_key,
            lap_start_time=now,
            ref_entry_speed=ref.get("entry_speed", 0.0),
            ref_apex_speed=ref.get("apex_speed", 0.0),
            ref_exit_speed=ref.get("exit_speed", 0.0),
            ref_brake_start_speed=ref.get("brake_start_speed", 0.0),
        )
        self._last_corner_key = current_corner_key
        self._last_corner_time = now
        return True

    def evaluate(self) -> list[str]:
        """
        Analyze the last completed corner and return coaching phrases.
        Returns [] if no completed corner is available.
        """
        if not self._completed:
            return []
        snap = self._completed[-1]
        if snap.corner_key == self._last_corner_key_done:
            # Already evaluated
            return self._last_coaching

        self._last_corner_key_done = snap.corner_key
        self._last_coaching = self._analyze_snapshot(snap)
        return self._last_coaching

    def calibrate_from_snap(self, snap: CornerSnapshot):
        """
        Add a completed corner snapshot to the calibration data.
        Used in self_calibrating mode to build per-corner averages.
        """
        key = snap.corner_key
        if key not in self._cal_data:
            self._cal_data[key] = {
                "entry_speeds": [],
                "apex_speeds": [],
                "exit_speeds": [],
            }
        d = self._cal_data[key]
        if snap.apex_speed > 0:
            d["entry_speeds"].append(snap.entry_speeds[-1] if snap.entry_speeds else 0)
            d["apex_speeds"].append(snap.apex_speed)
            d["exit_speeds"].append(snap.exit_speed)

    def get_calibration_summary(self) -> dict:
        """Return current calibration averages for all seen corners."""
        result = {}
        for key, d in self._cal_data.items():
            avg_entry = statistics.mean(d["entry_speeds"]) if d["entry_speeds"] else 0
            avg_apex = statistics.mean(d["apex_speeds"]) if d["apex_speeds"] else 0
            avg_exit = statistics.mean(d["exit_speeds"]) if d["exit_speeds"] else 0
            result[key] = {
                "avg_entry": round(avg_entry, 1),
                "avg_apex": round(avg_apex, 1),
                "avg_exit": round(avg_exit, 1),
                "samples": len(d["entry_speeds"]),
            }
        return result

    def reset_calibration(self):
        """Clear all calibration data."""
        self._cal_data = {}
        self._completed = []

    # ── Internal ──────────────────────────────────────────────────────────────

    def _finalize_snapshot(self):
        """Process the current snapshot into entry/apex/exit data."""
        if self._snapshot is None:
            return
        snap = self._snapshot

        if len(snap.entry_speeds) < 2:
            self._snapshot = None
            return

        # Apex: point of minimum speed in the middle 60% of the corner
        n = len(snap.entry_speeds)
        window = snap.entry_speeds[n // 5: 4 * n // 5]  # middle 60%
        if window:
            snap.apex_idx = snap.entry_speeds.index(min(window)) + n // 5
            snap.apex_speed = min(window)

        # Exit speed: speed at the last 20% of recorded corner data
        if snap.entry_speeds:
            exit_idx = max(0, len(snap.entry_speeds) - 3)
            snap.exit_idx = exit_idx
            snap.exit_speed = snap.entry_speeds[exit_idx]

        # Steer rate profile
        if len(snap.entry_steers) > 1:
            rates = [abs(snap.entry_steers[i] - snap.entry_steers[i-1])
                     for i in range(1, len(snap.entry_steers))]
            snap.steer_rate_profile = rates

        self._completed.append(snap)
        self._snapshot = None

    def _analyze_snapshot(self, snap: CornerSnapshot) -> list[str]:
        """Generate coaching phrases for a completed corner snapshot."""
        phrases: list[str] = []
        n = len(snap.entry_speeds)
        if n < 3:
            return phrases

        # ── Entry speed (first reading, ~50m before turn-in)
        entry_speed = snap.entry_speeds[0]
        ref_entry = snap.ref_entry_speed
        ref_apex = snap.ref_apex_speed
        ref_exit = snap.ref_exit_speed
        ref_brake = snap.ref_brake_start_speed

        # ── Self-calibrating overrides
        if self.mode == "self_calibrating" and snap.corner_key in self._cal_data:
            d = self._cal_data[snap.corner_key]
            if d["entry_speeds"]:
                ref_entry = statistics.mean(d["entry_speeds"])
            if d["apex_speeds"]:
                ref_apex = statistics.mean(d["apex_speeds"])
            if d["exit_speeds"]:
                ref_exit = statistics.mean(d["exit_speeds"])

        # ── Entry analysis (braking zone)
        if ref_brake > 0:
            entry_delta = entry_speed - ref_brake
            if entry_delta < -10:
                ph = brake_too_early(entry_speed, ref_brake, entry_delta)
                if ph:
                    phrases.append(ph)
            elif entry_delta > 10:
                ph = brake_too_late(entry_speed, ref_brake)
                if ph:
                    phrases.append(ph)

        # ── Entry speed vs reference entry
        if ref_entry > 0:
            if entry_speed > ref_entry + 12:
                phrases.append(too_wide(entry_speed, ref_entry))
            elif entry_speed < ref_entry - 20:
                phrases.append(too_tight(entry_speed, ref_entry))

        # ── Apex speed
        if snap.apex_speed > 0 and ref_apex > 0:
            ph = slow_apex(snap.apex_speed, ref_apex)
            if ph:
                phrases.append(ph)

        # ── Exit speed
        if ref_exit > 0:
            ph = exit_too_slow(snap.exit_speed, ref_exit, snap.entry_throttles[-1] if snap.entry_throttles else 0)
            if ph:
                phrases.append(ph)

        # ── Brake pressure analysis (trail-brake detection)
        if len(snap.entry_brakes) >= 3:
            # Check if brake drops off early in the corner
            mid = snap.entry_brakes[n // 2] if n // 2 < len(snap.entry_brakes) else 0
            first = snap.entry_brakes[0]
            if first > 0.3 and mid < 0.1:
                # Trail-braking confirmed (big brake up front, lifted for apex)
                pass  # This is actually correct technique, no phrase needed
            elif first < 0.1 and mid > 0.2:
                # Brake getting applied INSIDE the corner — not ideal
                phrases.append(f"brake before the corner in {snap.corner_key}, not inside")

        # ── Throttle at exit
        if snap.entry_throttles:
            last_throttle = snap.entry_throttles[-1]
            ph = early_throttle_lift(snap.exit_speed, ref_exit, last_throttle, snap.corner_key)
            if ph:
                phrases.append(ph)

        # ── Wheelspin on exit
        if snap.entry_throttles and snap.entry_speeds:
            last_t = snap.entry_throttles[-1]
            last_s = snap.entry_speeds[-1]
            last_steer = snap.entry_steers[-1] if snap.entry_steers else 0.0
            ph = wheelspin_on_exit(last_t, last_s, last_steer)
            if ph:
                phrases.append(ph)

        # ── Steering rate (early turn-in detection)
        if len(snap.steer_rate_profile) > 2:
            avg_steer_rate = statistics.mean(snap.steer_rate_profile)
            # Reference steer rate is just the observed average — we use self-calibration
            # For absolute mode, compare against a heuristic
            if self.mode == "self_calibrating" and snap.corner_key in self._cal_data:
                cal = self._cal_data[snap.corner_key]
                if cal["entry_speeds"] and len(cal["entry_speeds"]) >= 3:
                    # If current entry is much slower than average, we turned in too early
                    if entry_speed < statistics.mean(cal["entry_speeds"]) - 15:
                        phrases.append("turn in later, you're cutting inside early")

        # Deduplicate and LIMIT TO 1 PHRASE PER CORNER (most important only)
        seen = set()
        unique = []
        for p in phrases:
            normalized = p.lower()
            if normalized not in seen:
                seen.add(normalized)
                unique.append(p)
            if len(unique) >= 1:  # Max 1 coaching phrase per corner
                break
                unique.append(p)

        return unique

    # ── Buffer access ────────────────────────────────────────────────────────

    def get_buffer_speeds(self) -> list:
        """Return all speeds in the rolling buffer (oldest first)."""
        return [s for _, s, _, _, _ in self._buffer]

    def get_recent_sample(self, seconds_back: float) -> Optional[tuple]:
        """Get the telemetry sample from ~seconds_back ago."""
        now = time.time()
        for ts, spd, brk, thr, str_ in reversed(self._buffer):
            if now - ts >= seconds_back:
                return (spd, brk, thr, str_)
        return None


# ─── Corner Profile Builder ──────────────────────────────────────────────────
# Builds reference corner profiles from the global pace_data.


def build_corner_profiles(pace_data: dict, track_key: str, sim: str) -> dict:
    """
    Construct per-corner speed profiles for a given track from pace_data.

    Returns a dict: { "T1": { "entry_speed": 280, "apex_speed": 95, ... }, ... }

    The profiles are inferred from braking zone data + physics heuristics.
    """
    if sim not in pace_data:
        return {}
    sim_data = pace_data[sim]
    car = sim_data.get("car", "")
    tracks = sim_data.get("tracks", {})
    if track_key not in tracks:
        return {}
    track = tracks[track_key]

    bz_list = track.get("braking_zones", [])
    az_list = track.get("acceleration_zones", [])

    profiles = {}

    for bz in bz_list:
        zone_name = bz.get("zone", "")
        tip = bz.get("tip", "")
        severity = bz.get("severity", "medium")

        # Extract T-number from zone name: "T1 (La Source)" -> "T1"
        import re
        t_match = re.search(r"T?\d+[A-Z]?", zone_name)
        corner_key = t_match.group(0) if t_match else zone_name.split()[0] if zone_name else ""

        # Heuristic: severity determines approach speed
        if severity == "very hard":
            # Top speed GT3/F1 = ~320 km/h (ACC) or ~330 km/h (AMS2 F-V10)
            entry_speed = 295 if sim == "acc" else 305
            apex_speed = 90 if "source" in zone_name.lower() or "la source" in zone_name.lower() else 110
        elif severity == "hard":
            entry_speed = 250 if sim == "acc" else 265
            apex_speed = 100
        elif severity == "medium":
            entry_speed = 180 if sim == "acc" else 195
            apex_speed = 120
        else:
            entry_speed = 140 if sim == "acc" else 150
            apex_speed = 105

        profiles[corner_key] = {
            "zone_name": zone_name,
            "entry_speed": entry_speed,
            "apex_speed": apex_speed,
            "exit_speed": round(apex_speed * 1.35),   # rough estimate
            "brake_start_speed": entry_speed,
            "tip": tip,
            "severity": severity,
        }

    return profiles
