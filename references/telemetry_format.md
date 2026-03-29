# SimRacing UDP Telemetry Formats

## ACC (Assetto Corsa Competizione)

**Port:** 9000 (UDP broadcast)

ACC sends UDP packets in chunks, with a header packet followed by per-car data packets.

### ACC UDP Packet Types

| Type | Description |
|------|-------------|
| 1 | Car info (static: car model, skin, driver name) |
| 2 | Car update (dynamic: position, speed, inputs) |
| 3 | Lap data |
| 4 | Race data (positions, gaps) |

### Type 2 - Car Update (most important for coaching)

```
Offset  Size  Field
0       1     PacketType (2)
1       1     PlayerCarIndex
2       4     Speed (float, km/h)
6       2     Gear (char, 0=N, 1=R, 2-8=Gear)
8       2     EngineRPM (unsigned short)
10      4     PosX, PosY, PosZ (float, world coords)
14      4     PosY
18      4     PosZ
22      2     Brake (0-32767, normalised)
24      2     Throttle (0-32767, normalised)
26      2     SteerAngle (float, radians)
30      2     Clutch (0-255)
32      1     OilTemp (celsius, signed char)
33      1     WaterTemp
34      4     LapTime (ms, current lap)
38      4     BestLapTime (ms)
42      4     LapCount (unsigned int)
46      4     CurrentSector (0=s1, 1=s2, 2=s3, 255=invalid)
```

### Type 3 - Lap Data

```
Offset  Size  Field
0       1     PacketType (3)
1       1     CarIndex
2       4     LapTime (ms)
6       4     S1Time, S2Time, S3Time (ms each, uint32)
14      2     S1Speed, S2Speed, S3Speed (km/h, uint16)
20      4     LapCount
24      1     IsValid (bool-like)
25      1     Tires[4] (byte, representing age/heat)
```

### Tire Data (per wheel, at offset per wheel)

```
BrakeTemp:  4 bytes float (celsius)
TireTemp:   4 bytes float (Surface/Inner/Left/Center/Right)
TirePressure: 4 bytes float (PSI)
```

**Note:** Exact offsets vary by ACC version. Community reference: CrewChiefV4.

---

## AC / AC Evo (Assetto Corsa)

**Port:** 9996 (UDP)

### AC Plugin UDP

AC uses a plugin system. Enable "UDP Plugin" in the game settings.

```
Offset  Size  Field
0       4     Signature ("ACSH")
4       2     Version
6       4     PacketType
10      4     Size

# Type 0 - Car info
# Type 1 - Car update (speed, inputs, tyres)
# Type 2 - Lap info
```

### Car Update (Type 1)

```
Offset  Size  Field
0       4     Signature
4       2     Version
6       4     PacketType = 1
10      4     Size
14      4     PosX
18      4     PosY
22      4     PosZ
26      4     Speed (m/s, float)
30      4     AccelX, AccelY, AccelZ
42      4     Gear (int, 0=N, -1=R, 1-8=Gear)
46      2     EngineRPM
48      2     Steer (float, -1 to 1)
50      2     Throttle (0-1, float)
52      2     Brake (0-1, float)
54      4     BrakeTemp[4] (float)
70      4     TireTemp[4] (float, surface temp)
```

---

## AMS2 (Automobilista 2)

**Port:** 9900 (UDP)

AMS2 uses an mNaz switch broadcasting telemetry.

### Packet Structure

```
Offset  Size  Field
0       4     mPacketId (0=CarInfo, 1=CarUpdate, 2=LapInfo)
4       4     mTotalPackets (per frame)
8       4     mPlayerIndex
12      4     mRPM
16      4     mSpeed (m/s)
20      4     mTurboBoost
24      4     mOilPressure
28      4     mWaterTemp
32      4     mFuel
36      4     mGear
40      4     mNeutral
44      4     mSpeedLimiter
48      4     mEngineBrake
52      4     mERS
56      4     mDRS
60      4     mThrottle (0-1)
64      4     mBrake (0-1)
68      4     mClutch (0-1)
72      4     mSteer (0 to 1)
76      4     mTyreTemp[4]
92      4     mTyreWear[4]
108     4     mTyrePressure[4]
124     4     mBrakeTemp[4]
140     4     mDent[4]
156     4     mSFriction
160     4     mRFriction
164     4     mSFFluidTemp
168     4     mRFFluidTemp
172     4     mS RFluidTemp
176     4     mRRFluidTemp
```

---

## Shared Telemetry Keys

All three sims share these concepts (normalize to these keys):

| Key | Description | Unit |
|-----|-------------|------|
| `speed` | Speed | km/h |
| `brake` | Brake input | 0.0-1.0 |
| `throttle` | Throttle input | 0.0-1.0 |
| `steer` | Steering input | -1.0 to 1.0 |
| `gear` | Current gear | 0=N, -1=R, 1-8 |
| `rpm` | Engine RPM | int |
| `lap_time_ms` | Current lap time | ms |
| `best_lap_ms` | Best lap time | ms |
| `lap` | Lap count | int |
| `sector` | Current sector | 0, 1, 2 |
| `s1_time_ms` | Sector 1 time | ms |
| `s2_time_ms` | Sector 2 time | ms |
| `s3_time_ms` | Sector 3 time | ms |
| `fuel` | Fuel remaining | liters |
| `tire_temp_fl` | Front left tire temp | °C |
| `tire_temp_fr` | Front right tire temp | °C |
| `tire_temp_rl` | Rear left tire temp | °C |
| `tire_temp_rr` | Rear right tire temp | °C |
| `brake_temp_fl` | Front left brake temp | °C |
| `brake_temp_fr` | Front right brake temp | °C |
| `pos_x/y/z` | World position | float |
| `delta_ms` | Delta vs personal best | ms (+ = slower) |

---

## Verification Needed

- ACC exact packet offsets should be verified against `ACC Shared Memory` plugin docs
- AC Evo is newer and may have updated format
- AMS2 structure is based on rF2 telemetry (similar codebase)
- **Recommended:** Use `CrewChiefV4` Python imports as reference for exact parsing

