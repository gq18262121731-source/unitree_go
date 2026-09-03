# ROS 2 HUMBLE INSTALL PLAN — PHASE 5.2.3-VM

Status: PREPARED, NOT YET EXECUTED

Execution is blocked until the 30-minute time stability Gate passes with zero
backward jumps.

## Official sequence

1. Confirm a UTF-8 locale.
2. Enable Ubuntu Universe.
3. Install the current `ros2-apt-source` package, which manages the official
   ROS keyring and apt source configuration.
4. Run `apt update` and fully upgrade the fresh Ubuntu 22.04 installation.
5. Install ROS 2 Humble Desktop and development tools.
6. Install the CycloneDDS RMW package.
7. Source Humble, select CycloneDDS, and run read-only CLI checks.

## Commands

```bash
locale
sudo apt update
sudo apt install -y locales software-properties-common curl
sudo locale-gen en_US en_US.UTF-8
sudo update-locale LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8
export LANG=en_US.UTF-8
sudo add-apt-repository -y universe

export ROS_APT_SOURCE_VERSION="$(
  curl -s https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest |
  grep -F '"tag_name"' |
  awk -F'"' '{print $4}'
)"
curl -L -o /tmp/ros2-apt-source.deb \
  "https://github.com/ros-infrastructure/ros-apt-source/releases/download/${ROS_APT_SOURCE_VERSION}/ros2-apt-source_${ROS_APT_SOURCE_VERSION}.$(. /etc/os-release && echo "${UBUNTU_CODENAME:-${VERSION_CODENAME}}")_all.deb"
sudo dpkg -i /tmp/ros2-apt-source.deb

sudo apt update
sudo apt upgrade -y
sudo apt install -y ros-humble-desktop ros-dev-tools
sudo apt install -y ros-humble-rmw-cyclonedds-cpp
```

## Environment and verification

```bash
source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp

ros2 --help
ros2 node list
ros2 doctor --report
dpkg-query -W ros-humble-desktop ros-humble-rmw-cyclonedds-cpp
printenv RMW_IMPLEMENTATION
```

Only after those checks pass should the following lines be added idempotently
to `~/.bashrc`:

```bash
source /opt/ros/humble/setup.bash
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
```

No ROS2 Bridge, DDS publisher, robot motion command, TF, SLAM, or Nav2 action is
included in this plan.

