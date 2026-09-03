#!/usr/bin/env bash
set -euo pipefail
ip link set lo up
ip link add dummy0 type dummy
ip address add 10.200.0.1/24 dev dummy0
ip link set dummy0 up
exec runuser -u test1 -- bash \
  "/mnt/e/笨笨狗/phase672_tools/phase672_run_dds_netns_probe_inner.sh"
