from __future__ import annotations

import html
import json
import queue
import re
import socket
import threading
from collections import deque
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStyle,
    QStyleOptionButton,
    QTabBar,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from main import (
    APP_ICON_PATH,
    APP_NAME,
    APP_TITLE,
    APP_VERSION,
    BAUD_RATES,
    CONFIG_SCHEMA_VERSION,
    CONNECTION_MODES,
    CRC_ALGORITHM_MODBUS,
    CRC_ALGORITHMS,
    ENCODINGS,
    FLOW_OPTIONS,
    HAS_PYSERIAL,
    MODE_SERIAL,
    MODE_TCP_CLIENT,
    MODE_TCP_SERVER,
    MODE_UDP_CLIENT,
    MODE_UDP_SERVER,
    NETWORK_MODES,
    PARITY_OPTIONS,
    ConnectionSession,
    append_crc16_modbus_if_missing,
    app_config_path,
    bytes_to_hex,
    config_bool,
    list_ports,
    parse_hex_payload,
    parse_port,
    resource_path,
    serial,
)


DEFAULT_GEOMETRY = "1500x840"
DEFAULT_LEFT_PANEL_WIDTH = 360
THEME = {
    "bg": "#0B1220",
    "header": "#0D1626",
    "card": "#101827",
    "card_alt": "#121C2C",
    "input": "#0D1625",
    "table": "#0A1424",
    "table_header": "#0E192A",
    "border": "#26344A",
    "border_soft": "#1C293B",
    "text": "#E6EDF7",
    "muted": "#9AA8BC",
    "disabled": "#5F6B7A",
    "accent": "#2F6BFF",
    "accent_hover": "#3D78FF",
    "accent_pressed": "#2459D6",
    "accent_soft": "#173B88",
    "accent_faint": "#10264F",
    "success": "#22C55E",
    "warning": "#F59E0B",
    "error": "#EF4444",
    "disconnected": "#64748B",
}


class TickCheckBox(QCheckBox):
    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().paintEvent(event)
        if not self.isChecked():
            return
        option = QStyleOptionButton()
        self.initStyleOption(option)
        rect = self.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, self)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(
            QPen(
                QColor("#FFFFFF"),
                1.8,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        painter.drawLine(rect.left() + 4, rect.center().y(), rect.center().x() - 1, rect.bottom() - 4)
        painter.drawLine(rect.center().x() - 1, rect.bottom() - 4, rect.right() - 3, rect.top() + 4)
        painter.end()


class SelectableLogEdit(QTextEdit):
    HEADERS = ("发送时间", "发送内容", "接收时间", "连接", "接收数据")
    COLUMN_WIDTHS = ("13%", "25%", "13%", "18%", "31%")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.records: list[tuple[str, str, str, str, str]] = []
        self.setReadOnly(True)
        self.setAcceptRichText(True)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        data_font = QFont("Consolas")
        data_font.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(data_font)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_copy_menu)
        self._render()

    def append_record(self, sent_timestamp: str, sent_data: str, recv_timestamp: str, connection: str, data: str) -> None:
        self.records.append((sent_timestamp, sent_data, recv_timestamp, connection, data))
        self._render(scroll_to_bottom=True)

    def set_records(self, records: list[tuple[str, str, str, str, str]]) -> None:
        self.records = list(records)
        self._render()

    def record_snapshot(self) -> list[tuple[str, str, str, str, str]]:
        return list(self.records)

    def clear(self) -> None:  # type: ignore[override]
        self.records.clear()
        self._render()

    def _show_copy_menu(self, point: QPoint) -> None:
        if not self.textCursor().hasSelection():
            return
        menu = QMenu(self)
        menu.addAction("复制", self.copy)
        menu.exec(self.viewport().mapToGlobal(point))

    def to_log_text(self) -> str:
        lines = ["\t".join(self.HEADERS)]
        lines.extend("\t".join(record).rstrip() for record in self.records)
        return "\n".join(lines)

    def _render(self, *, scroll_to_bottom: bool = False) -> None:
        header_html = "".join(
            f'<th width="{width}">{self._cell_text(title)}</th>'
            for title, width in zip(self.HEADERS, self.COLUMN_WIDTHS)
        )
        if self.records:
            rows_html = "\n".join(
                "<tr>"
                + "".join(
                    f'<td width="{width}">{self._cell_text(value)}</td>'
                    for value, width in zip(record, self.COLUMN_WIDTHS)
                )
                + "</tr>"
                for record in self.records
            )
        else:
            rows_html = '<tr><td class="empty" colspan="5">暂无数据</td></tr>'
        colgroup_html = "".join(f'<col width="{width}">' for width in self.COLUMN_WIDTHS)
        self.setHtml(
            f"""
            <!doctype html>
            <html>
            <head>
            <style>
            body {{
                margin: 0;
                background: {THEME["table"]};
                color: {THEME["text"]};
                font-family: "Microsoft YaHei UI", "Segoe UI", Consolas, monospace;
                font-size: 13px;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
            }}
            th {{
                background: {THEME["table_header"]};
                color: {THEME["text"]};
                font-weight: 600;
                padding: 5px 6px;
                border-right: 1px solid {THEME["border_soft"]};
                border-bottom: 1px solid {THEME["border_soft"]};
                white-space: nowrap;
            }}
            td {{
                color: {THEME["text"]};
                padding: 4px 6px;
                border-right: 1px solid {THEME["border_soft"]};
                border-bottom: 1px solid {THEME["border_soft"]};
                vertical-align: top;
                white-space: pre-wrap;
            }}
            td.empty {{
                color: {THEME["disabled"]};
                text-align: center;
                padding: 70px 0;
                border-right: 0;
            }}
            </style>
            </head>
            <body>
            <table width="100%">
                <colgroup>{colgroup_html}</colgroup>
                <thead><tr>{header_html}</tr></thead>
                <tbody>{rows_html}</tbody>
            </table>
            </body>
            </html>
            """
        )
        if scroll_to_bottom:
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.setTextCursor(cursor)
            self.ensureCursorVisible()

    def _cell_text(self, value: str) -> str:
        return html.escape(value).replace("\n", "<br>")


class SerialDebugQtTool(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(1500, 840)
        self.setMinimumSize(1180, 700)
        self._set_window_icon()

        self.sessions: dict[int, ConnectionSession] = {}
        self.mode_root_items: dict[str, QTreeWidgetItem] = {}
        self.session_items: dict[int, QTreeWidgetItem] = {}
        self.pending_send_records: dict[int, deque[tuple[str, str]]] = {}
        self.next_session_id = 1
        self.active_session_id: int | None = None
        self.mode = MODE_SERIAL
        self.rx_queue: queue.Queue[tuple[str, int, bytes | str]] = queue.Queue()
        self.config_path = app_config_path()
        self._loading_config = True
        self._switching_session = False
        self._syncing_session_tabs = False

        self.sent_last = 0
        self.recv_last = 0
        self.auto_send_timer = QTimer(self)
        self.auto_send_timer.timeout.connect(lambda: self.send_now(silent=True))
        self.rx_timer = QTimer(self)
        self.rx_timer.timeout.connect(self._drain_rx_queue)
        self.speed_timer = QTimer(self)
        self.speed_timer.timeout.connect(self._update_speed)
        self.config_save_timer = QTimer(self)
        self.config_save_timer.setSingleShot(True)
        self.config_save_timer.timeout.connect(self._save_config_now)

        self.status_icons = {
            "connected": self._dot_icon(THEME["success"]),
            "disconnected": self._dot_icon(THEME["disconnected"]),
            "session_idle": self._dot_icon(THEME["accent"]),
            "mode": self._mode_icon(),
        }

        self._build_ui()
        self.refresh_ports()
        self._load_config_on_start()
        self._bind_config_signals()
        self._loading_config = False
        self._set_connected_state(False)
        self.rx_timer.start(60)
        self.speed_timer.start(1000)

    def _set_window_icon(self) -> None:
        icon_path = resource_path(APP_ICON_PATH)
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

    def _dot_icon(self, color: str) -> QIcon:
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(color))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(3, 3, 10, 10)
        painter.end()
        return QIcon(pixmap)

    def _mode_icon(self) -> QIcon:
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(THEME["muted"]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(3, 3, 10, 8, 2, 2)
        painter.drawRect(6, 12, 4, 2)
        painter.end()
        return QIcon(pixmap)

    def _build_ui(self) -> None:
        self.setStyleSheet(self._style_sheet())
        root = QWidget()
        root.setObjectName("Root")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._build_header())

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setObjectName("BodySplitter")
        body.addWidget(self._build_left_panel())
        body.addWidget(self._build_workspace())
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setSizes((DEFAULT_LEFT_PANEL_WIDTH, 1140))
        layout.addWidget(body, 1)

        layout.addWidget(self._build_status_bar())
        self.setCentralWidget(root)

    def _style_sheet(self) -> str:
        return f"""
        * {{
            color: {THEME["text"]};
            font-family: "Microsoft YaHei UI", "Segoe UI";
            font-size: 13px;
        }}
        QWidget {{
            background: {THEME["bg"]};
        }}
        QWidget#Root {{
            background: {THEME["bg"]};
        }}
        QWidget#SidebarContent {{
            background: transparent;
        }}
        QScrollArea {{
            background: transparent;
            border: 0;
        }}
        QScrollArea > QWidget > QWidget {{
            background: transparent;
        }}
        QLabel, QCheckBox {{
            background: transparent;
        }}
        QWidget#Header {{
            background: {THEME["header"]};
            border-bottom: 1px solid {THEME["border_soft"]};
        }}
        QLabel#SectionTitle {{
            font-size: 14px;
            font-weight: 700;
            color: {THEME["text"]};
        }}
        QLabel#FieldLabel {{
            color: {THEME["muted"]};
            font-size: 12px;
        }}
        QMenuBar {{
            background: {THEME["header"]};
            color: {THEME["muted"]};
            spacing: 8px;
            padding: 0;
        }}
        QMenuBar::item {{
            background: transparent;
            padding: 6px 10px;
        }}
        QMenuBar::item:selected {{
            color: {THEME["text"]};
            background: {THEME["card_alt"]};
            border-radius: 6px;
        }}
        QMenu {{
            background: {THEME["card"]};
            color: {THEME["text"]};
            border: 1px solid {THEME["border"]};
            padding: 6px;
        }}
        QMenu::item {{
            padding: 7px 28px 7px 12px;
            border-radius: 5px;
        }}
        QMenu::item:selected {{
            background: {THEME["accent"]};
            color: white;
        }}
        QFrame#Card {{
            background: {THEME["card"]};
            border: 1px solid {THEME["border"]};
            border-radius: 8px;
        }}
        QWidget#FieldStack {{
            background: transparent;
        }}
        QLineEdit, QComboBox, QSpinBox, QTextEdit {{
            background: {THEME["input"]};
            color: {THEME["text"]};
            border: 1px solid {THEME["border"]};
            border-radius: 5px;
            min-height: 17px;
            padding: 2px 6px;
            selection-background-color: {THEME["accent"]};
        }}
        QComboBox, QSpinBox {{
            min-width: 74px;
        }}
        QTextEdit {{
            padding: 6px;
        }}
        QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus {{
            border-color: {THEME["accent"]};
        }}
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
            color: {THEME["disabled"]};
            background: {THEME["card_alt"]};
        }}
        QComboBox::drop-down, QSpinBox::up-button, QSpinBox::down-button {{
            border: 0;
            width: 18px;
        }}
        QComboBox QAbstractItemView {{
            background: {THEME["card"]};
            color: {THEME["text"]};
            border: 1px solid {THEME["border"]};
            selection-background-color: {THEME["accent"]};
            outline: 0;
        }}
        QPushButton {{
            background: {THEME["card_alt"]};
            color: {THEME["text"]};
            border: 1px solid {THEME["border"]};
            border-radius: 6px;
            padding: 3px 10px;
            min-height: 17px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background: #17243A;
            border-color: {THEME["accent_hover"]};
        }}
        QPushButton:pressed {{
            background: {THEME["accent_pressed"]};
        }}
        QPushButton:disabled {{
            color: {THEME["disabled"]};
            background: {THEME["input"]};
            border-color: {THEME["border_soft"]};
        }}
        QPushButton[primary="true"] {{
            background: {THEME["accent"]};
            color: white;
            border-color: {THEME["accent"]};
            font-weight: 700;
        }}
        QPushButton[primary="true"]:hover {{
            background: {THEME["accent_hover"]};
        }}
        QPushButton#ActionButton {{
            min-height: 22px;
            font-size: 14px;
            font-weight: 700;
        }}
        QPushButton#SendButton {{
            min-width: 82px;
            min-height: 21px;
        }}
        QPushButton#IconButton {{
            min-width: 32px;
            padding-left: 7px;
            padding-right: 7px;
        }}
        QCheckBox {{
            spacing: 6px;
            color: {THEME["text"]};
        }}
        QCheckBox::indicator {{
            width: 14px;
            height: 14px;
            border: 1px solid {THEME["border"]};
            border-radius: 4px;
            background: {THEME["input"]};
        }}
        QCheckBox::indicator:checked {{
            background: {THEME["accent"]};
            border-color: {THEME["accent"]};
        }}
        QTreeWidget {{
            background: {THEME["table"]};
            color: {THEME["text"]};
            border: 1px solid {THEME["border"]};
            border-radius: 6px;
            outline: 0;
            alternate-background-color: {THEME["table"]};
        }}
        QTreeWidget {{
            padding: 6px;
        }}
        QTreeWidget::item {{
            min-height: 26px;
            padding: 2px 6px;
            border-radius: 5px;
        }}
        QTreeWidget::item:selected {{
            background: {THEME["accent_soft"]};
            color: white;
        }}
        QTreeWidget::item:hover:!selected {{
            background: {THEME["accent_faint"]};
        }}
        QTabBar {{
            background: transparent;
        }}
        QTabBar::tab {{
            background: {THEME["card_alt"]};
            color: {THEME["muted"]};
            border: 1px solid {THEME["border"]};
            border-bottom: 0;
            border-top-left-radius: 6px;
            border-top-right-radius: 6px;
            padding: 7px 16px;
            margin-right: 4px;
        }}
        QTabBar::tab:selected {{
            background: {THEME["accent_soft"]};
            color: {THEME["text"]};
        }}
        QSplitter::handle {{
            background: {THEME["border_soft"]};
        }}
        QSplitter::handle:horizontal {{
            width: 1px;
        }}
        QSplitter::handle:vertical {{
            height: 1px;
        }}
        QScrollBar:vertical, QScrollBar:horizontal {{
            background: {THEME["input"]};
            border: 0;
            margin: 0;
        }}
        QScrollBar:vertical {{
            width: 12px;
        }}
        QScrollBar:horizontal {{
            height: 12px;
        }}
        QScrollBar::handle {{
            background: #32425A;
            border-radius: 5px;
        }}
        QScrollBar::handle:hover {{
            background: {THEME["accent_hover"]};
        }}
        QScrollBar::add-line, QScrollBar::sub-line {{
            width: 0;
            height: 0;
        }}
        QWidget#StatusBar {{
            background: {THEME["card"]};
            border-top: 1px solid {THEME["border"]};
        }}
        QLabel[muted="true"] {{
            color: {THEME["muted"]};
        }}
        QToolTip {{
            background: {THEME["card"]};
            color: {THEME["text"]};
            border: 1px solid {THEME["border"]};
            padding: 6px;
        }}
        """

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("Header")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(0)

        menu_bar = QMenuBar()
        menu_bar.addMenu(self._make_action_menu())
        menu_bar.addMenu(self._make_view_menu())
        menu_bar.addMenu(self._make_config_menu())
        menu_bar.addMenu(self._make_window_menu())
        menu_bar.addMenu(self._make_help_menu())
        layout.addWidget(menu_bar)
        layout.addStretch(1)
        return header

    def _make_action_menu(self) -> QMenu:
        menu = QMenu("操作(O)", self)
        menu.addAction("刷新连接列表", self.refresh_ports)
        menu.addAction("创建连接", self.create_connection)
        menu.addAction("删除连接", self.delete_current_connection)
        menu.addAction("打开连接", self.connect_current)
        menu.addAction("关闭连接", self.disconnect_current)
        menu.addSeparator()
        menu.addAction("退出", self.close)
        return menu

    def _make_view_menu(self) -> QMenu:
        menu = QMenu("查看(V)", self)
        menu.addAction("清空发送区", self.clear_send)
        menu.addAction("清空接收区", self.clear_receive)
        menu.addAction("清空计数", self.clear_counts)
        return menu

    def _make_config_menu(self) -> QMenu:
        menu = QMenu("配置(C)", self)
        menu.addAction("导入配置", self.import_config)
        menu.addAction("导出配置", self.export_config)
        menu.addSeparator()
        menu.addAction("清除配置", self.clear_config)
        return menu

    def _make_window_menu(self) -> QMenu:
        menu = QMenu("窗口(W)", self)
        menu.addAction("恢复默认大小", lambda: self.resize(1500, 840))
        return menu

    def _make_help_menu(self) -> QMenu:
        menu = QMenu("帮助(H)", self)
        menu.addAction("关于", self.show_about)
        return menu

    def _card(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        layout.addWidget(self._section_label(title))
        return card, layout

    def _field_stack(self, label_text: str, widget: QWidget) -> QWidget:
        field = QWidget()
        field.setObjectName("FieldStack")
        layout = QVBoxLayout(field)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(label_text)
        label.setObjectName("FieldLabel")
        layout.addWidget(label)
        layout.addWidget(widget)
        return field

    def _build_left_panel(self) -> QWidget:
        outer = QWidget()
        outer.setFixedWidth(DEFAULT_LEFT_PANEL_WIDTH)
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        panel = QWidget()
        panel.setObjectName("SidebarContent")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        list_box, list_layout = self._card("连接列表")
        list_box.setMinimumHeight(210)
        self.connection_tree = QTreeWidget()
        self.connection_tree.setHeaderHidden(True)
        self.connection_tree.setIndentation(18)
        self.connection_tree.setRootIsDecorated(True)
        self.connection_tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.connection_tree.itemSelectionChanged.connect(self._on_tree_select)
        self.connection_tree.itemDoubleClicked.connect(lambda _item, _col: self.toggle_connection())
        self.connection_tree.customContextMenuRequested.connect(self._show_connection_context_menu)
        list_layout.addWidget(self.connection_tree)
        layout.addWidget(list_box, 1)

        self.serial_box, serial_body = self._card("串口参数")
        self.port_combo = QComboBox()
        self.port_combo.setEditable(True)
        refresh_btn = self._button("刷新")
        refresh_btn.setFixedWidth(68)
        refresh_btn.clicked.connect(self.refresh_ports)
        port_label = QLabel("串口")
        port_label.setObjectName("FieldLabel")
        port_row = QHBoxLayout()
        port_row.setContentsMargins(0, 0, 0, 0)
        port_row.setSpacing(8)
        port_row.addWidget(self.port_combo, 1)
        port_row.addWidget(refresh_btn)
        serial_body.addWidget(port_label)
        serial_body.addLayout(port_row)

        serial_grid = QGridLayout()
        serial_grid.setContentsMargins(0, 0, 0, 0)
        serial_grid.setHorizontalSpacing(10)
        serial_grid.setVerticalSpacing(6)
        serial_grid.setColumnStretch(0, 1)
        serial_grid.setColumnStretch(1, 1)
        serial_grid.setColumnStretch(2, 1)
        self.baud_combo = self._combo(BAUD_RATES, "115200")
        self.data_bits_combo = self._combo(("5", "6", "7", "8"), "8")
        self.parity_combo = self._combo(tuple(PARITY_OPTIONS), "无")
        self.stop_bits_combo = self._combo(("1", "1.5", "2"), "1")
        self.flow_combo = self._combo(FLOW_OPTIONS, "无")
        self.encoding_combo = self._combo(ENCODINGS, "utf-8")
        serial_grid.addWidget(self._field_stack("波特率", self.baud_combo), 0, 0)
        serial_grid.addWidget(self._field_stack("数据位", self.data_bits_combo), 0, 1)
        serial_grid.addWidget(self._field_stack("校验", self.parity_combo), 0, 2)
        serial_grid.addWidget(self._field_stack("停止位", self.stop_bits_combo), 1, 0)
        serial_grid.addWidget(self._field_stack("流控", self.flow_combo), 1, 1)
        serial_grid.addWidget(self._field_stack("编码", self.encoding_combo), 1, 2)
        serial_body.addLayout(serial_grid)

        line_row = QHBoxLayout()
        line_row.setContentsMargins(0, 0, 0, 0)
        line_row.setSpacing(14)
        self.dtr_check = TickCheckBox("DTR")
        self.dtr_check.setChecked(True)
        self.rts_check = TickCheckBox("RTS")
        self.rts_check.setChecked(True)
        line_row.addWidget(self.dtr_check)
        line_row.addWidget(self.rts_check)
        line_row.addStretch(1)
        serial_body.addLayout(line_row)
        layout.addWidget(self.serial_box)

        self.network_box, network_body = self._card("网络参数")
        network_layout = QGridLayout()
        network_layout.setContentsMargins(0, 0, 0, 0)
        network_layout.setVerticalSpacing(8)
        network_layout.setHorizontalSpacing(10)
        network_layout.setColumnStretch(0, 1)
        network_layout.setColumnStretch(1, 1)
        self.remote_host_edit = QLineEdit("127.0.0.1")
        self.remote_port_edit = QLineEdit("10123")
        self.local_port_edit = QLineEdit("10123")
        remote_host_field = self._field_stack("目标IP", self.remote_host_edit)
        remote_port_field = self._field_stack("目标端口", self.remote_port_edit)
        local_port_field = self._field_stack("本地端口", self.local_port_edit)
        network_layout.addWidget(remote_host_field, 0, 0)
        network_layout.addWidget(remote_port_field, 0, 1)
        network_layout.addWidget(local_port_field, 1, 0, 1, 2)
        self.network_rows = {
            "remote_host": (remote_host_field, self.remote_host_edit),
            "remote_port": (remote_port_field, self.remote_port_edit),
            "local_port": (local_port_field, self.local_port_edit),
        }
        self.network_layout = network_layout
        network_body.addLayout(network_layout)
        layout.addWidget(self.network_box)

        action_box, action_layout = self._card("连接操作")
        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        self.create_btn = self._button("创建连接", primary=True)
        self.create_btn.setObjectName("ActionButton")
        self.create_btn.clicked.connect(self.create_connection)
        self.connect_btn = self._button("打开连接", primary=True)
        self.connect_btn.setObjectName("ActionButton")
        self.connect_btn.clicked.connect(self.toggle_connection)
        button_row.addWidget(self.create_btn, 1)
        button_row.addWidget(self.connect_btn, 1)
        action_layout.addLayout(button_row)
        layout.addWidget(action_box)

        count_box, count_layout = self._card("计数")
        self.count_label = QLabel("发送: 0 字节    接收: 0 字节")
        self.count_label.setProperty("muted", True)
        clear_count_btn = self._button("清空计数")
        clear_count_btn.clicked.connect(self.clear_counts)
        count_row = QHBoxLayout()
        count_row.setContentsMargins(0, 0, 0, 0)
        count_row.setSpacing(8)
        count_row.addWidget(self.count_label, 1)
        count_row.addWidget(clear_count_btn)
        count_layout.addLayout(count_row)
        layout.addWidget(count_box)
        self._update_mode_controls()

        scroll.setWidget(panel)
        outer_layout.addWidget(scroll)
        return outer

    def _build_workspace(self) -> QWidget:
        workspace = QWidget()
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(8, 8, 10, 8)
        layout.setSpacing(6)

        self.session_tabs = QTabBar()
        self.session_tabs.setDocumentMode(True)
        self.session_tabs.setTabsClosable(True)
        self.session_tabs.setExpanding(False)
        self.session_tabs.setUsesScrollButtons(True)
        self.session_tabs.currentChanged.connect(self._on_session_tab_changed)
        self.session_tabs.tabCloseRequested.connect(self._close_session_tab)
        layout.addWidget(self.session_tabs)

        session = QWidget()
        session_layout = QVBoxLayout(session)
        session_layout.setContentsMargins(8, 8, 8, 8)
        session_layout.setSpacing(8)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._build_send_card())
        splitter.addWidget(self._build_receive_card())
        splitter.setChildrenCollapsible(False)
        splitter.setSizes((300, 500))
        session_layout.addWidget(splitter)
        layout.addWidget(session, 1)
        return workspace

    def _build_send_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 8, 14, 9)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(7)
        header.addWidget(self._section_label("发送区"))
        self.hex_send_check = TickCheckBox("16进制")
        self.append_crlf_check = TickCheckBox("追加CRLF")
        self.auto_crc_check = TickCheckBox("自动CRC")
        self.crc_combo = self._combo(CRC_ALGORITHMS, CRC_ALGORITHM_MODBUS)
        self.crc_combo.setFixedWidth(158)
        header.addWidget(self.hex_send_check)
        header.addWidget(self.append_crlf_check)
        header.addWidget(self.auto_crc_check)
        header.addWidget(self.crc_combo)
        header.addStretch(1)
        self.send_btn = self._button("发送", primary=True)
        self.send_btn.setObjectName("SendButton")
        self.stop_btn = self._button("停止")
        self.clear_send_btn = self._button("清空")
        self.stop_btn.setMinimumWidth(62)
        self.clear_send_btn.setMinimumWidth(62)
        self.send_btn.clicked.connect(self.send_now)
        self.stop_btn.clicked.connect(self.stop_auto_send)
        self.clear_send_btn.clicked.connect(self.clear_send)
        header.addWidget(self.send_btn)
        header.addWidget(self.stop_btn)
        header.addWidget(self.clear_send_btn)
        layout.addLayout(header)

        file_row = QHBoxLayout()
        file_row.setContentsMargins(0, 0, 0, 0)
        file_row.setSpacing(5)
        self.send_file_check = TickCheckBox("发送文件")
        self.auto_send_check = TickCheckBox("自动发送")
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(10, 999999)
        self.interval_spin.setValue(1000)
        self.interval_spin.setFixedWidth(88)
        self.interval_spin.setSuffix("")
        file_row.addWidget(self.send_file_check)
        file_row.addWidget(self.auto_send_check)
        file_row.addWidget(QLabel("间隔"))
        file_row.addWidget(self.interval_spin)
        file_row.addWidget(QLabel("ms"))
        file_row.addSpacing(2)
        file_label = QLabel("文件:")
        file_label.setProperty("muted", True)
        file_row.addWidget(file_label)
        self.send_file_edit = QLineEdit()
        self.send_file_btn = self._button("...")
        self.send_file_btn.setObjectName("IconButton")
        self.send_file_btn.clicked.connect(self.choose_send_file)
        file_row.addWidget(self.send_file_edit, 1)
        file_row.addWidget(self.send_file_btn)
        layout.addLayout(file_row)

        input_label = QLabel("发送内容")
        input_label.setProperty("muted", True)
        layout.addWidget(input_label)
        self.send_edit = QTextEdit()
        self.send_edit.setPlaceholderText("在此输入要发送的数据...")
        self.send_edit.setMinimumHeight(126)
        self.send_edit.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.send_edit.customContextMenuRequested.connect(self._show_send_context_menu)
        layout.addWidget(self.send_edit, 1)
        self._toggle_file_send_controls()
        self._toggle_crc_controls()
        return card

    def _build_receive_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 8, 14, 9)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(7)
        header.addWidget(self._section_label("接收区"))
        header.addStretch(1)
        self.pause_display_check = TickCheckBox("暂停显示")
        self.hex_recv_check = TickCheckBox("16进制")
        clear_btn = self._button("清空")
        save_btn = self._button("保存")
        clear_btn.setMinimumWidth(62)
        save_btn.setMinimumWidth(62)
        clear_btn.clicked.connect(self.clear_receive)
        save_btn.clicked.connect(self.save_receive)
        header.addWidget(self.pause_display_check)
        header.addWidget(self.hex_recv_check)
        header.addWidget(clear_btn)
        header.addWidget(save_btn)
        layout.addLayout(header)

        realtime_row = QHBoxLayout()
        realtime_row.setContentsMargins(0, 0, 0, 0)
        realtime_row.setSpacing(5)
        self.realtime_save_check = TickCheckBox("保存到文件(实时)")
        self.realtime_edit = QLineEdit()
        realtime_btn = self._button("...")
        realtime_btn.setObjectName("IconButton")
        realtime_btn.clicked.connect(self.choose_realtime_file)
        realtime_row.addWidget(self.realtime_save_check)
        realtime_row.addWidget(self.realtime_edit, 1)
        realtime_row.addWidget(realtime_btn)
        layout.addLayout(realtime_row)

        self.receive_log = SelectableLogEdit()
        self.receive_log.setMinimumHeight(200)
        layout.addWidget(self.receive_log, 1)
        return card

    def _build_status_bar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("StatusBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(18, 5, 18, 5)
        layout.setSpacing(12)
        self.status_dot = QLabel()
        self.status_dot.setPixmap(self.status_icons["disconnected"].pixmap(14, 14))
        self.connection_state_label = QLabel("已断开")
        self.connection_address_label = QLabel(MODE_SERIAL)
        self.connection_address_label.setProperty("muted", True)
        self.status_message_label = QLabel("就绪")
        self.status_message_label.setProperty("muted", True)
        self.status_count_label = QLabel("发送: 0 字节    接收: 0 字节")
        self.status_count_label.setProperty("muted", True)
        self.speed_label = QLabel("发送速度(B/S): 0    接收速度(B/S): 0")
        self.speed_label.setProperty("muted", True)
        layout.addWidget(self.status_dot)
        layout.addWidget(self.connection_state_label)
        layout.addWidget(self.connection_address_label)
        layout.addWidget(self.status_message_label, 1)
        layout.addWidget(self.status_count_label)
        layout.addWidget(self.speed_label)
        return bar

    def _button(self, text: str, *, primary: bool = False, icon: QIcon | None = None) -> QPushButton:
        button = QPushButton(text)
        if icon is not None:
            button.setIcon(icon)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if primary:
            button.setProperty("primary", True)
        return button

    def _combo(self, values: tuple[str, ...], current: str) -> QComboBox:
        combo = QComboBox()
        combo.addItems(values)
        combo.setCurrentText(current)
        return combo

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("SectionTitle")
        return label

    def _show_send_context_menu(self, point: QPoint) -> None:
        menu = QMenu(self.send_edit)
        document = self.send_edit.document()
        cursor = self.send_edit.textCursor()
        undo_action = menu.addAction("撤销", self.send_edit.undo)
        undo_action.setEnabled(document.isUndoAvailable())
        redo_action = menu.addAction("恢复", self.send_edit.redo)
        redo_action.setEnabled(document.isRedoAvailable())
        menu.addSeparator()
        cut_action = menu.addAction("剪切", self.send_edit.cut)
        copy_action = menu.addAction("复制", self.send_edit.copy)
        delete_action = menu.addAction("删除", lambda: self.send_edit.textCursor().removeSelectedText())
        has_selection = cursor.hasSelection()
        cut_action.setEnabled(has_selection)
        copy_action.setEnabled(has_selection)
        delete_action.setEnabled(has_selection)
        paste_action = menu.addAction("粘贴", self.send_edit.paste)
        paste_action.setEnabled(bool(QApplication.clipboard().text()))
        menu.addSeparator()
        select_all_action = menu.addAction("全选", self.send_edit.selectAll)
        select_all_action.setEnabled(bool(self.send_edit.toPlainText()))
        menu.exec(self.send_edit.viewport().mapToGlobal(point))

    def _bind_config_signals(self) -> None:
        widgets = (
            self.port_combo,
            self.baud_combo,
            self.data_bits_combo,
            self.parity_combo,
            self.stop_bits_combo,
            self.flow_combo,
            self.encoding_combo,
            self.remote_host_edit,
            self.remote_port_edit,
            self.local_port_edit,
            self.hex_send_check,
            self.hex_recv_check,
            self.append_crlf_check,
            self.auto_crc_check,
            self.auto_send_check,
            self.interval_spin,
            self.send_file_check,
            self.send_file_edit,
            self.pause_display_check,
            self.realtime_save_check,
            self.realtime_edit,
            self.dtr_check,
            self.rts_check,
        )
        for widget in widgets:
            if isinstance(widget, QComboBox):
                widget.currentTextChanged.connect(self._on_config_changed)
            elif isinstance(widget, QLineEdit):
                widget.textChanged.connect(self._on_config_changed)
            elif isinstance(widget, QCheckBox):
                widget.toggled.connect(self._on_config_changed)
            elif isinstance(widget, QSpinBox):
                widget.valueChanged.connect(self._on_config_changed)
        self.auto_crc_check.toggled.connect(self._toggle_crc_controls)
        self.send_file_check.toggled.connect(self._toggle_file_send_controls)
        self.auto_send_check.toggled.connect(self._on_auto_send_toggle)
        self.interval_spin.valueChanged.connect(lambda _value: self._restart_auto_send_if_needed())
        self.realtime_save_check.toggled.connect(self._on_realtime_save_toggle)
        self.dtr_check.toggled.connect(lambda _checked: self._apply_line_state())
        self.rts_check.toggled.connect(lambda _checked: self._apply_line_state())

    def _on_config_changed(self, *_args: object) -> None:
        if self._loading_config or self._switching_session:
            return
        self._sync_active_session_from_controls()
        self._schedule_config_save()

    def _schedule_config_save(self) -> None:
        if not self._loading_config:
            self.config_save_timer.start(200)

    def _save_config_now(self) -> None:
        if self._loading_config:
            return
        try:
            self._sync_active_session_from_controls()
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.config_path.with_suffix(".json.tmp")
            temp_path.write_text(json.dumps(self._collect_config(), ensure_ascii=False, indent=2), encoding="utf-8")
            temp_path.replace(self.config_path)
        except Exception as exc:
            self._set_status(f"配置保存失败: {exc}")

    def _collect_config(self) -> dict[str, object]:
        return {
            "schema_version": CONFIG_SCHEMA_VERSION,
            "app_version": APP_VERSION,
            "window": {"geometry": f"{self.width()}x{self.height()}"},
            "mode": self.mode,
            "active_session_id": self.active_session_id,
            "serial": self._raw_current_config(MODE_SERIAL),
            "network": {
                "remote_host": self.remote_host_edit.text(),
                "remote_port": self.remote_port_edit.text(),
                "local_host": "0.0.0.0",
                "local_port": self.local_port_edit.text(),
            },
            "send": {
                "hex_send": self.hex_send_check.isChecked(),
                "append_crlf": self.append_crlf_check.isChecked(),
                "auto_crc": self.auto_crc_check.isChecked(),
                "crc_algorithm": self.crc_combo.currentText(),
                "auto_send": self.auto_send_check.isChecked(),
                "interval": str(self.interval_spin.value()),
                "send_file": self.send_file_check.isChecked(),
                "send_file_path": self.send_file_edit.text(),
            },
            "receive": {
                "hex_recv": self.hex_recv_check.isChecked(),
                "pause_display": self.pause_display_check.isChecked(),
                "realtime_save": self.realtime_save_check.isChecked(),
                "realtime_path": self.realtime_edit.text(),
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
            "network": {"remote_host": "127.0.0.1", "remote_port": "10123", "local_host": "0.0.0.0", "local_port": "10123"},
            "send": {"hex_send": False, "append_crlf": False, "auto_crc": False, "crc_algorithm": CRC_ALGORITHM_MODBUS, "auto_send": False, "interval": "1000", "send_file": False, "send_file_path": ""},
            "receive": {"hex_recv": False, "pause_display": False, "realtime_save": False, "realtime_path": ""},
            "connections": [],
        }

    def _load_config_on_start(self) -> None:
        if not self.config_path.exists():
            return
        try:
            data = json.loads(self.config_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("配置文件格式错误")
            self._apply_config(data)
            self._set_status(f"已加载配置: {self.config_path}")
        except Exception as exc:
            self._set_status(f"配置加载失败: {exc}")

    def _apply_config(self, data: dict[str, object]) -> None:
        self._loading_config = True
        try:
            geometry = self._section(data, "window").get("geometry")
            if isinstance(geometry, str):
                match = re.match(r"(\d+)x(\d+)", geometry)
                if match:
                    self.resize(int(match.group(1)), int(match.group(2)))

            serial_config = self._section(data, "serial")
            self.port_combo.setCurrentText(str(serial_config.get("port", "")))
            self.baud_combo.setCurrentText(str(serial_config.get("baud", "115200")))
            self.data_bits_combo.setCurrentText(str(serial_config.get("data_bits", "8")))
            self.parity_combo.setCurrentText(str(serial_config.get("parity", "无")))
            self.stop_bits_combo.setCurrentText(str(serial_config.get("stop_bits", "1")))
            self.flow_combo.setCurrentText(str(serial_config.get("flow", "无")))
            self.encoding_combo.setCurrentText(str(serial_config.get("encoding", "utf-8")))
            self.dtr_check.setChecked(config_bool(serial_config.get("dtr"), True))
            self.rts_check.setChecked(config_bool(serial_config.get("rts"), True))

            network_config = self._section(data, "network")
            self.remote_host_edit.setText(str(network_config.get("remote_host", "127.0.0.1")))
            self.remote_port_edit.setText(str(network_config.get("remote_port", "10123")))
            self.local_port_edit.setText(str(network_config.get("local_port", "10123")))

            send_config = self._section(data, "send")
            self.hex_send_check.setChecked(config_bool(send_config.get("hex_send"), False))
            self.append_crlf_check.setChecked(config_bool(send_config.get("append_crlf"), False))
            self.auto_crc_check.setChecked(config_bool(send_config.get("auto_crc"), False))
            self.crc_combo.setCurrentText(str(send_config.get("crc_algorithm", CRC_ALGORITHM_MODBUS)))
            self.auto_send_check.setChecked(config_bool(send_config.get("auto_send"), False))
            self.interval_spin.setValue(self._safe_int(send_config.get("interval"), 1000))
            self.send_file_check.setChecked(config_bool(send_config.get("send_file"), False))
            self.send_file_edit.setText(str(send_config.get("send_file_path", "")))

            receive_config = self._section(data, "receive")
            self.hex_recv_check.setChecked(config_bool(receive_config.get("hex_recv"), False))
            self.pause_display_check.setChecked(config_bool(receive_config.get("pause_display"), False))
            self.realtime_save_check.setChecked(config_bool(receive_config.get("realtime_save"), False))
            self.realtime_edit.setText(str(receive_config.get("realtime_path", "")))

            self.sessions.clear()
            self.pending_send_records.clear()
            self.next_session_id = 1
            self.active_session_id = None
            self._load_configured_sessions(data)

            mode = data.get("mode")
            self.mode = str(mode) if mode in CONNECTION_MODES else MODE_SERIAL
            active_session_id = self._safe_int(data.get("active_session_id"), -1)
            if active_session_id in self.sessions:
                self.active_session_id = active_session_id

            self._rebuild_connection_tree()
            if self.active_session_id in self.sessions:
                session = self.sessions[self.active_session_id]
                self.mode = session.mode
                self._load_session_config(session)
            self._refresh_session_tabs()
            self._update_mode_controls()
            self._toggle_file_send_controls()
            self._toggle_crc_controls()
            self._set_connected_state(False)
            self._update_counts()
        finally:
            self._loading_config = False

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
            normalized = self._normalize_session_config(str(mode), config if isinstance(config, dict) else {})
            session_id = self._safe_int(item.get("id"), self.next_session_id)
            if session_id in used_ids:
                session_id = self.next_session_id
            used_ids.add(session_id)
            self.next_session_id = max(self.next_session_id, session_id + 1)
            raw_name = item.get("name")
            base_name = str(raw_name) if raw_name else self._session_label(str(mode), normalized)
            self.sessions[session_id] = ConnectionSession(
                id=session_id,
                mode=str(mode),
                name=self._unique_session_name(base_name),
                config=normalized,
            )

    def _section(self, data: dict[str, object], key: str) -> dict[str, object]:
        value = data.get(key)
        return value if isinstance(value, dict) else {}

    def _safe_int(self, value: object, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def refresh_ports(self) -> None:
        values: list[str] = []
        if not HAS_PYSERIAL:
            self._set_status("缺少 pyserial，串口不可用；网络模式仍可使用")
        else:
            values = [item.device for item in list_ports.comports()]
            self._set_status("未发现本机 COM 串口" if not values else f"发现 {len(values)} 个串口")
        current = self.port_combo.currentText()
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        self.port_combo.addItems(values)
        self.port_combo.setCurrentText(current or (values[0] if values else ""))
        self.port_combo.blockSignals(False)
        self._rebuild_connection_tree()

    def _rebuild_connection_tree(self) -> None:
        self.connection_tree.clear()
        self.mode_root_items.clear()
        self.session_items.clear()
        for mode in CONNECTION_MODES:
            item = QTreeWidgetItem([mode])
            item.setIcon(0, self.status_icons["mode"])
            item.setData(0, Qt.ItemDataRole.UserRole, {"kind": "mode", "mode": mode})
            self.connection_tree.addTopLevelItem(item)
            item.setExpanded(True)
            self.mode_root_items[mode] = item
        for session in sorted(self.sessions.values(), key=lambda item: item.id):
            parent = self.mode_root_items[session.mode]
            item = QTreeWidgetItem([session.name])
            item.setIcon(0, self._session_status_icon(session))
            item.setData(0, Qt.ItemDataRole.UserRole, {"kind": "session", "id": session.id})
            parent.addChild(item)
            self.session_items[session.id] = item
        if self.active_session_id in self.session_items:
            self.connection_tree.setCurrentItem(self.session_items[self.active_session_id])
        self._refresh_session_tabs()

    def _refresh_session_tabs(self) -> None:
        if not hasattr(self, "session_tabs"):
            return
        self._syncing_session_tabs = True
        try:
            while self.session_tabs.count():
                self.session_tabs.removeTab(0)
            active_index = -1
            visible_sessions = [session for session in sorted(self.sessions.values(), key=lambda item: item.id) if session.tab_open]
            for session in visible_sessions:
                index = self.session_tabs.addTab(session.name)
                self.session_tabs.setTabData(index, session.id)
                self.session_tabs.setTabToolTip(index, session.name)
                if session.id == self.active_session_id:
                    active_index = index
            self.session_tabs.setVisible(bool(visible_sessions))
            if active_index >= 0:
                self.session_tabs.setCurrentIndex(active_index)
        finally:
            self._syncing_session_tabs = False

    def _update_session_tab(self, session: ConnectionSession) -> None:
        if not hasattr(self, "session_tabs"):
            return
        if not session.tab_open:
            self._refresh_session_tabs()
            return
        for index in range(self.session_tabs.count()):
            if self.session_tabs.tabData(index) == session.id:
                self.session_tabs.setTabText(index, session.name)
                self.session_tabs.setTabToolTip(index, session.name)
                return
        self._refresh_session_tabs()

    def _on_session_tab_changed(self, index: int) -> None:
        if self._syncing_session_tabs or index < 0:
            return
        session_id = self.session_tabs.tabData(index)
        session = self.sessions.get(int(session_id)) if session_id is not None else None
        if session is not None:
            self._select_session(session, sync_tabs=False)

    def _close_session_tab(self, index: int) -> None:
        session_id = self.session_tabs.tabData(index)
        session = self.sessions.get(int(session_id)) if session_id is not None else None
        if session is not None:
            if session.is_connected:
                self.disconnect_session(session)
            session.tab_open = False
            was_active = self.active_session_id == session.id
            self._refresh_session_tabs()
            if was_active:
                replacement = self._first_tab_session()
                if replacement is not None:
                    self._select_session(replacement)
                else:
                    self._select_session(session)
            self._set_status(f"已断开：{session.name}")
            self._schedule_config_save()

    def _first_tab_session(self) -> ConnectionSession | None:
        return next((session for session in sorted(self.sessions.values(), key=lambda item: item.id) if session.tab_open), None)

    def _session_status_icon(self, session: ConnectionSession) -> QIcon:
        return self.status_icons["connected" if session.is_connected else "session_idle"]

    def _on_tree_select(self) -> None:
        item = self.connection_tree.currentItem()
        if item is None:
            return
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return
        if data.get("kind") == "mode":
            self._save_active_work_area()
            self.active_session_id = None
            self.mode = str(data.get("mode", MODE_SERIAL))
            self._apply_mode_defaults()
            self._update_mode_controls()
            self._set_connected_state(False)
            self._update_counts()
            self._clear_work_area()
            self._set_status("")
            self._schedule_config_save()
            return
        session = self.sessions.get(int(data.get("id", -1)))
        if session is None:
            return
        self._select_session(session)
        self._set_status("")
        self._schedule_config_save()

    def _show_connection_context_menu(self, point: QPoint) -> None:
        item = self.connection_tree.itemAt(point)
        if item is None:
            return
        self.connection_tree.setCurrentItem(item)
        self._on_tree_select()
        data = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, dict):
            return
        menu = QMenu(self)
        if data.get("kind") == "mode":
            menu.addAction("刷新连接列表", self.refresh_ports)
        elif data.get("kind") == "session":
            session = self.sessions.get(int(data.get("id", -1)))
            if session is None:
                return
            if session.is_connected:
                menu.addAction("关闭串口" if session.mode == MODE_SERIAL else "关闭连接", self.disconnect_current)
            else:
                menu.addAction("打开串口" if session.mode == MODE_SERIAL else "打开连接", self.connect_current)
            menu.addSeparator()
            menu.addAction("删除连接", self.delete_current_connection)
        menu.exec(self.connection_tree.viewport().mapToGlobal(point))

    @property
    def active_session(self) -> ConnectionSession | None:
        if self.active_session_id is None:
            return None
        return self.sessions.get(self.active_session_id)

    @property
    def is_connected(self) -> bool:
        session = self.active_session
        return bool(session and session.is_connected)

    def _update_mode_controls(self) -> None:
        is_serial = self.mode == MODE_SERIAL
        self.serial_box.setVisible(is_serial)
        self.network_box.setVisible(not is_serial)
        self.create_btn.setVisible(not is_serial)
        if is_serial:
            self.connect_btn.setText("关闭串口" if self.is_connected else "打开串口")
            self.connect_btn.setEnabled(True)
        else:
            self.connect_btn.setText("关闭连接" if self.is_connected else "打开连接")
            self.connect_btn.setEnabled(self.active_session is not None)
            self.create_btn.setEnabled(True)
        if not is_serial:
            self._update_network_rows()

    def _update_network_rows(self) -> None:
        visible = {
            MODE_TCP_CLIENT: {"remote_host", "remote_port"},
            MODE_UDP_CLIENT: {"remote_host", "remote_port"},
            MODE_TCP_SERVER: {"local_port"},
            MODE_UDP_SERVER: {"local_port"},
        }.get(self.mode, set())
        for key, (field, _widget) in self.network_rows.items():
            field.setVisible(key in visible)

    def _apply_mode_defaults(self) -> None:
        if self.mode in (MODE_TCP_SERVER, MODE_UDP_SERVER) and self.local_port_edit.text().strip() in ("", "0"):
            self.local_port_edit.setText("10123")

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

    def _raw_current_config(self, mode: str) -> dict[str, str]:
        if mode == MODE_SERIAL:
            return {
                "port": self.port_combo.currentText().strip(),
                "baud": self.baud_combo.currentText().strip(),
                "data_bits": self.data_bits_combo.currentText().strip(),
                "parity": self.parity_combo.currentText().strip(),
                "stop_bits": self.stop_bits_combo.currentText().strip(),
                "flow": self.flow_combo.currentText().strip(),
                "dtr": "1" if self.dtr_check.isChecked() else "0",
                "rts": "1" if self.rts_check.isChecked() else "0",
            }
        if mode in (MODE_TCP_CLIENT, MODE_UDP_CLIENT):
            return {
                "remote_host": self.remote_host_edit.text().strip(),
                "remote_port": self.remote_port_edit.text().strip(),
                "local_host": "0.0.0.0",
                "local_port": "0",
            }
        return {
            "remote_host": "",
            "remote_port": "",
            "local_host": "0.0.0.0",
            "local_port": self.local_port_edit.text().strip() or "0",
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
            self._update_session_tab(session)

    def create_connection(self) -> ConnectionSession | None:
        if self.mode == MODE_SERIAL:
            QMessageBox.warning(self, "无需创建", "COM串口不需要创建连接，请选择串口后直接打开。")
            return None
        try:
            config = self._capture_current_config(self.mode)
        except Exception as exc:
            QMessageBox.critical(self, "创建连接失败", str(exc))
            return None
        session = self._new_session(self.mode, config)
        self._rebuild_connection_tree()
        self._select_session(session)
        self._set_status(f"已创建连接：{session.name}")
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

    def _select_session(self, session: ConnectionSession, *, sync_tabs: bool = True) -> None:
        if self.active_session_id != session.id:
            self._save_active_work_area()
        self.active_session_id = session.id
        self.mode = session.mode
        self._switching_session = True
        try:
            self._load_session_config(session)
            self._load_session_work_area(session)
        finally:
            self._switching_session = False
        item = self.session_items.get(session.id)
        if item is not None:
            was_blocked = self.connection_tree.blockSignals(True)
            self.connection_tree.setCurrentItem(item)
            self.connection_tree.blockSignals(was_blocked)
        if sync_tabs:
            self._refresh_session_tabs()
        self._update_mode_controls()
        self._set_connected_state(session.is_connected)
        self._update_counts()

    def _save_active_work_area(self) -> None:
        if not hasattr(self, "send_edit") or not hasattr(self, "receive_log"):
            return
        session = self.active_session
        if session is None:
            return
        session.send_text = self.send_edit.toPlainText()
        session.send_file_path = self.send_file_edit.text()
        session.receive_records = self.receive_log.record_snapshot()

    def _load_session_work_area(self, session: ConnectionSession) -> None:
        if not hasattr(self, "send_edit") or not hasattr(self, "receive_log"):
            return
        was_send_file_blocked = self.send_file_edit.blockSignals(True)
        try:
            self.send_edit.setPlainText(session.send_text)
            self.send_file_edit.setText(session.send_file_path)
            self.receive_log.set_records(session.receive_records)
        finally:
            self.send_file_edit.blockSignals(was_send_file_blocked)

    def _clear_work_area(self) -> None:
        if not hasattr(self, "send_edit") or not hasattr(self, "receive_log"):
            return
        was_send_file_blocked = self.send_file_edit.blockSignals(True)
        try:
            self.send_edit.clear()
            self.send_file_edit.clear()
            self.receive_log.set_records([])
        finally:
            self.send_file_edit.blockSignals(was_send_file_blocked)

    def delete_current_connection(self) -> None:
        session = self.active_session
        if session is None:
            QMessageBox.warning(self, "未选择连接", "请先在连接列表中选择要删除的连接。")
            return
        self._remove_session(session)
        self._set_status(f"已删除连接：{session.name}")
        self._schedule_config_save()

    def _remove_session(self, session: ConnectionSession) -> None:
        if session.is_connected:
            self.disconnect_session(session)
        was_active = self.active_session_id == session.id
        self.sessions.pop(session.id, None)
        self.pending_send_records.pop(session.id, None)
        if was_active:
            self.active_session_id = None
        self._rebuild_connection_tree()
        if was_active:
            remaining = sorted(self.sessions.values(), key=lambda item: item.id)
            if remaining:
                self._select_session(remaining[0])
            else:
                self.mode = MODE_SERIAL
                self._apply_mode_defaults()
                self._update_mode_controls()
                self._set_connected_state(False)
        self._refresh_session_tabs()
        self._update_counts()

    def _capture_current_config(self, mode: str) -> dict[str, str]:
        config = self._raw_current_config(mode)
        if mode == MODE_SERIAL:
            if not config.get("port", ""):
                raise ValueError("请先选择或输入 COM 口，例如 COM3。")
            return config
        if mode in (MODE_TCP_CLIENT, MODE_UDP_CLIENT):
            if not config.get("remote_host", ""):
                raise ValueError("目标IP不能为空")
            if parse_port(config.get("remote_port", ""), "目标端口") == 0:
                raise ValueError("目标端口不能为 0")
            return config
        if mode in (MODE_TCP_SERVER, MODE_UDP_SERVER):
            parse_port(config.get("local_port", "0"), "本地端口")
            return config
        raise ValueError("未知连接类型")

    def _load_session_config(self, session: ConnectionSession) -> None:
        config = session.config
        if session.mode == MODE_SERIAL:
            self.port_combo.setCurrentText(config.get("port", ""))
            self.baud_combo.setCurrentText(config.get("baud", "115200"))
            self.data_bits_combo.setCurrentText(config.get("data_bits", "8"))
            self.parity_combo.setCurrentText(config.get("parity", "无"))
            self.stop_bits_combo.setCurrentText(config.get("stop_bits", "1"))
            self.flow_combo.setCurrentText(config.get("flow", "无"))
            self.dtr_check.setChecked(config.get("dtr", "1") == "1")
            self.rts_check.setChecked(config.get("rts", "1") == "1")
        else:
            self.remote_host_edit.setText(config.get("remote_host", "127.0.0.1"))
            self.remote_port_edit.setText(config.get("remote_port", "10123"))
            self.local_port_edit.setText(config.get("local_port", "0"))

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

    def _unique_session_name_for_session(self, session: ConnectionSession, base_name: str) -> str:
        existing = {item.name for item in self.sessions.values() if item.id != session.id}
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

    def connect_current(self) -> None:
        session = self.active_session
        if session is None:
            if self.mode == MODE_SERIAL:
                try:
                    session = self._new_session(MODE_SERIAL, self._capture_current_config(MODE_SERIAL))
                    self._rebuild_connection_tree()
                    self._select_session(session)
                except Exception as exc:
                    QMessageBox.critical(self, "打开串口失败", str(exc))
                    return
            else:
                QMessageBox.warning(self, "未选择连接", "请先点击创建连接按钮，并在连接列表中选择一个连接。")
                return
        if session.is_connected:
            return
        try:
            session.config = self._capture_current_config(session.mode)
            session.name = self._unique_session_name_for_session(session, self._session_label(session.mode, session.config))
            self._update_session_tree_status(session)
            self._schedule_config_save()
        except Exception as exc:
            QMessageBox.critical(self, "连接参数错误", str(exc))
            return
        if session.mode == MODE_SERIAL:
            self.connect_serial(session)
        else:
            self.connect_network(session)

    def disconnect_current(self) -> None:
        session = self.active_session
        if session is not None:
            self.disconnect_session(session)

    def _connected_sessions(self) -> list[ConnectionSession]:
        return [session for session in self.sessions.values() if session.is_connected]

    def connect_serial(self, session: ConnectionSession) -> None:
        if not HAS_PYSERIAL:
            QMessageBox.critical(self, "缺少依赖", "请先执行：python -m pip install -r requirements.txt")
            return
        port_name = session.config.get("port", "").strip()
        if not port_name:
            QMessageBox.warning(self, "请选择串口", "请先选择或输入 COM 口，例如 COM3。")
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
            QMessageBox.critical(self, "打开串口失败", str(exc))
            self._set_status(f"打开 {port_name} 失败")
            return
        thread = threading.Thread(target=self._reader_loop, args=(session,), name="serial-reader", daemon=True)
        session.threads.append(thread)
        thread.start()
        session.name = self._unique_session_name_for_session(session, port_name)
        session.tab_open = True
        self._set_connected_state(session.is_connected)
        self._update_session_tree_status(session)
        self._refresh_session_tabs()
        self._set_status("")
        self._on_auto_send_toggle()

    def connect_network(self, session: ConnectionSession) -> None:
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
                thread = threading.Thread(target=self._tcp_client_reader_loop, args=(session, sock), name="tcp-client-reader", daemon=True)
                session.threads.append(thread)
                thread.start()
                label = f"{host}:{port}"
            elif mode == MODE_TCP_SERVER:
                host, port = self._local_endpoint(session)
                server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server.bind((host, port))
                server.listen()
                server.settimeout(0.2)
                session.tcp_server_socket = server
                thread = threading.Thread(target=self._tcp_accept_loop, args=(session, server), name="tcp-server-accept", daemon=True)
                session.threads.append(thread)
                thread.start()
                actual_host, actual_port = server.getsockname()
                label = f"本机:{actual_port}"
            elif mode == MODE_UDP_CLIENT:
                host, port = self._remote_endpoint(session)
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self._bind_udp_if_needed(session, sock)
                sock.settimeout(0.2)
                session.udp_socket = sock
                session.udp_default_peer = (host, port)
                thread = threading.Thread(target=self._udp_reader_loop, args=(session, sock), name="udp-client-reader", daemon=True)
                session.threads.append(thread)
                thread.start()
                label = f"{host}:{port}"
            elif mode == MODE_UDP_SERVER:
                host, port = self._local_endpoint(session)
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind((host, port))
                sock.settimeout(0.2)
                session.udp_socket = sock
                thread = threading.Thread(target=self._udp_reader_loop, args=(session, sock), name="udp-server-reader", daemon=True)
                session.threads.append(thread)
                thread.start()
                actual_host, actual_port = sock.getsockname()
                label = f"本机:{actual_port}"
            else:
                raise ValueError("未知连接模式")
        except Exception as exc:
            self.disconnect_network(session)
            QMessageBox.critical(self, "打开网络连接失败", str(exc))
            self._set_status(f"打开 {mode} 失败")
            return
        session.name = self._unique_session_name_for_session(session, label)
        session.tab_open = True
        self._set_connected_state(session.is_connected)
        self._update_session_tree_status(session)
        self._refresh_session_tabs()
        self._set_status("")
        self._on_auto_send_toggle()

    def disconnect_session(self, session: ConnectionSession) -> None:
        if session.mode == MODE_SERIAL:
            self.disconnect_serial(session)
        else:
            self.disconnect_network(session)

    def disconnect_serial(self, session: ConnectionSession) -> None:
        was_connected = session.is_connected
        if was_connected and session.id == self.active_session_id:
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
            self._update_session_tab(session)
        if was_connected:
            self._set_status("")

    def disconnect_network(self, session: ConnectionSession) -> None:
        was_connected = session.is_connected
        if was_connected and session.id == self.active_session_id:
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
            self._update_session_tab(session)
        if was_connected:
            self._set_status("")

    def _remote_endpoint(self, session: ConnectionSession | None = None) -> tuple[str, int]:
        config = session.config if session is not None else None
        host = (config.get("remote_host", "") if config else self.remote_host_edit.text()).strip()
        if not host:
            raise ValueError("目标IP不能为空")
        port = parse_port(config.get("remote_port", "") if config else self.remote_port_edit.text(), "目标端口")
        if port == 0:
            raise ValueError("目标端口不能为 0")
        return host, port

    def _local_endpoint(self, session: ConnectionSession | None = None) -> tuple[str, int]:
        config = session.config if session is not None else None
        host = (config.get("local_host", "") if config else "0.0.0.0").strip() or "0.0.0.0"
        port = parse_port(config.get("local_port", "") if config else self.local_port_edit.text(), "本地端口")
        return host, port

    def _bind_udp_if_needed(self, session: ConnectionSession, sock: socket.socket) -> None:
        host = session.config.get("local_host", "").strip() or "0.0.0.0"
        port = parse_port(session.config.get("local_port", "0"), "本地端口")
        if port or host not in ("", "0.0.0.0"):
            sock.bind((host, port))

    def _join_session_threads(self, session: ConnectionSession) -> None:
        current_thread = threading.current_thread()
        for thread in list(session.threads):
            if thread is not current_thread and thread.is_alive():
                thread.join(timeout=0.4)
        session.threads.clear()

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
            thread = threading.Thread(target=self._tcp_server_client_reader_loop, args=(session, client, addr), name=f"tcp-client-{addr[0]}:{addr[1]}", daemon=True)
            session.threads.append(thread)
            thread.start()

    def _tcp_server_client_reader_loop(self, session: ConnectionSession, client: socket.socket, addr: tuple[str, int]) -> None:
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
                session.tcp_clients = [(sock, item_addr) for sock, item_addr in session.tcp_clients if sock is not client]
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
                    self._set_status(str(payload))
                    self.disconnect_session(session)
                elif kind == "error":
                    self._set_status(f"连接错误: {payload}")
                    QMessageBox.critical(self, "连接错误", str(payload))
                    self.disconnect_session(session)
        except queue.Empty:
            pass
        self._update_counts()

    def _append_received_data(self, session: ConnectionSession, data: bytes) -> None:
        display = self._format_received_data(data)
        if not display:
            return
        recv_timestamp = self._log_timestamp()
        sent_timestamp, sent_data = self._take_pending_send_record(session)
        if not self.pause_display_check.isChecked():
            self._append_receive_record(session, sent_timestamp, sent_data, recv_timestamp, display)
        if self.realtime_save_check.isChecked():
            self._append_realtime_file(self._format_record_for_file(sent_timestamp, sent_data, recv_timestamp, session.name, display))

    def _append_system_message(self, session: ConnectionSession, text: str) -> None:
        timestamp = self._log_timestamp()
        if not self.pause_display_check.isChecked():
            self._append_receive_record(session, "", "", timestamp, text)
        if self.realtime_save_check.isChecked():
            self._append_realtime_file(self._format_record_for_file("", "", timestamp, session.name, text))

    def _remember_sent_data(self, session: ConnectionSession, data: bytes) -> None:
        display = self._format_sent_data(data)
        if display:
            records = self.pending_send_records.setdefault(session.id, deque())
            records.append((self._log_timestamp(), display))

    def _take_pending_send_record(self, session: ConnectionSession) -> tuple[str, str]:
        records = self.pending_send_records.get(session.id)
        if not records:
            return "", ""
        return records.popleft()

    def _append_receive_record(self, session: ConnectionSession, sent_timestamp: str, sent_data: str, recv_timestamp: str, data: str) -> None:
        record = (sent_timestamp, sent_data, recv_timestamp, session.name, data)
        session.receive_records.append(record)
        if session.id == self.active_session_id:
            self.receive_log.append_record(*record)

    def _format_sent_data(self, data: bytes) -> str:
        if self.hex_send_check.isChecked():
            return bytes_to_hex(data)
        return data.decode(self.encoding_combo.currentText() or "utf-8", errors="replace")

    def _format_received_data(self, data: bytes) -> str:
        if self.hex_recv_check.isChecked():
            return bytes_to_hex(data)
        return data.decode(self.encoding_combo.currentText() or "utf-8", errors="replace")

    def _log_timestamp(self) -> str:
        now = datetime.now()
        return f"{now:%H:%M:%S}.{now.microsecond // 1000:03d}"

    def send_now(self, silent: bool = False) -> None:
        session = self.active_session
        if session is None or not session.is_connected:
            if not silent:
                QMessageBox.warning(self, "连接未打开", "请先选择并打开一个连接。")
            return
        try:
            data, crc_appended, should_update_send_area = self._build_send_payload()
        except Exception as exc:
            if not silent:
                QMessageBox.critical(self, "发送内容错误", str(exc))
            self._set_status("发送内容错误")
            return
        if not data:
            if not silent:
                self._set_status("发送内容为空")
            return
        try:
            written, target_text = self._write_payload(session, data)
            if crc_appended and should_update_send_area:
                self._write_full_payload_to_send_area(data)
            self._remember_sent_data(session, data)
            session.sent_bytes += written
            self._update_counts()
            crc_text = "，已自动追加CRC" if crc_appended else ""
            self._set_status(f"已发送 {written} 字节{target_text}{crc_text}")
        except Exception as exc:
            if not silent:
                QMessageBox.critical(self, "发送失败", str(exc))
            self._set_status(f"发送失败: {exc}")

    def _build_send_payload(self) -> tuple[bytes, bool, bool]:
        if self.send_file_check.isChecked():
            path_text = self.send_file_edit.text().strip()
            if not path_text:
                raise ValueError("请选择要发送的文件")
            path = Path(path_text)
            if not path.is_file():
                raise ValueError("发送文件不存在")
            data = path.read_bytes()
            should_update_send_area = False
        else:
            text = self.send_edit.toPlainText()
            if self.hex_send_check.isChecked():
                data = parse_hex_payload(text)
            else:
                if self.append_crlf_check.isChecked():
                    text += "\r\n"
                data = text.encode(self.encoding_combo.currentText() or "utf-8", errors="replace")
            should_update_send_area = True
        data, crc_appended = self._apply_auto_crc(data)
        return data, crc_appended, should_update_send_area

    def _apply_auto_crc(self, data: bytes) -> tuple[bytes, bool]:
        if not self.auto_crc_check.isChecked() or not data:
            return data, False
        algorithm = self.crc_combo.currentText() or CRC_ALGORITHM_MODBUS
        if algorithm != CRC_ALGORITHM_MODBUS:
            raise ValueError(f"暂不支持 CRC 算法: {algorithm}")
        return append_crc16_modbus_if_missing(data)

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
            peer = session.udp_default_peer if session.mode == MODE_UDP_CLIENT else session.udp_peer
            if peer is None:
                peer = self._remote_endpoint(session)
            written = session.udp_socket.sendto(data, peer)
            return int(written), f" 到 {peer[0]}:{peer[1]}"
        raise RuntimeError("连接未打开")

    def _remove_tcp_clients(self, session: ConnectionSession, clients: list[tuple[socket.socket, tuple[str, int]]]) -> None:
        failed_sockets = {client for client, _addr in clients}
        with session.tcp_clients_lock:
            session.tcp_clients = [(client, addr) for client, addr in session.tcp_clients if client not in failed_sockets]
        for client, _addr in clients:
            try:
                client.close()
            except Exception:
                pass

    def _write_full_payload_to_send_area(self, data: bytes) -> None:
        self.hex_send_check.setChecked(True)
        self.send_edit.setPlainText(bytes_to_hex(data))
        self._schedule_config_save()

    def _on_auto_send_toggle(self) -> None:
        if self.auto_send_check.isChecked() and self.is_connected:
            self.auto_send_timer.start(max(10, self.interval_spin.value()))
        else:
            self.auto_send_timer.stop()

    def _restart_auto_send_if_needed(self) -> None:
        if self.auto_send_timer.isActive():
            self.auto_send_timer.start(max(10, self.interval_spin.value()))

    def stop_auto_send(self) -> None:
        self.auto_send_check.setChecked(False)
        self.auto_send_timer.stop()

    def _toggle_file_send_controls(self) -> None:
        enabled = self.send_file_check.isChecked()
        self.send_file_edit.setEnabled(enabled)
        self.send_file_btn.setEnabled(enabled)

    def _toggle_crc_controls(self) -> None:
        self.crc_combo.setEnabled(self.auto_crc_check.isChecked())

    def choose_send_file(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "选择要发送的文件")
        if path:
            self.send_file_edit.setText(path)

    def choose_realtime_file(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(self, "选择实时保存文件", "", "日志文件 (*.log);;文本文件 (*.txt);;所有文件 (*.*)")
        if path:
            self.realtime_edit.setText(path)

    def _on_realtime_save_toggle(self) -> None:
        if self._loading_config or not self.realtime_save_check.isChecked():
            return
        if not self.realtime_edit.text().strip():
            self.choose_realtime_file()
        if not self.realtime_edit.text().strip():
            self.realtime_save_check.setChecked(False)
            return
        try:
            Path(self.realtime_edit.text()).parent.mkdir(parents=True, exist_ok=True)
            with open(self.realtime_edit.text(), "a", encoding="utf-8"):
                pass
            self._set_status("实时保存已开启")
        except Exception as exc:
            self.realtime_save_check.setChecked(False)
            QMessageBox.critical(self, "实时保存失败", str(exc))

    def _append_realtime_file(self, text: str) -> None:
        path = self.realtime_edit.text().strip()
        if not path:
            self.realtime_save_check.setChecked(False)
            return
        try:
            with open(path, "a", encoding="utf-8", newline="") as file:
                file.write(text)
        except Exception as exc:
            self.realtime_save_check.setChecked(False)
            self._set_status(f"实时保存失败: {exc}")

    def clear_send(self) -> None:
        self.send_edit.clear()
        session = self.active_session
        if session is not None:
            session.send_text = ""

    def clear_receive(self) -> None:
        session = self.active_session
        if session is not None:
            session.receive_records.clear()
        self.receive_log.clear()

    def clear_counts(self) -> None:
        session = self.active_session
        targets = [session] if session is not None else list(self.sessions.values())
        for item in targets:
            item.sent_bytes = 0
            item.recv_bytes = 0
            item.sent_last = 0
            item.recv_last = 0
        self.sent_last = 0
        self.recv_last = 0
        self._update_counts()
        self.speed_label.setText("发送速度(B/S): 0    接收速度(B/S): 0")

    def save_receive(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(self, "保存接收区", "", "文本文件 (*.txt);;日志文件 (*.log);;所有文件 (*.*)")
        if not path:
            return
        try:
            Path(path).write_text(self.receive_log.to_log_text(), encoding="utf-8")
            self._set_status(f"已保存到 {path}")
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))

    def import_config(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(self, "导入配置", "", "JSON配置文件 (*.json);;所有文件 (*.*)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("配置文件格式错误")
            for session in list(self.sessions.values()):
                if session.is_connected:
                    self.disconnect_session(session)
            self.config_save_timer.stop()
            self._apply_config(data)
            self._save_config_now()
            self._set_status(f"已导入配置: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "导入配置失败", str(exc))

    def export_config(self) -> None:
        path, _filter = QFileDialog.getSaveFileName(self, "导出配置", "config.json", "JSON配置文件 (*.json);;所有文件 (*.*)")
        if not path:
            return
        try:
            self._sync_active_session_from_controls()
            Path(path).write_text(json.dumps(self._collect_config(), ensure_ascii=False, indent=2), encoding="utf-8")
            self._set_status(f"已导出配置: {path}")
        except Exception as exc:
            QMessageBox.critical(self, "导出配置失败", str(exc))

    def clear_config(self) -> None:
        if QMessageBox.question(self, "清除配置", "确定要清除已保存配置并恢复默认设置吗？") != QMessageBox.StandardButton.Yes:
            return
        for session in list(self.sessions.values()):
            if session.is_connected:
                self.disconnect_session(session)
        self.config_save_timer.stop()
        self._apply_config(self._default_config())
        try:
            if self.config_path.exists():
                self.config_path.unlink()
            self._set_status("已清除配置")
        except Exception as exc:
            QMessageBox.critical(self, "清除配置失败", str(exc))

    def _format_record_for_file(self, sent_timestamp: str, sent_data: str, recv_timestamp: str, connection: str, data: str) -> str:
        return f"{sent_timestamp}\t{sent_data}\t{recv_timestamp}\t{connection}\t{data}\n"

    def _set_connected_state(self, connected: bool) -> None:
        mode = self.active_session.mode if self.active_session is not None else self.mode
        if mode == MODE_SERIAL:
            text = "关闭串口" if connected else "打开串口"
        else:
            text = "关闭连接" if connected else "打开连接"
        self.connect_btn.setText(text)
        if mode != MODE_SERIAL:
            self.create_btn.setEnabled(True)
            self.connect_btn.setEnabled(self.active_session is not None)
        self._refresh_status_summary()

    def _refresh_status_summary(self) -> None:
        connected = self.is_connected
        self.connection_state_label.setText("已连接" if connected else "已断开")
        self.status_dot.setPixmap(self.status_icons["connected" if connected else "disconnected"].pixmap(14, 14))
        session = self.active_session
        self.connection_address_label.setText(session.name if session is not None else self.mode)

    def _update_session_tree_status(self, session: ConnectionSession) -> None:
        item = self.session_items.get(session.id)
        if item is not None:
            item.setText(0, session.name)
            item.setIcon(0, self._session_status_icon(session))
        self._update_session_tab(session)

    def _apply_line_state(self, session: ConnectionSession | None = None) -> None:
        session = session or self.active_session
        if session is None:
            return
        if session.id == self.active_session_id:
            session.config["dtr"] = "1" if self.dtr_check.isChecked() else "0"
            session.config["rts"] = "1" if self.rts_check.isChecked() else "0"
        port = session.serial_port
        if not port or not getattr(port, "is_open", False):
            return
        try:
            port.dtr = session.config.get("dtr", "1") == "1"
            port.rts = session.config.get("rts", "1") == "1"
        except Exception as exc:
            self._set_status(f"DTR/RTS 设置失败: {exc}")

    def _update_counts(self) -> None:
        session = self.active_session
        if session is not None:
            text = f"发送: {session.sent_bytes} 字节    接收: {session.recv_bytes} 字节"
        else:
            sent_total = sum(item.sent_bytes for item in self.sessions.values())
            recv_total = sum(item.recv_bytes for item in self.sessions.values())
            text = f"发送: {sent_total} 字节    接收: {recv_total} 字节"
        self.count_label.setText(text)
        self.status_count_label.setText(text)
        self._refresh_status_summary()

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
        self.speed_label.setText(f"发送速度(B/S): {tx_speed}    接收速度(B/S): {rx_speed}")

    def _set_status(self, text: str) -> None:
        self.status_message_label.setText(text)

    def show_about(self) -> None:
        QMessageBox.information(
            self,
            "关于",
            f"{APP_NAME}\n版本: v{APP_VERSION}\n\n"
            "支持本机 COM 串口、TCP客户端、TCP服务端、UDP客户端、UDP服务端，"
            "可进行文本/16进制发送、自动发送、接收显示、实时保存和收发计数。",
        )

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.config_save_timer.isActive():
            self.config_save_timer.stop()
            self._save_config_now()
        self.rx_timer.stop()
        self.speed_timer.stop()
        self.auto_send_timer.stop()
        for session in list(self.sessions.values()):
            if session.is_connected:
                self.disconnect_session(session)
        event.accept()


def run() -> None:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setStyle("Fusion")
    window = SerialDebugQtTool()
    window.show()
    app.exec()
