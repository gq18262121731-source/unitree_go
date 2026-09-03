# Unitree Go2 TF bridge — Phase 5.4 partial result

This package is read-only. It publishes no robot-control topic.

Validated transforms:

- `odom -> base_link`: copied from `/odom` pose with the original sensor
  timestamp, after checking `header.frame_id` and `child_frame_id`.
- `utlidar_lidar -> utlidar_imu`: Unitree L1 factory geometry,
  translation `[-0.007698, -0.014655, 0.00667]` m and identity rotation.

Intentionally absent:

- `base_link -> utlidar_lidar`

The Go2 URDF `radar_joint` pose was tested against the real robot's
timestamp-matched `/utlidar/cloud` and `/utlidar/cloud_base` data and failed
the validation gate. It must not be relabeled as `utlidar_lidar`.

Therefore this package is a safe partial implementation and is not sufficient
to mark Phase 5.4 complete.
