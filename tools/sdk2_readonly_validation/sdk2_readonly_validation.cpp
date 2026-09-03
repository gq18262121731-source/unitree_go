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
#include <unitree/robot/channel/channel_factory.hpp>
#include <unitree/robot/channel/channel_subscriber.hpp>

namespace {

std::atomic<std::uint64_t> lowstate_samples{0};
std::atomic<std::uint64_t> sportmode_samples{0};

void OnLowState(const void*) {
    lowstate_samples.fetch_add(1, std::memory_order_relaxed);
}

void OnSportModeState(const void*) {
    sportmode_samples.fetch_add(1, std::memory_order_relaxed);
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
        lowstate_subscriber("rt/lowstate");
    unitree::robot::ChannelSubscriber<unitree_go::msg::dds_::SportModeState_>
        sportmode_subscriber("rt/sportmodestate");

    lowstate_subscriber.InitChannel(OnLowState, 1);
    sportmode_subscriber.InitChannel(OnSportModeState, 1);

    // Allow DDS discovery to settle before starting the measured interval.
    std::this_thread::sleep_for(std::chrono::seconds(2));
    lowstate_samples.store(0, std::memory_order_relaxed);
    sportmode_samples.store(0, std::memory_order_relaxed);

    const auto started = std::chrono::steady_clock::now();
    std::this_thread::sleep_for(std::chrono::seconds(duration_seconds));
    const auto stopped = std::chrono::steady_clock::now();

    lowstate_subscriber.CloseChannel();
    sportmode_subscriber.CloseChannel();

    const double elapsed =
        std::chrono::duration<double>(stopped - started).count();
    const std::uint64_t low_count =
        lowstate_samples.load(std::memory_order_relaxed);
    const std::uint64_t sport_count =
        sportmode_samples.load(std::memory_order_relaxed);

    std::cout << std::fixed << std::setprecision(3);
    std::cout << "interface=" << interface_name << '\n';
    std::cout << "duration_seconds=" << elapsed << '\n';
    std::cout << "lowstate_topic=rt/lowstate\n";
    std::cout << "lowstate_samples=" << low_count << '\n';
    std::cout << "lowstate_hz=" << (low_count / elapsed) << '\n';
    std::cout << "sportmodestate_topic=rt/sportmodestate\n";
    std::cout << "sportmodestate_samples=" << sport_count << '\n';
    std::cout << "sportmodestate_hz=" << (sport_count / elapsed) << '\n';
    std::cout << "publisher_count=0\n";

    return (low_count > 0 && sport_count > 0) ? 0 : 1;
}
