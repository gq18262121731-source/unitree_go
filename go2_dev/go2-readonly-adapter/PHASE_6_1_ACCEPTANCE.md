# Phase 6.1 UnitreeReadonlyProvider acceptance

Status: offline implementation complete; real-hardware soak pending.

## Frozen boundaries

```text
D:\health_new                         NOT MODIFIED
go2-gateway Mock Provider             NOT MODIFIED
Phase 5.5 SLAM                        HOLD
motion/control publishers             ABSENT
```

## Offline acceptance

- stable JSON contract;
- replay source;
- ROS2 subscription-only source;
- SDK2 DDS subscription-only source for public Python IDL types;
- `/utlidar/imu` explicitly marked semantically invalid;
- localization, navigation, and motion capabilities forced false;
- configuration cannot enable real motion;
- safety AST test rejects publisher and motion surfaces.

## Real-hardware gate

Not executable while the robot is powered off. On the next read-only session:

1. run Phase 5.4.11 passive internal-localization probe separately;
2. run this provider for 30 minutes;
3. record CPU/RSS and sample-frequency stability;
4. require zero timestamp rollback and zero unexpected exits;
5. keep localization, navigation, and motion false regardless of sensor health.

Passing the soak test does not authorize integration into `health_new`.

