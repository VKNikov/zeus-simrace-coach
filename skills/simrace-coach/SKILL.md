---
name: simrace-coach
description: Real-time AI simrace coaching using local STT/TTS. Provides voice coaching for Assetto Corsa Competizione, Assetto Corsa, and Automobilista 2 using telemetry from UDP. Use when user wants to improve lap times, needs race analysis, asks about telemetry, or wants coaching setup. Triggers on: "coach me", "simrace", "lap analysis", "telemetry", "brake point", "racing line", "sector time", "how do I improve", "ACC coaching", "AMS2 coaching", "Assetto Corsa coaching".
---

# SimRace Coach Skill

Turns Zeus into your personal real-time simrace coach using local TTS (sherpa-onnx) and Whisper STT.

## How It Works

```
Sim (ACC/AC/AMS2)
    └─ UDP broadcast (port 9000/9996/9900)
           └─ scripts/telemetry_reader.py → ~/.openclaw/var/simrace_telemetry.json
                  └─ scripts/coach.py → reads telemetry + generates coaching
                         └─ speak.ps1 (sherpa-onnx TTS) → voice in your headset
```

**Phase 1** (now): Manual coaching — you share lap data or screenshots, Zeus analyzes and tells you what to work on.

**Phase 2** (after setup): Live coaching — telemetry reader runs in background, coach.py generates phrases, TTS speaks to you in real time while you drive.

## Phase 1: Manual Coaching

When the user shares lap data or asks for advice:

### Analyze Lap Times

```
Input: lap time, sector times, track, car
Output: coaching priorities

Example:
User: "2:01.2 at Silverstone GP, BMW M4 GT3, sectors: 43.2 / 38.1 / 39.9"
→ "S2 is your weak point, 38.1 is nearly a second slow vs your sector 1.
  Focus on trail braking into Club and apex speed on the exits.
  Your S3 is solid. Main gain is in the Complex and Village."
```

### Sector Analysis Template

For each sector, compare against:
- Entry speed vs optimal
- Brake point (early/on-time/late)
- Apex speed
- Exit throttle application
- Track position (wide/mid/narrow)

Generate ranked fix list:
1. "Biggest time gain" — specific corner + specific action
2. "Second priority" — next area
3. "Quick wins" — things requiring small changes

### Common Coaching Calls

```
Brake too early: "You're giving up entry speed. Brake one car length later and carry more momentum."
Brake too late: "You're locking up. Brake earlier, smoother pressure, trail brake into the apex."
Throttle too early: "Early throttle is killing your exit. Get the car settled before applying power."
Throttle too aggressive: "Wheelspin on exit. Ease off the throttle smoothly to hook up the car."
Steering too sharp: "Your inputs are too jerky. Smooth out the steering for better weight transfer."
Off throttle: "You lifted — that costs time in the corners. Stay on it through the corner."
Track limits: "You ran wide at the exit. Shorten your radius to keep full throttle on the next straight."
Consistent but slow: "Your laps are consistent, which is good. But you're slow in the middle sector. Look at your entry speed into T7."
```

## Phase 2: Live Coaching Setup

### 1. Run Telemetry Reader

In a terminal (PowerShell):

```powershell
cd C:\Users\Vassil\zeus-simrace-coach\scripts
python telemetry_reader.py
```

- ACC: port 9000
- AC: port 9996
- AMS2: port 9900
- Auto-detects which sim is running
- Writes to `%USERPROFILE%\.openclaw\var\simrace_telemetry.json`

### 2. Run Coach

In a second terminal:

```powershell
cd C:\Users\Vassil\zeus-simrace-coach\scripts
python coach.py
```

- Reads telemetry every 0.5s
- Generates coaching phrases
- Speaks via `speak.ps1` (libritts_r-male voice)
- Cooldown between calls: 3 seconds

### 3. Start Driving

When you load a lap, you'll hear coaching calls:
- Sector deltas
- Brake/throttle advice
- Consistency feedback
- Lap completion summaries

## Telemetry Reference

See [references/telemetry_format.md](references/telemetry_format.md) for UDP packet structures.

## Coaching Templates

### Sector Delta Callout
```
"[sector name], [delta_description]"
"Delta: [plus/minus] [X] hundredths"
```

### Corner Approach
```
"Approach turn [N]: [brake advice]. [entry advice]. [exit advice]."
"Turn [N], [target brake point]. Trail brake to the apex."
```

### Consistency
```
"Consistent laps. Keep it smooth."
"Laps vary by [X] tenths. Focus on [corner]."
```

### Mistake Callout
```
"You made a mistake at [corner]. [what happened]. [correction]."
"Track limits at the exit. Shorten your line."
```

## Sim-Specific Notes

### ACC
- DRS and ABS are active — coach usage: "DRS on the straight" / "ABS intervention detected"
- Tyre management matters more — coach: "front temps are high, manage brake pressure"
- Pit window strategy coaching available

### AC (Original)
- No ABS/DRS — focus on driver skill
- More setups vary wildly — use caution with setup advice
- TC settings important

### AMS2
- Similar to ACC in structure
- Fewer assists — beginner-friendly coaching on TC/ABS if enabled
- Weather changes affect grip significantly

## Files

| File | Purpose |
|------|---------|
| `scripts/telemetry_reader.py` | UDP listener, writes telemetry JSON |
| `scripts/coach.py` | Coaching engine, TTS output |
| `scripts/tts_wrapper.py` | Python wrapper for speak.ps1 |
| `references/telemetry_format.md` | UDP packet structure reference |
