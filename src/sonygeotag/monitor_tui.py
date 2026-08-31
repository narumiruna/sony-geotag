from __future__ import annotations

from typing import Any
from typing import ClassVar

from rich.table import Table
from rich.text import Text
from textual.app import App
from textual.app import ComposeResult
from textual.containers import Grid
from textual.widgets import Footer
from textual.widgets import Header
from textual.widgets import Static

from sonygeotag.camera_monitor import MonitorPhase
from sonygeotag.camera_monitor import MonitorUpdate
from sonygeotag.camera_monitor import stream_camera_status


class CameraMonitorApp(App[None]):
    """Textual dashboard for the read-only Sony BLE monitor."""

    TITLE = "Sony Camera Monitor"
    SUB_TITLE = "Read-only BLE status"
    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("q", "quit", "Quit"),
        ("ctrl+c", "quit", "Quit"),
    ]
    CSS = """
    Screen {
        layout: vertical;
        background: $surface;
    }

    Header {
        dock: top;
    }

    #connection {
        height: 3;
        margin: 1 2 0 2;
        padding: 0 2;
        content-align-vertical: middle;
        border: round $warning;
    }

    #connection.phase-connected {
        border: round $success;
    }

    #connection.phase-disconnected {
        border: round $error;
    }

    #dashboard {
        height: 1fr;
        margin: 1 2;
        grid-size: 2 2;
        grid-columns: 1fr 1fr;
        grid-rows: 1fr 1fr;
        grid-gutter: 1 2;
    }

    .card {
        border: round $primary;
        padding: 1 2;
        min-height: 8;
    }

    #safety {
        height: 1;
        margin: 0 2 1 2;
        color: $text-muted;
        content-align: center middle;
    }

    Footer {
        dock: bottom;
    }
    """

    def __init__(
        self,
        *,
        targets: tuple[str, ...],
        scan_timeout: float,
        connect_timeout: float,
        poll_interval: float,
        pair: bool,
        duration: float | None,
    ) -> None:
        super().__init__()
        self.targets = targets
        self.scan_timeout = scan_timeout
        self.connect_timeout = connect_timeout
        self.poll_interval = poll_interval
        self.pair = pair
        self.duration = duration

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static("Starting monitor…", id="connection")
        with Grid(id="dashboard"):
            overview = Static(id="overview", classes="card")
            overview.border_title = "Overview"
            yield overview
            camera = Static(id="camera", classes="card")
            camera.border_title = "Camera"
            yield camera
            storage = Static(id="storage", classes="card")
            storage.border_title = "Storage"
            yield storage
            location = Static(id="location", classes="card")
            location.border_title = "Location Link"
            yield location
        yield Static("No GATT writes or notification subscriptions • Auto-reconnect enabled", id="safety")
        yield Footer()

    def on_mount(self) -> None:
        self._apply_update(
            MonitorUpdate(
                phase=MonitorPhase.SCANNING,
                message=f"Starting scan for {', '.join(self.targets)}",
                captured_at="—",
            )
        )
        self.run_worker(self._run_monitor(), name="camera-monitor", exclusive=True)

    async def _run_monitor(self) -> None:
        await stream_camera_status(
            targets=self.targets,
            scan_timeout=self.scan_timeout,
            connect_timeout=self.connect_timeout,
            poll_interval=self.poll_interval,
            pair=self.pair,
            duration=self.duration,
            on_update=self._apply_update,
        )
        if self.duration is not None:
            self.exit()

    def _apply_update(self, update: MonitorUpdate) -> None:
        connection = self.query_one("#connection", Static)
        connection.remove_class("phase-connected", "phase-disconnected")
        if update.phase is MonitorPhase.CONNECTED:
            connection.add_class("phase-connected")
        elif update.phase is MonitorPhase.DISCONNECTED:
            connection.add_class("phase-disconnected")
        connection.update(_connection_status(update))

        summary = update.summary
        camera_fields = _mapping(summary.get("camera_status"))
        media_fields = _mapping(summary.get("primary_media"))
        location_fields = _mapping(summary.get("location"))
        self.query_one("#overview", Static).update(_overview_table(update, summary))
        self.query_one("#camera", Static).update(_camera_table(camera_fields))
        self.query_one("#storage", Static).update(_storage_table(media_fields))
        self.query_one("#location", Static).update(_location_table(location_fields))


def run_camera_monitor_tui(
    *,
    targets: tuple[str, ...],
    scan_timeout: float,
    connect_timeout: float,
    poll_interval: float,
    pair: bool,
    duration: float | None,
) -> None:
    CameraMonitorApp(
        targets=targets,
        scan_timeout=scan_timeout,
        connect_timeout=connect_timeout,
        poll_interval=poll_interval,
        pair=pair,
        duration=duration,
    ).run()


def _connection_status(update: MonitorUpdate) -> Table:
    styles = {
        MonitorPhase.SCANNING: "bold yellow",
        MonitorPhase.CONNECTING: "bold yellow",
        MonitorPhase.CONNECTED: "bold green",
        MonitorPhase.DISCONNECTED: "bold red",
        MonitorPhase.STOPPED: "dim",
    }
    table = Table.grid(expand=True)
    table.add_column()
    table.add_column(justify="right")
    label = Text(update.phase.value.upper(), style=styles[update.phase])
    table.add_row(Text.assemble(label, "  ", update.message), f"Poll #{update.poll_count}  •  {update.captured_at}")
    return table


def _overview_table(update: MonitorUpdate, summary: dict[str, Any]) -> Table:
    table = _key_value_table()
    device_name = update.device.name if update.device is not None else None
    table.add_row("Model", _label(summary.get("model") or device_name))
    table.add_row("Firmware", _label(summary.get("firmware_version")))
    table.add_row("Signal", _format_rssi(update.device.rssi if update.device is not None else None))
    table.add_row("Battery", _format_percent(summary.get("battery_percent")))
    table.add_row("External power", _label(summary.get("external_power_state")))
    table.add_row("Read errors", str(update.read_error_count))
    return table


def _camera_table(fields: dict[str, Any]) -> Table:
    table = _key_value_table()
    table.add_row("Recording", _indicator(fields.get("movie_recording")))
    table.add_row("Live streaming", _indicator(fields.get("live_streaming")))
    table.add_row("Wi-Fi", _label(fields.get("wifi_state")))
    table.add_row("Remote control", _indicator(fields.get("remote_control_available")))
    table.add_row("Image transfer", _indicator(fields.get("image_transfer_available")))
    return table


def _storage_table(fields: dict[str, Any]) -> Table:
    table = _key_value_table()
    table.add_row("Media", _label(fields.get("status")))
    table.add_row("Shots remaining", _label(fields.get("remaining_shots")))
    table.add_row("Recording time", _format_seconds(fields.get("remaining_recording_seconds")))
    table.add_row("Primary slot", _label(fields.get("position_code")))
    return table


def _location_table(fields: dict[str, Any]) -> Table:
    table = _key_value_table()
    table.add_row("Lock", _indicator(fields.get("location_locked")))
    table.add_row("Transfer", _indicator(fields.get("location_transfer_enabled")))
    table.add_row("Time correction", _indicator(fields.get("time_correction_enabled")))
    table.add_row("Area adjustment", _indicator(fields.get("area_adjustment_enabled")))
    table.add_row("Packet size", _suffix(fields.get("location_packet_size"), " bytes"))
    table.add_row("Timezone", _indicator(fields.get("timezone_supported")))
    return table


def _key_value_table() -> Table:
    table = Table.grid(expand=True, padding=(0, 1))
    table.add_column(style="bold cyan", ratio=1)
    table.add_column(ratio=1)
    return table


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _label(value: object) -> str:
    if value is None:
        return "—"
    return str(value).replace("_", " ")


def _indicator(value: object) -> Text:
    if value is True:
        return Text("● ON", style="bold green")
    if value is False:
        return Text("○ OFF", style="dim")
    return Text("—", style="yellow")


def _format_percent(value: object) -> str:
    return f"{value}%" if isinstance(value, int | float) else "—"


def _format_rssi(value: int | None) -> str:
    return f"{value} dBm" if value is not None else "—"


def _format_seconds(value: object) -> str:
    if not isinstance(value, int):
        return "—"
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _suffix(value: object, suffix: str) -> str:
    return f"{value}{suffix}" if value is not None else "—"
