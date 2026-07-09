# Consumer Device Capture Capabilities for Robotics Data

**July 2026** | What devices people already own can contribute to a physical-world data layer

---

## At a Glance

- **Smartphones** (iPhone Pro, Pixel, Galaxy) are the primary capture device: RGB + LiDAR/depth + IMU + GNSS — a research-grade sensor suite in everyone's pocket
- **Smartwatches/rings/bands** are NOT viable for raw robotics data: all major vendors lock raw IMU behind closed APIs
- **Standalone developer IMUs** (MbientLab ~$110, x-io x-IMU3 ~$380) are the workable path for wrist-pose enrichment
- **VR headsets** (Quest 3, Vision Pro) enable low-cost teleop demos (<$500 vs. $300K ALOHA)
- **GoPro + UMI gripper** is the proven gold standard for manipulation data ($500/station)
- **LeRobot** (HuggingFace, 25.6k stars) is the de facto dataset format
- **Key gap:** No consumer device provides calibrated force/torque or hardware-synced multi-camera

---

## 1. Capture-to-Robotics Data Mapping

This table shows what each device class can contribute. Use it to decide which devices matter for your use case.

| Device Class | Manip Trajectory | Ego Task Video | 3D Scene Scan | Nav Episode | Depth | Force/Contact |
|---|---|---|---|---|---|---|
| **iPhone Pro (ARKit+LiDAR)** | ★★★ ARKit+SLAM | ★★★ ProRes LOG, 4K@120 | ★★★★ RoomPlan, 1–3cm | ★★★ VIO+GNSS | ★★★★ LiDAR 256×192@30 | ★ IMU spike only |
| **Android (ARCore)** | ★★ pose+Depth API | ★★★ Camera2 RAW | ★ no mesh API | ★★★★ Geospatial VPS | ★★ ML/ToF depth | ★ same |
| **Meta Ray-Ban** | ★ head pose only | ★★★ 1080p@30 ego | — | ★★ phone GNSS | — | — |
| **Apple Vision Pro** | ★★★★ hand tracking | ★★★★ Spatial Video | ★★★★ LiDAR+Room | ★★★ world tracking | ★★★★ LiDAR | ★ |
| **Quest 3** | ★★★ hand+controller | ★ passthrough closed | ★★★ Scene Mesh | ★ | ★ low-res | — |
| **GoPro / Action Cam** | ★★★★ IMU+SLAM (UMI) | ★★★★ 5.3K@60 | — | ★★ GPS+IMU | — | — |
| **Smartwatch (any)** | — locked APIs | — | — | ★ GPS | ★ aggregate | ★ aggregate |
| **Smart rings/bands** | — closed | — | — | — | — | ★ HR only |
| **MbientLab IMU** | ★★★ wrist BLE 100Hz | — | — | — | — | ★★ accel spike |
| **x-io x-IMU3** | ★★★★ 400Hz AHRS | — | — | — | — | ★★ 200g impact |
| **UMI Gripper** | ★★★★ SLAM bimanual | ★★★★ wrist GoPro | — | — | — | ★ spring est. |
| **Xsens/Rokoko MoCap** | ★★★★★ sub-degree | — | — | — | — | ★ optional |

**Key:** ★★★★★ ground-truth | ★★★★ research-grade | ★★★ useful | ★★ weak | ★ barely usable | — not available

---

## 2. Smartphones

### Summary

| Platform | Best signals | Depth source | On-device ML | Key limitation |
|---|---|---|---|---|
| **iOS (ARKit + LiDAR)** | 6DoF pose, LiDAR depth, RoomPlan scene mesh, hand/body tracking | LiDAR 256×192@30fps (0.3–5m) | CoreML, Vision framework | Thermal throttle at 20min; no exposure lock |
| **Android (ARCore)** | 6DoF pose, Geospatial VPS, ML depth | ML-based or 8×8 ToF (Pixel) | ML Kit | No mesh API; no LiDAR on any Android phone |

### Robotics-Relevant Signals

| Signal | iOS ARKit | Android ARCore | Rate | Robotics use |
|---|---|---|---|---|
| 6DoF world pose | ✅ `ARFrame.camera` | ✅ `Frame.getCamera()` | 30–120 Hz | Manip trajectory, nav episode |
| Depth map | ✅ LiDAR CVPixelBuffer | ✅ DepthImage (ML/ToF) | 30 fps | Scene scan, collision avoidance |
| Scene mesh | ✅ `ARMeshAnchor` (LiDAR) | ❌ no mesh API | On-change | 3D scene reconstruction |
| RoomPlan | ✅ parametric room (walls, doors, furniture) | ❌ | Real-time | Scene priors for sim |
| Body skeleton | ✅ 19 joints @ 60Hz | ✅ ML Kit | 30–60 Hz | Teleop retargeting |
| Hand tracking | ✅ 21 joints @ 60Hz (iOS 17+) | ✅ ML Kit landmarks | 30–60 Hz | Dexterous manipulation |
| Raw IMU | ✅ CMMotionManager 100Hz | ✅ SensorManager 200–400Hz | 100–400 Hz | Trajectory smoothing |
| GNSS | ✅ dual-freq L1+L5 (iPhone 15+) | ✅ + Geospatial VPS | 1 Hz | Geo-annotated nav |

### Critical Limitations

- **IMU-camera clock drift:** separate clocks, ~1–10ms drift (needs Kalibr post-hoc correction)
- **No force/torque:** wrist IMU can infer grasp from accel spikes but cannot measure contact force
- **Thermal throttle:** sustained capture >20min → camera framerate drops; needs external cooling for long sessions
- **Auto-exposure only:** cannot lock exposure/gain programmatically in ARKit
- **Storage:** ProRes 4K@30 = 400 MB/min (24 GB/hr); HEVC with depth = 80 MB/min (5 GB/hr)

### Battery / Thermal / Storage

| Factor | Limit |
|---|---|
| Continuous ARKit+video | 45–75 min battery |
| Thermal throttle onset | 15–25 min in warm ambient |
| ProRes 4K@30 storage | 400 MB/min |
| HEVC+depth storage | 80 MB/min |

---

## 3. Wearables & Egocentric Devices

### Smart Glasses

| Device | Sensors | Dev access | Robotics fit |
|---|---|---|---|
| **Meta Ray-Ban** (2025) | 12MP ultra-wide cam, 3-mic array, IMU | Live streaming API (1080p); Movement SDK (IMU 30–200Hz) | Egocentric task video; no depth |
| **Apple Vision Pro** | Stereo RGB, LiDAR, 6 IR cams, eye tracking, 4 IMUs | Full ARKit: 6DoF, hand skeleton, gaze, scene mesh, LiDAR | ★★★★ — best sensor suite for teleop (DART system uses AVP) |
| **Quest 3** | RGB passthrough, IR tracking, depth projector, 6 IMUs | Scene Mesh + hand tracking (26 joints @ 60Hz); **NO raw passthrough frames** | Hand tracking for teleop; no video capture |
| **Xreal Air 2 Ultra** | Stereo cams + IMU | Raw cam via NDK | Stereo depth + pose |

### Action Cameras (UMI-validated)

**GoPro Hero 13:** 5.3K@60 / 4K@120; GPS+IMU at 200Hz in `.gpmf` metadata; frame-accurate IMU-video sync; 177° FOV with Max Lens Mod. **This is the UMI sensor** — proven for bimanual manipulation policy training.

**UMI system (Chi et al., RSS 2024):** 3D-printed gripper (~$75) + GoPro + SLAM → 6DoF end-effector trajectory. Total cost ~$500/station. Format: LeRobot. 30+ hours of demos across labs worldwide.

### Smartwatches, Bands & Rings — The Dead End

**Verdict: Consumer wearables are NOT viable for robotics-grade raw capture.** Every major vendor locks raw IMU behind closed or aggregate-only APIs.

| Device | Raw IMU access? | What's exposed |
|---|---|---|
| Apple Watch | ❌ CoreMotion device-local only; no BLE export | Aggregates (HR, activity) |
| Samsung Galaxy Watch | ⚠️ Wear OS SensorManager (throttled) | Sustained 200Hz drains battery in 2–4h |
| Garmin | ❌ Connect IQ aggregates only | HR, steps, power |
| Whoop | ❌ API returns Recovery/Strain/Sleep | Analytics, not signals |
| Oura Ring | ❌ API = sleep/HRV/recovery scores | No accelerometer |
| Samsung Galaxy Ring | ❌ No developer API | Closed |
| Fitbit / Mi Band | ❌ HealthKit/Health Connect scopes only | Aggregates |

**What wearables CAN contribute (stretch-goal co-variates only):**
- HR/HRV from HealthKit → stress/arousal quality flag per clip
- "Active wrist minutes" → passive screen for candidate capture sessions
- Accel spikes → weak labels for failure/drop events

### Standalone Developer IMUs (the workable wrist-pose path)

| Device | IMU | Rate | Price | BLE | Battery | Verdict |
|---|---|---|---|---|---|---|
| **MbientLab MetaMotionC/RL/S** | BMI270 9-axis | 100Hz stream / 800Hz log | ~$105–$150 | BLE 4.0/5.0 | 8–72h | ★★★★ cheapest viable |
| **x-io x-IMU3** | Calibrated gyro+accel+mag | 400Hz + AHRS quaternions | ~$380 | Wi-Fi+BT | 13h log / 10h BT | ★★★★★ best sync, calibration cert |
| **Movella DOT** | BMI270 9-axis | 1kHz log / 60Hz BLE | ~$290 | BLE 5.0 | ~6h | ★★★ tighter SDK |

---

## 4. VR Teleoperation Systems

| System | Hardware | Output | Rate | Robot platforms | Cost |
|---|---|---|---|---|---|
| **AnyTeleop** | Quest 2/3 + hand tracking | Joint-angle retargeting | 30+ Hz | Franka, Allegro, multiple | <$500 |
| **Open TeleVision** | Quest 3 + stereo cam rig | Stereo video + EE commands | 60 Hz | Any ROS robot | <$500 |
| **DART** | Apple Vision Pro | Hand→robot via AR overlay | 20+ Hz | Bimanual arms | ~$3,500 |
| **Bunny-VisionPro** | Apple Vision Pro | Dexterous hand teleop | 30 Hz | Bimanual tasks | ~$3,500 |
| **GELLO** | 3D-printed follower + phone | Joint angles via encoders | 10–30 Hz | Franka, UR5 | ~$200 |

**Key finding:** VR teleop via Quest 3 is 10–50× cheaper than ALOHA ($300K) or professional haptic masters ($50K+).

---

## 5. Data Formats & Standards

| Format | Use | Status | Key feature |
|---|---|---|---|
| **LeRobot** (HuggingFace) | Imitation learning datasets | **De facto standard** (25.6k stars) | Parquet (state/actions) + H.264 MP4; HF Hub streaming |
| **Open X-Embodiment** | Cross-embodiment pre-training | Important for VLA pre-training | Multi-camera, language, reward signals |
| **ROS 2 / MCAP** | Real-robot data recording | Standard in robotics stacks | Hardware-timestamped multi-sensor; heavy for phone capture |
| **USD / glTF** | 3D scenes | Simulation + web | Apple RoomPlan → USD → Isaac Sim pipeline |
| **COCO / YOLO / CVAT** | Object detection/segmentation | Annotation interchange | No standardized action annotation format yet |

### LeRobot Dataset Structure

```
dataset/
├── meta/info.json          # task, fps, robot description
├── data/
│   ├── episode_0000/
│   │   ├── observation.mp4  # synchronized video
│   │   ├── state.parquet    # joint angles, EE pose, timestamps
│   │   └── action.parquet   # commanded actions
│   └── episode_0001/...
└── README.md
```

---

## Appendix: Detailed Sensor Specs

### iOS ARKit Signals (full)

| Signal | API | Rate | Format |
|---|---|---|---|
| 6DoF pose | `ARFrame.camera` | 60–120 Hz | `simd_float4x4` transform |
| Scene mesh | `ARMeshAnchor` | On-change | Vertex + face arrays with classification |
| Depth map | `ARDepthData` | 30 fps | `CVPixelBuffer` Float32 (meters) |
| Person segmentation | `ARFrame.segmentationBuffer` | 30 fps | Alpha matte |
| Body skeleton | `ARBodyAnchor` | 60 Hz | 19 joints, `simd_float4x4` per joint |
| Hand skeleton | `ARHandAnchor` | 60 Hz | 21 joints + chirality (iOS 17+) |
| LiDAR specs | — | 256×192 @ 30fps | 0.3–5m range, 2–5mm precision @ 1m |
| TrueDepth | — | 640×480 @ 60fps | 0.2–1.5m range (Face ID sensor) |

### Android ARCore Signals (full)

| Signal | API | Rate | Notes |
|---|---|---|---|
| 6DoF pose | `Frame.getCamera()` | 30–60 Hz | Visual-inertial |
| Depth | `DepthImage` | 30 fps | ML-based or ToF upsampled; confidence mask included |
| Geospatial VPS | `Earth` object | Per-frame | ±1m accuracy using Street View localization |
| Raw IMU | `SensorManager` | 200–400 Hz | Not synchronized to camera |
| ML Kit | Various | 15–30 fps | Pose, objects, hands, segmentation |

### Apple Vision Pro Sensors (full)

- 2× high-res RGB (stereo, 18mm, f/2.0)
- 6× world-facing IR tracking cameras
- 4× eye-tracking IR cameras (120 Hz)
- 1× LiDAR scanner (world-facing, same as iPad Pro)
- 4× IMUs
- Flicker + ambient light sensors
- Hand tracking: full skeleton, 60 Hz, ~3mm precision
- Eye gaze: origin + direction, 120 Hz
