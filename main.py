from __future__ import annotations

import queue
import re
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import serial
    from serial.tools import list_ports

    HAS_PYSERIAL = True
except ImportError:
    serial = None
    list_ports = None
    HAS_PYSERIAL = False


APP_TITLE = "本地COM串口调试工具"
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


def bytes_to_hex(data: bytes) -> str:
    return " ".join(f"{value:02X}" for value in data)


class SerialDebugTool(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1180x760")
        self.minsize(980, 620)

        self.serial_port = None
        self.reader_thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.rx_queue: queue.Queue[tuple[str, bytes | str]] = queue.Queue()
        self.auto_send_job: str | None = None

        self.sent_bytes = 0
        self.recv_bytes = 0
        self.sent_last = 0
        self.recv_last = 0

        self.port_var = tk.StringVar()
        self.baud_var = tk.StringVar(value="115200")
        self.data_bits_var = tk.StringVar(value="8")
        self.parity_var = tk.StringVar(value="无")
        self.stop_bits_var = tk.StringVar(value="1")
        self.flow_var = tk.StringVar(value="无")
        self.encoding_var = tk.StringVar(value="utf-8")
        self.dtr_var = tk.BooleanVar(value=True)
        self.rts_var = tk.BooleanVar(value=True)

        self.hex_send_var = tk.BooleanVar(value=False)
        self.hex_recv_var = tk.BooleanVar(value=True)
        self.send_newline_var = tk.BooleanVar(value=False)
        self.auto_send_var = tk.BooleanVar(value=False)
        self.interval_var = tk.StringVar(value="1000")
        self.send_file_var = tk.BooleanVar(value=False)
        self.send_file_path_var = tk.StringVar()

        self.pause_display_var = tk.BooleanVar(value=False)
        self.timestamp_var = tk.BooleanVar(value=False)
        self.realtime_save_var = tk.BooleanVar(value=False)
        self.realtime_path_var = tk.StringVar()

        self.status_var = tk.StringVar(value="就绪")
        self.count_var = tk.StringVar(value="发送: 0 字节    接收: 0 字节")
        self.speed_var = tk.StringVar(value="发送速度(B/S): 0    接收速度(B/S): 0")

        self._build_style()
        self._build_menu()
        self._build_toolbar()
        self._build_body()
        self._build_status_bar()

        self.refresh_ports()
        self._set_connected_state(False)
        self.after(60, self._drain_rx_queue)
        self.after(1000, self._update_speed)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Toolbar.TFrame", background="#f2f2f2")
        style.configure("Status.TLabel", padding=(8, 2))
        style.configure("Pane.TLabelframe", padding=6)
        style.configure("Small.TButton", padding=(8, 2))

    def _build_menu(self) -> None:
        menu_bar = tk.Menu(self)

        action_menu = tk.Menu(menu_bar, tearoff=False)
        action_menu.add_command(label="刷新串口", command=self.refresh_ports)
        action_menu.add_command(label="打开串口", command=self.connect_serial)
        action_menu.add_command(label="关闭串口", command=self.disconnect_serial)
        action_menu.add_separator()
        action_menu.add_command(label="退出", command=self.on_close)
        menu_bar.add_cascade(label="操作(O)", menu=action_menu)

        view_menu = tk.Menu(menu_bar, tearoff=False)
        view_menu.add_command(label="清空发送区", command=self.clear_send)
        view_menu.add_command(label="清空接收区", command=self.clear_receive)
        view_menu.add_command(label="清空计数", command=self.clear_counts)
        menu_bar.add_cascade(label="查看(V)", menu=view_menu)

        window_menu = tk.Menu(menu_bar, tearoff=False)
        window_menu.add_command(label="恢复默认大小", command=lambda: self.geometry("1180x760"))
        menu_bar.add_cascade(label="窗口(W)", menu=window_menu)

        help_menu = tk.Menu(menu_bar, tearoff=False)
        help_menu.add_command(label="关于", command=self.show_about)
        menu_bar.add_cascade(label="帮助(H)", menu=help_menu)

        self.config(menu=menu_bar)

    def _build_toolbar(self) -> None:
        toolbar = ttk.Frame(self, style="Toolbar.TFrame", padding=(6, 4))
        toolbar.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(toolbar, text="刷新串口", style="Small.TButton", command=self.refresh_ports).pack(
            side=tk.LEFT, padx=(0, 4)
        )
        self.toolbar_connect_btn = ttk.Button(
            toolbar, text="打开串口", style="Small.TButton", command=self.toggle_connection
        )
        self.toolbar_connect_btn.pack(side=tk.LEFT, padx=(0, 4))
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Button(toolbar, text="清空接收", style="Small.TButton", command=self.clear_receive).pack(
            side=tk.LEFT, padx=(0, 4)
        )
        ttk.Button(toolbar, text="保存接收", style="Small.TButton", command=self.save_receive).pack(
            side=tk.LEFT, padx=(0, 4)
        )
        ttk.Button(toolbar, text="清空计数", style="Small.TButton", command=self.clear_counts).pack(
            side=tk.LEFT, padx=(0, 4)
        )

    def _build_body(self) -> None:
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(paned, width=250, padding=(6, 4))
        right = ttk.Frame(paned, padding=(4, 4))
        paned.add(left, weight=0)
        paned.add(right, weight=1)

        self._build_left_panel(left)
        self._build_workspace(right)

    def _build_left_panel(self, parent: ttk.Frame) -> None:
        port_box = ttk.LabelFrame(parent, text="串口列表", style="Pane.TLabelframe")
        port_box.pack(fill=tk.BOTH, expand=False)

        self.port_tree = ttk.Treeview(port_box, show="tree", height=8)
        self.port_tree.pack(fill=tk.BOTH, expand=True)
        self.port_tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        config_box = ttk.LabelFrame(parent, text="串口参数", style="Pane.TLabelframe")
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

        self.connect_btn = ttk.Button(config_box, text="打开串口", command=self.toggle_connection)
        self.connect_btn.grid(row=8, column=0, columnspan=2, sticky=tk.EW, pady=(8, 0))

        count_box = ttk.LabelFrame(parent, text="计数", style="Pane.TLabelframe")
        count_box.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(count_box, textvariable=self.count_var, justify=tk.LEFT).pack(anchor=tk.W)
        ttk.Button(count_box, text="清空计数", command=self.clear_counts).pack(anchor=tk.W, pady=(6, 0))

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

    def _build_workspace(self, parent: ttk.Frame) -> None:
        self.notebook = ttk.Notebook(parent)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        session = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(session, text="串口会话")
        session.rowconfigure(2, weight=2)
        session.rowconfigure(6, weight=3)
        session.columnconfigure(0, weight=1)

        self._build_send_area(session)
        ttk.Separator(session, orient=tk.HORIZONTAL).grid(row=3, column=0, sticky=tk.EW, pady=(6, 6))
        self._build_receive_area(session)

    def _build_send_area(self, parent: ttk.Frame) -> None:
        send_toolbar = ttk.Frame(parent)
        send_toolbar.grid(row=0, column=0, sticky=tk.EW)
        send_toolbar.columnconfigure(12, weight=1)

        ttk.Label(send_toolbar, text="发送区").grid(row=0, column=0, padx=(0, 8))
        ttk.Checkbutton(send_toolbar, text="16进制", variable=self.hex_send_var).grid(row=0, column=1, padx=2)
        ttk.Checkbutton(send_toolbar, text="追加CRLF", variable=self.send_newline_var).grid(
            row=0, column=2, padx=2
        )
        ttk.Checkbutton(
            send_toolbar,
            text="发送文件",
            variable=self.send_file_var,
            command=self._toggle_file_send_controls,
        ).grid(row=0, column=3, padx=2)
        ttk.Checkbutton(
            send_toolbar,
            text="自动发送",
            variable=self.auto_send_var,
            command=self._on_auto_send_toggle,
        ).grid(row=0, column=4, padx=2)
        ttk.Label(send_toolbar, text="间隔").grid(row=0, column=5, padx=(8, 2))
        ttk.Entry(send_toolbar, textvariable=self.interval_var, width=7).grid(row=0, column=6)
        ttk.Label(send_toolbar, text="ms").grid(row=0, column=7, padx=(2, 8))
        ttk.Button(send_toolbar, text="发送", command=self.send_now, width=8).grid(row=0, column=8, padx=2)
        ttk.Button(send_toolbar, text="停止", command=self.stop_auto_send, width=8).grid(row=0, column=9, padx=2)
        ttk.Button(send_toolbar, text="清空", command=self.clear_send, width=8).grid(row=0, column=10, padx=2)

        file_bar = ttk.Frame(parent)
        file_bar.grid(row=1, column=0, sticky=tk.EW, pady=(5, 4))
        file_bar.columnconfigure(1, weight=1)
        ttk.Label(file_bar, text="文件:").grid(row=0, column=0, sticky=tk.W)
        self.send_file_entry = ttk.Entry(file_bar, textvariable=self.send_file_path_var)
        self.send_file_entry.grid(row=0, column=1, sticky=tk.EW, padx=(4, 4))
        self.send_file_btn = ttk.Button(file_bar, text="...", width=4, command=self.choose_send_file)
        self.send_file_btn.grid(row=0, column=2)

        self.send_text = tk.Text(parent, height=8, wrap=tk.NONE, undo=True)
        self.send_text.grid(row=2, column=0, sticky=tk.NSEW, pady=(4, 0))
        self.send_text.insert("1.0", "4E 57 00 13 00 00 00 00 06 02 00 00 00 00 00 00 68 00 00 01 28")

        self._toggle_file_send_controls()

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
        ttk.Checkbutton(receive_toolbar, text="时间戳", variable=self.timestamp_var).grid(row=0, column=5, padx=2)

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
        receive_frame.rowconfigure(0, weight=1)
        receive_frame.columnconfigure(0, weight=1)

        self.receive_text = tk.Text(receive_frame, wrap=tk.NONE)
        self.receive_text.grid(row=0, column=0, sticky=tk.NSEW)
        y_scroll = ttk.Scrollbar(receive_frame, orient=tk.VERTICAL, command=self.receive_text.yview)
        x_scroll = ttk.Scrollbar(receive_frame, orient=tk.HORIZONTAL, command=self.receive_text.xview)
        y_scroll.grid(row=0, column=1, sticky=tk.NS)
        x_scroll.grid(row=1, column=0, sticky=tk.EW)
        self.receive_text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

    def _build_status_bar(self) -> None:
        status = ttk.Frame(self)
        status.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(status, textvariable=self.status_var, style="Status.TLabel").pack(side=tk.LEFT)
        ttk.Label(status, textvariable=self.speed_var, style="Status.TLabel").pack(side=tk.RIGHT)

    def refresh_ports(self) -> None:
        self.port_tree.delete(*self.port_tree.get_children())
        root_id = self.port_tree.insert("", tk.END, text="本机COM串口", open=True)

        if not HAS_PYSERIAL:
            self.port_combo.configure(values=())
            self.port_tree.insert(root_id, tk.END, text="未安装 pyserial")
            self.status_var.set("缺少 pyserial，请先执行：python -m pip install -r requirements.txt")
            return

        ports = list(list_ports.comports())
        values: list[str] = []
        for item in ports:
            label = f"{item.device} - {item.description}"
            self.port_tree.insert(root_id, tk.END, text=label, values=(item.device,))
            values.append(item.device)

        self.port_combo.configure(values=values)
        if values and (not self.port_var.get() or self.port_var.get() not in values):
            self.port_var.set(values[0])
        if not values:
            self.port_tree.insert(root_id, tk.END, text="未发现串口")
            self.status_var.set("未发现本机 COM 串口")
        else:
            self.status_var.set(f"发现 {len(values)} 个串口")

    def _on_tree_select(self, _event: tk.Event) -> None:
        selected = self.port_tree.selection()
        if not selected:
            return
        text = self.port_tree.item(selected[0], "text")
        match = re.match(r"(COM\d+)", text, flags=re.IGNORECASE)
        if match:
            self.port_var.set(match.group(1).upper())

    def toggle_connection(self) -> None:
        if self.is_connected:
            self.disconnect_serial()
        else:
            self.connect_serial()

    @property
    def is_connected(self) -> bool:
        return bool(self.serial_port and getattr(self.serial_port, "is_open", False))

    def connect_serial(self) -> None:
        if self.is_connected:
            return
        if not HAS_PYSERIAL:
            messagebox.showerror("缺少依赖", "请先执行：python -m pip install -r requirements.txt")
            return

        port_name = self.port_var.get().strip()
        if not port_name:
            messagebox.showwarning("请选择串口", "请先选择或输入 COM 口，例如 COM3。")
            return

        try:
            flow = self.flow_var.get()
            self.stop_event.clear()
            self.serial_port = serial.Serial(
                port=port_name,
                baudrate=int(self.baud_var.get()),
                bytesize=int(self.data_bits_var.get()),
                parity=PARITY_OPTIONS.get(self.parity_var.get(), "N"),
                stopbits=float(self.stop_bits_var.get()),
                timeout=0.05,
                write_timeout=2,
                rtscts=flow == "RTS/CTS",
                xonxoff=flow == "XON/XOFF",
                dsrdtr=flow == "DSR/DTR",
            )
            self._apply_line_state()
        except Exception as exc:
            self.serial_port = None
            messagebox.showerror("打开串口失败", str(exc))
            self.status_var.set(f"打开 {port_name} 失败")
            return

        self.reader_thread = threading.Thread(target=self._reader_loop, name="serial-reader", daemon=True)
        self.reader_thread.start()
        self._set_connected_state(True)
        self.notebook.tab(0, text=port_name)
        self.status_var.set(f"{port_name} 已打开")
        self._on_auto_send_toggle()

    def disconnect_serial(self) -> None:
        was_connected = self.is_connected
        self.stop_auto_send()
        self.stop_event.set()

        port = self.serial_port
        self.serial_port = None
        if port is not None:
            try:
                if getattr(port, "is_open", False):
                    port.close()
            except Exception:
                pass

        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=0.4)
        self.reader_thread = None

        self._set_connected_state(False)
        self.notebook.tab(0, text="串口会话")
        if was_connected:
            self.status_var.set("串口已关闭")

    def _set_connected_state(self, connected: bool) -> None:
        text = "关闭串口" if connected else "打开串口"
        self.connect_btn.configure(text=text)
        self.toolbar_connect_btn.configure(text=text)

    def _apply_line_state(self) -> None:
        port = self.serial_port
        if not port or not getattr(port, "is_open", False):
            return
        try:
            port.dtr = self.dtr_var.get()
            port.rts = self.rts_var.get()
        except Exception as exc:
            self.status_var.set(f"DTR/RTS 设置失败: {exc}")

    def _reader_loop(self) -> None:
        port = self.serial_port
        while port is not None and not self.stop_event.is_set():
            try:
                waiting = getattr(port, "in_waiting", 0)
                data = port.read(waiting or 1)
                if data:
                    self.rx_queue.put(("data", data))
            except Exception as exc:
                if not self.stop_event.is_set():
                    self.rx_queue.put(("error", str(exc)))
                break

    def _drain_rx_queue(self) -> None:
        try:
            while True:
                kind, payload = self.rx_queue.get_nowait()
                if kind == "data":
                    data = payload if isinstance(payload, bytes) else bytes(payload)
                    self.recv_bytes += len(data)
                    self._append_received_data(data)
                elif kind == "error":
                    self.status_var.set(f"串口读取错误: {payload}")
                    messagebox.showerror("串口读取错误", str(payload))
                    self.disconnect_serial()
        except queue.Empty:
            pass

        self._update_counts()
        self.after(60, self._drain_rx_queue)

    def _append_received_data(self, data: bytes) -> None:
        display = self._format_received_data(data)
        if display and not self.pause_display_var.get():
            self.receive_text.insert(tk.END, display)
            self.receive_text.see(tk.END)
        if display and self.realtime_save_var.get():
            self._append_realtime_file(display)

    def _format_received_data(self, data: bytes) -> str:
        prefix = ""
        if self.timestamp_var.get():
            now = datetime.now()
            prefix = f"[{now:%H:%M:%S}.{now.microsecond // 1000:03d}] "

        if self.hex_recv_var.get():
            text = bytes_to_hex(data)
            return f"{prefix}{text}\n" if text else ""

        encoding = self.encoding_var.get() or "utf-8"
        text = data.decode(encoding, errors="replace")
        return f"{prefix}{text}" if prefix else text

    def send_now(self, silent: bool = False) -> None:
        if not self.is_connected:
            if not silent:
                messagebox.showwarning("串口未打开", "请先打开串口。")
            return

        try:
            data = self._build_send_payload()
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
            written = self.serial_port.write(data)
            self.sent_bytes += int(written)
            self._update_counts()
            self.status_var.set(f"已发送 {written} 字节")
        except Exception as exc:
            if not silent:
                messagebox.showerror("发送失败", str(exc))
            self.status_var.set(f"发送失败: {exc}")

    def _build_send_payload(self) -> bytes:
        if self.send_file_var.get():
            path_text = self.send_file_path_var.get().strip()
            if not path_text:
                raise ValueError("请选择要发送的文件")
            path = Path(path_text)
            if not path.is_file():
                raise ValueError("发送文件不存在")
            return path.read_bytes()

        text = self.send_text.get("1.0", "end-1c")
        if self.hex_send_var.get():
            return parse_hex_payload(text)

        if self.send_newline_var.get():
            text += "\r\n"
        encoding = self.encoding_var.get() or "utf-8"
        return text.encode(encoding, errors="replace")

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

    def clear_receive(self) -> None:
        self.receive_text.delete("1.0", tk.END)

    def clear_counts(self) -> None:
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
            Path(path).write_text(self.receive_text.get("1.0", "end-1c"), encoding="utf-8")
            self.status_var.set(f"已保存到 {path}")
        except Exception as exc:
            messagebox.showerror("保存失败", str(exc))

    def _update_counts(self) -> None:
        self.count_var.set(f"发送: {self.sent_bytes} 字节    接收: {self.recv_bytes} 字节")

    def _update_speed(self) -> None:
        tx_speed = self.sent_bytes - self.sent_last
        rx_speed = self.recv_bytes - self.recv_last
        self.sent_last = self.sent_bytes
        self.recv_last = self.recv_bytes
        self.speed_var.set(f"发送速度(B/S): {tx_speed}    接收速度(B/S): {rx_speed}")
        self.after(1000, self._update_speed)

    def show_about(self) -> None:
        messagebox.showinfo(
            "关于",
            "本地COM串口调试工具\n\n"
            "支持本机 COM 串口枚举、串口参数配置、文本/16进制发送、"
            "自动发送、接收显示、实时保存和收发计数。",
        )

    def on_close(self) -> None:
        self.disconnect_serial()
        self.destroy()


def main() -> None:
    app = SerialDebugTool()
    app.mainloop()


if __name__ == "__main__":
    main()
