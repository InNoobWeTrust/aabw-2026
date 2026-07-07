# Physical World Data Layer

Robotics and embodied AI need large volumes of real-world interaction data, but collecting that data through robot fleets, labs, and custom sensors is expensive and slow.

## Problem statement

General-purpose robotics and embodied AI systems require massive amounts of high-quality real-world interaction data: how people move, manipulate objects, navigate spaces, complete tasks, and respond to changing environments.

Collecting that data is expensive, slow, and difficult to scale. Robot fleets are limited, physical labs are costly, and custom sensor setups make data collection hard to repeat across diverse environments.

At the same time, smartphones, wearable sensors, egocentric cameras, and head-mounted devices already capture rich physical-world signals at global scale, but there is no simple infrastructure layer for turning that distributed capture into reliable robotics-ready data.

## Challenge statement

How might we create a scalable data infrastructure layer for robotics and embodied AI using devices people already own?

## Product vision

- Task-based data collection workflows for physical-world actions and environments.
- Mobile or wearable capture using video, motion, depth, audio, or sensor signals where available.
- Privacy-aware consent, redaction, and contributor controls.
- Quality scoring, deduplication, annotation, and dataset packaging.
- APIs or exports that make collected data useful for robotics and embodied AI teams.

## Build challenge

RoboData: The Physical World Data Layer.

Build a platform for distributed, privacy-aware physical-world data collection using smartphones, wearable sensors, egocentric cameras, or head-mounted devices.

Goal: reduce robotics data collection cost with scalable crowdsourced collection using devices people already own.