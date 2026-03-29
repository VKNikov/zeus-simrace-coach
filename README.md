# Zeus SimRace Coach

Real-time AI voice coaching for sim racing — powered by Zeus (OpenClaw) + local STT/TTS.

## Features

- 🎙️ **Real-time voice coaching** via local sherpa-onnx TTS — no cloud, no API costs
- 📊 **UDP telemetry reader** — ACC, Assetto Corsa, Automobilista 2
- 🏎️ **Corner-by-corner coaching** — brake points, throttle application, sector deltas
- 🧠 **Consistency analysis** — lap time spread, mistake detection
- 🏆 **Personal best tracking** — per-track, per-car records
- 🔇 **Fully local** — everything runs on your PC

## Supported Sims

| Sim | Port | Status |
|-----|------|--------|
| Assetto Corsa Competizione | 9000 | ✅ Tested |
| Assetto Corsa / AC Evo | 9996 | ✅ Tested |
| Automobilista 2 | 5606 (PC1 protocol) | ✅ Tested |

## Quick Start

### Prerequisites

- Python 3.8+
- [sherpa-onnx TTS](https://github.com/k2-fsa/sherpa-onnx) (already installed at `~/.openclaw/tools/sherpa-onnx-tts/`)
- OpenAI Whisper (optional, for voice commands)

### 1. Clone / Install

```bash
git clone https://github.com/VKNikov/zeus-simrace-coach.git
cd zeus-simrace-coach
```

### 2. Start Telemetry Reader

```powershell
cd scripts
python telemetry_reader.py
```

Leave this running in a terminal. It listens on UDP ports for your sim.

### 3. Start Coach

```powershell
# In a second terminal
cd scripts
python coach.py
```

You'll hear voice coaching in your headset as you drive.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Zeus (OpenClaw Agent)                                       │
│  ┌────────────────────────────────────────────────────────┐ │
│  │  simrace-coach skill                                   │ │
│  │  - Manual coaching: sector analysis, lap review         │ │
│  │  - Live coaching: coaching phrase generation            │ │
│  └────────────────────────────────────────────────────────┘ │
│                            ▲                                 │
│                            │ speaks via                      │
│  ┌────────────────────────┴─────────────────────────────┐  │
│  │  sherpa-onnx TTS (local)  ←  speak.ps1               │  │
│  │  Voice: libritts_r-male                             │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                              ▲
                              │ reads telemetry
┌─────────────────────────────┴───────────────────────────────┐
│  SimRacing Telemetry Stack                                  │
│  ┌────────────────────┐     ┌────────────────────────────┐  │
│  │ telemetry_reader.py│ ──► │ ~/.openclaw/var/           │  │
│  │ (UDP listener)     │     │   simrace_telemetry.json  │  │
│  │ ports: 9000/9996/5606     │   simrace_laps.json        │  │
│  └────────────────────┘     │   simrace_personal_best.json│  │
│                              └────────────────────────────┘  │
│  ┌────────────────────┐                                    │
│  │ coach.py            │  reads telemetry → coaching calls │
│  │ (coaching engine)  │  → speak.ps1 → voice             │
│  └────────────────────┘                                    │
└─────────────────────────────────────────────────────────────┘
```

## Coaching Features

### Real-Time Calls
- **Sector delta vs community reference** — compares your sector times to validated community Pro/Good/Average pace data
- **Brake point coaching** — per-track braking zones with specific advice for each corner
- **Throttle application coaching** — early/late throttle, commitment calls
- **Mistake callouts** — brake/throttle conflicts, early braking
- **Consistency scoring** — every 3 laps, stdev analysis
- **Personal best tracking** — per-track, per-car records

### Pace Reference Data
The coach ships with validated community reference data for:

**AMS2 — Formula V10 Gen2 (F1-era):**
- Imola 2001, Monza GP, Spa 1993, Silverstone 2001, Barcelona GP, Interlagos
- Includes your own recorded laps from `Documents/Automobilista 2/records/`

**ACC — GT3 (all major cars):**
- Spa, Monza, Barcelona, Imola, Brands Hatch, Silverstone GP, Nürburgring GP, Zandvoort, Hungaroring, Paul Ricard, Watkins Glen, COTA, Misano, Donington, Red Bull Ring, Laguna Seca, Zolder, Snetterton, Oulton Park, Kyalami, Mount Panorama, Indianapolis, Nordschleife, Valencia
- Sector-level pro pace for each track (S1/S2/S3)
- Per-car offsets (Ferrari 296, McLaren 720S, Porsche 992, etc.)

### Post-Session Analysis
- Sector-by-sector breakdown vs community reference
- Time loss prioritization by corner
- Setup suggestions (via coaching level)

## Configuration

Edit `scripts/coach.py` to change:

```python
state = CoachingState()
state.level = "intermediate"  # beginner | intermediate | advanced
state.cooldown_seconds = 3.0  # seconds between coaching calls
```

Edit `scripts/telemetry_reader.py` to change polling behavior.

## License

MIT — VKNikov
