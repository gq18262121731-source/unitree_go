from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager


ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
OUTPUT_DIR = ARTIFACTS / "ppt_ppo_strategy_effects"
REAL_UWB_CAPTURE = ARTIFACTS / "phase7_t1_uwb_live_20260823T105956Z.jsonl"
TARGET_DISTANCE_M = 1.5


def configure_matplotlib() -> None:
    preferred_fonts = [
        "Microsoft YaHei",
        "SimHei",
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "Arial Unicode MS",
    ]
    installed = {font.name for font in font_manager.fontManager.ttflist}
    for font_name in preferred_fonts:
        if font_name in installed:
            plt.rcParams["font.sans-serif"] = [font_name, "DejaVu Sans"]
            break
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["figure.dpi"] = 150
    plt.rcParams["savefig.dpi"] = 300
    plt.rcParams["axes.edgecolor"] = "#D6DAE2"
    plt.rcParams["axes.linewidth"] = 0.9
    plt.rcParams["grid.color"] = "#E8EBF0"
    plt.rcParams["grid.linewidth"] = 0.8


def load_real_uwb_distance() -> tuple[np.ndarray, np.ndarray, str]:
    if not REAL_UWB_CAPTURE.exists():
        return simulated_uwb_distance()

    times: list[float] = []
    distances: list[float] = []
    with REAL_UWB_CAPTURE.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") != "uwb_sample":
                continue
            sample = event.get("sample") or {}
            distance = sample.get("distance_est")
            elapsed = event.get("elapsed_seconds")
            if isinstance(distance, (int, float)) and isinstance(elapsed, (int, float)):
                times.append(float(elapsed))
                distances.append(float(distance))

    if len(distances) < 10:
        return simulated_uwb_distance()

    time_arr = np.asarray(times, dtype=float)
    dist_arr = np.asarray(distances, dtype=float)
    order = np.argsort(time_arr)
    time_arr = time_arr[order]
    dist_arr = dist_arr[order]
    time_arr = time_arr - time_arr[0]

    # Keep a compact PPT-friendly window while preserving the real jitter pattern.
    mask = time_arr <= min(35.0, float(time_arr[-1]))
    return time_arr[mask], dist_arr[mask], str(REAL_UWB_CAPTURE)


def simulated_uwb_distance() -> tuple[np.ndarray, np.ndarray, str]:
    rng = np.random.default_rng(20260909)
    time_arr = np.arange(0.0, 30.01, 0.1)
    approach = TARGET_DISTANCE_M + 0.95 * np.exp(-time_arr / 6.5)
    body_motion = 0.06 * np.sin(2.0 * math.pi * 0.34 * time_arr)
    multipath = 0.035 * np.sin(2.0 * math.pi * 1.2 * time_arr + 0.5)
    random_jitter = rng.normal(0.0, 0.045, size=time_arr.shape)
    return time_arr, approach + body_motion + multipath + random_jitter, "simulated"


def ewma(values: np.ndarray, alpha: float = 0.18) -> np.ndarray:
    filtered = np.empty_like(values)
    filtered[0] = values[0]
    for index in range(1, len(values)):
        filtered[index] = alpha * values[index] + (1.0 - alpha) * filtered[index - 1]
    return filtered


def build_control_demo() -> dict[str, np.ndarray]:
    time_arr = np.arange(0.0, 30.01, 0.1)
    target = TARGET_DISTANCE_M

    traditional_distance = (
        target
        + 1.18 * np.exp(-time_arr / 7.3)
        + 0.18 * np.exp(-time_arr / 8.0) * np.sin(1.65 * time_arr)
        + 0.035 * np.sin(4.4 * time_arr) * np.exp(-time_arr / 24.0)
    )
    ppo_distance = (
        target
        + 1.18 * np.exp(-time_arr / 3.35)
        + 0.055 * np.exp(-time_arr / 5.0) * np.sin(2.05 * time_arr + 0.25)
        + 0.010 * np.sin(3.4 * time_arr) * np.exp(-time_arr / 22.0)
    )

    traditional_vx = (
        0.15
        + 0.12 * np.exp(-time_arr / 6.0)
        + 0.035 * np.sin(2.35 * time_arr) * np.exp(-time_arr / 13.0)
        + 0.014 * np.sin(7.5 * time_arr)
    )
    ppo_vx = (
        0.15
        + 0.10 * np.exp(-time_arr / 3.1)
        + 0.012 * np.sin(2.0 * time_arr) * np.exp(-time_arr / 13.0)
        + 0.004 * np.sin(5.2 * time_arr)
    )

    return {
        "time": time_arr,
        "traditional_distance": traditional_distance,
        "ppo_distance": ppo_distance,
        "traditional_vx": np.clip(traditional_vx, 0.0, 0.30),
        "ppo_vx": np.clip(ppo_vx, 0.0, 0.30),
    }


def settling_time_seconds(time_arr: np.ndarray, values: np.ndarray, band: float = 0.08) -> float:
    errors = np.abs(values - TARGET_DISTANCE_M)
    for index in range(len(errors)):
        if np.all(errors[index:] <= band):
            return float(time_arr[index])
    return float("nan")


def control_variation(time_arr: np.ndarray, command: np.ndarray) -> float:
    derivative = np.diff(command) / np.diff(time_arr)
    return float(np.sqrt(np.mean(derivative * derivative)))


def summarize_metrics(data: dict[str, np.ndarray]) -> dict[str, dict[str, float]]:
    time_arr = data["time"]
    result: dict[str, dict[str, float]] = {}
    for label, series_key, control_key in [
        ("traditional_control", "traditional_distance", "traditional_vx"),
        ("ppo_policy_control", "ppo_distance", "ppo_vx"),
    ]:
        distance = data[series_key]
        error = distance - TARGET_DISTANCE_M
        result[label] = {
            "mae_m": float(np.mean(np.abs(error))),
            "rmse_m": float(np.sqrt(np.mean(error * error))),
            "steady_state_error_m": float(np.mean(np.abs(error[time_arr >= 25.0]))),
            "settling_time_s_band_0_08m": settling_time_seconds(time_arr, distance),
            "vx_variation_rms_mps2": control_variation(time_arr, data[control_key]),
        }
    return result


def style_axes(ax: plt.Axes) -> None:
    ax.grid(True, axis="y")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(colors="#445066", labelsize=10)
    ax.xaxis.label.set_color("#2E3440")
    ax.yaxis.label.set_color("#2E3440")
    ax.title.set_color("#111827")


def save_figure(fig: plt.Figure, stem: str) -> dict[str, str]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    png_path = OUTPUT_DIR / f"{stem}.png"
    svg_path = OUTPUT_DIR / f"{stem}.svg"
    fig.savefig(png_path, bbox_inches="tight", facecolor="white")
    fig.savefig(svg_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return {"png": str(png_path), "svg": str(svg_path)}


def add_caveat(fig: plt.Figure, text: str) -> None:
    fig.text(0.99, 0.01, text, ha="right", va="bottom", fontsize=9, color="#6B7280")


def plot_uwb_filter() -> dict[str, str | float]:
    time_arr, raw_distance, source = load_real_uwb_distance()
    filtered = ewma(raw_distance)

    fig, ax = plt.subplots(figsize=(10.5, 5.8))
    ax.plot(time_arr, raw_distance, color="#9AA3B2", linewidth=1.3, alpha=0.75, label="UWB 原始数据")
    ax.plot(time_arr, filtered, color="#2563EB", linewidth=2.4, label="滤波后数据")
    ax.axhline(TARGET_DISTANCE_M, color="#DC2626", linewidth=1.8, linestyle="--", label="目标距离 1.5 m")
    ax.set_title("UWB 距离数据滤波效果", fontsize=17, fontweight="bold", pad=12)
    ax.set_xlabel("时间 / s", fontsize=12)
    ax.set_ylabel("与老人距离 / m", fontsize=12)
    ax.legend(loc="upper right", frameon=False, fontsize=10)
    style_axes(ax)
    source_label = "真实 UWB 采集数据" if source != "simulated" else "示意/仿真数据"
    add_caveat(fig, source_label)
    paths = save_figure(fig, "01_uwb_filter_effect")
    paths.update(
        {
            "raw_std_m": float(np.std(raw_distance)),
            "filtered_std_m": float(np.std(filtered)),
            "source": source,
        }
    )
    return paths


def plot_distance_comparison(data: dict[str, np.ndarray]) -> dict[str, str]:
    fig, ax = plt.subplots(figsize=(10.5, 5.8))
    ax.plot(data["time"], data["traditional_distance"], color="#F97316", linewidth=2.2, label="滤波 UWB + 传统控制")
    ax.plot(data["time"], data["ppo_distance"], color="#16A34A", linewidth=2.6, label="UWB + PPO 策略控制")
    ax.axhline(TARGET_DISTANCE_M, color="#DC2626", linewidth=1.8, linestyle="--", label="目标距离 1.5 m")
    ax.set_title("传统控制 vs PPO 跟随距离", fontsize=17, fontweight="bold", pad=12)
    ax.set_xlabel("时间 / s", fontsize=12)
    ax.set_ylabel("跟随距离 / m", fontsize=12)
    ax.set_ylim(1.35, 2.85)
    ax.legend(loc="upper right", frameon=False, fontsize=10)
    style_axes(ax)
    add_caveat(fig, "示意/仿真结果，待实测日志替换")
    return save_figure(fig, "02_follow_distance_comparison")


def plot_control_smoothness(data: dict[str, np.ndarray]) -> dict[str, str]:
    fig, ax = plt.subplots(figsize=(10.5, 5.8))
    ax.plot(data["time"], data["traditional_vx"], color="#F97316", linewidth=2.0, label="传统控制")
    ax.plot(data["time"], data["ppo_vx"], color="#16A34A", linewidth=2.5, label="PPO 控制")
    ax.set_title("运动控制平滑度对比", fontsize=17, fontweight="bold", pad=12)
    ax.set_xlabel("时间 / s", fontsize=12)
    ax.set_ylabel("线速度 vx / (m/s)", fontsize=12)
    ax.set_ylim(0.10, 0.31)
    ax.legend(loc="upper right", frameon=False, fontsize=10)
    style_axes(ax)
    add_caveat(fig, "示意/仿真结果，待实测日志替换")
    return save_figure(fig, "03_control_smoothness_comparison")


def plot_summary_panel(data: dict[str, np.ndarray], metrics: dict[str, dict[str, float]]) -> dict[str, str]:
    fig = plt.figure(figsize=(13.33, 7.5))
    grid = fig.add_gridspec(2, 2, width_ratios=[1.25, 1.0], height_ratios=[1.0, 1.0], wspace=0.28, hspace=0.34)

    ax1 = fig.add_subplot(grid[0, 0])
    ax1.plot(data["time"], data["traditional_distance"], color="#F97316", linewidth=2.0, label="传统控制")
    ax1.plot(data["time"], data["ppo_distance"], color="#16A34A", linewidth=2.4, label="PPO 控制")
    ax1.axhline(TARGET_DISTANCE_M, color="#DC2626", linewidth=1.5, linestyle="--", label="目标距离")
    ax1.set_title("跟随距离响应", fontsize=13, fontweight="bold")
    ax1.set_xlabel("时间 / s")
    ax1.set_ylabel("距离 / m")
    ax1.legend(loc="upper right", frameon=False, fontsize=9)
    style_axes(ax1)

    ax2 = fig.add_subplot(grid[1, 0])
    ax2.plot(data["time"], data["traditional_vx"], color="#F97316", linewidth=1.8, label="传统控制")
    ax2.plot(data["time"], data["ppo_vx"], color="#16A34A", linewidth=2.2, label="PPO 控制")
    ax2.set_title("线速度控制量", fontsize=13, fontweight="bold")
    ax2.set_xlabel("时间 / s")
    ax2.set_ylabel("vx / (m/s)")
    ax2.legend(loc="upper right", frameon=False, fontsize=9)
    style_axes(ax2)

    ax3 = fig.add_subplot(grid[:, 1])
    labels = ["平均距离误差", "稳态误差", "控制波动"]
    traditional = [
        metrics["traditional_control"]["mae_m"],
        metrics["traditional_control"]["steady_state_error_m"],
        metrics["traditional_control"]["vx_variation_rms_mps2"],
    ]
    ppo = [
        metrics["ppo_policy_control"]["mae_m"],
        metrics["ppo_policy_control"]["steady_state_error_m"],
        metrics["ppo_policy_control"]["vx_variation_rms_mps2"],
    ]
    y = np.arange(len(labels))
    height = 0.32
    ax3.barh(y + height / 2, traditional, height, color="#FDBA74", label="传统控制")
    ax3.barh(y - height / 2, ppo, height, color="#86EFAC", label="PPO 控制")
    for yi, value in zip(y + height / 2, traditional):
        ax3.text(value + 0.01, yi, f"{value:.2f}", va="center", fontsize=10, color="#7C2D12")
    for yi, value in zip(y - height / 2, ppo):
        ax3.text(value + 0.01, yi, f"{value:.2f}", va="center", fontsize=10, color="#14532D")
    ax3.set_yticks(y, labels)
    ax3.invert_yaxis()
    ax3.set_title("核心指标对比", fontsize=13, fontweight="bold")
    ax3.set_xlabel("数值越低越好")
    ax3.legend(loc="lower right", frameon=False, fontsize=9)
    style_axes(ax3)

    fig.suptitle("PPO 策略优化效果", fontsize=22, fontweight="bold", x=0.06, ha="left")
    fig.text(
        0.06,
        0.91,
        "在 UWB 定位基础上，通过 PPO 动态优化运动策略，提高跟随稳定性与控制平滑度。",
        ha="left",
        fontsize=12,
        color="#445066",
    )
    add_caveat(fig, "示意/仿真结果，待实测日志替换")
    return save_figure(fig, "04_ppo_strategy_effects_summary")


def main() -> int:
    configure_matplotlib()
    data = build_control_demo()
    metrics = summarize_metrics(data)
    outputs: dict[str, object] = {
        "target_distance_m": TARGET_DISTANCE_M,
        "caveat": "Charts involving PPO are deterministic illustrative/simulation results, not real PPO test logs.",
        "figures": {
            "uwb_filter_effect": plot_uwb_filter(),
            "follow_distance_comparison": plot_distance_comparison(data),
            "control_smoothness_comparison": plot_control_smoothness(data),
            "ppo_strategy_effects_summary": plot_summary_panel(data, metrics),
        },
        "metrics": metrics,
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    metrics_path = OUTPUT_DIR / "metrics_and_sources.json"
    metrics_path.write_text(json.dumps(outputs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(outputs, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
