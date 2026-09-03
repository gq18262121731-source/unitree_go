#include <unitree/robot/channel/channel_factory.hpp>
#include <unitree/robot/go2/config/config_client.hpp>
#include <unitree/robot/go2/robot_state/robot_state_client.hpp>

#include <algorithm>
#include <cctype>
#include <chrono>
#include <iostream>
#include <set>
#include <string>
#include <thread>
#include <vector>

namespace {

std::string Lower(std::string value) {
  std::transform(value.begin(), value.end(), value.begin(),
                 [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
  return value;
}

bool ContainsCalibrationKeyword(const std::string& value) {
  const std::string lower = Lower(value);
  static const std::vector<std::string> keywords = {
      "extrinsic", "calibrat", "lidar", "utlidar", "radar", "sensor",
      "transform", "base_link", "pose", "mount"};
  for (const auto& keyword : keywords) {
    if (lower.find(keyword) != std::string::npos) {
      return true;
    }
  }
  return false;
}

std::string EscapeLine(std::string value) {
  for (char& c : value) {
    if (c == '\n' || c == '\r' || c == '\t') {
      c = ' ';
    }
  }
  return value;
}

void PrintKeywordWindows(const std::string& name, const std::string& content) {
  const std::string lower = Lower(content);
  static const std::vector<std::string> keywords = {
      "extrinsic", "calibrat", "utlidar", "lidar", "radar",
      "sensor", "transform", "base_link", "pose", "mount"};
  std::set<std::pair<std::size_t, std::size_t>> windows;
  for (const auto& keyword : keywords) {
    std::size_t pos = 0;
    while ((pos = lower.find(keyword, pos)) != std::string::npos) {
      const std::size_t begin = pos > 160 ? pos - 160 : 0;
      const std::size_t end = std::min(content.size(), pos + keyword.size() + 320);
      windows.emplace(begin, end);
      pos += keyword.size();
    }
  }

  std::size_t index = 0;
  for (const auto& window : windows) {
    std::cout << "CONFIG_MATCH name=" << name << " index=" << index++
              << " text=" << EscapeLine(content.substr(
                     window.first, window.second - window.first))
              << "\n";
  }
}

}  // namespace

int main(int argc, char** argv) {
  if (argc != 2) {
    std::cerr << "Usage: phase542_sdk_api_probe <network-interface>\n";
    return 2;
  }

  const std::string interface_name = argv[1];
  std::cout << "PHASE=5.4.2\n";
  std::cout << "MODE=READ_ONLY_RPC\n";
  std::cout << "INTERFACE=" << interface_name << "\n";
  std::cout << "FORBIDDEN_CALLS=Config.Set,Config.Del,ServiceSwitch,"
               "SetReportFreq,SportClient,move,TF,SLAM,Nav2\n";

  unitree::robot::ChannelFactory::Instance()->Init(0, interface_name);
  std::this_thread::sleep_for(std::chrono::milliseconds(500));

  unitree::robot::go2::RobotStateClient robot_state;
  robot_state.SetTimeout(3.0F);
  robot_state.Init();

  std::cout << "ROBOT_STATE_CLIENT_API_VERSION="
            << robot_state.GetApiVersion() << "\n";
  std::cout << "ROBOT_STATE_SERVER_API_VERSION="
            << robot_state.GetServerApiVersion() << "\n";

  std::vector<unitree::robot::go2::ServiceState> services;
  const int32_t service_code = robot_state.ServiceList(services);
  std::cout << "ROBOT_STATE_SERVICE_LIST_CODE=" << service_code << "\n";
  std::cout << "ROBOT_STATE_SERVICE_COUNT=" << services.size() << "\n";

  std::set<std::string> config_candidates = {
      "lidar",       "utlidar",       "lidar_config",
      "utlidar_config", "sensor",     "sensor_config",
      "calibration", "extrinsic",     "radar",
      "robot",       "robot_config",  "slam",
      "uslam",       "mapping",       "pose"};

  for (const auto& service : services) {
    std::cout << "SERVICE name=" << service.name
              << " status=" << service.status
              << " protect=" << service.protect << "\n";
    if (ContainsCalibrationKeyword(service.name)) {
      config_candidates.insert(service.name);
    }
  }

  unitree::robot::go2::ConfigClient config;
  config.SetTimeout(3.0F);
  config.Init();
  std::cout << "CONFIG_CLIENT_API_VERSION=" << config.GetApiVersion() << "\n";
  std::cout << "CONFIG_SERVER_API_VERSION="
            << config.GetServerApiVersion() << "\n";

  for (const auto& name : config_candidates) {
    unitree::robot::go2::ConfigMeta meta;
    const int32_t meta_code = config.Meta(name, meta);
    std::cout << "CONFIG_META name=" << name
              << " code=" << meta_code;
    if (meta_code == 0) {
      std::cout << " size=" << meta.size
                << " epoch=" << meta.epoch
                << " lastModified=" << EscapeLine(meta.lastModified);
    }
    std::cout << "\n";

    if (meta_code != 0) {
      continue;
    }

    std::string content;
    const int32_t get_code = config.Get(name, content);
    std::cout << "CONFIG_GET name=" << name
              << " code=" << get_code
              << " content_size=" << content.size()
              << " keyword_match="
              << (ContainsCalibrationKeyword(content) ? "true" : "false")
              << "\n";
    if (get_code == 0 && ContainsCalibrationKeyword(content)) {
      PrintKeywordWindows(name, content);
    }
  }

  std::cout << "WRITE_API_CALL_COUNT=0\n";
  std::cout << "CONTROL_API_CALL_COUNT=0\n";
  return service_code == 0 ? 0 : 3;
}
