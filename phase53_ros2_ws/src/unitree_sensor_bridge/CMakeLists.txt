cmake_minimum_required(VERSION 3.8)
project(unitree_sensor_bridge)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(nav_msgs REQUIRED)
find_package(rclcpp REQUIRED)
find_package(sensor_msgs REQUIRED)

add_executable(unitree_sensor_bridge_node src/unitree_sensor_bridge_node.cpp)
target_compile_features(unitree_sensor_bridge_node PUBLIC cxx_std_17)
ament_target_dependencies(
  unitree_sensor_bridge_node
  nav_msgs
  rclcpp
  sensor_msgs
)

install(
  TARGETS unitree_sensor_bridge_node
  DESTINATION lib/${PROJECT_NAME}
)

install(
  DIRECTORY config launch
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
