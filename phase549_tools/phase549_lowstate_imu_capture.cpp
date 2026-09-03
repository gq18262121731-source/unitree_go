#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>

#include <unitree/idl/go2/LowState_.hpp>
#include <unitree/robot/channel/channel_factory.hpp>
#include <unitree/robot/channel/channel_subscriber.hpp>

namespace {

std::atomic<bool> capture_enabled{false};
std::atomic<std::uint64_t> sample_count{0};
std::mutex output_mutex;
std::ofstream output;

void OnLowState(const void* message) {
    if (!capture_enabled.load(std::memory_order_relaxed)) {
        return;
    }

    const auto& state =
        *static_cast<const unitree_go::msg::dds_::LowState_*>(message);
    const auto& imu = state.imu_state();
    const auto system_ns =
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::system_clock::now().time_since_epoch())
            .count();
    const auto steady_ns =
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch())
            .count();

    std::lock_guard<std::mutex> lock(output_mutex);
    output << system_ns << ',' << steady_ns << ',' << state.tick();
    for (const float value : imu.quaternion()) {
        output << ',' << value;
    }
    for (const float value : imu.gyroscope()) {
        output << ',' << value;
    }
    for (const float value : imu.accelerometer()) {
        output << ',' << value;
    }
    for (const float value : imu.rpy()) {
        output << ',' << value;
    }
    output << ',' << static_cast<unsigned>(imu.temperature()) << '\n';
    sample_count.fetch_add(1, std::memory_order_relaxed);
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 4) {
        std::cerr << "Usage: " << argv[0]
                  << " network_interface duration_seconds output_csv\n";
        return 2;
    }

    const std::string interface_name = argv[1];
    const int duration_seconds = std::atoi(argv[2]);
    const std::string output_path = argv[3];
    if (duration_seconds < 5 || duration_seconds > 120) {
        std::cerr << "duration_seconds must be between 5 and 120\n";
        return 2;
    }

    output.open(output_path, std::ios::out | std::ios::trunc);
    if (!output) {
        std::cerr << "Unable to open output: " << output_path << '\n';
        return 3;
    }
    output << std::setprecision(9) << std::fixed;
    output
        << "system_time_ns,steady_time_ns,tick,"
        << "q0,q1,q2,q3,"
        << "gyro_x,gyro_y,gyro_z,"
        << "acc_x,acc_y,acc_z,"
        << "rpy_x,rpy_y,rpy_z,temperature\n";

    unitree::robot::ChannelFactory::Instance()->Init(0, interface_name);
    unitree::robot::ChannelSubscriber<unitree_go::msg::dds_::LowState_>
        subscriber("rt/lowstate");
    subscriber.InitChannel(OnLowState, 1);

    std::this_thread::sleep_for(std::chrono::seconds(2));
    sample_count.store(0, std::memory_order_relaxed);
    capture_enabled.store(true, std::memory_order_release);
    const auto started = std::chrono::steady_clock::now();
    std::this_thread::sleep_for(std::chrono::seconds(duration_seconds));
    const auto stopped = std::chrono::steady_clock::now();
    capture_enabled.store(false, std::memory_order_release);

    {
        std::lock_guard<std::mutex> lock(output_mutex);
        output.flush();
        output.close();
    }

    const double elapsed =
        std::chrono::duration<double>(stopped - started).count();
    const auto count = sample_count.load(std::memory_order_relaxed);
    std::cout << std::fixed << std::setprecision(3);
    std::cout << "interface=" << interface_name << '\n';
    std::cout << "topic=rt/lowstate\n";
    std::cout << "duration_seconds=" << elapsed << '\n';
    std::cout << "samples=" << count << '\n';
    std::cout << "frequency_hz=" << (count / elapsed) << '\n';
    std::cout << "output=" << output_path << '\n';
    std::cout << "publisher_count=0\n";
    std::cout.flush();

    // This SDK2/CycloneDDS build can corrupt its heap in CloseChannel() after a
    // longer capture. All callbacks are gated off and the CSV is flushed and
    // closed above, so bypass SDK/static teardown and let the OS reclaim DDS
    // resources. This does not skip any pending capture writes.
    std::_Exit(count > 0 ? 0 : 4);
}
