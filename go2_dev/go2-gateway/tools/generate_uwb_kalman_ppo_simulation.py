from __future__ import annotations

import csv
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "artifacts" / "uwb_kalman_ppo_simulation"

RANDOM_SEED = 42
DT = 0.1
SIM_TIME = 60.0
IDEAL_DISTANCE = 1.2

FIGSIZE = (16, 7.5)
XLIM = (0.0, 60.0)
YLIM = (0.75, 1.80)
EVENTS = (
    (20.0, "突然转向"),
    (32.0, "突然减速"),
    (38.0, "再次转向"),
)


def configure_matplotlib() -> None:
    """配置中文字体和适合 PPT 的基础绘图风格。"""
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
    plt.rcParams["axes.edgecolor"] = "#D0D7E2"
    plt.rcParams["axes.linewidth"] = 1.2
    plt.rcParams["grid.color"] = "#E8EDF4"
    plt.rcParams["grid.linewidth"] = 1.0
    plt.rcParams["svg.fonttype"] = "none"


def smooth_step(time: np.ndarray, start: float, duration: float) -> np.ndarray:
    phase = np.clip((time - start) / duration, 0.0, 1.0)
    return phase * phase * (3.0 - 2.0 * phase)


def decaying_step(time: np.ndarray, center: float, amplitude: float, tau: float) -> np.ndarray:
    value = np.zeros_like(time, dtype=float)
    mask = time >= center
    value[mask] = amplitude * np.exp(-(time[mask] - center) / tau)
    return value


def decaying_wave(
    time: np.ndarray,
    center: float,
    amplitude: float,
    tau: float,
    frequency: float,
) -> np.ndarray:
    value = np.zeros_like(time, dtype=float)
    mask = time >= center
    elapsed = time[mask] - center
    value[mask] = amplitude * np.exp(-elapsed / tau) * np.sin(frequency * elapsed)
    return value


def moving_average(values: np.ndarray, window: int) -> np.ndarray:
    kernel = np.ones(window, dtype=float) / window
    pad_left = window // 2
    pad_right = window - 1 - pad_left
    padded = np.pad(values, (pad_left, pad_right), mode="edge")
    return np.convolve(padded, kernel, mode="valid")


def generate_target_trajectory(time: np.ndarray) -> dict[str, np.ndarray]:
    """生成同一套老人运动事件，三种方案都基于这些事件响应。"""
    walking_speed = np.full_like(time, 0.60)
    walking_speed += -0.05 * smooth_step(time, 12.0, 8.0)
    walking_speed += 0.06 * smooth_step(time, 22.0, 5.0)
    walking_speed += -0.36 * smooth_step(time, 32.0, 1.0)
    walking_speed += 0.20 * smooth_step(time, 35.0, 3.0)
    walking_speed += -0.44 * smooth_step(time, 45.0, 2.0)
    walking_speed += 0.50 * smooth_step(time, 50.0, 3.0)
    walking_speed = np.clip(walking_speed, 0.05, 0.72)

    direction_deg = (
        4.0
        + 10.0 * smooth_step(time, 12.0, 8.0)
        + 42.0 * smooth_step(time, 19.7, 0.7)
        - 55.0 * smooth_step(time, 37.7, 0.8)
        + 6.0 * smooth_step(time, 50.0, 6.0)
    )

    # 这些事件不是单独画出来的数据源，而是三种控制曲线共同面对的动态扰动。
    event_distance = (
        decaying_step(time, 20.0, 0.34, 4.6)
        + decaying_wave(time, 20.0, 0.09, 3.0, 2.8)
        - decaying_step(time, 32.0, 0.18, 3.4)
        + decaying_wave(time, 32.0, 0.05, 2.2, 3.1)
        + decaying_step(time, 38.0, 0.38, 4.9)
        + decaying_wave(time, 38.0, 0.10, 3.2, 2.7)
    )

    low_speed_bias = -0.035 * (
        smooth_step(time, 45.0, 1.3) - smooth_step(time, 50.0, 1.5)
    )

    return {
        "walking_speed": walking_speed,
        "direction_deg": direction_deg,
        "event_distance": event_distance,
        "low_speed_bias": low_speed_bias,
    }


def simulate_uwb_measurement(
    time: np.ndarray,
    scene: dict[str, np.ndarray],
    rng: np.random.Generator,
) -> np.ndarray:
    """方案1：UWB定位 + 基础跟随控制，保留明显但合理的测量抖动。"""
    low_frequency_motion = 0.055 * np.sin(0.42 * time + 0.2) + 0.033 * np.sin(
        0.95 * time
    )
    high_frequency_noise = 0.052 * np.sin(5.4 * time + 0.6) + 0.034 * np.sin(
        11.3 * time
    )
    random_noise = rng.normal(0.0, 0.050, size=time.shape)

    distance = (
        IDEAL_DISTANCE
        + scene["event_distance"]
        + scene["low_speed_bias"]
        + low_frequency_motion
        + high_frequency_noise
        + random_noise
    )

    outlier_count = max(1, int(0.015 * len(time)))
    outlier_index = rng.choice(len(time), size=outlier_count, replace=False)
    distance[outlier_index] += rng.normal(0.0, 0.14, size=outlier_count)

    return np.clip(distance, 0.88, 1.72)


def kalman_filter(
    measurement: np.ndarray,
    process_var: float = 0.0022,
    measurement_var: float = 0.010,
) -> np.ndarray:
    """一维随机游走卡尔曼滤波，主要降低 UWB 高频抖动。"""
    estimate = float(measurement[0])
    covariance = 1.0
    filtered = np.empty_like(measurement, dtype=float)

    for index, value in enumerate(measurement):
        covariance += process_var
        kalman_gain = covariance / (covariance + measurement_var)
        estimate = estimate + kalman_gain * (float(value) - estimate)
        covariance = (1.0 - kalman_gain) * covariance
        filtered[index] = estimate

    return filtered


def simulate_kalman_controller(
    time: np.ndarray,
    uwb_distance: np.ndarray,
    scene: dict[str, np.ndarray],
) -> np.ndarray:
    """方案2：滤波后随机抖动更小，但突发变化后仍存在拖尾。"""
    filtered_measurement = kalman_filter(uwb_distance)
    event_lag = (
        decaying_step(time, 20.0, 0.075, 3.8)
        - decaying_step(time, 32.0, 0.035, 2.8)
        + decaying_step(time, 38.0, 0.085, 4.0)
    )
    slow_drift = 0.018 * np.sin(0.38 * time + 1.1) + 0.010 * np.sin(0.12 * time)

    distance = (
        0.58 * filtered_measurement
        + 0.42 * IDEAL_DISTANCE
        + 0.34 * scene["event_distance"]
        + event_lag
        + 0.35 * scene["low_speed_bias"]
        + 0.78 * slow_drift
    )
    return np.clip(distance, 0.94, 1.55)


def simulate_ppo_controller(
    time: np.ndarray,
    kalman_distance: np.ndarray,
    scene: dict[str, np.ndarray],
    rng: np.random.Generator,
) -> np.ndarray:
    """方案3：PPO优化控制策略，突发变化后峰值更低且恢复更快。"""
    gentle_wave = 0.060 * np.sin(0.52 * time + 0.8) + 0.025 * np.sin(1.4 * time)
    residual_noise = moving_average(rng.normal(0.0, 0.026, size=time.shape), 5)
    fast_event_response = (
        decaying_step(time, 20.0, 0.205, 1.45)
        - decaying_step(time, 32.0, 0.100, 1.20)
        + decaying_step(time, 38.0, 0.235, 1.55)
        + decaying_wave(time, 20.0, 0.045, 1.2, 3.0)
        + decaying_wave(time, 38.0, 0.050, 1.3, 2.8)
    )

    # PPO是控制策略优化，不是对UWB再次滤波；这里保留自然残余波动。
    distance = (
        IDEAL_DISTANCE
        + 0.28 * (kalman_distance - IDEAL_DISTANCE)
        + 0.30 * scene["low_speed_bias"]
        + fast_event_response
        + gentle_wave
        + residual_noise
    )
    return np.clip(distance, 1.08, 1.44)


def generate_progressive_data() -> dict[str, np.ndarray]:
    np.random.seed(RANDOM_SEED)
    rng = np.random.default_rng(RANDOM_SEED)
    time = np.arange(0.0, SIM_TIME + DT / 2.0, DT)
    scene = generate_target_trajectory(time)
    uwb_distance = simulate_uwb_measurement(time, scene, rng)
    kalman_distance = simulate_kalman_controller(time, uwb_distance, scene)
    ppo_distance = simulate_ppo_controller(time, kalman_distance, scene, rng)

    return {
        "time": time,
        "ideal_distance": np.full_like(time, IDEAL_DISTANCE),
        "uwb_distance": uwb_distance,
        "kalman_distance": kalman_distance,
        "ppo_distance": ppo_distance,
    }


def save_csv(data: dict[str, np.ndarray]) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "progressive_tracking_comparison.csv"
    columns = [
        "time",
        "ideal_distance",
        "uwb_distance",
        "kalman_distance",
        "ppo_distance",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for index in range(len(data["time"])):
            writer.writerow([f"{float(data[column][index]):.6f}" for column in columns])
    return csv_path


def load_progressive_csv(csv_path: Path) -> dict[str, np.ndarray]:
    table = np.genfromtxt(csv_path, delimiter=",", names=True, encoding="utf-8-sig")
    return {name: np.asarray(table[name], dtype=float) for name in table.dtype.names}


def style_axes(ax: plt.Axes) -> None:
    ax.set_xlim(*XLIM)
    ax.set_ylim(*YLIM)
    ax.set_xticks(np.arange(0, 61, 10))
    ax.set_yticks(np.arange(0.8, 1.81, 0.2))
    ax.grid(True, axis="y", alpha=0.95)
    ax.grid(False, axis="x")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.tick_params(colors="#374151", labelsize=20)
    ax.xaxis.label.set_color("#111827")
    ax.yaxis.label.set_color("#111827")
    ax.title.set_color("#111827")


def add_common_elements(
    ax: plt.Axes,
    show_event_labels: bool = True,
    ideal_linewidth: float = 3.0,
    ideal_color: str = "#4B5563",
    ideal_zorder: int = 1,
) -> None:
    ax.axhline(
        IDEAL_DISTANCE,
        color=ideal_color,
        linewidth=ideal_linewidth,
        linestyle=":",
        label="理想跟随距离 1.2 m",
        zorder=ideal_zorder,
    )
    for event_time, label in EVENTS:
        ax.axvline(
            event_time,
            color="#9CA3AF",
            linewidth=1.8,
            linestyle="--",
            alpha=0.42,
            zorder=0,
        )
        if show_event_labels:
            ax.text(
                event_time + 0.35,
                YLIM[1] - 0.055,
                label,
                ha="left",
                va="top",
                fontsize=17,
                color="#7A8494",
            )


def add_simulation_mark(fig: plt.Figure) -> None:
    fig.text(
        0.985,
        0.018,
        "模拟数据 / Simulation",
        ha="right",
        va="bottom",
        fontsize=16,
        color="#6B7280",
    )


def save_figure(fig: plt.Figure, stem: str) -> list[Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    png_path = OUTPUT_DIR / f"{stem}.png"
    transparent_path = OUTPUT_DIR / f"{stem}_transparent.png"
    svg_path = OUTPUT_DIR / f"{stem}.svg"
    fig.savefig(png_path, facecolor="white", dpi=300)
    fig.savefig(transparent_path, transparent=True, dpi=300)
    fig.savefig(svg_path, facecolor="white")
    plt.close(fig)
    return [png_path, transparent_path, svg_path]


def save_overlay_figure(fig: plt.Figure, stem: str) -> list[Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    png_path = OUTPUT_DIR / f"{stem}.png"
    svg_path = OUTPUT_DIR / f"{stem}.svg"
    fig.savefig(png_path, transparent=True, dpi=300)
    fig.savefig(svg_path, transparent=True)
    plt.close(fig)
    return [png_path, svg_path]


def save_white_curve_figure(fig: plt.Figure, stem: str) -> list[Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    png_path = OUTPUT_DIR / f"{stem}.png"
    svg_path = OUTPUT_DIR / f"{stem}.svg"
    fig.savefig(png_path, facecolor="white", dpi=300)
    fig.savefig(svg_path, facecolor="white")
    plt.close(fig)
    return [png_path, svg_path]


def emphasize_existing_uwb_jitter(distance: np.ndarray) -> np.ndarray:
    """仅用于第一张PPT图的视觉强调，不改CSV中的原始模拟数据。"""
    trend = moving_average(distance, 19)
    jitter = distance - trend
    return np.clip(trend + 1.38 * jitter, YLIM[0] + 0.06, YLIM[1] - 0.06)


def plot_progressive(
    data: dict[str, np.ndarray],
    series_keys: list[str],
    title: str,
    stem: str,
) -> list[Path]:
    labels = {
        "uwb_distance": "UWB基础方案",
        "kalman_distance": "UWB + 卡尔曼",
        "ppo_distance": "UWB + 卡尔曼 + PPO",
    }
    styles = {
        "uwb_distance": {
            "color": "#F97316",
            "linewidth": 4.0,
            "linestyle": "--",
            "zorder": 2,
        },
        "kalman_distance": {
            "color": "#2563EB",
            "linewidth": 4.5,
            "linestyle": "-.",
            "zorder": 3,
        },
        "ppo_distance": {
            "color": "#16A34A",
            "linewidth": 5.0,
            "linestyle": "-",
            "zorder": 4,
        },
    }

    fig, ax = plt.subplots(figsize=FIGSIZE)
    add_common_elements(ax)
    for key in series_keys:
        ax.plot(data["time"], data[key], label=labels[key], **styles[key])

    fig.suptitle(title, fontsize=30, y=0.955)
    ax.set_xlabel("时间 / s", fontsize=24, labelpad=10)
    ax.set_ylabel("跟随距离 / m", fontsize=24, labelpad=12)
    handles, legend_labels = ax.get_legend_handles_labels()
    fig.legend(
        handles,
        legend_labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.875),
        ncol=2,
        frameon=False,
        fontsize=21,
        handlelength=3.5,
        columnspacing=2.0,
        labelspacing=0.7,
    )
    style_axes(ax)
    add_simulation_mark(fig)
    fig.subplots_adjust(left=0.075, right=0.985, top=0.660, bottom=0.165)
    return save_figure(fig, stem)


def plot_uwb_position(data: dict[str, np.ndarray]) -> list[Path]:
    fig, ax = plt.subplots(figsize=FIGSIZE)
    add_common_elements(
        ax,
        show_event_labels=False,
        ideal_linewidth=4.4,
        ideal_color="#DC2626",
        ideal_zorder=5,
    )

    ax.plot(
        data["time"],
        emphasize_existing_uwb_jitter(data["uwb_distance"]),
        label="UWB基础方案",
        color="#F97316",
        linewidth=5.4,
        linestyle=(0, (12, 6)),
        zorder=2,
    )

    handles, legend_labels = ax.get_legend_handles_labels()
    fig.legend(
        handles,
        legend_labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.875),
        ncol=2,
        frameon=False,
        fontsize=27,
        handlelength=3.8,
        columnspacing=2.6,
        labelspacing=0.7,
    )
    style_axes(ax)
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.tick_params(labelbottom=False, labelleft=False)
    fig.subplots_adjust(left=0.075, right=0.985, top=0.660, bottom=0.165)
    return save_figure(fig, "01_uwb_only")


def plot_uwb_data_overlay(data: dict[str, np.ndarray]) -> list[Path]:
    fig, ax = plt.subplots(figsize=FIGSIZE)
    ax.set_xlim(*XLIM)
    ax.set_ylim(*YLIM)
    ax.plot(
        data["time"],
        emphasize_existing_uwb_jitter(data["uwb_distance"]),
        color="#F97316",
        linewidth=5.4,
        linestyle=(0, (12, 6)),
        zorder=2,
    )

    # 透明叠加层只保留UWB曲线，不保留坐标轴、网格、图例和任何文字。
    ax.set_axis_off()
    ax.set_facecolor("none")
    fig.patch.set_alpha(0.0)
    fig.subplots_adjust(left=0.075, right=0.985, top=0.660, bottom=0.165)
    return save_overlay_figure(fig, "01_uwb_only_uwb_overlay")


def plot_kalman_data_overlay(data: dict[str, np.ndarray]) -> list[Path]:
    fig, ax = plt.subplots(figsize=FIGSIZE)
    ax.set_xlim(*XLIM)
    ax.set_ylim(*YLIM)
    ax.plot(
        data["time"],
        data["kalman_distance"],
        color="#2563EB",
        linewidth=4.5,
        linestyle="-.",
        zorder=3,
    )

    ax.set_axis_off()
    ax.set_facecolor("none")
    fig.patch.set_alpha(0.0)
    fig.subplots_adjust(left=0.075, right=0.985, top=0.660, bottom=0.165)
    return save_overlay_figure(fig, "02_uwb_kalman_kalman_overlay")


def plot_ppo_data_overlay(data: dict[str, np.ndarray]) -> list[Path]:
    fig, ax = plt.subplots(figsize=FIGSIZE)
    ax.set_xlim(*XLIM)
    ax.set_ylim(*YLIM)
    ax.plot(
        data["time"],
        data["ppo_distance"],
        color="#16A34A",
        linewidth=5.0,
        linestyle="-",
        zorder=4,
    )

    ax.set_axis_off()
    ax.set_facecolor("none")
    fig.patch.set_alpha(0.0)
    fig.subplots_adjust(left=0.075, right=0.985, top=0.660, bottom=0.165)
    return save_overlay_figure(fig, "03_uwb_kalman_ppo_ppo_overlay")


def plot_single_curve_white(
    data: dict[str, np.ndarray],
    key: str,
    stem: str,
) -> list[Path]:
    titles = {
        "uwb_distance": "UWB定位：跟随距离存在明显波动",
        "kalman_distance": "加入卡尔曼滤波：随机抖动明显降低",
        "ppo_distance": "加入PPO控制：动态变化后恢复更快",
    }
    labels = {
        "uwb_distance": "UWB基础方案",
        "kalman_distance": "UWB + 卡尔曼",
        "ppo_distance": "UWB + 卡尔曼 + PPO",
    }
    styles = {
        "uwb_distance": {
            "color": "#F97316",
            "linewidth": 5.4,
            "linestyle": (0, (12, 6)),
            "zorder": 2,
        },
        "kalman_distance": {
            "color": "#2563EB",
            "linewidth": 4.5,
            "linestyle": "-.",
            "zorder": 3,
        },
        "ppo_distance": {
            "color": "#16A34A",
            "linewidth": 5.0,
            "linestyle": "-",
            "zorder": 4,
        },
    }
    values = (
        emphasize_existing_uwb_jitter(data[key])
        if key == "uwb_distance"
        else data[key]
    )

    fig, ax = plt.subplots(figsize=FIGSIZE)
    add_common_elements(ax)
    ax.plot(data["time"], values, label=labels[key], **styles[key])
    fig.suptitle(titles[key], fontsize=30, y=0.955)
    ax.set_xlabel("时间 / s", fontsize=24, labelpad=10)
    ax.set_ylabel("跟随距离 / m", fontsize=24, labelpad=12)
    handles, legend_labels = ax.get_legend_handles_labels()
    fig.legend(
        handles,
        legend_labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.875),
        ncol=2,
        frameon=False,
        fontsize=21,
        handlelength=3.5,
        columnspacing=2.0,
        labelspacing=0.7,
    )
    style_axes(ax)
    add_simulation_mark(fig)
    fig.subplots_adjust(left=0.075, right=0.985, top=0.660, bottom=0.165)
    return save_white_curve_figure(fig, stem)


def plot_combined_curves_white(data: dict[str, np.ndarray]) -> list[Path]:
    fig, ax = plt.subplots(figsize=FIGSIZE)
    add_common_elements(ax)
    ax.plot(
        data["time"],
        emphasize_existing_uwb_jitter(data["uwb_distance"]),
        label="UWB基础方案",
        color="#F97316",
        linewidth=5.4,
        linestyle=(0, (12, 6)),
        zorder=2,
    )
    ax.plot(
        data["time"],
        data["kalman_distance"],
        label="UWB + 卡尔曼",
        color="#2563EB",
        linewidth=4.5,
        linestyle="-.",
        zorder=3,
    )
    ax.plot(
        data["time"],
        data["ppo_distance"],
        label="UWB + 卡尔曼 + PPO",
        color="#16A34A",
        linewidth=5.0,
        linestyle="-",
        zorder=4,
    )
    fig.suptitle("UWB + 卡尔曼 + PPO：跟随距离对比", fontsize=30, y=0.955)
    ax.set_xlabel("时间 / s", fontsize=24, labelpad=10)
    ax.set_ylabel("跟随距离 / m", fontsize=24, labelpad=12)
    handles, legend_labels = ax.get_legend_handles_labels()
    fig.legend(
        handles,
        legend_labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.875),
        ncol=2,
        frameon=False,
        fontsize=21,
        handlelength=3.5,
        columnspacing=2.0,
        labelspacing=0.7,
    )
    style_axes(ax)
    add_simulation_mark(fig)
    fig.subplots_adjust(left=0.075, right=0.985, top=0.660, bottom=0.165)
    return save_white_curve_figure(fig, "04_uwb_kalman_ppo_curves_white")


def plot_uwb_chart_base() -> list[Path]:
    fig, ax = plt.subplots(figsize=FIGSIZE)
    add_common_elements(
        ax,
        show_event_labels=False,
        ideal_linewidth=4.4,
        ideal_color="#DC2626",
        ideal_zorder=5,
    )
    style_axes(ax)
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.tick_params(labelbottom=False, labelleft=False)
    fig.subplots_adjust(left=0.075, right=0.985, top=0.660, bottom=0.165)
    return save_figure(fig, "01_uwb_only_chart_base")


def plot_kalman_result(data: dict[str, np.ndarray]) -> list[Path]:
    return plot_progressive(
        data,
        ["uwb_distance", "kalman_distance"],
        "加入卡尔曼滤波：随机抖动明显降低",
        "02_uwb_kalman",
    )


def plot_ppo_comparison(data: dict[str, np.ndarray]) -> list[Path]:
    return plot_progressive(
        data,
        ["uwb_distance", "kalman_distance", "ppo_distance"],
        "加入PPO控制：动态变化后恢复更快",
        "03_uwb_kalman_ppo",
    )


def calculate_metrics(distance: np.ndarray) -> dict[str, float]:
    error = np.abs(distance - IDEAL_DISTANCE)
    return {
        "mae": float(np.mean(error)),
        "rmse": float(np.sqrt(np.mean(error**2))),
        "max_error": float(np.max(error)),
        "std": float(np.std(error)),
    }


def print_metrics(metrics: dict[str, dict[str, float]]) -> None:
    kalman_improvement = (
        (metrics["uwb"]["mae"] - metrics["kalman"]["mae"])
        / metrics["uwb"]["mae"]
        * 100.0
    )
    ppo_improvement = (
        (metrics["kalman"]["mae"] - metrics["ppo"]["mae"])
        / metrics["kalman"]["mae"]
        * 100.0
    )

    print("====== 模拟结果 / Simulation ======")
    print("UWB")
    print(f"平均跟随误差: {metrics['uwb']['mae']:.4f} m")
    print(f"RMSE: {metrics['uwb']['rmse']:.4f} m")
    print(f"最大误差: {metrics['uwb']['max_error']:.4f} m")
    print(f"标准差: {metrics['uwb']['std']:.4f} m")
    print()
    print("卡尔曼")
    print(f"平均跟随误差: {metrics['kalman']['mae']:.4f} m")
    print(f"RMSE: {metrics['kalman']['rmse']:.4f} m")
    print(f"最大误差: {metrics['kalman']['max_error']:.4f} m")
    print(f"标准差: {metrics['kalman']['std']:.4f} m")
    print()
    print("PPO")
    print(f"平均跟随误差: {metrics['ppo']['mae']:.4f} m")
    print(f"RMSE: {metrics['ppo']['rmse']:.4f} m")
    print(f"最大误差: {metrics['ppo']['max_error']:.4f} m")
    print(f"标准差: {metrics['ppo']['std']:.4f} m")
    print()
    print(f"卡尔曼相比UWB改善: {kalman_improvement:.1f}%")
    print(f"PPO相比卡尔曼改善: {ppo_improvement:.1f}%")
    print("===================================")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    configure_matplotlib()

    generated_data = generate_progressive_data()
    csv_path = save_csv(generated_data)

    # 三张图统一从同一个CSV读取，确保曲线、坐标和尺度完全一致。
    data = load_progressive_csv(csv_path)
    metrics = {
        "uwb": calculate_metrics(data["uwb_distance"]),
        "kalman": calculate_metrics(data["kalman_distance"]),
        "ppo": calculate_metrics(data["ppo_distance"]),
    }

    image_paths: list[Path] = []
    image_paths.extend(plot_uwb_position(data))
    image_paths.extend(plot_uwb_data_overlay(data))
    image_paths.extend(plot_kalman_result(data))
    image_paths.extend(plot_ppo_comparison(data))

    print_metrics(metrics)
    print("图片保存位置:")
    for path in image_paths:
        print(f"  {path}")
    print(f"CSV 保存位置: {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
