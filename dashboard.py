# dashboard.py
import sys
import json
import socket
import random
import re
from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *
from PySide6.QtMultimedia import QCamera, QMediaDevices, QMediaCaptureSession
from PySide6.QtMultimediaWidgets import QVideoWidget

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

NAV_MIN, NAV_MAX, NAV_NEUTRAL = 1000, 2000, 1500


class TelemetryReceiver(QThread):
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
            print(f"[UDP RECEIVER] Listening for Jetson Orin telemetry on port {self.port}...")
        except Exception as e:
            print(f"[UDP RECEIVER] Bind warning for port {self.port}: {e}")
            return

        self.sock.settimeout(0.5)
        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
                payload = json.loads(data.decode('utf-8'))
                self.data_received.emit(payload)
                print(f"[JETSON TELEMETRY] from {addr[0]}:{addr[1]} -> {payload}")
            except socket.timeout:
                continue
            except Exception:
                pass

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


class NavGauge(QWidget):
    """Horizontal channel gauge, 1000-2000, filled from the 1500 neutral centre."""
    def __init__(self):
        super().__init__()
        self.setFixedHeight(10)
        self.value = NAV_NEUTRAL

    def set_value(self, v):
        self.value = v
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # track
        painter.setPen(QPen(QColor(BORDER), 1))
        painter.setBrush(QColor(PANEL_2))
        painter.drawRoundedRect(0, 1, w - 1, h - 2, 4, 4)

        # fill, from centre (1500) toward current value
        pct = (self.value - NAV_MIN) / (NAV_MAX - NAV_MIN)
        center_x = w / 2
        val_x = pct * w
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(ORANGE))
        if val_x >= center_x:
            painter.drawRoundedRect(int(center_x), 1, int(val_x - center_x), h - 2, 4, 4)
        else:
            painter.drawRoundedRect(int(val_x), 1, int(center_x - val_x), h - 2, 4, 4)

        # centre tick
        painter.setPen(QPen(QColor(TEXT_FAINT), 1))
        painter.drawLine(int(center_x), -3, int(center_x), h + 3)


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
        self.bot_ip = "192.168.2.116"      # Jetson Orin IP
        self.udp_port = 8888                # Command port to Jetson
        self.telemetry_port = 8889          # Telemetry reception port

        self.is_running = False
        self.simulated_mode = True

        self.gas_val = 0.0
        self.throttle = NAV_NEUTRAL
        self.steering = NAV_NEUTRAL
        self.keys_held = {"w": False, "a": False, "s": False, "d": False}

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.init_ui()
        self.setup_camera()

        # global WASD capture regardless of which widget currently has focus
        QApplication.instance().installEventFilter(self)

        self.receiver = TelemetryReceiver(self.telemetry_port)
        self.receiver.data_received.connect(self.handle_telemetry)
        self.receiver.start()

        self.sim_timer = QTimer()
        self.sim_timer.timeout.connect(self.simulate_telemetry)
        self.sim_timer.start(1000)

        print("\n" + "=" * 60)
        print("  [+] SHONDHAN BOT - JETSON ORIN COMMAND CENTER [+]")
        print("=" * 60)
        print(f"Target Bot IP (Jetson)  : {self.bot_ip}")
        print(f"UDP Command Port        : {self.udp_port}")
        print(f"UDP Telemetry Port      : {self.telemetry_port}")
        print("=" * 60 + "\n")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def init_ui(self):
        self.setWindowTitle("Shondhan Bot")
        self.resize(1200, 820)
        self.setMinimumSize(540, 550)
        self.setFocusPolicy(Qt.StrongFocus)

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

        info_badge = QLabel(f"Jetson IP: {self.bot_ip}:{self.udp_port}")
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

        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(420)
        self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_widget.setStyleSheet("background-color: #0f172a; border-radius: 8px;")
        v_layout.addWidget(self.video_widget)

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

        # Manual navigation (WASD)
        nav_box = QGroupBox("Manual navigation \u2014 WASD")
        nav_layout = QVBoxLayout(nav_box)
        nav_layout.setSpacing(10)

        keys_grid = QGridLayout()
        keys_grid.setSpacing(6)
        self.key_lbls = {}
        for key, (r, c) in {"w": (0, 1), "a": (1, 0), "s": (1, 1), "d": (1, 2)}.items():
            lbl = QLabel(key.upper())
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setFixedSize(40, 36)
            self.key_lbls[key] = lbl
            keys_grid.addWidget(lbl, r, c)
        keys_wrap = QHBoxLayout()
        keys_wrap.addStretch()
        keys_wrap.addLayout(keys_grid)
        keys_wrap.addStretch()
        nav_layout.addLayout(keys_wrap)
        self._paint_keys()

        self.throttle_lbl, self.throttle_gauge = self._build_gauge_row(nav_layout, "Throttle (W / S)")
        self.steer_lbl, self.steer_gauge = self._build_gauge_row(nav_layout, "Steering (A / D)")

        hint = QLabel("Click on the window, then hold W / A / S / D. 1500 is neutral, 2000 / 1000 is full deflection; releasing a key snaps it back to neutral.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {TEXT_FAINT}; font-size: 10.5px; border:none; background:transparent;")
        nav_layout.addWidget(hint)

        right_col.addWidget(nav_box)

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

    def _build_gauge_row(self, parent_layout, title):
        row = QVBoxLayout()
        row.setSpacing(4)
        head = QHBoxLayout()
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color: {TEXT_DIM}; font-size: 11px; border:none; background:transparent;")
        val_lbl = QLabel(str(NAV_NEUTRAL))
        val_lbl.setStyleSheet(f"color: {TEXT}; font-size: 11px; font-weight: 700; font-family: Consolas, monospace; border:none; background:transparent;")
        head.addWidget(title_lbl)
        head.addStretch()
        head.addWidget(val_lbl)
        row.addLayout(head)
        gauge = NavGauge()
        row.addWidget(gauge)
        parent_layout.addLayout(row)
        return val_lbl, gauge

    def _paint_keys(self):
        for key, lbl in self.key_lbls.items():
            active = self.keys_held[key]
            if active:
                lbl.setStyleSheet(f"""
                    background-color: {ORANGE}; color: #2a1103; border: 1px solid {ORANGE};
                    border-radius: 6px; font-family: Consolas, monospace; font-weight: 700; font-size: 13px;
                """)
            else:
                lbl.setStyleSheet(f"""
                    background-color: {PANEL_2}; color: {TEXT_DIM}; border: 1px solid {BORDER};
                    border-radius: 6px; font-family: Consolas, monospace; font-size: 13px;
                """)

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
    def setup_camera(self):
        try:
            cameras = QMediaDevices.videoInputs()
            if cameras:
                self.camera = QCamera(cameras[0])
                self.capture_session = QMediaCaptureSession()
                self.capture_session.setCamera(self.camera)
                self.capture_session.setVideoOutput(self.video_widget)
                self.camera.start()
                self.cam_status_lbl.setText("ZED 2i Feed: Active")
                self.cam_status_lbl.setStyleSheet(f"color: {GREEN}; font-size: 11px; font-weight: 600; border:none; background:transparent;")
                print("[ZED 2i CAMERA] Local capture session connected.")
            else:
                self.cam_status_lbl.setText("ZED 2i Feed: Awaiting Stream from Jetson")
                self.cam_status_lbl.setStyleSheet(f"color: #b3690a; font-size: 11px; font-weight: 600; border:none; background:transparent;")
                print("[ZED 2i CAMERA] Ready for network stream.")
        except Exception as e:
            print(f"[ZED 2i CAMERA ERROR] {e}")
            self.cam_status_lbl.setText("Camera Stream Error")
            self.cam_status_lbl.setStyleSheet(f"color: {RED}; font-size: 11px; font-weight: 600; border:none; background:transparent;")

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------
    def start_bot(self):
        self.is_running = True
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.status_pill.setText("STATUS: ACTIVE")
        self.status_pill.setStyleSheet(self._pill_style("active"))
        self.send_udp_command("start")
        print("[ACTION] Start command sent to Jetson Orin.")

    def stop_bot(self):
        self.is_running = False
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.status_pill.setText("STATUS: IDLE")
        self.status_pill.setStyleSheet(self._pill_style("idle"))
        # zero out manual nav so the rover doesn't keep the last held direction
        self.keys_held = {k: False for k in self.keys_held}
        self._update_nav()
        self.send_udp_command("stop")
        print("[ACTION] Stop command sent to Jetson Orin.")

    # ------------------------------------------------------------------
    # Manual navigation (WASD -> throttle / steering pulses)
    # ------------------------------------------------------------------
    # An application-wide event filter is used instead of overriding
    # keyPressEvent/keyReleaseEvent on the window: with a QScrollArea as the
    # central widget, keyboard focus usually lands on whichever button or
    # field was last clicked, and QMainWindow.keyPressEvent never sees the
    # key. The filter intercepts W/A/S/D no matter which widget has focus.
    # It's safe to swallow these globally since no field in this app accepts
    # letter input (servo custom angles are digits-only).
    def eventFilter(self, obj, event):
        if event.type() in (QEvent.KeyPress, QEvent.KeyRelease) and not event.isAutoRepeat():
            if event.key() in (Qt.Key_W, Qt.Key_A, Qt.Key_S, Qt.Key_D):
                self._handle_key(event.key(), event.type() == QEvent.KeyPress)
                return True
        return super().eventFilter(obj, event)

    def _handle_key(self, qt_key, pressed):
        mapping = {Qt.Key_W: "w", Qt.Key_A: "a", Qt.Key_S: "s", Qt.Key_D: "d"}
        key = mapping.get(qt_key)
        if key is None:
            return
        if not self.is_running:
            if pressed:
                print("[NOTICE] Click 'Start' first to enable Sabertooth motor control.")
            return
        self.keys_held[key] = pressed
        self._update_nav()

    def _update_nav(self):
        self.throttle = NAV_MAX if self.keys_held["w"] else NAV_MIN if self.keys_held["s"] else NAV_NEUTRAL
        self.steering = NAV_MAX if self.keys_held["a"] else NAV_MIN if self.keys_held["d"] else NAV_NEUTRAL

        self.throttle_lbl.setText(str(self.throttle))
        self.steer_lbl.setText(str(self.steering))
        self.throttle_gauge.set_value(self.throttle)
        self.steer_gauge.set_value(self.steering)
        self._paint_keys()

        self.send_udp_command("nav", {"throttle": self.throttle, "steering": self.steering})
        print(f"[JETSON COMMAND] Nav -> throttle={self.throttle} steering={self.steering}")

    # ------------------------------------------------------------------
    # Servos
    # ------------------------------------------------------------------
    def send_servo_command(self, index, angle):
        if not self.is_running:
            QMessageBox.information(self, "System Idle", "Please click 'Start' before sending servo commands.")
            return
        self.send_udp_command("servo", {"channel": index, "angle": angle})
        print(f"[JETSON COMMAND] Servo {index} -> {angle}\u00b0")

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
            print(f"[TEENSY SIMULATION] MQ2: {self.gas_val:.2f} ppm")
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
    def send_udp_command(self, command, data=None):
        try:
            payload = {"command": command, "data": data or {}}
            json_data = json.dumps(payload)
            self.sock.sendto(json_data.encode('utf-8'), (self.bot_ip, self.udp_port))
        except Exception as e:
            print(f"[UDP ERROR] {e}")

    def closeEvent(self, event):
        self.receiver.stop()
        self.receiver.wait()
        if hasattr(self, 'camera') and self.camera:
            self.camera.stop()
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