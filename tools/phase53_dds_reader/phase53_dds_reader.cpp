#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <iomanip>
#include <iostream>
#include <string>
#include <thread>

#include <unitree/idl/go2/LowState_.hpp>
#include <unitree/idl/go2/SportModeState_.hpp>
#include <unitree/idl/ros2/Imu_.hpp>
#include <unitree/idl/ros2/Odometry_.hpp>
#include <unitree/idl/ros2/PointCloud2_.hpp>
#include <unitree/robot/channel/channel_factory.hpp>
#include <unitree/robot/channel/channel_subscriber.hpp>

namespace {

struct Counters {
    std::atomic<std::uint64_t> lowstate{0};
    std::atomic<std::uint64_t> sportmodestate{0};
    std::atomic<std::uint64_t> lidar{0};
    std::atomic<std::uint64_t> imu{0};
    std::atomic<std::uint64_t> odom{0};
} counters;

void OnLowState(const void*) {
    counters.lowstate.fetch_add(1, std::memory_order_relaxed);
}

void OnSportModeState(const void*) {
    counters.sportmodestate.fetch_add(1, std::memory_order_relaxed);
}

void OnLidar(const void*) {
    counters.lidar.fetch_add(1, std::memory_order_relaxed);
}

void OnImu(const void*) {
    counters.imu.fetch_add(1, std::memory_order_relaxed);
}

void OnOdom(const void*) {
    counters.odom.fetch_add(1, std::memory_order_relaxed);
}

void ResetCounters() {
    counters.lowstate.store(0, std::memory_order_relaxed);
    counters.sportmodestate.store(0, std::memory_order_relaxed);
    counters.lidar.store(0, std::memory_order_relaxed);
    counters.imu.store(0, std::memory_order_relaxed);
    counters.odom.store(0, std::memory_order_relaxed);
}

void PrintResult(
    const char* name,
    const char* topic,
    std::uint64_t samples,
    double elapsed) {
    std::cout << name << "_topic=" << topic << '\n';
    std::cout << name << "_samples=" << samples << '\n';
    std::cout << name << "_hz=" << (samples / elapsed) << '\n';
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2 || argc > 3) {
        std::cerr << "Usage: " << argv[0] << " networkInterface [seconds]\n";
        return 2;
    }

    const std::string interface_name = argv[1];
    const int duration_seconds = argc == 3 ? std::atoi(argv[2]) : 10;
    if (duration_seconds <= 0 || duration_seconds > 300) {
        std::cerr << "Duration must be between 1 and 300 seconds\n";
        return 2;
    }

    unitree::robot::ChannelFactory::Instance()->Init(0, interface_name);

    unitree::robot::ChannelSubscriber<unitree_go::msg::dds_::LowState_>
        lowstate_reader("rt/lowstate");
    unitree::robot::ChannelSubscriber<unitree_go::msg::dds_::SportModeState_>
        sportmode_reader("rt/sportmodestate");
    unitree::robot::ChannelSubscriber<sensor_msgs::msg::dds_::PointCloud2_>
        lidar_reader("rt/utlidar/cloud");
    unitree::robot::ChannelSubscriber<sensor_msgs::msg::dds_::Imu_>
        imu_reader("rt/utlidar/imu");
    unitree::robot::ChannelSubscriber<nav_msgs::msg::dds_::Odometry_>
        odom_reader("rt/utlidar/robot_odom");

    lowstate_reader.InitChannel(OnLowState, 1);
    sportmode_reader.InitChannel(OnSportModeState, 1);
    lidar_reader.InitChannel(OnLidar, 1);
    imu_reader.InitChannel(OnImu, 1);
    odom_reader.InitChannel(OnOdom, 1);

    std::this_thread::sleep_for(std::chrono::seconds(2));
    ResetCounters();

    const auto started = std::chrono::steady_clock::now();
    std::this_thread::sleep_for(std::chrono::seconds(duration_seconds));
    const auto stopped = std::chrono::steady_clock::now();

    lowstate_reader.CloseChannel();
    sportmode_reader.CloseChannel();
    lidar_reader.CloseChannel();
    imu_reader.CloseChannel();
    odom_reader.CloseChannel();

    const double elapsed =
        std::chrono::duration<double>(stopped - started).count();
    const std::uint64_t lowstate =
        counters.lowstate.load(std::memory_order_relaxed);
    const std::uint64_t sportmodestate =
        counters.sportmodestate.load(std::memory_order_relaxed);
    const std::uint64_t lidar =
        counters.lidar.load(std::memory_order_relaxed);
    const std::uint64_t imu =
        counters.imu.load(std::memory_order_relaxed);
    const std::uint64_t odom =
        counters.odom.load(std::memory_order_relaxed);

    std::cout << std::fixed << std::setprecision(3);
    std::cout << "interface=" << interface_name << '\n';
    std::cout << "duration_seconds=" << elapsed << '\n';
    PrintResult("lowstate", "rt/lowstate", lowstate, elapsed);
    PrintResult(
        "sportmodestate",
        "rt/sportmodestate",
        sportmodestate,
        elapsed);
    PrintResult("lidar", "rt/utlidar/cloud", lidar, elapsed);
    PrintResult("imu", "rt/utlidar/imu", imu, elapsed);
    PrintResult("odom", "rt/utlidar/robot_odom", odom, elapsed);
    std::cout << "publisher_count=0\n";

    return (
        lowstate > 0 &&
        sportmodestate > 0 &&
        lidar > 0 &&
        imu > 0 &&
        odom > 0) ? 0 : 1;
}
