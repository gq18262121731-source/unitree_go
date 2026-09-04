# Go2 EDU 第一阶段实施报告

更新时间：2026-07-13

## 当前环境

```text
Windows 版本：10.0.26200.8655
WSL 默认分发：Ubuntu-20.04
Ubuntu 版本：20.04.6 LTS
Linux Python：3.8.10
虚拟环境路径：/home/est1/.venvs/go2-gateway
项目运行路径：/mnt/e/笨笨狗/go2_dev/go2-gateway
机器狗型号：Unitree Go2 EDU
序列号：B42N6000Q3PABHGC
机器人 IP：192.168.123.161
预期电脑侧接口：eth0 / 192.168.123.222
```

## 已完成

- 已检查项目结构、依赖文件、脚本和测试目录。
- 已在 WSL Ubuntu 20.04 中创建独立 Linux 虚拟环境。
- 已安装项目依赖。
- 已安装并验证 `unitree_sdk2py`。
- 已安装并验证 `cyclonedds==0.10.2`。
- 已验证 OpenCV 和 NumPy 可导入。
- 已在 Linux 虚拟环境中复现自动化测试。
- 已冻结 Linux 依赖到 `/home/est1/go2-gateway-linux-requirements-lock.txt`。
- 已增加 `GO2_CONTROL_ENABLED=false` 只读模式总闸。
- 已增强环境检查脚本，输出 SDK、OpenCV、NumPy、网卡、路由和 ping 状态。

## 项目检查结果

```text
依赖文件：requirements.txt
Gateway 入口：app/main.py
Real 适配器：app/adapters/unitree_adapter.py
Mock 适配器：app/adapters/mock_adapter.py
环境检查脚本：scripts/check_environment.py
状态验证脚本：scripts/verify_state.py
相机验证脚本：scripts/verify_camera.py
测试目录：tests/
```

当前项目没有声明强制 Python 3.10。Ubuntu 20.04 的 Python 3.8.10 可以完成当前只读验证所需依赖安装和测试运行。正式部署仍建议迁移到 Ubuntu 22.04 + Python 3.10。

## 安装结果

```text
unitree_sdk2py 路径：/mnt/e/笨笨狗/go2_dev/unitree_sdk2_python/unitree_sdk2py/__init__.py
SDK Commit：37116c521f1588482e238d8450e471ba78ab9863
CycloneDDS：0.10.2
OpenCV：5.0.0
NumPy：1.24.4
```

第一次访问官方 PyPI 时出现 TLS 连接错误；已改用清华 PyPI 镜像完成安装。

## 测试结果

```text
命令：python -m pytest -q
结果：18 passed
```

原有 17 项测试通过；新增 1 项测试用于验证 `GO2_CONTROL_ENABLED=false` 时会拒绝运动接口，并且不会调用 Mock 适配器的 move 或 stand。

## 本次修改

| 文件 | 修改原因 | 内容 |
| --- | --- | --- |
| `.env.example` | 明确只读验证默认禁用运动控制 | 增加 `GO2_CONTROL_ENABLED=false` |
| `app/config.py` | 支持读取运动控制总闸 | 增加布尔环境变量解析和 `control_enabled` |
| `app/services/robot_service.py` | Gateway Real 只读模式安全保护 | 禁用时拒绝 stand、lie-down、move |
| `app/adapters/mock_adapter.py` | 测试可观测性 | 增加 `stand_count` |
| `tests/test_motion_validation.py` | 覆盖只读模式总闸 | 增加禁用运动控制测试 |
| `scripts/check_environment.py` | 环境验收更明确 | 增加控制开关、NumPy、网卡、路由、ping 检查 |

这些修改属于环境验证和安全保护，不改变已有 API 路径，不重写 Gateway 架构。

## 当前阻塞点

只读 DDS 状态验证尚未执行成功，因为当前物理以太网链路已经断开。

最新检查结果：

```text
Windows 以太网：Media disconnected
WSL eth0：DOWN
ip route get 192.168.123.161：错误走向 eth1 / 192.168.8.1
ping -I eth0 192.168.123.161：failed
```

因此当前不能继续运行：

```text
scripts/verify_state.py
scripts/verify_camera.py
Gateway Real 模式
任何真实运动接口
```

## 恢复后下一步

先恢复网线连接，使 Windows 以太网重新显示 `192.168.123.222/24`，然后在 WSL 中确认：

```bash
ip -br addr
ip route get 192.168.123.161
ping -I eth0 -c 4 192.168.123.161
```

通过后继续：

```bash
cd "/mnt/e/笨笨狗/go2_dev/go2-gateway"
source ~/.venvs/go2-gateway/bin/activate

export GO2_MODE=real
export GO2_NETWORK_INTERFACE=eth0
export GO2_ROBOT_ID=go2-edu-001
export GO2_CONTROL_ENABLED=false

python scripts/check_environment.py
python scripts/verify_state.py
```

状态读取通过后，再进行相机和 Gateway Real 只读验证。

## 安全状态

本轮没有执行任何真实运动命令，没有调用 stand、lie-down、move、低层关节或电机控制。当前项目已具备 `GO2_CONTROL_ENABLED=false` 只读模式保护。
