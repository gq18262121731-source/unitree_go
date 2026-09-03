#!/usr/bin/env bash
set -euo pipefail

sdk_root="/home/go2/phase5410_unilidar_sdk"
ros2_root="${sdk_root}/unitree_lidar_ros2"
node_path="${ros2_root}/install/unitree_lidar_ros2/lib/unitree_lidar_ros2/unitree_lidar_ros2_node"

echo "PHASE_5_4_10_UNILIDAR_PREFLIGHT"
echo "mode=READ_ONLY_NO_NODE_START"

if [[ ! -x "${node_path}" ]]; then
  echo "sdk_executable=UNAVAILABLE"
  exit 10
fi
echo "sdk_executable=READY"

mapfile -t devices < <(
  find /dev -maxdepth 1 \
    \( -name 'ttyUSB*' -o -name 'ttyACM*' \) \
    -print | sort
)

if (( ${#devices[@]} == 0 )); then
  echo "serial_device=NONE"
  echo "result=BLOCKED_NO_L1_USB_SERIAL"
  exit 20
fi

echo "serial_device_count=${#devices[@]}"
for device in "${devices[@]}"; do
  echo "serial_device=${device}"
  stat -c 'device_mode=%A device_owner=%U device_group=%G' "${device}"
  udevadm info --query=property --name="${device}" |
    grep -E '^(ID_VENDOR_ID|ID_MODEL_ID|ID_VENDOR|ID_MODEL|ID_SERIAL)=' ||
    true
done

echo "result=DEVICE_PRESENT_IDENTITY_REVIEW_REQUIRED"
echo "No SDK node was started."
