from __future__ import annotations

import json
import logging
import math
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

import plotly.graph_objects as go
from dash import Dash, Input, Output, dcc, html


BACKGROUND = "#091018"
PANEL = "#0d1721"
GRID = "#233442"
TEXT = "#e8f0f5"
MUTED = "#8093a1"
CYAN = "#27c2d1"
AMBER = "#f0ad4e"
GREEN = "#42c98a"
RED = "#ef6b73"


@dataclass(frozen=True)
class TelemetrySample:
    captured_at: float
    distance_m: float | None
    bearing_rad: float | None
    target_distance_m: float
    target_bearing_rad: float
    vx: float
    wz: float
    uwb_state: str
    lidar_state: str
    control_state: str
    simulated: bool = False
    debug: Mapping[str, object] = field(default_factory=dict)


class TelemetrySource(Protocol):
    def read(self) -> TelemetrySample: ...


class _RateTracker:
    def __init__(self) -> None:
        self._last_count: int | None = None
        self._last_at: float | None = None
        self.value: float | None = None

    def update(self, count: object, now: float) -> float | None:
        parsed = _integer(count)
        if parsed is None:
            return self.value
        if self._last_count is not None and self._last_at is not None:
            elapsed = now - self._last_at
            if elapsed > 0.0 and parsed >= self._last_count:
                self.value = (parsed - self._last_count) / elapsed
        self._last_count = parsed
        self._last_at = now
        return self.value


class CompanionStatusSource:
    """GET-only adapter for the running Companion Runtime status endpoint."""

    def __init__(
        self,
        status_url: str,
        *,
        target_distance_m: float,
        target_bearing_rad: float,
        timeout_seconds: float = 0.50,
        interface: str | None = None,
    ) -> None:
        self.status_url = status_url
        self.target_distance_m = target_distance_m
        self.target_bearing_rad = target_bearing_rad
        self.timeout_seconds = timeout_seconds
        self.interface = interface
        self._uwb_rate = _RateTracker()
        self._lidar_rate = _RateTracker()

    def read(self) -> TelemetrySample:
        now = time.monotonic()
        request = urllib.request.Request(
            self.status_url,
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
            data = _mapping(payload.get("data"), "status.data")
            return self._from_status(data, now)
        except Exception as exc:
            return TelemetrySample(
                captured_at=now,
                distance_m=None,
                bearing_rad=None,
                target_distance_m=self.target_distance_m,
                target_bearing_rad=self.target_bearing_rad,
                vx=0.0,
                wz=0.0,
                uwb_state="等待数据",
                lidar_state="等待数据",
                control_state="等待数据",
                debug={
                    "状态接口": self.status_url,
                    "网络接口": self.interface or "由 Gateway 管理",
                    "读取错误": f"{type(exc).__name__}: {exc}",
                },
            )

    def _from_status(
        self, data: Mapping[str, object], now: float
    ) -> TelemetrySample:
        uwb = _mapping(data.get("uwb"), "status.uwb")
        lidar = _mapping(data.get("lidar"), "status.lidar")
        motion = _mapping(data.get("motion"), "status.motion")
        runtime = _mapping(data.get("runtime") or {}, "status.runtime")
        runtime_input = _mapping(runtime.get("input") or {}, "runtime.input")
        control = _mapping(runtime.get("control") or {}, "runtime.control")
        configuration = _mapping(
            data.get("configuration") or {}, "status.configuration"
        )

        self.target_distance_m = _finite_float(
            configuration.get("target_distance_m"), self.target_distance_m
        )
        self.target_bearing_rad = _finite_float(
            configuration.get("target_bearing_rad"), self.target_bearing_rad
        )

        uwb_valid = bool(uwb.get("valid"))
        lidar_valid = bool(lidar.get("valid"))
        lidar_level = str(lidar.get("state") or "").upper()
        runtime_active = bool(data.get("runtime_active"))
        worker_alive = bool(runtime.get("worker_alive"))
        runtime_failed = bool(runtime.get("failure"))
        execution_status = str(control.get("execution_status") or "NOT_STARTED")
        control_ok = (
            runtime_active
            and worker_alive
            and not runtime_failed
            and execution_status
            in {"SENT", "RATE_LIMITED", "DUPLICATE_DECISION"}
        )
        uwb_error = str(uwb.get("error") or "")
        uwb_state = (
            "正常"
            if uwb_valid
            else (
                "等待数据"
                if not uwb_error or uwb_error == "uwb_not_ready"
                else "异常"
            )
        )
        control_state = (
            "正常"
            if control_ok
            else (
                "异常"
                if runtime_failed
                or (
                    runtime_active
                    and worker_alive
                    and execution_status in {"STOPPED", "RESUME_REQUIRED"}
                )
                else "等待数据"
            )
        )

        uwb_hz = self._uwb_rate.update(runtime_input.get("uwb_samples"), now)
        lidar_hz = self._lidar_rate.update(runtime_input.get("lidar_samples"), now)
        distance = _optional_finite_float(uwb.get("distance_m"))
        bearing = _optional_finite_float(uwb.get("bearing_rad"))
        if bearing is None:
            bearing_degrees = _optional_finite_float(uwb.get("bearing_deg"))
            if bearing_degrees is not None:
                bearing = math.radians(bearing_degrees)
        if not uwb_valid:
            distance = None
            bearing = None

        return TelemetrySample(
            captured_at=now,
            distance_m=distance,
            bearing_rad=bearing,
            target_distance_m=self.target_distance_m,
            target_bearing_rad=self.target_bearing_rad,
            vx=_finite_float(motion.get("vx"), 0.0),
            wz=_finite_float(motion.get("wz"), 0.0),
            uwb_state=uwb_state,
            lidar_state=(
                "不可用"
                if lidar_level == "UNAVAILABLE"
                else (
                    "正常"
                    if lidar_valid and lidar_level != "STOP"
                    else ("异常" if lidar_valid else "等待数据")
                )
            ),
            control_state=control_state,
            debug={
                "DDS主题": runtime_input.get("uwb_topic", "rt/uwbstate"),
                "LiDAR主题": runtime_input.get(
                    "lidar_topic", "rt/utlidar/cloud_base"
                ),
                "UWB频率": _format_rate(uwb_hz),
                "LiDAR频率": _format_rate(lidar_hz),
                "控制频率": _format_rate(
                    _optional_finite_float(
                        configuration.get("control_frequency_hz")
                    )
                ),
                "UWB数据年龄": _format_age(uwb.get("age_ms")),
                "LiDAR数据年龄": _format_age(lidar.get("age_ms")),
                "原始orientation_est": _format_radians(
                    uwb.get("orientation_est_rad")
                ),
                "校准后bearing": _format_radians(uwb.get("bearing_rad")),
                "最终vx": f"{_finite_float(motion.get('vx'), 0.0):+.3f} m/s",
                "最终wz": f"{_finite_float(motion.get('wz'), 0.0):+.3f} rad/s",
                "执行状态": execution_status,
                "SportClient链路": "在线" if data.get("robot_online") else "离线",
                "状态接口": self.status_url,
                "网络接口": self.interface or "由 Gateway 管理",
            },
        )


class MockTelemetrySource:
    def __init__(
        self,
        *,
        target_distance_m: float = 1.75,
        target_bearing_rad: float = math.atan2(0.5, 1.5),
    ) -> None:
        self.target_distance_m = target_distance_m
        self.target_bearing_rad = target_bearing_rad
        self._started = time.monotonic()

    def read(self) -> TelemetrySample:
        now = time.monotonic()
        elapsed = now - self._started
        distance = 1.95 + 0.34 * math.sin(elapsed * 0.55) + 0.06 * math.sin(
            elapsed * 1.7
        )
        distance = max(1.5, min(2.4, distance))
        bearing = self.target_bearing_rad + 0.28 * math.sin(elapsed * 0.31)
        distance_error = distance - self.target_distance_m
        vx = 0.0 if distance_error < -0.04 else max(0.0, min(0.30, distance_error * 0.75))
        wz = max(-0.30, min(0.30, (bearing - self.target_bearing_rad) * 0.85))
        return TelemetrySample(
            captured_at=now,
            distance_m=distance,
            bearing_rad=bearing,
            target_distance_m=self.target_distance_m,
            target_bearing_rad=self.target_bearing_rad,
            vx=vx,
            wz=wz,
            uwb_state="正常",
            lidar_state="正常",
            control_state="正常",
            simulated=True,
            debug={
                "数据源": "内置模拟遥测",
                "UWB频率": "5.0 Hz",
                "LiDAR频率": "15.0 Hz",
                "控制频率": "5.0 Hz",
                "原始orientation_est": f"{bearing - 0.55:+.3f} rad",
                "校准后bearing": f"{bearing:+.3f} rad",
                "最终vx": f"{vx:+.3f} m/s",
                "最终wz": f"{wz:+.3f} rad/s",
            },
        )


class TelemetryHistory:
    def __init__(self, max_points: int = 300) -> None:
        self._samples: deque[TelemetrySample] = deque(maxlen=max_points)
        self._lock = threading.RLock()

    def append(self, sample: TelemetrySample) -> None:
        if sample.distance_m is None:
            return
        with self._lock:
            self._samples.append(sample)

    def snapshot(self) -> tuple[TelemetrySample, ...]:
        with self._lock:
            return tuple(self._samples)


def create_dashboard(
    source: TelemetrySource,
    *,
    assets_folder: str,
    debug_mode: bool = False,
    history_points: int = 300,
) -> Dash:
    history = TelemetryHistory(history_points)
    initial = source.read()
    history.append(initial)
    app = Dash(
        __name__,
        assets_folder=assets_folder,
        title="Go2 UWB伴随实时监测",
        update_title=None,
    )
    app.layout = _layout(initial, debug_mode=debug_mode)

    @app.callback(
        Output("current-distance", "children"),
        Output("target-distance", "children"),
        Output("forward-speed", "children"),
        Output("turn-speed", "children"),
        Output("relative-position", "figure"),
        Output("distance-history", "figure"),
        Output("speed-history", "figure"),
        Output("runtime-status", "children"),
        Output("mock-label", "style"),
        Output("debug-panel", "children"),
        Input("telemetry-tick", "n_intervals"),
    )
    def update_dashboard(_tick: int):
        sample = source.read()
        history.append(sample)
        samples = history.snapshot()
        return (
            _distance_value(sample.distance_m),
            f"{sample.target_distance_m:.2f} m",
            f"{sample.vx:.2f} m/s",
            f"{sample.wz:.2f} rad/s",
            relative_position_figure(sample),
            distance_history_figure(samples, sample.target_distance_m),
            speed_history_figure(samples),
            _status_bar(sample),
            {"display": "inline-flex"} if sample.simulated else {"display": "none"},
            _debug_content(sample) if debug_mode else [],
        )

    return app


def _layout(initial: TelemetrySample, *, debug_mode: bool) -> html.Div:
    return html.Div(
        className="telemetry-shell",
        children=[
            html.Header(
                className="page-header",
                children=[
                    html.Div(className="header-rule"),
                    html.H1("Go2 UWB伴随实时监测"),
                    html.Span("模拟数据", id="mock-label", className="mock-label", style={"display": "inline-flex"} if initial.simulated else {"display": "none"}),
                ],
            ),
            html.Section(
                className="metrics",
                children=[
                    _metric("当前距离", "current-distance", _distance_value(initial.distance_m), True),
                    _metric("目标距离", "target-distance", f"{initial.target_distance_m:.2f} m", True),
                    _metric("前进速度", "forward-speed", f"{initial.vx:.2f} m/s", False, "vx"),
                    _metric("转向速度", "turn-speed", f"{initial.wz:.2f} rad/s", False, "wz"),
                ],
            ),
            html.Main(
                className="monitor-grid",
                children=[
                    _chart_panel("UWB相对位置", "relative-position", relative_position_figure(initial), "position-panel"),
                    _chart_panel("伴随距离", "distance-history", distance_history_figure((), initial.target_distance_m), "distance-panel"),
                    _chart_panel("运动速度", "speed-history", speed_history_figure(()), "speed-panel"),
                ],
            ),
            html.Div(id="runtime-status", className="runtime-status", children=_status_bar(initial)),
            html.Div(id="debug-panel", className="debug-panel", style={"display": "grid" if debug_mode else "none"}, children=_debug_content(initial) if debug_mode else []),
            dcc.Interval(id="telemetry-tick", interval=200, n_intervals=0),
        ],
    )


def _metric(
    label: str,
    value_id: str,
    value: str,
    primary: bool,
    variable: str | None = None,
) -> html.Div:
    label_children: list[object] = [label]
    if variable:
        label_children.append(html.Span(f"（{variable}）", className="metric-variable"))
    return html.Div(
        className=f"metric {'metric-primary' if primary else 'metric-secondary'}",
        children=[
            html.Div(label_children, className="metric-label"),
            html.Div(value, id=value_id, className="metric-value"),
        ],
    )


def _chart_panel(title: str, graph_id: str, figure: go.Figure, class_name: str) -> html.Section:
    return html.Section(
        className=f"chart-panel {class_name}",
        children=[
            html.H2(title),
            dcc.Graph(
                id=graph_id,
                figure=figure,
                className="telemetry-graph",
                config={
                    "displayModeBar": False,
                    "responsive": True,
                    "scrollZoom": False,
                    "doubleClick": False,
                },
            ),
        ],
    )


def relative_position_figure(sample: TelemetrySample) -> go.Figure:
    figure = go.Figure()
    # The expected distance point follows the latest measured line of sight.
    # This keeps the engineering view tied to real UWB bearing changes while
    # preserving the configured target distance as the radial reference.
    expected_bearing = (
        sample.bearing_rad
        if sample.bearing_rad is not None
        else sample.target_bearing_rad
    )
    target_x = sample.target_distance_m * math.cos(expected_bearing)
    target_y = sample.target_distance_m * math.sin(expected_bearing)
    values = [sample.target_distance_m]

    figure.add_trace(
        go.Scatter(
            x=[target_x],
            y=[target_y],
            mode="markers+text",
            marker={"symbol": "star", "size": 17, "color": AMBER},
            text=["期望位置"],
            textposition="bottom center",
            textfont={"color": MUTED, "size": 12},
            hoverinfo="skip",
            showlegend=False,
        )
    )
    if sample.distance_m is not None and sample.bearing_rad is not None:
        x = sample.distance_m * math.cos(sample.bearing_rad)
        y = sample.distance_m * math.sin(sample.bearing_rad)
        values.append(sample.distance_m)
        figure.add_trace(
            go.Scatter(
                x=[0.0, x],
                y=[0.0, y],
                mode="lines",
                line={"color": CYAN, "width": 2},
                hoverinfo="skip",
                showlegend=False,
            )
        )
        figure.add_trace(
            go.Scatter(
                x=[x],
                y=[y],
                mode="markers+text",
                marker={"size": 14, "color": CYAN, "line": {"color": TEXT, "width": 1}},
                text=["当前目标"],
                textposition="top center",
                textfont={"color": TEXT, "size": 13},
                hovertemplate="当前距离 %{customdata:.2f} m<extra></extra>",
                customdata=[sample.distance_m],
                showlegend=False,
            )
        )
        figure.add_annotation(
            x=x * 0.52,
            y=y * 0.52,
            text=f"{sample.distance_m:.2f} m",
            showarrow=False,
            bgcolor=BACKGROUND,
            borderpad=3,
            font={"color": TEXT, "size": 13},
        )
        figure.add_annotation(
            xref="paper",
            yref="paper",
            x=0.98,
            y=0.98,
            text=f"方位：{math.degrees(sample.bearing_rad):+.1f}°",
            showarrow=False,
            xanchor="right",
            yanchor="top",
            font={"color": MUTED, "size": 12},
        )

    figure.add_trace(
        go.Scatter(
            x=[0.0],
            y=[0.0],
            mode="markers+text",
            marker={"symbol": "circle-open", "size": 17, "color": TEXT, "line": {"width": 2}},
            text=["Go2"],
            textposition="bottom center",
            textfont={"color": TEXT, "size": 13},
            hoverinfo="skip",
            showlegend=False,
        )
    )
    radius = max(1.2, max(values) * 1.30)
    figure.update_layout(
        **_base_layout(),
        margin={"l": 54, "r": 25, "t": 18, "b": 45},
        uirevision="uwb-position",
    )
    figure.update_xaxes(
        range=[-radius * 0.22, radius],
        title="X / m",
        zeroline=True,
        scaleanchor="y",
        scaleratio=1,
        **_axis_style(),
    )
    figure.update_yaxes(
        range=[-radius * 0.68, radius * 0.68],
        title="Y / m",
        zeroline=True,
        **_axis_style(),
    )
    return figure


def distance_history_figure(
    samples: tuple[TelemetrySample, ...], target_distance_m: float
) -> go.Figure:
    x = _relative_times(samples)
    distances = [sample.distance_m for sample in samples]
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=x,
            y=distances,
            mode="lines",
            name="当前距离",
            line={"color": CYAN, "width": 3},
            hovertemplate="%{y:.2f} m<extra></extra>",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=[-60, 0],
            y=[target_distance_m, target_distance_m],
            mode="lines",
            name="目标距离",
            line={"color": AMBER, "width": 1.5, "dash": "dash"},
            hoverinfo="skip",
        )
    )
    figure.update_layout(
        **_base_layout(),
        margin={"l": 52, "r": 22, "t": 8, "b": 38},
        legend=_legend_style(),
        uirevision="distance-history",
    )
    figure.update_xaxes(range=[-60, 0], title="最近时间 / s", **_axis_style())
    figure.update_yaxes(title="距离 / m", rangemode="tozero", **_axis_style())
    return figure


def speed_history_figure(samples: tuple[TelemetrySample, ...]) -> go.Figure:
    x = _relative_times(samples)
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=x,
            y=[sample.vx for sample in samples],
            mode="lines",
            name="前进速度（vx）",
            line={"color": GREEN, "width": 2.5},
            hovertemplate="%{y:.2f} m/s<extra></extra>",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=x,
            y=[sample.wz for sample in samples],
            mode="lines",
            name="转向速度（wz）",
            line={"color": AMBER, "width": 2.5},
            hovertemplate="%{y:.2f} rad/s<extra></extra>",
        )
    )
    figure.update_layout(
        **_base_layout(),
        margin={"l": 52, "r": 22, "t": 8, "b": 38},
        legend=_legend_style(),
        uirevision="speed-history",
    )
    figure.update_xaxes(range=[-60, 0], title="最近时间 / s", **_axis_style())
    figure.update_yaxes(title="速度", zeroline=True, **_axis_style())
    return figure


def _base_layout() -> dict[str, object]:
    return {
        "paper_bgcolor": PANEL,
        "plot_bgcolor": PANEL,
        "font": {"family": "Microsoft YaHei, Noto Sans CJK SC, sans-serif", "color": TEXT},
        "hoverlabel": {"bgcolor": "#111d27", "bordercolor": GRID, "font": {"color": TEXT}},
    }


def _axis_style() -> dict[str, object]:
    return {
        "showgrid": True,
        "gridcolor": GRID,
        "gridwidth": 1,
        "zerolinecolor": "#4a6170",
        "zerolinewidth": 1,
        "tickfont": {"color": MUTED, "size": 10},
        "title_font": {"color": MUTED, "size": 11},
        "fixedrange": True,
    }


def _legend_style() -> dict[str, object]:
    return {
        "orientation": "h",
        "x": 0.0,
        "y": 1.02,
        "xanchor": "left",
        "yanchor": "bottom",
        "font": {"color": MUTED, "size": 11},
        "bgcolor": "rgba(0,0,0,0)",
    }


def _relative_times(samples: tuple[TelemetrySample, ...]) -> list[float]:
    if not samples:
        return []
    latest = samples[-1].captured_at
    return [sample.captured_at - latest for sample in samples]


def _status_bar(sample: TelemetrySample) -> list[html.Span]:
    return [
        _status_item("UWB", sample.uwb_state),
        _status_item("LiDAR", sample.lidar_state),
        _status_item("运动控制", sample.control_state),
    ]


def _status_item(label: str, state: str) -> html.Span:
    css_state = (
        "ok"
        if state == "正常"
        else (
            "error"
            if state == "异常"
            else ("unavailable" if state == "不可用" else "waiting")
        )
    )
    return html.Span(
        className="status-item",
        children=[
            html.Span(label, className="status-label"),
            html.Span(className=f"status-dot status-{css_state}"),
            html.Span(state, className="status-value"),
        ],
    )


def _debug_content(sample: TelemetrySample) -> list[html.Div]:
    return [
        html.Div(
            className="debug-item",
            children=[html.Span(str(label)), html.Code(str(value))],
        )
        for label, value in sample.debug.items()
    ]


def _distance_value(value: float | None) -> str:
    return "--" if value is None else f"{value:.2f} m"


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} is not an object")
    return value


def _optional_finite_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _finite_float(value: object, default: float) -> float:
    parsed = _optional_finite_float(value)
    return default if parsed is None else parsed


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_rate(value: float | None) -> str:
    return "--" if value is None else f"{value:.1f} Hz"


def _format_age(value: object) -> str:
    parsed = _optional_finite_float(value)
    return "--" if parsed is None else f"{parsed:.0f} ms"


def _format_radians(value: object) -> str:
    parsed = _optional_finite_float(value)
    return "--" if parsed is None else f"{parsed:+.3f} rad"


def quiet_dashboard_logs(debug_mode: bool) -> None:
    if debug_mode:
        return
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    logging.getLogger("dash").setLevel(logging.ERROR)
