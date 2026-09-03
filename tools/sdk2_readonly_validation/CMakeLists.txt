cmake_minimum_required(VERSION 3.16)
project(sdk2_readonly_validation LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(BUILD_EXAMPLES OFF CACHE BOOL "Do not build SDK2 control examples" FORCE)

add_subdirectory(unitree_sdk2)

add_executable(sdk2_readonly_validation sdk2_readonly_validation.cpp)
target_link_libraries(sdk2_readonly_validation PRIVATE unitree_sdk2)
