// Read-only Go2 Utrack status query.
//
// This tool sends only the SDK2 SwitchGet and IsTracking RPC requests. It
// never calls SwitchSet and contains no motion or configuration client.
//
// It can be compiled as a standalone C++17 executable against the existing
// Unitree SDK2 and bundled CycloneDDS libraries.

#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <exception>
#include <iomanip>
#include <iostream>
#include <string>

#include <unitree/common/error.hpp>
#include <unitree/robot/channel/channel_factory.hpp>
#include <unitree/robot/go2/utrack/utrack_client.hpp>
#include <unitree/robot/internal/internal_error.hpp>

namespace {

constexpr int kSuccess = 0;
constexpr float kDefaultTimeoutSeconds = 2.0F;
constexpr float kMinTimeoutSeconds = 0.1F;
constexpr float kMaxTimeoutSeconds = 30.0F;

enum class QueryState {
    kOk,
    kTimeout,
    kError,
};

struct QueryResult {
    QueryState state;
    std::int32_t return_code;
    bool value;
};

const char* QueryStateName(QueryState state) {
    switch (state) {
        case QueryState::kOk:
            return "ok";
        case QueryState::kTimeout:
            return "timeout";
        case QueryState::kError:
            return "error";
    }
    return "error";
}

QueryState ClassifyReturnCode(std::int32_t return_code) {
    if (return_code == kSuccess) {
        return QueryState::kOk;
    }
    if (return_code == unitree::UT_ERR_TIMEOUT ||
        return_code == unitree::robot::UT_ROBOT_ERR_CLIENT_API_TIMEOUT) {
        return QueryState::kTimeout;
    }
    return QueryState::kError;
}

bool ParseTimeout(const char* text, float& timeout_seconds) {
    if (text == nullptr || *text == '\0') {
        return false;
    }

    errno = 0;
    char* end = nullptr;
    const float parsed = std::strtof(text, &end);
    if (errno != 0 || end == text || *end != '\0') {
        return false;
    }
    if (parsed < kMinTimeoutSeconds || parsed > kMaxTimeoutSeconds) {
        return false;
    }

    timeout_seconds = parsed;
    return true;
}

void PrintNullableBool(const QueryResult& result) {
    if (result.state != QueryState::kOk) {
        std::cout << "null";
        return;
    }
    std::cout << (result.value ? "true" : "false");
}

void PrintQueryObject(const QueryResult& result) {
    std::cout << "{\"status\":\"" << QueryStateName(result.state)
              << "\",\"return_code\":" << result.return_code
              << ",\"value\":";
    PrintNullableBool(result);
    std::cout << '}';
}

const char* CombinedQueryStatus(
    const QueryResult& switch_get,
    const QueryResult& is_tracking) {
    if (switch_get.state == QueryState::kOk &&
        is_tracking.state == QueryState::kOk) {
        return "ok";
    }
    if (switch_get.state == QueryState::kTimeout ||
        is_tracking.state == QueryState::kTimeout) {
        return "timeout";
    }
    return "error";
}

void PrintResult(
    const std::string& interface_name,
    float timeout_seconds,
    const QueryResult& switch_get,
    const QueryResult& is_tracking) {
    const bool both_ok =
        switch_get.state == QueryState::kOk &&
        is_tracking.state == QueryState::kOk;

    const char* verdict = "UTRACK_QUERY_UNAVAILABLE";
    const char* next_action = "CHECK_UTRACK_SERVICE_AND_DDS_RPC";
    if (both_ok && is_tracking.value) {
        verdict = "UTRACK_TRACKING_ACTIVE";
        next_action = "RUN_UWB_PROBE_FOR_SAMPLES";
    } else if (both_ok) {
        verdict = "UTRACK_NOT_IN_OFFICIAL_TRACKING_STATE";
        next_action = "CORRELATE_WITH_UWB_SAMPLE_PROBE";
    }

    std::cout << std::setprecision(6);
    std::cout << '{'
              << "\"tool\":\"go2_utrack_readonly_query\","
              << "\"read_only\":true,"
              << "\"interface\":\"" << interface_name << "\","
              << "\"timeout_seconds\":" << timeout_seconds << ','
              << "\"query_status\":\""
              << CombinedQueryStatus(switch_get, is_tracking) << "\","
              << "\"uwb_switch\":";
    PrintNullableBool(switch_get);
    std::cout << ",\"is_tracking\":";
    PrintNullableBool(is_tracking);
    std::cout << ",\"switch_get\":";
    PrintQueryObject(switch_get);
    std::cout << ",\"is_tracking_query\":";
    PrintQueryObject(is_tracking);
    std::cout << ",\"verdict\":\"" << verdict << "\","
              << "\"next_action\":\"" << next_action << "\""
              << "}\n";
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2 || argc > 3) {
        std::cerr
            << "Usage: " << argv[0]
            << " network_interface [timeout_seconds]\n";
        return 2;
    }

    const std::string interface_name = argv[1];
    if (interface_name.empty()) {
        std::cerr << "network_interface cannot be empty\n";
        return 2;
    }

    float timeout_seconds = kDefaultTimeoutSeconds;
    if (argc == 3 && !ParseTimeout(argv[2], timeout_seconds)) {
        std::cerr
            << "timeout_seconds must be a number between "
            << kMinTimeoutSeconds << " and " << kMaxTimeoutSeconds << '\n';
        return 2;
    }

    try {
        unitree::robot::ChannelFactory::Instance()->Init(
            0,
            interface_name);

        unitree::robot::go2::UtrackClient client;
        client.SetTimeout(timeout_seconds);
        client.Init();

        bool switch_enabled = false;
        const std::int32_t switch_return_code =
            client.SwitchGet(switch_enabled);
        const QueryResult switch_get{
            ClassifyReturnCode(switch_return_code),
            switch_return_code,
            switch_enabled,
        };

        bool tracking_enabled = false;
        const std::int32_t tracking_return_code =
            client.IsTracking(tracking_enabled);
        const QueryResult is_tracking{
            ClassifyReturnCode(tracking_return_code),
            tracking_return_code,
            tracking_enabled,
        };

        PrintResult(
            interface_name,
            timeout_seconds,
            switch_get,
            is_tracking);

        return switch_get.state == QueryState::kOk &&
                       is_tracking.state == QueryState::kOk
                   ? 0
                   : 1;
    } catch (const std::exception& exception) {
        std::cerr << "Utrack query failed before RPC results were available: "
                  << exception.what() << '\n';
        std::cout
            << "{\"tool\":\"go2_utrack_readonly_query\","
            << "\"read_only\":true,"
            << "\"query_status\":\"error\","
            << "\"uwb_switch\":null,"
            << "\"is_tracking\":null,"
            << "\"verdict\":\"UTRACK_QUERY_UNAVAILABLE\","
            << "\"next_action\":\"CHECK_DDS_INITIALIZATION_AND_INTERFACE\""
            << "}\n";
        return 1;
    } catch (...) {
        std::cerr << "Utrack query failed with an unknown exception\n";
        std::cout
            << "{\"tool\":\"go2_utrack_readonly_query\","
            << "\"read_only\":true,"
            << "\"query_status\":\"error\","
            << "\"uwb_switch\":null,"
            << "\"is_tracking\":null,"
            << "\"verdict\":\"UTRACK_QUERY_UNAVAILABLE\","
            << "\"next_action\":\"CHECK_DDS_INITIALIZATION_AND_INTERFACE\""
            << "}\n";
        return 1;
    }
}
