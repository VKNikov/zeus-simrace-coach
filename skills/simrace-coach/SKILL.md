# SimRace Coach — Zeus Skill

Real-time AI sim racing coach using community pace data, per-corner speed trace analysis, and voice coaching.

## Quick Start

```bash
# Terminal 1 — telemetry reader
cd ~/zeus-simrace-coach/scripts
python telemetry_reader.py

# Terminal 2 — coach (default: audio DISABLED, safe for gaming)
python coach.py --mode absolute --track spa --car f-v10-gen2
```

## Game Setup

### AMS2 (Automobilista 2)
1. Options → System → Use shared memory → **Project Cars 2** (for SimHub wind/fans)
2. Options → System → UDP Protocol version → **Project Cars 1** (NOT PC2 — PC2 is buggy)
3. Options → System → UDP Frequency → **1** (max = 60Hz, NOT 9!)
4. Port: **5606** (AMS2 broadcasts on this port with PC1 protocol)

### ACC (Assetto Corsa Competizione)
- UDP broadcasting is enabled by default
- Port: **9000**
- No additional config needed

### AC (Assetto Corsa)
- Port: **9996**

## Audio Coaching — IMPORTANT

**Audio is DISABLED by default (`--audio` flag)** because:
- AMS2 and ACC use WASAPI exclusive audio mode
- Playing audio through the default device while in a game causes crashes
- This is how ALL sim racing coaching apps handle it (CrewChief, Accoustic, etc.)

**Correct usage:**
```bash
# DURING RACING — text only, no audio
python coach.py --mode absolute --track spa --car f-v10-gen2
# Coaching appears as 💬 in the coach terminal window

# NOT DRIVING (replay, menu, setup work) — audio enabled
python coach.py --mode absolute --track spa --car f-v10-gen2 --audio
```

**Real coaching apps like Accoustic work the same way** — audio only in pits/menu, silent during racing.

## Modes

### `--mode absolute` (default)
Compares your sector times vs community pro pace from `references/pace_data.json`.
- Phrases: "sector one 3 tenths off pro pace", "good lap, 1:44.2"

### `--mode self_calibrating`
Compares vs your own rolling average. Learns your style over laps.
- Better for consistency coaching ("you're 2 tenths slower than your average here")

## Manual Track/Car Override

AMS2 PC1 protocol does NOT transmit track/car names over UDP. Use:
```bash
python coach.py --track spa --car f-v10-gen2
```

To find what you're driving: check the shared memory or the coach startup log for "current_track" / "current_car" values from telemetry.

## Known Issues

### Audio crashes games
**Root cause:** WASAPI exclusive mode. No workaround plays audio during racing without risking crashes.
**Fix:** Audio is disabled by default. Use `--audio` only when NOT driving.

### AMS2 track/car names not transmitted
AMS2's PC1 UDP protocol doesn't include track/car names. Options:
1. Use `--track` / `--car` flags manually
2. Enable Shared Memory = Project Cars 2 → SimHub reads it → same SHM accessible to us
3. Run CREST2 (`github.com/viper4gh/CREST2-AMS2`) — reads SHM, serves JSON at :8180

### Lap time offsets wrong for AMS2
AMS2's PC1 implementation uses different offset positions for lap/sector times than standard PCars1.
Workaround: lap completion guard requires 30s < lap_time < 600s to filter garbage.

## Per-Corner Coaching Phrases

The speed trace analyzer generates these when a corner is completed:

| Pattern | Phrase |
|---------|--------|
| Braking 20+ km/h earlier than reference | "brake N meters later" |
| Overspeed entry 20+ km/h over reference | "you need to brake earlier" |
| Entry speed 25+ km/h over reference | "you're too wide, apex later" |
| Entry speed 30+ km/h under reference | "you're too tight" |
| Apex speed 20+ km/h below reference | "get back on throttle sooner" |
| Lifting early mid-corner | "don't lift, commitment through corner" |
| Wheelspin on exit | "ease off throttle, wheelspin" |

Max 1 phrase per corner, minimum 12 seconds between phrases.

## Architecture

```
telemetry_reader.py   — UDP listener (AMS2:5606, ACC:9000, AC:9996)
                       writes ~/.openclaw/var/simrace_telemetry.json

speed_trace_analyzer.py — corner detection via steering spikes
                        rolling 5s buffer of [speed,brake,throttle,steer]
                        compares vs reference profiles

coach.py              — reads telemetry, generates coaching
                       speaks via speak.ps1 (sherpa-onnx TTS)
                       pace data from references/pace_data.json
```

## State Files

```
~/.openclaw/var/
  simrace_telemetry.json   — live telemetry (updated every ~0.5s)
  simrace_laps.json        — lap history (last 100 laps)
  simrace_personal_best.json — per-track PB (cleared on corruption)
```
