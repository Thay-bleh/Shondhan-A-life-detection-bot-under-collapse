# dashboard.py
import sys
import json
import socket
import random
import re
import struct
import time
from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *

# ---------------------------------------------------------------------------
# Palette (matches the approved HTML mockup)
# ---------------------------------------------------------------------------
BG = "#f3f5f8"
PANEL = "#ffffff"
PANEL_2 = "#eef1f5"
BORDER = "#dde3ea"
TEXT = "#10161d"
TEXT_DIM = "#5a6472"
TEXT_FAINT = "#93a0ad"
ORANGE = "#e8590c"
ORANGE_HOVER = "#c94b09"
TEAL = "#0f7f8c"
GREEN = "#15803d"
GREEN_HOVER = "#1a9a4a"
RED = "#b91c1c"


class TelemetryReceiver(QThread):
    """Listens for JSON telemetry (gas sensor readings) sent by the Teensy 4.1."""
    data_received = Signal(dict)

    def __init__(self, port=8889):
        super().__init__()
        self.port = port
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    def run(self):
        try:
            self.sock.bind(("0.0.0.0", self.port))
            print(f"[UDP TELEMETRY] Listening for Teensy sensor data on port {self.port}...")
        except Exception as e:
            print(f"[UDP TELEMETRY] Bind warning for port {self.port}: {e}")
            return

        self.sock.settimeout(0.5)
        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
                payload = json.loads(data.decode('utf-8'))
                self.data_received.emit(payload)
                print(f"[TEENSY TELEMETRY] from {addr[0]}:{addr[1]} -> {payload}")
            except socket.timeout:
                continue
            except Exception:
                pass

    def stop(self):
        self.running = False
        self.sock.close()


class VideoFrameReceiver(QThread):
    """
    Receives the ZED 2i feed (raw + object-detection) from the Jetson as chunked,
    JPEG-encoded UDP frames and reassembles them into QImages.

    Wire format expected from the Jetson, per UDP datagram:

        struct format "!IHHB"  (9-byte header, network byte order)
        ------------------------------------------------------------
        frame_id      uint32   monotonically increasing per encoded frame
        chunk_index   uint16   0-based index of this chunk within the frame
        total_chunks  uint16   total number of chunks that make up the frame
        stream_type   uint8    0 = raw ZED 2i feed, 1 = object-detection overlay feed
        ------------------------------------------------------------
        followed by the raw chunk payload (a slice of the JPEG-encoded frame).

    Frames should be split into chunks of ~1400 bytes (VIDEO_CHUNK_PAYLOAD) so
    each datagram fits under a standard 1500-byte MTU without IP fragmentation.
    A single-chunk frame just sends total_chunks=1, chunk_index=0.

    The dashboard reassembles chunks per frame_id, decodes the completed JPEG,
    and emits it with its stream_type. Incomplete frames older than ~1s are
    dropped so one lost packet can't stall the buffer forever.
    """
    frame_received = Signal(QImage, int)

    HEADER_FMT = "!IHHB"
    HEADER_SIZE = struct.calcsize(HEADER_FMT)
    STALE_FRAME_SECONDS = 1.0

    def __init__(self, port=8890):
        super().__init__()
        self.port = port
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            # video needs a bigger receive buffer than the small JSON command/telemetry sockets
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        except OSError:
            pass
        self._buffers = {}  # frame_id -> {"chunks": [bytes|None]*n, "count": int, "stream_type": int, "ts": float}

    def run(self):
        try:
            self.sock.bind(("0.0.0.0", self.port))
            print(f"[UDP VIDEO] Listening for ZED 2i frames on port {self.port}...")
        except Exception as e:
            print(f"[UDP VIDEO] Bind warning for port {self.port}: {e}")
            return

        self.sock.settimeout(0.5)
        while self.running:
            try:
                data, addr = self.sock.recvfrom(65535)
            except socket.timeout:
                self._expire_stale_frames()
                continue
            except Exception:
                continue

            if len(data) < self.HEADER_SIZE:
                continue
            frame_id, chunk_index, total_chunks, stream_type = struct.unpack(
                self.HEADER_FMT, data[:self.HEADER_SIZE]
            )
            chunk_payload = data[self.HEADER_SIZE:]

            entry = self._buffers.get(frame_id)
            if entry is None:
                if total_chunks == 0 or total_chunks > 4096:
                    continue  # guard against a corrupt header
                entry = {"chunks": [None] * total_chunks, "count": 0, "stream_type": stream_type, "ts": time.time()}
                self._buffers[frame_id] = entry

            if 0 <= chunk_index < len(entry["chunks"]) and entry["chunks"][chunk_index] is None:
                entry["chunks"][chunk_index] = chunk_payload
                entry["count"] += 1

            if entry["count"] == len(entry["chunks"]):
                jpeg_bytes = b"".join(entry["chunks"])
                del self._buffers[frame_id]
                image = QImage.fromData(jpeg_bytes, "JPG")
                if not image.isNull():
                    self.frame_received.emit(image, entry["stream_type"])

            self._expire_stale_frames()

    def _expire_stale_frames(self):
        now = time.time()
        stale_ids = [fid for fid, e in self._buffers.items() if now - e["ts"] > self.STALE_FRAME_SECONDS]
        for fid in stale_ids:
            del self._buffers[fid]

    def stop(self):
        self.running = False
        self.sock.close()


class GasDial(QWidget):
    """Circular gauge matching the HTML mockup's SVG ring: track + value arc + centred value."""
    def __init__(self, max_val=50.0, diameter=84):
        super().__init__()
        self.max_val = max_val
        self.value = 0.0
        self.is_alert = False
        self.setFixedSize(diameter, diameter)

    def set_value(self, value, is_alert):
        self.value = value
        self.is_alert = is_alert
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        stroke = 8
        rect = QRectF(stroke / 2, stroke / 2, self.width() - stroke, self.height() - stroke)

        # track
        painter.setPen(QPen(QColor(BORDER), stroke, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(rect, 0, 360 * 16)

        # value arc, starting at 12 o'clock, sweeping clockwise
        pct = max(0.0, min(1.0, self.value / self.max_val))
        color = QColor(RED) if self.is_alert else QColor(TEAL)
        painter.setPen(QPen(color, stroke, Qt.SolidLine, Qt.RoundCap))
        span = int(-pct * 360 * 16)
        painter.drawArc(rect, 90 * 16, span)

        # centred value text
        painter.setPen(QColor(RED) if self.is_alert else QColor(TEXT))
        font = painter.font()
        font.setFamily("Consolas")
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(self.rect().adjusted(0, -6, 0, -6), Qt.AlignCenter, f"{self.value:.1f}")

        painter.setPen(QColor(TEXT_FAINT))
        font.setPointSize(7)
        font.setBold(False)
        painter.setFont(font)
        painter.drawText(self.rect().adjusted(0, 16, 0, 16), Qt.AlignCenter, "PPM")


class ToggleSwitch(QAbstractButton):
    """Small animated pill toggle, styled to match the console (used for Object Detection)."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(42, 22)
        self._knob_pos = 3.0
        self._anim = QPropertyAnimation(self, b"knob_pos", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._animate_to)

    def _animate_to(self, checked):
        self._anim.stop()
        self._anim.setStartValue(self._knob_pos)
        self._anim.setEndValue(self.width() - 19.0 if checked else 3.0)
        self._anim.start()

    def get_knob_pos(self):
        return self._knob_pos

    def set_knob_pos(self, pos):
        self._knob_pos = pos
        self.update()

    knob_pos = Property(float, get_knob_pos, set_knob_pos)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        if self.isChecked():
            track_fill = QColor(ORANGE)
            track_border = QColor(ORANGE_HOVER)
        else:
            track_fill = QColor("#c7cfd8")   # a clearly visible mid-gray, not near-white
            track_border = QColor("#aab3bf")

        painter.setPen(QPen(track_border, 1))
        painter.setBrush(track_fill)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        painter.drawRoundedRect(rect, self.height() / 2, self.height() / 2)

        knob_d = self.height() - 6
        painter.setPen(QPen(QColor("#aab3bf"), 1))
        painter.setBrush(QColor("#ffffff"))
        painter.drawEllipse(QRectF(self._knob_pos, 3, knob_d, knob_d))


class SensorCard(QFrame):
    """MQ2 gas sensor card: a radial dial plus name/description, mirroring the mockup."""
    def __init__(self, title, unit="ppm", max_val=50.0, alert_val=20.0, accent_color=TEAL):
        super().__init__()
        self.unit = unit
        self.max_val = max_val
        self.alert_val = alert_val
        self.accent_color = accent_color

        self.setStyleSheet(f"""
            QFrame {{
                background-color: {PANEL_2};
                border: 1px solid {BORDER};
                border-radius: 10px;
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(16)

        self.dial = GasDial(max_val=max_val)
        layout.addWidget(self.dial, 0, Qt.AlignTop)

        meta = QVBoxLayout()
        meta.setSpacing(4)
        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet(f"color: {TEXT}; font-size: 13px; font-weight: 700; border:none; background:transparent;")
        meta.addWidget(self.title_lbl)

        self.desc_lbl = QLabel(f"Analog reading from Teensy, scaled 0\u201350 {unit}. Alerts above {alert_val:.0f} {unit}.")
        self.desc_lbl.setWordWrap(True)
        self.desc_lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px; border:none; background:transparent;")
        meta.addWidget(self.desc_lbl)
        meta.addStretch()

        layout.addLayout(meta, 1)

    def update_value(self, val):
        is_alert = val > self.alert_val
        self.dial.set_value(val, is_alert)
        return is_alert


class ServoCard(QFrame):
    """One servo channel: preset angle buttons + a validated custom-angle input."""
    def __init__(self, index, send_callback):
        super().__init__()
        self.index = index
        self.send_callback = send_callback
        self.angle = 0

        self.setStyleSheet(f"""
            QFrame {{ background-color: {PANEL_2}; border: 1px solid {BORDER}; border-radius: 8px; }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        head = QHBoxLayout()
        name_lbl = QLabel(f"Servo {index}")
        name_lbl.setStyleSheet(f"color:{TEXT}; font-size:12px; font-weight:700; border:none; background:transparent;")
        self.angle_lbl = QLabel("000\u00b0")
        self.angle_lbl.setStyleSheet(f"color:{TEAL}; font-size:15px; font-weight:700; font-family: Consolas, monospace; border:none; background:transparent;")
        head.addWidget(name_lbl)
        head.addStretch()
        head.addWidget(self.angle_lbl)
        layout.addLayout(head)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(5)
        self.preset_btns = {}
        for label, angle in [("0\u00b0", 0), ("90\u00b0", 90), ("180\u00b0", 180)]:
            b = QPushButton(label)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _, a=angle: self._select_preset(a))
            btn_row.addWidget(b)
            self.preset_btns[angle] = b

        self.custom_btn = QPushButton("Custom")
        self.custom_btn.setCursor(Qt.PointingHandCursor)
        self.custom_btn.clicked.connect(self._open_custom)
        btn_row.addWidget(self.custom_btn)
        layout.addLayout(btn_row)

        # custom input row (hidden until "Custom" is clicked)
        self.custom_row = QWidget()
        custom_layout = QHBoxLayout(self.custom_row)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.setSpacing(6)

        self.custom_input = QLineEdit()
        self.custom_input.setPlaceholderText("0\u2013180")
        self.custom_input.setValidator(QIntValidator(0, 999))  # loose validator; real check below
        self.custom_input.textChanged.connect(self._validate_input)
        custom_layout.addWidget(self.custom_input)

        self.send_btn = QPushButton("Send")
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.clicked.connect(self._send_custom)
        custom_layout.addWidget(self.send_btn)

        layout.addWidget(self.custom_row)
        self.custom_row.setVisible(False)

        self.error_lbl = QLabel("Enter a number between 0 and 180.")
        self.error_lbl.setStyleSheet(f"color:{RED}; font-size:10px; border:none; background:transparent;")
        self.error_lbl.setVisible(False)
        layout.addWidget(self.error_lbl)

        self._style_buttons()
        self._select_preset(0, send=False)

    def _style_buttons(self):
        idle = f"""
            QPushButton {{
                background-color: {PANEL}; color: {TEXT_DIM}; font-size: 10.5px; font-weight: 600;
                border: 1px solid {BORDER}; border-radius: 5px; padding: 6px 2px;
            }}
            QPushButton:hover {{ border-color: {ORANGE}; color: {TEXT}; }}
        """
        selected = f"""
            QPushButton {{
                background-color: {ORANGE}; color: #2a1103; font-size: 10.5px; font-weight: 700;
                border: 1px solid {ORANGE}; border-radius: 5px; padding: 6px 2px;
            }}
        """
        for angle, b in self.preset_btns.items():
            b.setStyleSheet(selected if angle == self.angle and self.custom_btn.property("selected") != True else idle)
        self.custom_btn.setStyleSheet(selected if self.custom_btn.property("selected") else idle)

        self.send_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {TEAL}; color: #ffffff; font-size: 10.5px; font-weight: 700;
                border: none; border-radius: 5px; padding: 6px 12px;
            }}
        """)
        self.custom_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {PANEL}; color: {TEXT}; border: 1px solid {BORDER};
                border-radius: 5px; padding: 5px 8px; font-family: Consolas, monospace; font-size: 11px;
            }}
            QLineEdit:focus {{ border-color: {TEAL}; }}
        """)

    def _select_preset(self, angle, send=True):
        self.angle = angle
        self.angle_lbl.setText(f"{angle:03d}\u00b0")
        self.custom_btn.setProperty("selected", False)
        self.custom_row.setVisible(False)
        self.error_lbl.setVisible(False)
        self._style_buttons()
        if send:
            self.send_callback(self.index, angle)

    def _open_custom(self):
        self.custom_btn.setProperty("selected", True)
        self._style_buttons()
        self.custom_row.setVisible(True)
        self.error_lbl.setVisible(False)
        self.custom_input.setFocus()

    def _validate_input(self):
        text = self.custom_input.text()
        valid = bool(re.fullmatch(r"\d+", text)) and 0 <= int(text or -1) <= 180
        self.custom_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {PANEL}; color: {TEXT};
                border: 1px solid {BORDER if (valid or not text) else RED};
                border-radius: 5px; padding: 5px 8px; font-family: Consolas, monospace; font-size: 11px;
            }}
            QLineEdit:focus {{ border-color: {TEAL if (valid or not text) else RED}; }}
        """)
        self.error_lbl.setVisible(bool(text) and not valid)
        return valid

    def _send_custom(self):
        text = self.custom_input.text()
        if not text or not self._validate_input():
            self.error_lbl.setVisible(True)
            return
        angle = int(text)
        self.angle = angle
        self.angle_lbl.setText(f"{angle:03d}\u00b0")
        self.custom_input.clear()
        self.custom_btn.setProperty("selected", False)
        self.custom_row.setVisible(False)
        self._style_buttons()
        self.send_callback(self.index, angle)


class FormalDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        # Jetson Orin Nano: camera / object-detection compute
        self.jetson_ip = "192.168.2.116"
        self.jetson_cmd_port = 8888     # start/stop + object-detection toggle commands
        self.jetson_video_port = 8890   # incoming ZED 2i frames (raw + detection), see VideoFrameReceiver

        # Teensy 4.1: motor control (Sabertooth), servos, MQ2 gas sensor
        # NOTE: the Teensy 4.1 has no networking of its own -- this assumes it (or a
        # bridge in front of it, e.g. an attached Ethernet/WiFi module, or the Jetson
        # relaying UDP<->Serial) is reachable at this IP. Update it to match your setup.
        self.teensy_ip = "192.168.2.117"
        self.teensy_cmd_port = 8888        # servo commands
        self.teensy_telemetry_port = 8889  # incoming gas sensor readings

        self.is_running = False
        self.simulated_mode = True

        self.gas_val = 0.0

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.init_ui()
        self.setup_video_receiver()

        self.receiver = TelemetryReceiver(self.teensy_telemetry_port)
        self.receiver.data_received.connect(self.handle_telemetry)
        self.receiver.start()

        self.sim_timer = QTimer()
        self.sim_timer.timeout.connect(self.simulate_telemetry)
        self.sim_timer.start(1000)

        self.video_watchdog = QTimer()
        self.video_watchdog.timeout.connect(self._check_video_staleness)
        self.video_watchdog.start(1000)

        print("\n" + "=" * 60)
        print("  [+] SHONDHAN BOT - COMMAND CENTER [+]")
        print("=" * 60)
        print(f"Jetson (camera / object detection) : {self.jetson_ip}:{self.jetson_cmd_port} (video in on :{self.jetson_video_port})")
        print(f"Teensy (servos / gas sensor)        : {self.teensy_ip}:{self.teensy_cmd_port} (telemetry in on :{self.teensy_telemetry_port})")
        print("=" * 60 + "\n")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def init_ui(self):
        self.setWindowTitle("Shondhan Bot")
        self.resize(1200, 820)
        self.setMinimumSize(540, 550)

        self.setStyleSheet(f"""
            QMainWindow {{ background-color: {BG}; }}
            QWidget {{ font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif; }}
            QScrollArea {{ border: none; background-color: {BG}; }}
            QGroupBox {{
                font-weight: 700; font-size: 12.5px; color: {TEXT_DIM};
                background-color: {PANEL}; border: 1px solid {BORDER};
                border-radius: 10px; margin-top: 12px; padding: 16px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin; left: 14px; padding: 0 6px; background-color: {PANEL}; color: {TEXT_DIM};
            }}
        """)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.setCentralWidget(scroll)

        main_widget = QWidget()
        main_widget.setObjectName("mainWidget")
        main_widget.setStyleSheet(f"QWidget#mainWidget {{ background-color: {BG}; }}")
        scroll.setWidget(main_widget)
        # QScrollArea's viewport is a separate widget from the scroll area frame itself;
        # without this it falls back to the OS/Qt-style default palette (often dark).
        scroll.viewport().setStyleSheet(f"background-color: {BG};")

        root_layout = QVBoxLayout(main_widget)
        root_layout.setContentsMargins(18, 16, 18, 16)
        root_layout.setSpacing(14)

        # --- HEADER ---
        header_card = QFrame()
        header_card.setStyleSheet(f"QFrame {{ background-color: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; }}")
        header_layout = QHBoxLayout(header_card)
        header_layout.setContentsMargins(16, 12, 16, 12)

        title_box = QVBoxLayout()
        app_title = QLabel("Shondhan Command Console")
        app_title.setStyleSheet(f"font-size: 18px; font-weight: 800; color: {TEXT}; border:none; background:transparent;")
        app_subtitle = QLabel("Jetson Orin Nano \u00b7 ZED 2i \u00b7 Teensy 4.1 \u00b7 Sabertooth")
        app_subtitle.setStyleSheet(f"font-size: 11px; color: {TEXT_DIM}; border:none; background:transparent;")
        title_box.addWidget(app_title)
        title_box.addWidget(app_subtitle)
        header_layout.addLayout(title_box)
        header_layout.addStretch()

        info_badge = QLabel(f"Jetson: {self.jetson_ip}:{self.jetson_cmd_port}   \u00b7   Teensy: {self.teensy_ip}:{self.teensy_cmd_port}")
        info_badge.setStyleSheet(f"background-color: {PANEL_2}; color: {TEXT_DIM}; font-size: 11px; font-weight: 600; padding: 6px 12px; border-radius: 8px;")
        header_layout.addWidget(info_badge)

        self.status_pill = QLabel("STATUS: IDLE")
        self.status_pill.setStyleSheet(self._pill_style("idle"))
        header_layout.addWidget(self.status_pill)

        root_layout.addWidget(header_card)

        # --- BODY: two columns ---
        body_layout = QHBoxLayout()
        body_layout.setSpacing(14)

        # LEFT: camera + gas sensor
        left_col = QVBoxLayout()
        left_col.setSpacing(14)

        video_box = QGroupBox("Camera feed \u2014 ZED 2i")
        v_layout = QVBoxLayout(video_box)
        v_layout.setContentsMargins(8, 12, 8, 8)
        v_layout.setSpacing(8)

        od_row = QHBoxLayout()
        od_row.setSpacing(8)
        od_label = QLabel("Object detection")
        od_label.setStyleSheet(f"color: {TEXT}; font-size: 12px; font-weight: 600; border:none; background:transparent;")
        od_row.addWidget(od_label)
        self.object_detection_enabled = False
        self.od_toggle = ToggleSwitch()
        self.od_toggle.toggled.connect(self.on_object_detection_toggled)
        od_row.addWidget(self.od_toggle)
        od_row.addStretch()
        v_layout.addLayout(od_row)

        self.video_label = QLabel("Awaiting ZED 2i stream\u2026")
        self.video_label.setMinimumHeight(420)
        self.video_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setScaledContents(False)
        self.video_label.setStyleSheet(f"background-color: #0f172a; border-radius: 8px; color: {TEXT_FAINT}; font-size: 12px;")
        v_layout.addWidget(self.video_label)

        self.cam_status_lbl = QLabel("ZED 2i Stream: Connecting...")
        self.cam_status_lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px; font-weight: 600; border:none; background:transparent;")
        self.cam_status_lbl.setAlignment(Qt.AlignCenter)
        v_layout.addWidget(self.cam_status_lbl)

        left_col.addWidget(video_box, 5)

        self.gas_card = SensorCard("MQ2 gas sensor", unit="ppm", max_val=50.0, alert_val=20.0)
        left_col.addWidget(self.gas_card, 1)

        body_layout.addLayout(left_col, 6)

        # RIGHT: system operation, manual nav, servos (2x2)
        right_col = QVBoxLayout()
        right_col.setSpacing(14)

        power_box = QGroupBox("System operation")
        p_layout = QHBoxLayout(power_box)
        p_layout.setSpacing(10)

        self.start_btn = QPushButton("Start")
        self.start_btn.setFixedHeight(40)
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.setStyleSheet(f"""
            QPushButton {{ background-color: {GREEN}; color: #ffffff; font-size: 13px; font-weight: 700; border: none; border-radius: 8px; }}
            QPushButton:hover {{ background-color: {GREEN_HOVER}; }}
            QPushButton:disabled {{ background-color: {BORDER}; color: {TEXT_FAINT}; }}
        """)
        self.start_btn.clicked.connect(self.start_bot)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setFixedHeight(40)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.setStyleSheet(f"""
            QPushButton {{ background-color: transparent; color: {RED}; font-size: 13px; font-weight: 700; border: 1px solid {RED}; border-radius: 8px; }}
            QPushButton:hover {{ background-color: #fdecea; }}
            QPushButton:disabled {{ background-color: {BORDER}; color: {TEXT_FAINT}; border: none; }}
        """)
        self.stop_btn.clicked.connect(self.stop_bot)

        p_layout.addWidget(self.start_btn)
        p_layout.addWidget(self.stop_btn)
        right_col.addWidget(power_box)

        # Servo control, 2x2
        servo_box = QGroupBox("Servo control \u2014 4 channels")
        servo_grid = QGridLayout(servo_box)
        servo_grid.setSpacing(10)
        self.servo_cards = []
        for i in range(1, 5):
            card = ServoCard(i, self.send_servo_command)
            self.servo_cards.append(card)
            servo_grid.addWidget(card, (i - 1) // 2, (i - 1) % 2)
        right_col.addWidget(servo_box)

        body_layout.addLayout(right_col, 4)
        root_layout.addLayout(body_layout)

    def _pill_style(self, mode):
        styles = {
            "idle": f"background-color: {PANEL_2}; color: {TEXT_DIM}; font-size: 12px; font-weight: 700; padding: 6px 16px; border-radius: 16px;",
            "active": f"background-color: #e6f4ec; color: {GREEN}; font-size: 12px; font-weight: 700; padding: 6px 16px; border-radius: 16px;",
            "alert": f"background-color: #fdecea; color: {RED}; font-size: 12px; font-weight: 800; padding: 6px 16px; border-radius: 16px;",
        }
        return styles[mode]

    # ------------------------------------------------------------------
    # Camera
    # ------------------------------------------------------------------
    def setup_video_receiver(self):
        self._video_last_seen = {0: 0.0, 1: 0.0}  # stream_type -> last time a frame of that type arrived
        self._video_cur_pixmap = None
        self.video_receiver = VideoFrameReceiver(self.jetson_video_port)
        self.video_receiver.frame_received.connect(self.handle_video_frame)
        self.video_receiver.start()
        self._refresh_cam_status()

    def handle_video_frame(self, image, stream_type):
        self._video_last_seen[stream_type] = time.time()

        desired_type = 1 if self.object_detection_enabled else 0
        if stream_type != desired_type:
            return  # a frame for the stream we're not currently displaying

        pix = QPixmap.fromImage(image)
        scaled = pix.scaled(self.video_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._video_cur_pixmap = scaled
        self.video_label.setPixmap(scaled)
        self._refresh_cam_status()

    def _check_video_staleness(self):
        self._refresh_cam_status()

    def _refresh_cam_status(self):
        """Combine live-feed staleness + the Object Detection toggle into the status line under the feed."""
        desired_type = 1 if self.object_detection_enabled else 0
        last_seen = self._video_last_seen.get(desired_type, 0.0)
        is_live = last_seen > 0 and (time.time() - last_seen) < 1.5
        mode_label = "Object detection" if desired_type == 1 else "Raw"

        if is_live:
            self.cam_status_lbl.setText(f"ZED 2i Feed: Active \u00b7 {mode_label}")
            self.cam_status_lbl.setStyleSheet(f"color: {GREEN}; font-size: 11px; font-weight: 600; border:none; background:transparent;")
        else:
            if last_seen > 0:
                # we've seen this stream before but it's gone stale -- drop the frozen frame
                self.video_label.setPixmap(QPixmap())
                self.video_label.setText("Signal lost\u2026")
            self.cam_status_lbl.setText(f"ZED 2i Feed: No Signal \u00b7 waiting for {mode_label.lower()} stream on :{self.jetson_video_port}")
            self.cam_status_lbl.setStyleSheet(f"color: #b3690a; font-size: 11px; font-weight: 600; border:none; background:transparent;")

    def on_object_detection_toggled(self, checked):
        self.object_detection_enabled = checked
        self.video_label.setPixmap(QPixmap())
        self.video_label.setText("Switching stream\u2026")
        self._refresh_cam_status()
        self.send_jetson_command("set_object_detection", {"enabled": checked})
        print(f"[JETSON COMMAND] Object detection overlay -> {'ON' if checked else 'OFF'}")

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------
    def start_bot(self):
        self.is_running = True
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status_pill.setText("STATUS: ACTIVE")
        self.status_pill.setStyleSheet(self._pill_style("active"))
        self.send_jetson_command("start")
        self.send_teensy_command("start")
        print("[ACTION] Start command sent to Jetson + Teensy.")

    def stop_bot(self):
        self.is_running = False
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_pill.setText("STATUS: IDLE")
        self.status_pill.setStyleSheet(self._pill_style("idle"))
        self.send_jetson_command("stop")
        self.send_teensy_command("stop")
        print("[ACTION] Stop command sent to Jetson + Teensy.")

    # ------------------------------------------------------------------
    # Servos (commands go to the Teensy over UDP)
    # ------------------------------------------------------------------
    def send_servo_command(self, index, angle):
        if not self.is_running:
            QMessageBox.information(self, "System Idle", "Please click 'Start' before sending servo commands.")
            return
        self.send_teensy_command("servo", {"channel": index, "angle": angle})
        print(f"[TEENSY COMMAND] Servo {index} -> {angle}\u00b0")

    # ------------------------------------------------------------------
    # Telemetry
    # ------------------------------------------------------------------
    def handle_telemetry(self, payload):
        self.simulated_mode = False
        self.gas_val = payload.get("gas", payload.get("mq2", payload.get("lpg", self.gas_val)))
        self.update_gas_ui()

    def simulate_telemetry(self):
        if not self.is_running:
            return
        if self.simulated_mode:
            self.gas_val = max(0.0, self.gas_val + random.uniform(-0.3, 0.3))
            print(f"[TEENSY SIMULATION] MQ2: {self.gas_val:.2f} ppm (no real telemetry received yet on :{self.teensy_telemetry_port})")
            self.update_gas_ui()

    def update_gas_ui(self):
        is_alert = self.gas_card.update_value(self.gas_val)
        if is_alert:
            self.status_pill.setText("ALERT: GAS HAZARD")
            self.status_pill.setStyleSheet(self._pill_style("alert"))
        elif self.is_running:
            self.status_pill.setText("STATUS: ACTIVE")
            self.status_pill.setStyleSheet(self._pill_style("active"))

    # ------------------------------------------------------------------
    # Networking / lifecycle
    # ------------------------------------------------------------------
    def _send(self, ip, port, command, data=None):
        try:
            payload = {"command": command, "data": data or {}}
            json_data = json.dumps(payload)
            self.sock.sendto(json_data.encode('utf-8'), (ip, port))
        except Exception as e:
            print(f"[UDP ERROR] {command} -> {ip}:{port} failed: {e}")

    def send_jetson_command(self, command, data=None):
        self._send(self.jetson_ip, self.jetson_cmd_port, command, data)

    def send_teensy_command(self, command, data=None):
        self._send(self.teensy_ip, self.teensy_cmd_port, command, data)

    def closeEvent(self, event):
        self.receiver.stop()
        self.receiver.wait()
        self.video_receiver.stop()
        self.video_receiver.wait()
        self.sock.close()
        event.accept()


def main():
    app = QApplication(sys.argv)

    # Force a light palette so any widget chrome not covered by our stylesheets
    # (scrollbars, native menus, etc.) doesn't fall back to a dark OS/Qt theme.
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(BG))
    palette.setColor(QPalette.WindowText, QColor(TEXT))
    palette.setColor(QPalette.Base, QColor(PANEL))
    palette.setColor(QPalette.AlternateBase, QColor(PANEL_2))
    palette.setColor(QPalette.Text, QColor(TEXT))
    palette.setColor(QPalette.Button, QColor(PANEL))
    palette.setColor(QPalette.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ToolTipBase, QColor(PANEL))
    palette.setColor(QPalette.ToolTipText, QColor(TEXT))
    app.setPalette(palette)

    dashboard = FormalDashboard()
    dashboard.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()