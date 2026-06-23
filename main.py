from __future__ import annotations

import json
import os
import queue
import re
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, font, messagebox, ttk

try:
    import serial
    from serial.tools import list_ports

    HAS_PYSERIAL = True
except ImportError:
    serial = None
    list_ports = None
    HAS_PYSERIAL = False


APP_NAME = "COM/TCP/UDP调试工具"
APP_VERSION = "1.1.11"
APP_TITLE = f"{APP_NAME} v{APP_VERSION}"
APP_ICON_PATH = Path("assets") / "app.png"
CONFIG_DIR_NAME = "Serial-port-debugging-tools"
CONFIG_FILE_NAME = "config.json"
CONFIG_SCHEMA_VERSION = 1
DEFAULT_GEOMETRY = "1500x840"
DEFAULT_LEFT_PANEL_WIDTH = 360
MODE_SERIAL = "COM串口"
MODE_TCP_CLIENT = "TCP客户端"
MODE_TCP_SERVER = "TCP服务端"
MODE_UDP_CLIENT = "UDP客户端"
MODE_UDP_SERVER = "UDP服务端"
NETWORK_MODES = (MODE_TCP_CLIENT, MODE_TCP_SERVER, MODE_UDP_CLIENT, MODE_UDP_SERVER)
CONNECTION_MODES = (MODE_SERIAL, *NETWORK_MODES)
BAUD_RATES = (
    "1200",
    "2400",
    "4800",
    "9600",
    "14400",
    "19200",
    "38400",
    "57600",
    "115200",
    "230400",
    "460800",
    "921600",
)
PARITY_OPTIONS = {
    "无": "N",
    "奇校验": "O",
    "偶校验": "E",
    "标志": "M",
    "空格": "S",
}
FLOW_OPTIONS = ("无", "RTS/CTS", "XON/XOFF", "DSR/DTR")
ENCODINGS = ("utf-8", "gbk", "ascii", "latin-1")
CRC_ALGORITHM_MODBUS = "CRC16-Modbus"
CRC_ALGORITHMS = (CRC_ALGORITHM_MODBUS,)


def app_config_path(appdata_dir: str | Path | None = None) -> Path:
    if appdata_dir is None:
        appdata_dir = os.environ.get("APPDATA")
    base_dir = Path(appdata_dir) if appdata_dir else Path.home() / "AppData" / "Roaming"
    return base_dir / CONFIG_DIR_NAME / CONFIG_FILE_NAME


def config_bool(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def parse_hex_payload(text: str) -> bytes:
    """Parse user-entered hex text such as '4E 57 00' or '0x4E,0x57'."""
    text = text.strip()
    if not text:
        return b""

    text = re.sub(r"(?i)0x", "", text)
    cleaned = re.sub(r"[\s,;:_-]+", "", text)
    if not cleaned:
        return b""
    if re.search(r"[^0-9a-fA-F]", cleaned):
        raise ValueError("16进制内容只能包含 0-9、A-F 以及空格/逗号等分隔符")
    if len(cleaned) % 2:
        raise ValueError("16进制内容长度必须是偶数，例如：4E 57 00")
    return bytes.fromhex(cleaned)


def resource_path(relative_path: Path) -> Path:
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base_path / relative_path


def bytes_to_hex(data: bytes) -> str:
    return " ".join(f"{value:02X}" for value in data)


def calculate_crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value
        for _bit in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def crc16_modbus_bytes(data: bytes) -> bytes:
    return calculate_crc16_modbus(data).to_bytes(2, "little")


def append_crc16_modbus_if_missing(data: bytes) -> tuple[bytes, bool]:
    if not data:
        return data, False
    if len(data) >= 3 and data[-2:] == crc16_modbus_bytes(data[:-2]):
        return data, False
    return data + crc16_modbus_bytes(data), True


def parse_port(value: str, field_name: str) -> int:
    try:
        port = int(value.strip())
    except ValueError as exc:
        raise ValueError(f"{field_name}必须是 0-65535 的数字") from exc
    if not 0 <= port <= 65535:
        raise ValueError(f"{field_name}必须在 0-65535 之间")
    return port


@dataclass
class ConnectionSession:
    id: int
    mode: str
    name: str
    config: dict[str, str]
    tree_id: str = ""
    serial_port: object | None = None
    tcp_socket: socket.socket | None = None
    tcp_server_socket: socket.socket | None = None
    udp_socket: socket.socket | None = None
    tcp_clients: list[tuple[socket.socket, tuple[str, int]]] = field(default_factory=list)
    tcp_clients_lock: threading.Lock = field(default_factory=threading.Lock)
    udp_peer: tuple[str, int] | None = None
    udp_default_peer: tuple[str, int] | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    threads: list[threading.Thread] = field(default_factory=list)
    sent_bytes: int = 0
    recv_bytes: int = 0
    sent_last: int = 0
    recv_last: int = 0

    @property
    def is_connected(self) -> bool:
        serial_open = bool(self.serial_port and getattr(self.serial_port, "is_open", False))
        return any((serial_open, self.tcp_socket, self.tcp_server_socket, self.udp_socket))


class ConnectionTree(tk.Canvas):
    def __init__(self, parent: tk.Widget, height: int = 12) -> None:
        self.row_height = 20
        super().__init__(
            parent,
            bg="white",
            height=height * self.row_height,
            highlightthickness=1,
            highlightbackground="#C8C8C8",
        )
        self._items: dict[str, dict[str, object]] = {}
        self._children: dict[str, list[str]] = {"": []}
        self._visible_items: list[tuple[str, int]] = []
        self._selection: str | None = None
        self._focus: str | None = None
        self._next_id = 1
        self._font = font.nametofont("TkDefaultFont")
        self.bind("<Button-1>", self._on_click)

    def insert(
        self,
        parent: str,
        _index: str,
        text: str = "",
        image: tk.PhotoImage | None = None,
        open: bool = True,
    ) -> str:
        item_id = f"I{self._next_id}"
        self._next_id += 1
        self._items[item_id] = {"parent": parent, "text": text, "image": image, "open": open}
        self._children.setdefault(item_id, [])
        self._children.setdefault(parent, []).append(item_id)
        self._redraw()
        return item_id

    def delete(self, *item_ids: str) -> None:
        for item_id in item_ids:
            self._delete_one(item_id)
        if self._selection not in self._items:
            self._selection = None
        self._redraw()

    def _delete_one(self, item_id: str) -> None:
        for child_id in list(self._children.get(item_id, [])):
            self._delete_one(child_id)
        parent = self._items.get(item_id, {}).get("parent", "")
        if parent in self._children and item_id in self._children[parent]:
            self._children[parent].remove(item_id)
        self._children.pop(item_id, None)
        self._items.pop(item_id, None)

    def get_children(self, item_id: str = "") -> list[str]:
        return list(self._children.get(item_id, []))

    def item(self, item_id: str, option: str | None = None, **kwargs: object) -> object:
        item = self._items[item_id]
        if option == "text":
            return item["text"]
        if "text" in kwargs:
            item["text"] = kwargs["text"]
        if "image" in kwargs:
            item["image"] = kwargs["image"]
        self._redraw()
        return item

    def selection(self) -> tuple[str, ...]:
        return (self._selection,) if self._selection else ()

    def selection_set(self, item_id: str) -> None:
        if item_id not in self._items:
            return
        self._selection = item_id
        self._focus = item_id
        self._redraw()
        self.event_generate("<<TreeviewSelect>>")

    def selection_remove(self, item_id: str) -> None:
        if self._selection == item_id:
            self._selection = None
            self._redraw()

    def focus(self, item_id: str | None = None) -> str | None:
        if item_id is not None:
            self._focus = item_id
        return self._focus

    def identify_row(self, y: int) -> str:
        y_canvas = int(self.canvasy(y))
        index = y_canvas // self.row_height
        if 0 <= index < len(self._visible_items):
            return self._visible_items[index][0]
        return ""

    def _on_click(self, event: tk.Event) -> None:
        item_id = self.identify_row(event.y)
        if item_id:
            self.selection_set(item_id)

    def _redraw(self) -> None:
        super().delete("all")
        self._visible_items = []
        self._collect_visible("", 0)

        for row, (item_id, depth) in enumerate(self._visible_items):
            item = self._items[item_id]
            y = row * self.row_height
            is_root = item["parent"] == ""
            indent = 4 + depth * 20

            if is_root:
                self._draw_expand_box(indent, y)
                icon_x = indent + 16
            else:
                icon_x = indent + 18

            image = item.get("image")
            if isinstance(image, tk.PhotoImage):
                self.create_image(icon_x, y + self.row_height // 2, image=image, anchor="w")

            text = str(item.get("text", ""))
            text_x = icon_x + 18
            text_y = y + self.row_height // 2
            if item_id == self._selection:
                width = self._font.measure(text)
                self.create_rectangle(
                    text_x - 2,
                    y + 2,
                    text_x + width + 3,
                    y + self.row_height - 2,
                    fill="#0078D7",
                    outline="#0078D7",
                )
                fill = "white"
            else:
                fill = "black"
            self.create_text(text_x, text_y, text=text, anchor="w", fill=fill, font=self._font)

        total_height = max(len(self._visible_items) * self.row_height, int(self["height"]))
        self.configure(scrollregion=(0, 0, 1, total_height))

    def _collect_visible(self, parent: str, depth: int) -> None:
        for item_id in self._children.get(parent, []):
            self._visible_items.append((item_id, depth))
            if self._items[item_id].get("open", True):
                self._collect_visible(item_id, depth + 1)

    def _draw_expand_box(self, x: int, y: int) -> None:
        top = y + 6
        self.create_rectangle(x, top, x + 8, top + 8, fill="white", outline="#7A7A7A")
        self.create_line(x + 2, top + 4, x + 6, top + 4, fill="#333333")


class SerialDebugTool(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self._set_window_icon()
        self.geometry(DEFAULT_GEOMETRY)
        self.minsize(980, 620)

        self.sessions: dict[int, ConnectionSession] = {}
        self.mode_root_ids: dict[str, str] = {}
        self.next_session_id = 1
        self.active_session_id: int | None = None
        self.rx_queue: queue.Queue[tuple[str, int, bytes | str]] = queue.Queue()
        self.auto_send_job: str | None = None
        self.config_path = app_config_path()
        self._loading_config = True
        self._config_save_after_id: str | None = None
        self._config_trace_tokens: list[tuple[tk.Variable, str]] = []

        self.sent_bytes = 0
        self.recv_bytes = 0
        self.sent_last = 0
        self.recv_last = 0

        self.mode_var = tk.StringVar(value=MODE_SERIAL)
        self.port_var = tk.StringVar()
        self.baud_var = tk.StringVar(value="115200")
        self.data_bits_var = tk.StringVar(value="8")
        self.parity_var = tk.StringVar(value="无")
        self.stop_bits_var = tk.StringVar(value="1")
        self.flow_var = tk.StringVar(value="无")
        self.encoding_var = tk.StringVar(value="utf-8")
        self.dtr_var = tk.BooleanVar(value=True)
        self.rts_var = tk.BooleanVar(value=True)

        self.remote_host_var = tk.StringVar(value="127.0.0.1")
        self.remote_port_var = tk.StringVar(value="10123")
        self.local_host_var = tk.StringVar(value="0.0.0.0")
        self.local_port_var = tk.StringVar(value="10123")

        self.hex_send_var = tk.BooleanVar(value=False)
        self.hex_recv_var = tk.BooleanVar(value=False)
        self.send_newline_var = tk.BooleanVar(value=False)
        self.auto_crc_var = tk.BooleanVar(value=False)
        self.crc_algorithm_var = tk.StringVar(value=CRC_ALGORITHM_MODBUS)
        self.auto_send_var = tk.BooleanVar(value=False)
        self.interval_var = tk.StringVar(value="1000")
        self.send_file_var = tk.BooleanVar(value=False)
        self.send_file_path_var = tk.StringVar()

        self.pause_display_var = tk.BooleanVar(value=False)
        self.realtime_save_var = tk.BooleanVar(value=False)
        self.realtime_path_var = tk.StringVar()

        self.status_var = tk.StringVar(value="就绪")
        self.count_var = tk.StringVar(value="发送: 0 字节    接收: 0 字节")
        self.speed_var = tk.StringVar(value="发送速度(B/S): 0    接收速度(B/S): 0")
        self.status_images = self._create_status_images()

        self._build_style()
        self._build_menu()
        self._build_body()
        self._build_status_bar()

        self.refresh_ports()
        self._load_config_on_start()
        self._bind_config_traces()
        self._loading_config = False
        self._set_connected_state(False)
        self.after(60, self._drain_rx_queue)
        self.after(1000, self._update_speed)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _set_window_icon(self) -> None:
        icon_path = resource_path(APP_ICON_PATH)
        if not icon_path.exists():
            return
        try:
            self._window_icon = tk.PhotoImage(file=str(icon_path))
            self.iconphoto(True, self._window_icon)
        except tk.TclError:
            pass

    def _build_style(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Toolbar.TFrame", background="#f2f2f2")
        style.configure("Status.TLabel", padding=(8, 2))
        style.configure("Pane.TLabelframe", padding=6)
        style.configure("Small.TButton", padding=(8, 2))

    def _create_status_images(self) -> dict[str, tk.PhotoImage]:
        def make_dot(color: str) -> tk.PhotoImage:
            image = tk.PhotoImage(width=12, height=12)
            center = 5.5
            radius = 4.2
            for x in range(12):
                for y in range(12):
                    if ((x - center) ** 2 + (y - center) ** 2) <= radius**2:
                        image.put(color, (x, y))
            return image

        images = {
            "disconnected": make_dot("#2F80ED"),
            "connected": make_dot("#27AE60"),
        }
        images["mode"] = tk.PhotoImage(width=12, height=12)
        for x in range(2, 10):
            for y in range(3, 9):
                images["mode"].put("#707070", (x, y))
        for x in range(4, 8):
            images["mode"].put("#4A4A4A", (x, 10))
        return images

    def _build_menu(self) -> None:
        menu_bar = tk.Menu(self)

        action_menu = tk.Menu(menu_bar, tearoff=False)
        action_menu.add_command(label="刷新连接列表", command=self.refresh_ports)
        action_menu.add_command(label="创建连接", command=self.create_connection)
        action_menu.add_command(label="删除连接", command=self.delete_current_connection)
        action_menu.add_command(label="打开连接", command=self.connect_current)
        action_menu.add_command(label="关闭连接", command=self.disconnect_current)
        action_menu.add_separator()
        action_menu.add_command(label="退出", command=self.on_close)
        menu_bar.add_cascade(label="操作(O)", menu=action_menu)

        view_menu = tk.Menu(menu_bar, tearoff=False)
        view_menu.add_command(label="清空发送区", command=self.clear_send)
        view_menu.add_command(label="清空接收区", command=self.clear_receive)
        view_menu.add_command(label="清空计数", command=self.clear_counts)
        menu_bar.add_cascade(label="查看(V)", menu=view_menu)

        config_menu = tk.Menu(menu_bar, tearoff=False)
        config_menu.add_command(label="导入配置", command=self.import_config)
        config_menu.add_command(label="导出配置", command=self.export_config)
        config_menu.add_separator()
        config_menu.add_command(label="清除配置", command=self.clear_config)
        menu_bar.add_cascade(label="配置(C)", menu=config_menu)

        window_menu = tk.Menu(menu_bar, tearoff=False)
        window_menu.add_command(label="恢复默认大小", command=lambda: self.geometry(DEFAULT_GEOMETRY))
        menu_bar.add_cascade(label="窗口(W)", menu=window_menu)

        help_menu = tk.Menu(menu_bar, tearoff=False)
        help_menu.add_command(label="关于", command=self.show_about)
        menu_bar.add_cascade(label="帮助(H)", menu=help_menu)

        self.config(menu=menu_bar)

    def _build_body(self) -> None:
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(paned, width=DEFAULT_LEFT_PANEL_WIDTH, padding=(4, 4))
        left.pack_propagate(False)
        right = ttk.Frame(paned, padding=(4, 4))
        paned.add(left, weight=0)
        paned.add(right, weight=1)

        self._build_left_panel(left)
        self._build_workspace(right)

    def _build_left_panel(self, parent: ttk.Frame) -> None:
        self.port_box = ttk.LabelFrame(parent, text="连接列表", style="Pane.TLabelframe")
        self.port_box.pack(fill=tk.BOTH, expand=False)

        self.port_tree = ConnectionTree(self.port_box, height=12)
        self.port_tree.pack(fill=tk.BOTH, expand=True)
        self.port_tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.port_tree.bind("<Button-3>", self._show_connection_context_menu)

        config_box = ttk.LabelFrame(parent, text="串口参数", style="Pane.TLabelframe")
        self.serial_config_box = config_box
        config_box.pack(fill=tk.X, pady=(8, 0))
        config_box.columnconfigure(1, weight=1)

        self._labeled_widget(config_box, 0, "串口:", self._make_port_selector(config_box))
        self._labeled_widget(
            config_box,
            1,
            "波特率:",
            ttk.Combobox(config_box, textvariable=self.baud_var, values=BAUD_RATES, width=12),
        )
        self._labeled_widget(
            config_box,
            2,
            "数据位:",
            ttk.Combobox(config_box, textvariable=self.data_bits_var, values=("5", "6", "7", "8"), width=12),
        )
        self._labeled_widget(
            config_box,
            3,
            "校验:",
            ttk.Combobox(config_box, textvariable=self.parity_var, values=tuple(PARITY_OPTIONS), width=12),
        )
        self._labeled_widget(
            config_box,
            4,
            "停止位:",
            ttk.Combobox(config_box, textvariable=self.stop_bits_var, values=("1", "1.5", "2"), width=12),
        )
        self._labeled_widget(
            config_box,
            5,
            "流控:",
            ttk.Combobox(config_box, textvariable=self.flow_var, values=FLOW_OPTIONS, width=12),
        )
        self._labeled_widget(
            config_box,
            6,
            "编码:",
            ttk.Combobox(config_box, textvariable=self.encoding_var, values=ENCODINGS, width=12),
        )

        line_box = ttk.Frame(config_box)
        line_box.grid(row=7, column=0, columnspan=2, sticky=tk.W, pady=(4, 0))
        ttk.Checkbutton(line_box, text="DTR", variable=self.dtr_var, command=self._apply_line_state).pack(
            side=tk.LEFT
        )
        ttk.Checkbutton(line_box, text="RTS", variable=self.rts_var, command=self._apply_line_state).pack(
            side=tk.LEFT, padx=(8, 0)
        )

        self.network_config_box = self._build_network_config(parent)

        self.connection_button_bar = ttk.Frame(parent)
        self.connection_button_bar.pack(fill=tk.X, pady=(8, 0))
        self.create_btn = ttk.Button(self.connection_button_bar, text="创建连接", command=self.create_connection)
        self.connect_btn = ttk.Button(self.connection_button_bar, text="打开连接", command=self.toggle_connection)
        self._update_connection_buttons()

        count_box = ttk.LabelFrame(parent, text="计数", style="Pane.TLabelframe")
        count_box.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(count_box, textvariable=self.count_var, justify=tk.LEFT).pack(anchor=tk.W)
        ttk.Button(count_box, text="清空计数", command=self.clear_counts).pack(anchor=tk.W, pady=(6, 0))

        self._update_mode_controls()

    def _build_network_config(self, parent: ttk.Frame) -> ttk.LabelFrame:
        network_box = ttk.LabelFrame(parent, text="网络参数", style="Pane.TLabelframe")
        network_box.columnconfigure(1, weight=1)
        self.network_rows: dict[str, tuple[ttk.Label, tk.Widget]] = {}

        self._network_row(network_box, "remote_host", 0, "目标IP:", ttk.Entry(network_box, textvariable=self.remote_host_var))
        self._network_row(
            network_box,
            "remote_port",
            1,
            "目标端口:",
            ttk.Entry(network_box, textvariable=self.remote_port_var),
        )
        self._network_row(network_box, "local_port", 2, "本地端口:", ttk.Entry(network_box, textvariable=self.local_port_var))
        return network_box

    def _network_row(self, parent: ttk.Frame, key: str, row: int, label: str, widget: tk.Widget) -> None:
        label_widget = ttk.Label(parent, text=label)
        label_widget.grid(row=row, column=0, sticky=tk.W, pady=2)
        widget.grid(row=row, column=1, sticky=tk.EW, pady=2)
        self.network_rows[key] = (label_widget, widget)

    def _make_port_selector(self, parent: ttk.Frame) -> ttk.Frame:
        frame = ttk.Frame(parent)
        frame.columnconfigure(0, weight=1)
        self.port_combo = ttk.Combobox(frame, textvariable=self.port_var, width=12)
        self.port_combo.grid(row=0, column=0, sticky=tk.EW)
        ttk.Button(frame, text="刷新", width=5, command=self.refresh_ports).grid(row=0, column=1, padx=(4, 0))
        return frame

    def _labeled_widget(self, parent: ttk.Frame, row: int, label: str, widget: tk.Widget) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, pady=2)
        widget.grid(row=row, column=1, sticky=tk.EW, pady=2)

    def _on_mode_change(self, _event: tk.Event | None = None) -> None:
        if _event is not None:
            self.active_session_id = None
            for item in self.port_tree.selection():
                self.port_tree.selection_remove(item)
        self._apply_mode_defaults()
        self._update_mode_controls()
        self._set_connected_state(self.is_connected)
        self.status_var.set(f"当前模式：{self.mode_var.get()}")

    def _apply_mode_defaults(self) -> None:
        mode = self.mode_var.get()
        local_port = self.local_port_var.get().strip()
        if mode in (MODE_TCP_SERVER, MODE_UDP_SERVER) and local_port in ("", "0"):
            self.local_port_var.set("10123")

    def _update_mode_controls(self) -> None:
        if not hasattr(self, "serial_config_box") or not hasattr(self, "network_config_box"):
            return

        self.serial_config_box.pack_forget()
        self.network_config_box.pack_forget()
        if self.mode_var.get() == MODE_SERIAL:
            self.serial_config_box.pack(fill=tk.X, pady=(8, 0), before=self.connection_button_bar)
        else:
            self._update_network_rows()
            self.network_config_box.pack(fill=tk.X, pady=(8, 0), before=self.connection_button_bar)
        self._update_connection_buttons()

    def _update_connection_buttons(self) -> None:
        if not hasattr(self, "create_btn") or not hasattr(self, "connect_btn"):
            return
        for child in self.connection_button_bar.winfo_children():
            child.pack_forget()

        if self.mode_var.get() != MODE_SERIAL:
            self.create_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))
            self.connect_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(3, 0))
        else:
            self.connect_btn.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _update_network_rows(self) -> None:
        mode = self.mode_var.get()
        visible_rows = {
            MODE_TCP_CLIENT: ("remote_host", "remote_port"),
            MODE_UDP_CLIENT: ("remote_host", "remote_port"),
            MODE_TCP_SERVER: ("local_port",),
            MODE_UDP_SERVER: ("local_port",),
        }.get(mode, ())

        for label, widget in self.network_rows.values():
            label.grid_remove()
            widget.grid_remove()
        for row, key in enumerate(visible_rows):
            label, widget = self.network_rows[key]
            label.grid(row=row, column=0, sticky=tk.W, pady=2)
            widget.grid(row=row, column=1, sticky=tk.EW, pady=2)

    def _build_workspace(self, parent: ttk.Frame) -> None:
        self.notebook = ttk.Notebook(parent)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        session = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(session, text="串口会话")
        session.rowconfigure(2, weight=1)
        session.rowconfigure(6, weight=1)
        session.columnconfigure(0, weight=1)

        self._build_send_area(session)
        ttk.Separator(session, orient=tk.HORIZONTAL).grid(row=3, column=0, sticky=tk.EW, pady=(6, 6))
        self._build_receive_area(session)

    def _build_send_area(self, parent: ttk.Frame) -> None:
        send_toolbar = ttk.Frame(parent)
        send_toolbar.grid(row=0, column=0, sticky=tk.EW)
        send_toolbar.columnconfigure(14, weight=1)

        ttk.Label(send_toolbar, text="发送区").grid(row=0, column=0, padx=(0, 8))
        ttk.Checkbutton(send_toolbar, text="16进制", variable=self.hex_send_var).grid(row=0, column=1, padx=2)
        ttk.Checkbutton(send_toolbar, text="追加CRLF", variable=self.send_newline_var).grid(
            row=0, column=2, padx=2
        )
        ttk.Checkbutton(
            send_toolbar,
            text="自动CRC",
            variable=self.auto_crc_var,
            command=self._toggle_crc_controls,
        ).grid(row=0, column=3, padx=2)
        self.crc_combo = ttk.Combobox(
            send_toolbar,
            textvariable=self.crc_algorithm_var,
            values=CRC_ALGORITHMS,
            width=13,
            state="readonly",
        )
        self.crc_combo.grid(row=0, column=4, padx=(0, 8))
        ttk.Checkbutton(
            send_toolbar,
            text="发送文件",
            variable=self.send_file_var,
            command=self._toggle_file_send_controls,
        ).grid(row=0, column=5, padx=2)
        ttk.Checkbutton(
            send_toolbar,
            text="自动发送",
            variable=self.auto_send_var,
            command=self._on_auto_send_toggle,
        ).grid(row=0, column=6, padx=2)
        ttk.Label(send_toolbar, text="间隔").grid(row=0, column=7, padx=(8, 2))
        ttk.Entry(send_toolbar, textvariable=self.interval_var, width=7).grid(row=0, column=8)
        ttk.Label(send_toolbar, text="ms").grid(row=0, column=9, padx=(2, 8))
        ttk.Button(send_toolbar, text="发送", command=self.send_now, width=8).grid(row=0, column=10, padx=2)
        ttk.Button(send_toolbar, text="停止", command=self.stop_auto_send, width=8).grid(row=0, column=11, padx=2)
        ttk.Button(send_toolbar, text="清空", command=self.clear_send, width=8).grid(row=0, column=12, padx=2)

        file_bar = ttk.Frame(parent)
        file_bar.grid(row=1, column=0, sticky=tk.EW, pady=(5, 4))
        file_bar.columnconfigure(1, weight=1)
        ttk.Label(file_bar, text="文件:").grid(row=0, column=0, sticky=tk.W)
        self.send_file_entry = ttk.Entry(file_bar, textvariable=self.send_file_path_var)
        self.send_file_entry.grid(row=0, column=1, sticky=tk.EW, padx=(4, 4))
        self.send_file_btn = ttk.Button(file_bar, text="...", width=4, command=self.choose_send_file)
        self.send_file_btn.grid(row=0, column=2)

        send_frame = ttk.Frame(parent)
        send_frame.grid(row=2, column=0, sticky=tk.NSEW, pady=(4, 0))
        send_frame.rowconfigure(1, weight=1)
        send_frame.columnconfigure(2, weight=1)

        ttk.Label(send_frame, text="时间", anchor=tk.W).grid(row=0, column=0, sticky=tk.EW, padx=(0, 4))
        ttk.Label(send_frame, text="连接", anchor=tk.W).grid(row=0, column=1, sticky=tk.EW, padx=(0, 4))
        ttk.Label(send_frame, text="数据", anchor=tk.W).grid(row=0, column=2, sticky=tk.EW)

        self.send_time_text = tk.Text(send_frame, width=14, wrap=tk.NONE)
        self.send_connection_text = tk.Text(send_frame, width=20, wrap=tk.NONE)
        self.send_history_text = tk.Text(send_frame, wrap=tk.NONE)
        self.send_time_text.grid(row=1, column=0, sticky=tk.NSEW, padx=(0, 4))
        self.send_connection_text.grid(row=1, column=1, sticky=tk.NSEW, padx=(0, 4))
        self.send_history_text.grid(row=1, column=2, sticky=tk.NSEW)

        send_y_scroll = ttk.Scrollbar(send_frame, orient=tk.VERTICAL, command=self._send_yview)
        send_y_scroll.grid(row=1, column=3, sticky=tk.NS)
        send_x_scroll = ttk.Scrollbar(send_frame, orient=tk.HORIZONTAL, command=self.send_history_text.xview)
        send_x_scroll.grid(row=2, column=2, sticky=tk.EW)
        self.send_history_text.configure(yscrollcommand=send_y_scroll.set, xscrollcommand=send_x_scroll.set)

        ttk.Label(send_frame, text="发送内容", anchor=tk.W).grid(
            row=3, column=0, columnspan=4, sticky=tk.EW, pady=(6, 2)
        )
        self.send_text = tk.Text(send_frame, height=4, wrap=tk.CHAR, undo=True)
        self.send_text.grid(row=4, column=0, columnspan=4, sticky=tk.EW)
        self.send_history_widgets = (self.send_time_text, self.send_connection_text, self.send_history_text)
        for widget in self.send_history_widgets:
            widget.configure(state=tk.DISABLED)
            widget.bind("<MouseWheel>", self._on_send_mousewheel)
            widget.bind("<Button-4>", self._on_send_mousewheel)
            widget.bind("<Button-5>", self._on_send_mousewheel)
            self._install_text_context_menu(widget, editable=False)
        self._install_text_context_menu(self.send_text, editable=True)

        self._toggle_file_send_controls()
        self._toggle_crc_controls()

    def _build_receive_area(self, parent: ttk.Frame) -> None:
        receive_toolbar = ttk.Frame(parent)
        receive_toolbar.grid(row=4, column=0, sticky=tk.EW)
        receive_toolbar.columnconfigure(9, weight=1)

        ttk.Label(receive_toolbar, text="接收区").grid(row=0, column=0, padx=(0, 8))
        ttk.Checkbutton(receive_toolbar, text="暂停显示", variable=self.pause_display_var).grid(
            row=0, column=1, padx=2
        )
        ttk.Button(receive_toolbar, text="清空", command=self.clear_receive, width=8).grid(row=0, column=2, padx=2)
        ttk.Button(receive_toolbar, text="保存", command=self.save_receive, width=8).grid(row=0, column=3, padx=2)
        ttk.Checkbutton(receive_toolbar, text="16进制", variable=self.hex_recv_var).grid(row=0, column=4, padx=2)

        save_bar = ttk.Frame(parent)
        save_bar.grid(row=5, column=0, sticky=tk.EW, pady=(5, 4))
        save_bar.columnconfigure(2, weight=1)
        ttk.Checkbutton(
            save_bar,
            text="保存到文件(实时)",
            variable=self.realtime_save_var,
            command=self._on_realtime_save_toggle,
        ).grid(row=0, column=0, sticky=tk.W)
        self.realtime_entry = ttk.Entry(save_bar, textvariable=self.realtime_path_var)
        self.realtime_entry.grid(row=0, column=2, sticky=tk.EW, padx=(4, 4))
        ttk.Button(save_bar, text="...", width=4, command=self.choose_realtime_file).grid(row=0, column=3)

        receive_frame = ttk.Frame(parent)
        receive_frame.grid(row=6, column=0, sticky=tk.NSEW)
        receive_frame.rowconfigure(1, weight=1)
        receive_frame.columnconfigure(2, weight=1)

        ttk.Label(receive_frame, text="时间", anchor=tk.W).grid(row=0, column=0, sticky=tk.EW, padx=(0, 4))
        ttk.Label(receive_frame, text="连接", anchor=tk.W).grid(row=0, column=1, sticky=tk.EW, padx=(0, 4))
        ttk.Label(receive_frame, text="数据", anchor=tk.W).grid(row=0, column=2, sticky=tk.EW)

        self.receive_time_text = tk.Text(receive_frame, width=14, wrap=tk.NONE)
        self.receive_connection_text = tk.Text(receive_frame, width=20, wrap=tk.NONE)
        self.receive_text = tk.Text(receive_frame, wrap=tk.NONE)
        self.receive_time_text.grid(row=1, column=0, sticky=tk.NSEW, padx=(0, 4))
        self.receive_connection_text.grid(row=1, column=1, sticky=tk.NSEW, padx=(0, 4))
        self.receive_text.grid(row=1, column=2, sticky=tk.NSEW)

        y_scroll = ttk.Scrollbar(receive_frame, orient=tk.VERTICAL, command=self._receive_yview)
        y_scroll.grid(row=1, column=3, sticky=tk.NS)
        x_scroll = ttk.Scrollbar(receive_frame, orient=tk.HORIZONTAL, command=self.receive_text.xview)
        x_scroll.grid(row=2, column=2, sticky=tk.EW)
        self.receive_text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        self.receive_widgets = (self.receive_time_text, self.receive_connection_text, self.receive_text)
        for widget in self.receive_widgets:
            widget.configure(state=tk.DISABLED)
            widget.bind("<MouseWheel>", self._on_receive_mousewheel)
            widget.bind("<Button-4>", self._on_receive_mousewheel)
            widget.bind("<Button-5>", self._on_receive_mousewheel)
            self._install_text_context_menu(widget, editable=False)

    def _install_text_context_menu(self, widget: tk.Text, editable: bool) -> None:
        widget.bind("<Button-3>", lambda event, item=widget, can_edit=editable: self._show_text_context_menu(event, item, can_edit))

    def _show_text_context_menu(self, event: tk.Event, widget: tk.Text, editable: bool) -> str:
        widget.focus_set()
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label="复制", command=lambda: self._copy_text_selection(widget))
        if editable:
            menu.add_command(label="剪切", command=lambda: self._cut_text_selection(widget))
            menu.add_command(label="粘贴", command=lambda: self._paste_text_clipboard(widget))
        menu.tk_popup(event.x_root, event.y_root)
        menu.grab_release()
        return "break"

    def _copy_text_selection(self, widget: tk.Text) -> None:
        try:
            selected = widget.get(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            return
        self.clipboard_clear()
        self.clipboard_append(selected)

    def _cut_text_selection(self, widget: tk.Text) -> None:
        try:
            selected = widget.get(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            return
        self.clipboard_clear()
        self.clipboard_append(selected)
        widget.delete(tk.SEL_FIRST, tk.SEL_LAST)

    def _paste_text_clipboard(self, widget: tk.Text) -> None:
        try:
            text = self.clipboard_get()
        except tk.TclError:
            return
        widget.insert(tk.INSERT, text)

    def _receive_yview(self, *args: object) -> None:
        for widget in getattr(self, "receive_widgets", ()):
            widget.yview(*args)

    def _on_receive_mousewheel(self, event: tk.Event) -> str:
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            units = -1
        else:
            units = 1
        self._receive_yview("scroll", units, "units")
        return "break"

    def _send_yview(self, *args: object) -> None:
        for widget in getattr(self, "send_history_widgets", ()):
            widget.yview(*args)

    def _on_send_mousewheel(self, event: tk.Event) -> str:
        if getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
            units = -1
        else:
            units = 1
        self._send_yview("scroll", units, "units")
        return "break"

    def _build_status_bar(self) -> None:
        status = ttk.Frame(self)
        status.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(status, textvariable=self.status_var, style="Status.TLabel").pack(side=tk.LEFT)
        ttk.Label(status, textvariable=self.speed_var, style="Status.TLabel").pack(side=tk.RIGHT)

    def _load_config_on_start(self) -> None:
        if not self.config_path.exists():
            return
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("配置文件格式错误")
            self._apply_config(data)
            self.status_var.set(f"已加载配置: {self.config_path}")
        except Exception as exc:
            self.status_var.set(f"配置加载失败: {exc}")

    def _bind_config_traces(self) -> None:
        variables: tuple[tk.Variable, ...] = (
            self.port_var,
            self.baud_var,
            self.data_bits_var,
            self.parity_var,
            self.stop_bits_var,
            self.flow_var,
            self.encoding_var,
            self.dtr_var,
            self.rts_var,
            self.remote_host_var,
            self.remote_port_var,
            self.local_host_var,
            self.local_port_var,
            self.hex_send_var,
            self.hex_recv_var,
            self.send_newline_var,
            self.auto_crc_var,
            self.crc_algorithm_var,
            self.auto_send_var,
            self.interval_var,
            self.send_file_var,
            self.send_file_path_var,
            self.pause_display_var,
            self.realtime_save_var,
            self.realtime_path_var,
        )
        for variable in variables:
            token = variable.trace_add("write", self._on_config_var_changed)
            self._config_trace_tokens.append((variable, token))

    def _on_config_var_changed(self, *_args: object) -> None:
        if self._loading_config:
            return
        self._sync_active_session_from_controls()
        self._schedule_config_save()

    def _schedule_config_save(self) -> None:
        if self._loading_config:
            return
        if self._config_save_after_id is not None:
            self.after_cancel(self._config_save_after_id)
        self._config_save_after_id = self.after(200, self._save_config_now)

    def _cancel_scheduled_config_save(self) -> None:
        if self._config_save_after_id is not None:
            self.after_cancel(self._config_save_after_id)
            self._config_save_after_id = None

    def _save_config_now(self) -> None:
        self._config_save_after_id = None
        if self._loading_config:
            return
        try:
            self._sync_active_session_from_controls()
            data = self._collect_config()
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.config_path.with_suffix(".json.tmp")
            temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(self.config_path)
        except Exception as exc:
            self.status_var.set(f"配置保存失败: {exc}")

    def _collect_config(self) -> dict[str, object]:
        return {
            "schema_version": CONFIG_SCHEMA_VERSION,
            "app_version": APP_VERSION,
            "window": {"geometry": self.geometry()},
            "mode": self.mode_var.get(),
            "active_session_id": self.active_session_id,
            "serial": {
                "port": self.port_var.get(),
                "baud": self.baud_var.get(),
                "data_bits": self.data_bits_var.get(),
                "parity": self.parity_var.get(),
                "stop_bits": self.stop_bits_var.get(),
                "flow": self.flow_var.get(),
                "encoding": self.encoding_var.get(),
                "dtr": self.dtr_var.get(),
                "rts": self.rts_var.get(),
            },
            "network": {
                "remote_host": self.remote_host_var.get(),
                "remote_port": self.remote_port_var.get(),
                "local_host": self.local_host_var.get(),
                "local_port": self.local_port_var.get(),
            },
            "send": {
                "hex_send": self.hex_send_var.get(),
                "append_crlf": self.send_newline_var.get(),
                "auto_crc": self.auto_crc_var.get(),
                "crc_algorithm": self.crc_algorithm_var.get(),
                "auto_send": self.auto_send_var.get(),
                "interval": self.interval_var.get(),
                "send_file": self.send_file_var.get(),
                "send_file_path": self.send_file_path_var.get(),
            },
            "receive": {
                "hex_recv": self.hex_recv_var.get(),
                "pause_display": self.pause_display_var.get(),
                "realtime_save": self.realtime_save_var.get(),
                "realtime_path": self.realtime_path_var.get(),
            },
            "connections": [
                {
                    "id": session.id,
                    "mode": session.mode,
                    "name": session.name,
                    "config": dict(session.config),
                }
                for session in sorted(self.sessions.values(), key=lambda item: item.id)
            ],
        }

    def _default_config(self) -> dict[str, object]:
        return {
            "schema_version": CONFIG_SCHEMA_VERSION,
            "app_version": APP_VERSION,
            "window": {"geometry": DEFAULT_GEOMETRY},
            "mode": MODE_SERIAL,
            "active_session_id": None,
            "serial": {
                "port": "",
                "baud": "115200",
                "data_bits": "8",
                "parity": "无",
                "stop_bits": "1",
                "flow": "无",
                "encoding": "utf-8",
                "dtr": True,
                "rts": True,
            },
            "network": {
                "remote_host": "127.0.0.1",
                "remote_port": "10123",
                "local_host": "0.0.0.0",
                "local_port": "10123",
            },
            "send": {
                "hex_send": False,
                "append_crlf": False,
                "auto_crc": False,
                "crc_algorithm": CRC_ALGORITHM_MODBUS,
                "auto_send": False,
                "interval": "1000",
                "send_file": False,
                "send_file_path": "",
            },
            "receive": {
                "hex_recv": False,
                "pause_display": False,
                "realtime_save": False,
                "realtime_path": "",
            },
            "connections": [],
        }

    def _apply_config(self, data: dict[str, object]) -> None:
        previous_loading = self._loading_config
        self._loading_config = True
        try:
            window = self._config_section(data, "window")
            geometry = window.get("geometry")
            if isinstance(geometry, str) and geometry:
                self.geometry(geometry)

            serial_config = self._config_section(data, "serial")
            self._set_string_var(self.port_var, serial_config.get("port"), "")
            self._set_string_var(self.baud_var, serial_config.get("baud"), "115200")
            self._set_string_var(self.data_bits_var, serial_config.get("data_bits"), "8")
            self._set_string_var(self.parity_var, serial_config.get("parity"), "无")
            self._set_string_var(self.stop_bits_var, serial_config.get("stop_bits"), "1")
            self._set_string_var(self.flow_var, serial_config.get("flow"), "无")
            self._set_string_var(self.encoding_var, serial_config.get("encoding"), "utf-8")
            self.dtr_var.set(config_bool(serial_config.get("dtr"), True))
            self.rts_var.set(config_bool(serial_config.get("rts"), True))

            network_config = self._config_section(data, "network")
            self._set_string_var(self.remote_host_var, network_config.get("remote_host"), "127.0.0.1")
            self._set_string_var(self.remote_port_var, network_config.get("remote_port"), "10123")
            self._set_string_var(self.local_host_var, network_config.get("local_host"), "0.0.0.0")
            self._set_string_var(self.local_port_var, network_config.get("local_port"), "10123")

            send_config = self._config_section(data, "send")
            self.hex_send_var.set(config_bool(send_config.get("hex_send"), False))
            self.send_newline_var.set(config_bool(send_config.get("append_crlf"), False))
            self.auto_crc_var.set(config_bool(send_config.get("auto_crc"), False))
            self._set_string_var(self.crc_algorithm_var, send_config.get("crc_algorithm"), CRC_ALGORITHM_MODBUS)
            self.auto_send_var.set(config_bool(send_config.get("auto_send"), False))
            self._set_string_var(self.interval_var, send_config.get("interval"), "1000")
            self.send_file_var.set(config_bool(send_config.get("send_file"), False))
            self._set_string_var(self.send_file_path_var, send_config.get("send_file_path"), "")

            receive_config = self._config_section(data, "receive")
            self.hex_recv_var.set(config_bool(receive_config.get("hex_recv"), False))
            self.pause_display_var.set(config_bool(receive_config.get("pause_display"), False))
            self.realtime_save_var.set(config_bool(receive_config.get("realtime_save"), False))
            self._set_string_var(self.realtime_path_var, receive_config.get("realtime_path"), "")

            self.sessions.clear()
            self.next_session_id = 1
            self.active_session_id = None
            self._load_configured_sessions(data)

            mode = data.get("mode")
            if mode not in CONNECTION_MODES:
                mode = MODE_SERIAL
            self.mode_var.set(str(mode))

            active_session_id = self._config_int(data.get("active_session_id"))
            if active_session_id in self.sessions:
                self.active_session_id = active_session_id

            self._rebuild_connection_tree()
            if self.active_session_id in self.sessions:
                session = self.sessions[self.active_session_id]
                self.mode_var.set(session.mode)
                self._load_session_config(session)
                self.notebook.tab(0, text=session.name)
            else:
                self.notebook.tab(0, text="串口会话")

            self._toggle_file_send_controls()
            self._toggle_crc_controls()
            self._update_mode_controls()
            self._set_connected_state(False)
            self._update_counts()
        finally:
            self._loading_config = previous_loading

    def _load_configured_sessions(self, data: dict[str, object]) -> None:
        connections = data.get("connections")
        if not isinstance(connections, list):
            return

        used_ids: set[int] = set()
        for item in connections:
            if not isinstance(item, dict):
                continue
            mode = item.get("mode")
            if mode not in CONNECTION_MODES:
                continue
            config = item.get("config")
            if not isinstance(config, dict):
                config = {}
            normalized_config = self._normalize_session_config(str(mode), config)
            session_id = self._config_int(item.get("id"))
            if session_id is None or session_id in used_ids:
                session_id = self.next_session_id
            used_ids.add(session_id)
            self.next_session_id = max(self.next_session_id, session_id + 1)

            raw_name = item.get("name")
            base_name = str(raw_name) if raw_name else self._session_label(str(mode), normalized_config)
            session = ConnectionSession(
                id=session_id,
                mode=str(mode),
                name=self._unique_session_name(base_name),
                config=normalized_config,
            )
            self.sessions[session.id] = session

    def _normalize_session_config(self, mode: str, config: dict[object, object]) -> dict[str, str]:
        normalized = {str(key): "" if value is None else str(value) for key, value in config.items()}
        if mode == MODE_SERIAL:
            normalized.setdefault("port", "")
            normalized.setdefault("baud", "115200")
            normalized.setdefault("data_bits", "8")
            normalized.setdefault("parity", "无")
            normalized.setdefault("stop_bits", "1")
            normalized.setdefault("flow", "无")
            normalized["dtr"] = "1" if config_bool(normalized.get("dtr"), True) else "0"
            normalized["rts"] = "1" if config_bool(normalized.get("rts"), True) else "0"
            return normalized

        normalized.setdefault("remote_host", "127.0.0.1" if mode in (MODE_TCP_CLIENT, MODE_UDP_CLIENT) else "")
        normalized.setdefault("remote_port", "10123" if mode in (MODE_TCP_CLIENT, MODE_UDP_CLIENT) else "")
        normalized.setdefault("local_host", "0.0.0.0")
        normalized.setdefault("local_port", "10123" if mode in (MODE_TCP_SERVER, MODE_UDP_SERVER) else "0")
        return normalized

    def _config_section(self, data: dict[str, object], key: str) -> dict[str, object]:
        section = data.get(key)
        return section if isinstance(section, dict) else {}

    def _set_string_var(self, variable: tk.StringVar, value: object, default: str) -> None:
        variable.set(default if value is None else str(value))

    def _config_int(self, value: object) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _raw_current_config(self, mode: str) -> dict[str, str]:
        if mode == MODE_SERIAL:
            return {
                "port": self.port_var.get().strip(),
                "baud": self.baud_var.get().strip(),
                "data_bits": self.data_bits_var.get().strip(),
                "parity": self.parity_var.get().strip(),
                "stop_bits": self.stop_bits_var.get().strip(),
                "flow": self.flow_var.get().strip(),
                "dtr": "1" if self.dtr_var.get() else "0",
                "rts": "1" if self.rts_var.get() else "0",
            }
        if mode in (MODE_TCP_CLIENT, MODE_UDP_CLIENT):
            return {
                "remote_host": self.remote_host_var.get().strip(),
                "remote_port": self.remote_port_var.get().strip(),
                "local_host": "0.0.0.0",
                "local_port": "0",
            }
        return {
            "remote_host": "",
            "remote_port": "",
            "local_host": "0.0.0.0",
            "local_port": self.local_port_var.get().strip() or "0",
        }

    def _sync_active_session_from_controls(self) -> None:
        session = self.active_session
        if session is None:
            return
        config = self._raw_current_config(session.mode)
        session.config = config
        base_name = self._session_label(session.mode, config)
        if base_name:
            session.name = self._unique_session_name_for_session(session, base_name)
            self._update_session_tree_status(session)
            if hasattr(self, "notebook"):
                self.notebook.tab(0, text=session.name)

    def import_config(self) -> None:
        path = filedialog.askopenfilename(
            title="导入配置",
            filetypes=(("JSON配置文件", "*.json"), ("所有文件", "*.*")),
        )
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("配置文件格式错误")
            for session in list(self.sessions.values()):
                if session.is_connected:
                    self.disconnect_session(session)
            self._cancel_scheduled_config_save()
            self._apply_config(data)
            self._save_config_now()
            self.status_var.set(f"已导入配置: {path}")
        except Exception as exc:
            messagebox.showerror("导入配置失败", str(exc))
            self.status_var.set("导入配置失败")

    def export_config(self) -> None:
        path = filedialog.asksaveasfilename(
            title="导出配置",
            defaultextension=".json",
            filetypes=(("JSON配置文件", "*.json"), ("所有文件", "*.*")),
            initialfile="config.json",
        )
        if not path:
            return
        try:
            self._sync_active_session_from_controls()
            data = self._collect_config()
            Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            self.status_var.set(f"已导出配置: {path}")
        except Exception as exc:
            messagebox.showerror("导出配置失败", str(exc))
            self.status_var.set("导出配置失败")

    def clear_config(self) -> None:
        if not messagebox.askyesno("清除配置", "确定要清除已保存配置并恢复默认设置吗？"):
            return
        for session in list(self.sessions.values()):
            if session.is_connected:
                self.disconnect_session(session)
        self._cancel_scheduled_config_save()
        self._apply_config(self._default_config())
        try:
            if self.config_path.exists():
                self.config_path.unlink()
            self.status_var.set("已清除配置")
        except Exception as exc:
            messagebox.showerror("清除配置失败", str(exc))
            self.status_var.set("清除配置失败")

    def refresh_ports(self) -> None:
        self._refresh_serial_ports()
        self._rebuild_connection_tree()

    def _refresh_serial_ports(self) -> None:
        values: list[str] = []
        status = ""
        if not HAS_PYSERIAL:
            self.port_combo.configure(values=())
            status = "缺少 pyserial，串口不可用；网络模式仍可使用"
        else:
            ports = list(list_ports.comports())
            for item in ports:
                values.append(item.device)

            self.port_combo.configure(values=values)
            if values and (not self.port_var.get() or self.port_var.get() not in values):
                self.port_var.set(values[0])
            status = "未发现本机 COM 串口" if not values else f"发现 {len(values)} 个串口"

        self.status_var.set(status)

    def _rebuild_connection_tree(self) -> None:
        self.port_tree.delete(*self.port_tree.get_children())
        self.mode_root_ids.clear()
        for mode in CONNECTION_MODES:
            self.mode_root_ids[mode] = self.port_tree.insert(
                "", tk.END, text=mode, image=self.status_images["mode"], open=True
            )

        for session in sorted(self.sessions.values(), key=lambda item: item.id):
            parent = self.mode_root_ids[session.mode]
            image = self._session_status_image(session)
            session.tree_id = self.port_tree.insert(parent, tk.END, text=session.name, image=image, open=True)

        if self.active_session_id in self.sessions:
            tree_id = self.sessions[self.active_session_id].tree_id
            if tree_id:
                self.port_tree.selection_set(tree_id)
                self.port_tree.focus(tree_id)

    def _session_status_image(self, session: ConnectionSession) -> tk.PhotoImage:
        key = "connected" if session.is_connected else "disconnected"
        return self.status_images[key]

    def _update_session_tree_status(self, session: ConnectionSession) -> None:
        if session.tree_id:
            self.port_tree.item(session.tree_id, image=self._session_status_image(session), text=session.name)

    def _on_tree_select(self, _event: tk.Event) -> None:
        selected = self.port_tree.selection()
        if not selected:
            return
        selected_id = selected[0]
        text = self.port_tree.item(selected[0], "text")

        for mode, root_id in self.mode_root_ids.items():
            if selected_id == root_id:
                self.active_session_id = None
                self.mode_var.set(mode)
                self._on_mode_change()
                self._update_counts()
                self._set_connected_state(False)
                self._schedule_config_save()
                return

        session = self._session_by_tree_id(selected_id)
        if session is not None:
            self.active_session_id = session.id
            self.mode_var.set(session.mode)
            self._load_session_config(session)
            self._update_mode_controls()
            self._set_connected_state(session.is_connected)
            self._update_counts()
            self.notebook.tab(0, text=session.name)
            self.status_var.set(f"当前连接：{session.name}")
            self._schedule_config_save()
            return

        if text in NETWORK_MODES:
            self.mode_var.set(text)
            self._on_mode_change()
            self._schedule_config_save()
            return
        match = re.match(r"(COM\d+)", text, flags=re.IGNORECASE)
        if match:
            self.mode_var.set(MODE_SERIAL)
            self._on_mode_change()
            self.port_var.set(match.group(1).upper())
            self._schedule_config_save()

    def _show_connection_context_menu(self, event: tk.Event) -> None:
        tree_id = self.port_tree.identify_row(event.y)
        if not tree_id:
            return

        self.port_tree.selection_set(tree_id)
        self.port_tree.focus(tree_id)
        self._on_tree_select(event)

        menu = tk.Menu(self, tearoff=False)
        mode = self._mode_by_root_id(tree_id)
        session = self._session_by_tree_id(tree_id)

        if mode is not None:
            menu.add_command(label="刷新连接列表", command=self.refresh_ports)
        elif session is not None:
            if session.is_connected:
                label = "关闭串口" if session.mode == MODE_SERIAL else "关闭连接"
                menu.add_command(label=label, command=self.disconnect_current)
            else:
                label = "打开串口" if session.mode == MODE_SERIAL else "打开连接"
                menu.add_command(label=label, command=self.connect_current)
            menu.add_separator()
            menu.add_command(label="删除连接", command=self.delete_current_connection)
        else:
            return

        menu.tk_popup(event.x_root, event.y_root)
        menu.grab_release()

    def _mode_by_root_id(self, tree_id: str) -> str | None:
        for mode, root_id in self.mode_root_ids.items():
            if root_id == tree_id:
                return mode
        return None

    def _session_by_tree_id(self, tree_id: str) -> ConnectionSession | None:
        for session in self.sessions.values():
            if session.tree_id == tree_id:
                return session
        return None

    @property
    def active_session(self) -> ConnectionSession | None:
        if self.active_session_id is None:
            return None
        return self.sessions.get(self.active_session_id)

    def create_connection(self) -> ConnectionSession | None:
        mode = self.mode_var.get()
        if mode == MODE_SERIAL:
            messagebox.showwarning("无需创建", "COM串口不需要创建连接，请选择串口后直接打开。")
            return None
        try:
            config = self._capture_current_config(mode)
        except Exception as exc:
            messagebox.showerror("创建连接失败", str(exc))
            return None

        session = self._new_session(mode, config)
        self._rebuild_connection_tree()
        self._set_connected_state(False)
        self._update_counts()
        self.status_var.set(f"已创建连接：{session.name}")
        self._schedule_config_save()
        return session

    def _new_session(self, mode: str, config: dict[str, str]) -> ConnectionSession:
        session = ConnectionSession(
            id=self.next_session_id,
            mode=mode,
            name=self._unique_session_name(self._session_label(mode, config)),
            config=config,
        )
        self.next_session_id += 1
        self.sessions[session.id] = session
        self.active_session_id = session.id
        return session

    def delete_current_connection(self) -> None:
        session = self.active_session
        if session is None:
            messagebox.showwarning("未选择连接", "请先在连接列表中选择要删除的连接。")
            return
        if session.is_connected:
            self.disconnect_session(session)
        if session.tree_id:
            self.port_tree.delete(session.tree_id)
        self.sessions.pop(session.id, None)
        self.active_session_id = None
        self._update_counts()
        self._set_connected_state(False)
        self.notebook.tab(0, text="串口会话")
        self.status_var.set(f"已删除连接：{session.name}")
        self._schedule_config_save()

    def _capture_current_config(self, mode: str) -> dict[str, str]:
        if mode == MODE_SERIAL:
            config = self._raw_current_config(mode)
            port_name = config.get("port", "")
            if not port_name:
                raise ValueError("请先选择或输入 COM 口，例如 COM3。")
            return config

        if mode in (MODE_TCP_CLIENT, MODE_UDP_CLIENT):
            config = self._raw_current_config(mode)
            remote_host = config.get("remote_host", "")
            remote_port = config.get("remote_port", "")
            if not remote_host:
                raise ValueError("目标IP不能为空")
            if parse_port(remote_port, "目标端口") == 0:
                raise ValueError("目标端口不能为 0")
            return config
        elif mode in (MODE_TCP_SERVER, MODE_UDP_SERVER):
            config = self._raw_current_config(mode)
            local_port = config.get("local_port", "0")
            parse_port(local_port, "本地端口")
            return config
        else:
            raise ValueError("未知连接类型")

    def _load_session_config(self, session: ConnectionSession) -> None:
        config = session.config
        if session.mode == MODE_SERIAL:
            self.port_var.set(config.get("port", ""))
            self.baud_var.set(config.get("baud", "115200"))
            self.data_bits_var.set(config.get("data_bits", "8"))
            self.parity_var.set(config.get("parity", "无"))
            self.stop_bits_var.set(config.get("stop_bits", "1"))
            self.flow_var.set(config.get("flow", "无"))
            self.dtr_var.set(config.get("dtr", "1") == "1")
            self.rts_var.set(config.get("rts", "1") == "1")
        else:
            self.remote_host_var.set(config.get("remote_host", "127.0.0.1"))
            self.remote_port_var.set(config.get("remote_port", "10123"))
            self.local_host_var.set(config.get("local_host", "0.0.0.0"))
            self.local_port_var.set(config.get("local_port", "0"))

    def _session_label(self, mode: str, config: dict[str, str]) -> str:
        if mode == MODE_SERIAL:
            return config.get("port", "")
        if mode in (MODE_TCP_CLIENT, MODE_UDP_CLIENT):
            return f"{config.get('remote_host', '')}:{config.get('remote_port', '')}"
        return f"本机:{config.get('local_port', '')}"

    def _unique_session_name(self, base_name: str) -> str:
        existing = {session.name for session in self.sessions.values()}
        if base_name not in existing:
            return base_name
        index = 2
        while f"{base_name} #{index}" in existing:
            index += 1
        return f"{base_name} #{index}"

    def toggle_connection(self) -> None:
        if self.is_connected:
            self.disconnect_current()
        else:
            self.connect_current()

    @property
    def is_connected(self) -> bool:
        session = self.active_session
        return bool(session and session.is_connected)

    def _connected_sessions(self) -> list[ConnectionSession]:
        return [session for session in self.sessions.values() if session.is_connected]

    def connect_current(self) -> None:
        session = self.active_session
        if session is None:
            if self.mode_var.get() == MODE_SERIAL:
                try:
                    session = self._new_session(MODE_SERIAL, self._capture_current_config(MODE_SERIAL))
                    self._rebuild_connection_tree()
                except Exception as exc:
                    messagebox.showerror("打开串口失败", str(exc))
                    return
            else:
                messagebox.showwarning("未选择连接", "请先点击创建连接按钮，并在连接列表中选择一个连接。")
                return
        if session.is_connected:
            return

        try:
            session.config = self._capture_current_config(session.mode)
            session.name = self._unique_session_name_for_session(session, self._session_label(session.mode, session.config))
            self._update_session_tree_status(session)
            self._schedule_config_save()
        except Exception as exc:
            messagebox.showerror("连接参数错误", str(exc))
            return

        for connected_session in self._connected_sessions():
            if connected_session.id != session.id:
                self.disconnect_session(connected_session)

        if session.mode == MODE_SERIAL:
            self.connect_serial(session)
        else:
            self.connect_network(session)

    def disconnect_current(self) -> None:
        session = self.active_session
        if session is not None:
            self.disconnect_session(session)

    def disconnect_session(self, session: ConnectionSession) -> None:
        if session.mode == MODE_SERIAL:
            self.disconnect_serial(session)
        else:
            self.disconnect_network(session)

    def _unique_session_name_for_session(self, session: ConnectionSession, base_name: str) -> str:
        existing = {item.name for item in self.sessions.values() if item.id != session.id}
        if base_name not in existing:
            return base_name
        index = 2
        while f"{base_name} #{index}" in existing:
            index += 1
        return f"{base_name} #{index}"

    def connect_serial(self, session: ConnectionSession) -> None:
        if session.is_connected:
            return
        if not HAS_PYSERIAL:
            messagebox.showerror("缺少依赖", "请先执行：python -m pip install -r requirements.txt")
            return

        port_name = session.config.get("port", "").strip()
        if not port_name:
            messagebox.showwarning("请选择串口", "请先选择或输入 COM 口，例如 COM3。")
            return

        try:
            flow = session.config.get("flow", "无")
            session.stop_event.clear()
            session.serial_port = serial.Serial(
                port=port_name,
                baudrate=int(session.config.get("baud", "115200")),
                bytesize=int(session.config.get("data_bits", "8")),
                parity=PARITY_OPTIONS.get(session.config.get("parity", "无"), "N"),
                stopbits=float(session.config.get("stop_bits", "1")),
                timeout=0.05,
                write_timeout=2,
                rtscts=flow == "RTS/CTS",
                xonxoff=flow == "XON/XOFF",
                dsrdtr=flow == "DSR/DTR",
            )
            self._apply_line_state(session)
        except Exception as exc:
            session.serial_port = None
            messagebox.showerror("打开串口失败", str(exc))
            self.status_var.set(f"打开 {port_name} 失败")
            return

        thread = threading.Thread(target=self._reader_loop, args=(session,), name="serial-reader", daemon=True)
        session.threads.append(thread)
        thread.start()
        session.name = self._unique_session_name_for_session(session, port_name)
        self._set_connected_state(session.is_connected)
        self._update_session_tree_status(session)
        self.notebook.tab(0, text=session.name)
        self.status_var.set(f"{port_name} 已打开")
        self._on_auto_send_toggle()

    def connect_network(self, session: ConnectionSession) -> None:
        if session.is_connected:
            return

        mode = session.mode
        try:
            session.stop_event.clear()
            session.udp_peer = None
            session.udp_default_peer = None
            if mode == MODE_TCP_CLIENT:
                host, port = self._remote_endpoint(session)
                sock = socket.create_connection((host, port), timeout=5)
                sock.settimeout(0.2)
                session.tcp_socket = sock
                thread = threading.Thread(
                    target=self._tcp_client_reader_loop,
                    args=(session, sock),
                    name="tcp-client-reader",
                    daemon=True,
                )
                session.threads.append(thread)
                thread.start()
                label = f"{host}:{port}"
                status_label = f"TCP客户端 {label}"
            elif mode == MODE_TCP_SERVER:
                host, port = self._local_endpoint(session)
                server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind((host, port))
                server.listen()
                server.settimeout(0.2)
                session.tcp_server_socket = server
                thread = threading.Thread(
                    target=self._tcp_accept_loop,
                    args=(session, server),
                    name="tcp-server-accept",
                    daemon=True,
                )
                session.threads.append(thread)
                thread.start()
                actual_host, actual_port = server.getsockname()
                label = f"本机:{actual_port}"
                status_label = f"TCP服务端 {actual_host}:{actual_port}"
            elif mode == MODE_UDP_CLIENT:
                host, port = self._remote_endpoint(session)
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self._bind_udp_if_needed(session, sock)
                sock.settimeout(0.2)
                session.udp_socket = sock
                session.udp_default_peer = (host, port)
                thread = threading.Thread(
                    target=self._udp_reader_loop,
                    args=(session, sock),
                    name="udp-client-reader",
                    daemon=True,
                )
                session.threads.append(thread)
                thread.start()
                label = f"{host}:{port}"
                status_label = f"UDP客户端 {label}"
            elif mode == MODE_UDP_SERVER:
                host, port = self._local_endpoint(session)
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind((host, port))
                sock.settimeout(0.2)
                session.udp_socket = sock
                thread = threading.Thread(
                    target=self._udp_reader_loop,
                    args=(session, sock),
                    name="udp-server-reader",
                    daemon=True,
                )
                session.threads.append(thread)
                thread.start()
                actual_host, actual_port = sock.getsockname()
                label = f"本机:{actual_port}"
                status_label = f"UDP服务端 {actual_host}:{actual_port}"
            else:
                raise ValueError("未知连接模式")
        except Exception as exc:
            self.disconnect_network(session)
            messagebox.showerror("打开网络连接失败", str(exc))
            self.status_var.set(f"打开 {mode} 失败")
            return

        session.name = self._unique_session_name_for_session(session, label)
        self._set_connected_state(session.is_connected)
        self._update_session_tree_status(session)
        self.notebook.tab(0, text=session.name)
        self.status_var.set(f"{status_label} 已打开")
        self._on_auto_send_toggle()

    def _remote_endpoint(self, session: ConnectionSession | None = None) -> tuple[str, int]:
        config = session.config if session is not None else None
        host = (config.get("remote_host", "") if config else self.remote_host_var.get()).strip()
        if not host:
            raise ValueError("目标IP不能为空")
        port = parse_port(config.get("remote_port", "") if config else self.remote_port_var.get(), "目标端口")
        if port == 0:
            raise ValueError("目标端口不能为 0")
        return host, port

    def _local_endpoint(self, session: ConnectionSession | None = None) -> tuple[str, int]:
        config = session.config if session is not None else None
        host = (config.get("local_host", "") if config else self.local_host_var.get()).strip() or "0.0.0.0"
        port = parse_port(config.get("local_port", "") if config else self.local_port_var.get(), "本地端口")
        return host, port

    def _bind_udp_if_needed(self, session: ConnectionSession, sock: socket.socket) -> None:
        host = session.config.get("local_host", "").strip() or "0.0.0.0"
        port = parse_port(session.config.get("local_port", "0"), "本地端口")
        if port or host not in ("", "0.0.0.0"):
            sock.bind((host, port))

    def disconnect_serial(self, session: ConnectionSession) -> None:
        was_connected = session.is_connected
        if was_connected:
            self.stop_auto_send()
        session.stop_event.set()

        port = session.serial_port
        session.serial_port = None
        if port is not None:
            try:
                if getattr(port, "is_open", False):
                    port.close()
            except Exception:
                pass

        self._join_session_threads(session)
        self._update_session_tree_status(session)

        if session.id == self.active_session_id:
            self._set_connected_state(False)
            self.notebook.tab(0, text=session.name)
        if was_connected:
            self.status_var.set(f"{session.name} 已关闭")

    def disconnect_network(self, session: ConnectionSession) -> None:
        was_connected = session.is_connected
        if was_connected:
            self.stop_auto_send()
        session.stop_event.set()

        for sock_name in ("tcp_socket", "tcp_server_socket", "udp_socket"):
            sock = getattr(session, sock_name)
            setattr(session, sock_name, None)
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

        with session.tcp_clients_lock:
            clients = list(session.tcp_clients)
            session.tcp_clients.clear()
        for client, _addr in clients:
            try:
                client.close()
            except Exception:
                pass

        session.udp_peer = None
        session.udp_default_peer = None
        self._join_session_threads(session)
        self._update_session_tree_status(session)

        if session.id == self.active_session_id:
            self._set_connected_state(False)
            self.notebook.tab(0, text=session.name)
        if was_connected:
            self.status_var.set(f"{session.name} 已关闭")

    def _join_session_threads(self, session: ConnectionSession) -> None:
        current_thread = threading.current_thread()
        for thread in list(session.threads):
            if thread is not current_thread and thread.is_alive():
                thread.join(timeout=0.4)
        session.threads.clear()

    def _tcp_client_reader_loop(self, session: ConnectionSession, sock: socket.socket) -> None:
        while not session.stop_event.is_set():
            try:
                data = sock.recv(4096)
            except socket.timeout:
                continue
            except Exception as exc:
                if not session.stop_event.is_set():
                    self.rx_queue.put(("error", session.id, f"TCP读取失败: {exc}"))
                break

            if not data:
                if not session.stop_event.is_set():
                    self.rx_queue.put(("closed", session.id, "TCP连接已断开"))
                break
            self.rx_queue.put(("data", session.id, data))

    def _tcp_accept_loop(self, session: ConnectionSession, server: socket.socket) -> None:
        while not session.stop_event.is_set():
            try:
                client, addr = server.accept()
            except socket.timeout:
                continue
            except Exception as exc:
                if not session.stop_event.is_set():
                    self.rx_queue.put(("error", session.id, f"TCP服务端监听失败: {exc}"))
                break

            client.settimeout(0.2)
            with session.tcp_clients_lock:
                session.tcp_clients.append((client, addr))
            self.rx_queue.put(("info", session.id, f"TCP客户端已连接 {addr[0]}:{addr[1]}"))
            thread = threading.Thread(
                target=self._tcp_server_client_reader_loop,
                args=(session, client, addr),
                name=f"tcp-client-{addr[0]}:{addr[1]}",
                daemon=True,
            )
            session.threads.append(thread)
            thread.start()

    def _tcp_server_client_reader_loop(
        self, session: ConnectionSession, client: socket.socket, addr: tuple[str, int]
    ) -> None:
        try:
            while not session.stop_event.is_set():
                try:
                    data = client.recv(4096)
                except socket.timeout:
                    continue
                except Exception as exc:
                    if not session.stop_event.is_set():
                        self.rx_queue.put(("info", session.id, f"TCP客户端 {addr[0]}:{addr[1]} 读取失败: {exc}"))
                    break

                if not data:
                    break
                self.rx_queue.put(("data", session.id, data))
        finally:
            with session.tcp_clients_lock:
                session.tcp_clients = [
                    (sock, item_addr) for sock, item_addr in session.tcp_clients if sock is not client
                ]
            try:
                client.close()
            except Exception:
                pass
            if not session.stop_event.is_set():
                self.rx_queue.put(("info", session.id, f"TCP客户端已断开 {addr[0]}:{addr[1]}"))

    def _udp_reader_loop(self, session: ConnectionSession, sock: socket.socket) -> None:
        while not session.stop_event.is_set():
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except Exception as exc:
                if not session.stop_event.is_set():
                    self.rx_queue.put(("error", session.id, f"UDP接收失败: {exc}"))
                break

            if data:
                session.udp_peer = addr
                self.rx_queue.put(("data", session.id, data))

    def _set_connected_state(self, connected: bool) -> None:
        session = self.active_session
        mode = session.mode if session is not None else self.mode_var.get()
        if mode == MODE_SERIAL:
            text = "关闭串口" if connected else "打开串口"
        else:
            text = "关闭连接" if connected else "打开连接"
        self.connect_btn.configure(text=text)
        self._update_connection_buttons()

    def _apply_line_state(self, session: ConnectionSession | None = None) -> None:
        session = session or self.active_session
        if session is None:
            return
        if session.id == self.active_session_id:
            session.config["dtr"] = "1" if self.dtr_var.get() else "0"
            session.config["rts"] = "1" if self.rts_var.get() else "0"
        port = session.serial_port
        if not port or not getattr(port, "is_open", False):
            return
        try:
            port.dtr = session.config.get("dtr", "1") == "1"
            port.rts = session.config.get("rts", "1") == "1"
        except Exception as exc:
            self.status_var.set(f"DTR/RTS 设置失败: {exc}")

    def _reader_loop(self, session: ConnectionSession) -> None:
        port = session.serial_port
        while port is not None and not session.stop_event.is_set():
            try:
                waiting = getattr(port, "in_waiting", 0)
                data = port.read(waiting or 1)
                if data:
                    self.rx_queue.put(("data", session.id, data))
            except Exception as exc:
                if not session.stop_event.is_set():
                    self.rx_queue.put(("error", session.id, str(exc)))
                break

    def _drain_rx_queue(self) -> None:
        try:
            while True:
                kind, session_id, payload = self.rx_queue.get_nowait()
                session = self.sessions.get(session_id)
                if session is None:
                    continue
                if kind == "data":
                    data = payload if isinstance(payload, bytes) else bytes(payload)
                    session.recv_bytes += len(data)
                    self._append_received_data(session, data)
                elif kind == "info":
                    self._append_system_message(session, str(payload))
                elif kind == "closed":
                    self._append_system_message(session, str(payload))
                    self.status_var.set(str(payload))
                    self.disconnect_session(session)
                elif kind == "error":
                    self.status_var.set(f"连接错误: {payload}")
                    messagebox.showerror("连接错误", str(payload))
                    self.disconnect_session(session)
        except queue.Empty:
            pass

        self._update_counts()
        self.after(60, self._drain_rx_queue)

    def _append_received_data(self, session: ConnectionSession, data: bytes) -> None:
        display = self._format_received_data(data)
        if not display:
            return
        timestamp = self._log_timestamp()
        if not self.pause_display_var.get():
            self._append_receive_record(timestamp, session.name, display)
        if self.realtime_save_var.get():
            self._append_realtime_file(self._format_receive_record_for_file(timestamp, session.name, display))

    def _append_system_message(self, session: ConnectionSession, text: str) -> None:
        timestamp = self._log_timestamp()
        if not self.pause_display_var.get():
            self._append_receive_record(timestamp, session.name, text)
        if self.realtime_save_var.get():
            self._append_realtime_file(self._format_receive_record_for_file(timestamp, session.name, text))

    def _append_sent_data(self, session: ConnectionSession, data: bytes) -> None:
        display = self._format_sent_data(data)
        if not display:
            return
        self._append_send_record(self._log_timestamp(), session.name, display)

    def _format_sent_data(self, data: bytes) -> str:
        if self.hex_send_var.get():
            return bytes_to_hex(data)
        encoding = self.encoding_var.get() or "utf-8"
        return data.decode(encoding, errors="replace")

    def _format_received_data(self, data: bytes) -> str:
        if self.hex_recv_var.get():
            return bytes_to_hex(data)

        encoding = self.encoding_var.get() or "utf-8"
        return data.decode(encoding, errors="replace")

    def _log_timestamp(self) -> str:
        now = datetime.now()
        return f"{now:%H:%M:%S}.{now.microsecond // 1000:03d}"

    def _append_send_record(self, timestamp: str, connection: str, data: str) -> None:
        data_text = self._receive_data_with_newline(data)
        row_count = max(1, data_text.count("\n"))
        time_text = self._metadata_column_text(timestamp, row_count)
        connection_text = self._metadata_column_text(connection, row_count)

        self._set_send_widgets_state(tk.NORMAL)
        self.send_time_text.insert(tk.END, time_text)
        self.send_connection_text.insert(tk.END, connection_text)
        self.send_history_text.insert(tk.END, data_text)
        self._set_send_widgets_state(tk.DISABLED)
        for widget in self.send_history_widgets:
            widget.see(tk.END)

    def _append_receive_record(self, timestamp: str, connection: str, data: str) -> None:
        data_text = self._receive_data_with_newline(data)
        row_count = max(1, data_text.count("\n"))
        time_text = self._metadata_column_text(timestamp, row_count)
        connection_text = self._metadata_column_text(connection, row_count)

        self._set_receive_widgets_state(tk.NORMAL)
        self.receive_time_text.insert(tk.END, time_text)
        self.receive_connection_text.insert(tk.END, connection_text)
        self.receive_text.insert(tk.END, data_text)
        self._set_receive_widgets_state(tk.DISABLED)
        for widget in self.receive_widgets:
            widget.see(tk.END)

    def _receive_data_with_newline(self, data: str) -> str:
        return data if data.endswith("\n") else f"{data}\n"

    def _metadata_column_text(self, value: str, row_count: int) -> str:
        lines = [value] + [""] * (row_count - 1)
        return "\n".join(lines) + "\n"

    def _set_receive_widgets_state(self, state: str) -> None:
        for widget in getattr(self, "receive_widgets", ()):
            widget.configure(state=state)

    def _set_send_widgets_state(self, state: str) -> None:
        for widget in getattr(self, "send_history_widgets", ()):
            widget.configure(state=state)

    def _format_receive_record_for_file(self, timestamp: str, connection: str, data: str) -> str:
        data_text = self._receive_data_with_newline(data)
        rows = data_text[:-1].split("\n")
        lines = []
        for index, row in enumerate(rows):
            time_text = timestamp if index == 0 else ""
            connection_text = connection if index == 0 else ""
            lines.append(f"{time_text}\t{connection_text}\t{row}")
        return "\n".join(lines) + "\n"

    def send_now(self, silent: bool = False) -> None:
        session = self.active_session
        if session is None or not session.is_connected:
            if not silent:
                messagebox.showwarning("连接未打开", "请先选择并打开一个连接。")
            return

        try:
            data, crc_appended, should_update_send_area = self._build_send_payload()
        except Exception as exc:
            if not silent:
                messagebox.showerror("发送内容错误", str(exc))
            self.status_var.set("发送内容错误")
            return

        if not data:
            if not silent:
                self.status_var.set("发送内容为空")
            return

        try:
            written, target_text = self._write_payload(session, data)
            if crc_appended and should_update_send_area:
                self._write_full_payload_to_send_area(data)
            self._append_sent_data(session, data)
            session.sent_bytes += written
            self._update_counts()
            crc_text = "，已自动追加CRC" if crc_appended else ""
            self.status_var.set(f"已发送 {written} 字节{target_text}{crc_text}")
        except Exception as exc:
            if not silent:
                messagebox.showerror("发送失败", str(exc))
            self.status_var.set(f"发送失败: {exc}")

    def _write_payload(self, session: ConnectionSession, data: bytes) -> tuple[int, str]:
        if session.serial_port is not None and getattr(session.serial_port, "is_open", False):
            written = int(session.serial_port.write(data))
            return written, ""

        if session.tcp_socket is not None:
            session.tcp_socket.sendall(data)
            return len(data), " 到 TCP 服务端"

        if session.tcp_server_socket is not None:
            with session.tcp_clients_lock:
                clients = list(session.tcp_clients)
            if not clients:
                raise RuntimeError("TCP服务端当前没有已连接客户端")

            sent_total = 0
            failed_clients: list[tuple[socket.socket, tuple[str, int]]] = []
            for client, addr in clients:
                try:
                    client.sendall(data)
                    sent_total += len(data)
                except Exception:
                    failed_clients.append((client, addr))
            if failed_clients:
                self._remove_tcp_clients(session, failed_clients)
            if sent_total == 0:
                raise RuntimeError("TCP客户端发送失败")
            return sent_total, f" 到 {sent_total // len(data)} 个 TCP 客户端"

        if session.udp_socket is not None:
            if session.mode == MODE_UDP_CLIENT:
                peer = session.udp_default_peer
            else:
                peer = session.udp_peer
            if peer is None:
                peer = self._remote_endpoint(session)
            written = session.udp_socket.sendto(data, peer)
            return int(written), f" 到 {peer[0]}:{peer[1]}"

        raise RuntimeError("连接未打开")

    def _remove_tcp_clients(
        self, session: ConnectionSession, clients: list[tuple[socket.socket, tuple[str, int]]]
    ) -> None:
        failed_sockets = {client for client, _addr in clients}
        with session.tcp_clients_lock:
            session.tcp_clients = [
                (client, addr) for client, addr in session.tcp_clients if client not in failed_sockets
            ]
        for client, _addr in clients:
            try:
                client.close()
            except Exception:
                pass

    def _build_send_payload(self) -> tuple[bytes, bool, bool]:
        if self.send_file_var.get():
            path_text = self.send_file_path_var.get().strip()
            if not path_text:
                raise ValueError("请选择要发送的文件")
            path = Path(path_text)
            if not path.is_file():
                raise ValueError("发送文件不存在")
            data = path.read_bytes()
            should_update_send_area = False
        else:
            text = self.send_text.get("1.0", "end-1c")
            if self.hex_send_var.get():
                data = parse_hex_payload(text)
            else:
                if self.send_newline_var.get():
                    text += "\r\n"
                encoding = self.encoding_var.get() or "utf-8"
                data = text.encode(encoding, errors="replace")
            should_update_send_area = True

        data, crc_appended = self._apply_auto_crc(data)
        return data, crc_appended, should_update_send_area

    def _apply_auto_crc(self, data: bytes) -> tuple[bytes, bool]:
        if not self.auto_crc_var.get() or not data:
            return data, False

        algorithm = self.crc_algorithm_var.get() or CRC_ALGORITHM_MODBUS
        if algorithm != CRC_ALGORITHM_MODBUS:
            raise ValueError(f"暂不支持 CRC 算法: {algorithm}")
        return append_crc16_modbus_if_missing(data)

    def _write_full_payload_to_send_area(self, data: bytes) -> None:
        if not self.hex_send_var.get():
            self.hex_send_var.set(True)
        self.send_text.delete("1.0", tk.END)
        self.send_text.insert("1.0", bytes_to_hex(data))
        self._schedule_config_save()

    def _on_auto_send_toggle(self) -> None:
        if self.auto_send_var.get() and self.is_connected:
            self._schedule_auto_send()
        else:
            self._cancel_auto_send_job()

    def _schedule_auto_send(self) -> None:
        self._cancel_auto_send_job()
        try:
            interval = max(10, int(self.interval_var.get()))
        except ValueError:
            interval = 1000
            self.interval_var.set("1000")
        self.auto_send_job = self.after(interval, self._auto_send_tick)

    def _auto_send_tick(self) -> None:
        self.auto_send_job = None
        if not self.auto_send_var.get() or not self.is_connected:
            return
        self.send_now(silent=True)
        self._schedule_auto_send()

    def _cancel_auto_send_job(self) -> None:
        if self.auto_send_job is not None:
            self.after_cancel(self.auto_send_job)
            self.auto_send_job = None

    def stop_auto_send(self) -> None:
        self.auto_send_var.set(False)
        self._cancel_auto_send_job()

    def _toggle_file_send_controls(self) -> None:
        state = tk.NORMAL if self.send_file_var.get() else tk.DISABLED
        self.send_file_entry.configure(state=state)
        self.send_file_btn.configure(state=state)

    def _toggle_crc_controls(self) -> None:
        if hasattr(self, "crc_combo"):
            state = "readonly" if self.auto_crc_var.get() else tk.DISABLED
            self.crc_combo.configure(state=state)

    def choose_send_file(self) -> None:
        path = filedialog.askopenfilename(title="选择要发送的文件")
        if path:
            self.send_file_path_var.set(path)

    def choose_realtime_file(self) -> None:
        path = filedialog.asksaveasfilename(
            title="选择实时保存文件",
            defaultextension=".log",
            filetypes=(("日志文件", "*.log"), ("文本文件", "*.txt"), ("所有文件", "*.*")),
        )
        if path:
            self.realtime_path_var.set(path)

    def _on_realtime_save_toggle(self) -> None:
        if not self.realtime_save_var.get():
            return
        if not self.realtime_path_var.get().strip():
            self.choose_realtime_file()
        if not self.realtime_path_var.get().strip():
            self.realtime_save_var.set(False)
            return
        try:
            Path(self.realtime_path_var.get()).parent.mkdir(parents=True, exist_ok=True)
            with open(self.realtime_path_var.get(), "a", encoding="utf-8"):
                pass
            self.status_var.set("实时保存已开启")
        except Exception as exc:
            self.realtime_save_var.set(False)
            messagebox.showerror("实时保存失败", str(exc))

    def _append_realtime_file(self, text: str) -> None:
        path = self.realtime_path_var.get().strip()
        if not path:
            self.realtime_save_var.set(False)
            return
        try:
            with open(path, "a", encoding="utf-8", newline="") as file:
                file.write(text)
        except Exception as exc:
            self.realtime_save_var.set(False)
            self.status_var.set(f"实时保存失败: {exc}")

    def clear_send(self) -> None:
        self.send_text.delete("1.0", tk.END)
        self._set_send_widgets_state(tk.NORMAL)
        for widget in self.send_history_widgets:
            widget.delete("1.0", tk.END)
        self._set_send_widgets_state(tk.DISABLED)

    def clear_receive(self) -> None:
        self._set_receive_widgets_state(tk.NORMAL)
        for widget in self.receive_widgets:
            widget.delete("1.0", tk.END)
        self._set_receive_widgets_state(tk.DISABLED)

    def clear_counts(self) -> None:
        session = self.active_session
        targets = [session] if session is not None else list(self.sessions.values())
        for item in targets:
            item.sent_bytes = 0
            item.recv_bytes = 0
            item.sent_last = 0
            item.recv_last = 0
        self.sent_bytes = 0
        self.recv_bytes = 0
        self.sent_last = 0
        self.recv_last = 0
        self._update_counts()
        self.speed_var.set("发送速度(B/S): 0    接收速度(B/S): 0")

    def save_receive(self) -> None:
        path = filedialog.asksaveasfilename(
            title="保存接收区",
            defaultextension=".txt",
            filetypes=(("文本文件", "*.txt"), ("日志文件", "*.log"), ("所有文件", "*.*")),
        )
        if not path:
            return
        try:
            Path(path).write_text(self._receive_log_text(), encoding="utf-8")
            self.status_var.set(f"已保存到 {path}")
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))

    def _receive_log_text(self) -> str:
        time_lines = self.receive_time_text.get("1.0", "end-1c").split("\n")
        connection_lines = self.receive_connection_text.get("1.0", "end-1c").split("\n")
        data_lines = self.receive_text.get("1.0", "end-1c").split("\n")
        row_count = max(len(time_lines), len(connection_lines), len(data_lines))
        lines: list[str] = []
        for index in range(row_count):
            time_text = time_lines[index] if index < len(time_lines) else ""
            connection_text = connection_lines[index] if index < len(connection_lines) else ""
            data_text = data_lines[index] if index < len(data_lines) else ""
            if time_text or connection_text or data_text:
                lines.append(f"{time_text}\t{connection_text}\t{data_text}".rstrip())
        return "\n".join(lines)

    def _update_counts(self) -> None:
        session = self.active_session
        if session is not None:
            self.count_var.set(f"发送: {session.sent_bytes} 字节    接收: {session.recv_bytes} 字节")
            return
        sent_total = sum(item.sent_bytes for item in self.sessions.values())
        recv_total = sum(item.recv_bytes for item in self.sessions.values())
        self.count_var.set(f"发送: {sent_total} 字节    接收: {recv_total} 字节")

    def _update_speed(self) -> None:
        session = self.active_session
        if session is not None:
            tx_speed = session.sent_bytes - session.sent_last
            rx_speed = session.recv_bytes - session.recv_last
            session.sent_last = session.sent_bytes
            session.recv_last = session.recv_bytes
        else:
            sent_total = sum(item.sent_bytes for item in self.sessions.values())
            recv_total = sum(item.recv_bytes for item in self.sessions.values())
            tx_speed = sent_total - self.sent_last
            rx_speed = recv_total - self.recv_last
            self.sent_last = sent_total
            self.recv_last = recv_total
        self.speed_var.set(f"发送速度(B/S): {tx_speed}    接收速度(B/S): {rx_speed}")
        self.after(1000, self._update_speed)

    def show_about(self) -> None:
        messagebox.showinfo(
            "关于",
            f"{APP_NAME}\n版本: v{APP_VERSION}\n\n"
            "支持本机 COM 串口、TCP客户端、TCP服务端、UDP客户端、UDP服务端，"
            "可进行文本/16进制发送、自动发送、接收显示、实时保存和收发计数。",
        )

    def on_close(self) -> None:
        if self._config_save_after_id is not None:
            self.after_cancel(self._config_save_after_id)
            self._save_config_now()
        for session in list(self.sessions.values()):
            if session.is_connected:
                self.disconnect_session(session)
        self.destroy()


def main() -> None:
    try:
        from qt_app import run
    except ImportError as exc:
        messagebox.showerror("缺少运行依赖", f"请先执行：python -m pip install -r requirements.txt\n\n{exc}")
        return
    run()


if __name__ == "__main__":
    main()
