#!/usr/bin/env bash
set -eo pipefail

source /opt/ros/humble/setup.bash
source /home/go2/phase541_msg_ws/install_corrected/setup.bash

exec python3 /home/go2/phase543b_capture.py "$@"
