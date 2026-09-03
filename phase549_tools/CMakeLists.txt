cmake_minimum_required(VERSION 3.16)
project(phase549_lowstate_imu_capture LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(BUILD_EXAMPLES OFF CACHE BOOL "Never build SDK control examples" FORCE)

add_subdirectory(unitree_sdk2)

add_executable(
  phase549_lowstate_imu_capture
  phase549_lowstate_imu_capture.cpp
)
target_link_libraries(
  phase549_lowstate_imu_capture
  PRIVATE unitree_sdk2
)
