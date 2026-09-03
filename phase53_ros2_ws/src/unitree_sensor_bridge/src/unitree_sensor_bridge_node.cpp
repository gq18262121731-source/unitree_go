#include <atomic>
#include <chrono>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>

#include <nav_msgs/msg/odometry.hpp>
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/imu.hpp>
#include <sensor_msgs/msg/point_cloud2.hpp>

namespace unitree_sensor_bridge {

class UnitreeSensorBridge final : public rclcpp::Node {
 public:
  UnitreeSensorBridge()
      : Node("unitree_sensor_bridge") {
    const auto lidar_source = declare_parameter<std::string>(
        "lidar_source", "/utlidar/cloud");
    const auto imu_source = declare_parameter<std::string>(
        "imu_source", "/utlidar/imu");
    const auto odom_source = declare_parameter<std::string>(
        "odom_source", "/utlidar/robot_odom");

    const auto lidar_target = declare_parameter<std::string>(
        "lidar_target", "/sensor/lidar");
    const auto imu_target = declare_parameter<std::string>(
        "imu_target", "/sensor/imu");
    const auto odom_target = declare_parameter<std::string>(
        "odom_target", "/odom");

    const auto lidar_qos = rclcpp::SensorDataQoS().keep_last(5);
    const auto imu_qos = rclcpp::SensorDataQoS().keep_last(50);
    const auto odom_qos = rclcpp::SensorDataQoS().keep_last(50);

    lidar_publisher_ =
        create_publisher<sensor_msgs::msg::PointCloud2>(
            lidar_target, lidar_qos);
    imu_publisher_ =
        create_publisher<sensor_msgs::msg::Imu>(imu_target, imu_qos);
    odom_publisher_ =
        create_publisher<nav_msgs::msg::Odometry>(odom_target, odom_qos);

    lidar_subscription_ =
        create_subscription<sensor_msgs::msg::PointCloud2>(
            lidar_source,
            lidar_qos,
            [this](sensor_msgs::msg::PointCloud2::ConstSharedPtr message) {
              // Forward the complete message without changing its source stamp,
              // frame ID, point fields, or payload.
              lidar_publisher_->publish(*message);
              lidar_count_.fetch_add(1, std::memory_order_relaxed);
            });

    imu_subscription_ =
        create_subscription<sensor_msgs::msg::Imu>(
            imu_source,
            imu_qos,
            [this](sensor_msgs::msg::Imu::ConstSharedPtr message) {
              // Preserve the source header and all covariance fields.
              imu_publisher_->publish(*message);
              imu_count_.fetch_add(1, std::memory_order_relaxed);
            });

    odom_subscription_ =
        create_subscription<nav_msgs::msg::Odometry>(
            odom_source,
            odom_qos,
            [this](nav_msgs::msg::Odometry::ConstSharedPtr message) {
              // Preserve source stamp, odom/base_link frames, pose, and twist.
              odom_publisher_->publish(*message);
              odom_count_.fetch_add(1, std::memory_order_relaxed);
            });

    status_timer_ = create_wall_timer(
        std::chrono::seconds(5),
        std::bind(&UnitreeSensorBridge::LogStatus, this));

    RCLCPP_INFO(
        get_logger(),
        "Read-only bridge started: %s -> %s, %s -> %s, %s -> %s",
        lidar_source.c_str(),
        lidar_target.c_str(),
        imu_source.c_str(),
        imu_target.c_str(),
        odom_source.c_str(),
        odom_target.c_str());
  }

 private:
  void LogStatus() {
    RCLCPP_INFO(
        get_logger(),
        "forwarded totals: lidar=%llu imu=%llu odom=%llu",
        static_cast<unsigned long long>(
            lidar_count_.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(
            imu_count_.load(std::memory_order_relaxed)),
        static_cast<unsigned long long>(
            odom_count_.load(std::memory_order_relaxed)));
  }

  rclcpp::Publisher<sensor_msgs::msg::PointCloud2>::SharedPtr
      lidar_publisher_;
  rclcpp::Publisher<sensor_msgs::msg::Imu>::SharedPtr imu_publisher_;
  rclcpp::Publisher<nav_msgs::msg::Odometry>::SharedPtr odom_publisher_;

  rclcpp::Subscription<sensor_msgs::msg::PointCloud2>::SharedPtr
      lidar_subscription_;
  rclcpp::Subscription<sensor_msgs::msg::Imu>::SharedPtr imu_subscription_;
  rclcpp::Subscription<nav_msgs::msg::Odometry>::SharedPtr
      odom_subscription_;

  rclcpp::TimerBase::SharedPtr status_timer_;

  std::atomic<std::uint64_t> lidar_count_{0};
  std::atomic<std::uint64_t> imu_count_{0};
  std::atomic<std::uint64_t> odom_count_{0};
};

}  // namespace unitree_sensor_bridge

int main(int argc, char* argv[]) {
  rclcpp::init(argc, argv);
  rclcpp::spin(
      std::make_shared<unitree_sensor_bridge::UnitreeSensorBridge>());
  rclcpp::shutdown();
  return 0;
}
