cmake_minimum_required(VERSION 3.8)
project(unitree_tf_bridge)

find_package(ament_cmake REQUIRED)

install(
  PROGRAMS unitree_tf_bridge/tf_bridge_node.py
  DESTINATION lib/${PROJECT_NAME}
  RENAME unitree_tf_bridge_node
)

install(
  DIRECTORY launch
  DESTINATION share/${PROJECT_NAME}
)

install(
  FILES README.md
  DESTINATION share/${PROJECT_NAME}
)

ament_package()
