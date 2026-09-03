cmake_minimum_required(VERSION 3.16)
project(phase53_dds_reader LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(BUILD_EXAMPLES OFF CACHE BOOL "Do not build SDK2 examples" FORCE)

add_subdirectory(unitree_sdk2)

add_executable(phase53_dds_reader phase53_dds_reader.cpp)
target_link_libraries(phase53_dds_reader PRIVATE unitree_sdk2)
