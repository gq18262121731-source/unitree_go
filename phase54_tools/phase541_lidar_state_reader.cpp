#include <atomic>
#include <chrono>
#include <cstdint>
#include <iomanip>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>

#include <unitree/idl/go2/LidarState_.hpp>
#include <unitree/robot/channel/channel_factory.hpp>
#include <unitree/robot/channel/channel_subscriber.hpp>

namespace {

std::atomic<bool> received{false};
std::mutex state_mutex;
unitree_go::msg::dds_::LidarState_ state;

void OnState(const void* message) {
    const auto* incoming =
        static_cast<const unitree_go::msg::dds_::LidarState_*>(message);
    {
        std::lock_guard<std::mutex> lock(state_mutex);
        state = *incoming;
    }
    received.store(true, std::memory_order_release);
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 2) {
        std::cerr << "Usage: " << argv[0] << " networkInterface\n";
        return 2;
    }

    unitree::robot::ChannelFactory::Instance()->Init(0, argv[1]);
    unitree::robot::ChannelSubscriber<unitree_go::msg::dds_::LidarState_>
        reader("rt/utlidar/lidar_state");
    reader.InitChannel(OnState, 1);

    const auto deadline =
        std::chrono::steady_clock::now() + std::chrono::seconds(12);
    while (!received.load(std::memory_order_acquire) &&
           std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    reader.CloseChannel();

    if (!received.load(std::memory_order_acquire)) {
        std::cerr << "No rt/utlidar/lidar_state sample within 12 seconds\n";
        return 1;
    }

    std::lock_guard<std::mutex> lock(state_mutex);
    std::cout << std::fixed << std::setprecision(6);
    std::cout << "topic=rt/utlidar/lidar_state\n";
    std::cout << "stamp=" << state.stamp() << '\n';
    std::cout << "firmware_version=" << state.firmware_version() << '\n';
    std::cout << "software_version=" << state.software_version() << '\n';
    std::cout << "sdk_version=" << state.sdk_version() << '\n';
    std::cout << "sys_rotation_speed=" << state.sys_rotation_speed() << '\n';
    std::cout << "com_rotation_speed=" << state.com_rotation_speed() << '\n';
    std::cout << "error_state=" << static_cast<unsigned>(state.error_state()) << '\n';
    std::cout << "dirty_percentage="
              << static_cast<unsigned>(state.dirty_percentage()) << '\n';
    std::cout << "cloud_frequency=" << state.cloud_frequency() << '\n';
    std::cout << "cloud_packet_loss_rate="
              << state.cloud_packet_loss_rate() << '\n';
    std::cout << "cloud_size=" << state.cloud_size() << '\n';
    std::cout << "cloud_scan_num=" << state.cloud_scan_num() << '\n';
    std::cout << "imu_frequency=" << state.imu_frequency() << '\n';
    std::cout << "imu_packet_loss_rate=" << state.imu_packet_loss_rate() << '\n';
    std::cout << "publisher_count=0\n";
    return 0;
}
