# dashboard.py
import sys
import json
import socket
import random
from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *
from PySide6.QtMultimedia import QCamera, QMediaDevices, QMediaCaptureSession
from PySide6.QtMultimediaWidgets import QVideoWidget

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


class SensorCard(QFrame):
    """Clean metric card tailored for MQ Gas Sensors connected via Teensy"""
    def __init__(self, title, unit="ppm", max_val=100.0, accent_color="#3b82f6"):
        super().__init__()
        self.unit = unit
        self.max_val = max_val
        self.accent_color = accent_color
        
        self.setStyleSheet("""
            QFrame {
                background-color: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 12px;
            }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        
        hdr_layout = QHBoxLayout()
        self.title_lbl = QLabel(title)
        self.title_lbl.setStyleSheet("color: #6b7280; font-size: 11px; font-weight: 600; text-transform: uppercase;")
        hdr_layout.addWidget(self.title_lbl)
        hdr_layout.addStretch()
        
        self.val_lbl = QLabel(f"0.00 {unit}")
        self.val_lbl.setStyleSheet("color: #1f2937; font-size: 15px; font-weight: 700;")
        hdr_layout.addWidget(self.val_lbl)
        layout.addLayout(hdr_layout)
        
        self.progress = QProgressBar()
        self.progress.setFixedHeight(8)
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setStyleSheet(f"""
            QProgressBar {{
                background-color: #f3f4f6;
                border: none;
                border-radius: 4px;
            }}
            QProgressBar::chunk {{
                background-color: {accent_color};
                border-radius: 4px;
            }}
        """)
        layout.addWidget(self.progress)
        
    def update_value(self, val, is_alert=False):
        self.val_lbl.setText(f"{val:.2f} {self.unit}")
        perc = int(min(100, max(0, (val / self.max_val) * 100)))
        self.progress.setValue(perc)
        
        if is_alert:
            self.val_lbl.setStyleSheet("color: #ef4444; font-size: 15px; font-weight: 800;")
            self.progress.setStyleSheet("""
                QProgressBar { background-color: #fee2e2; border: none; border-radius: 4px; }
                QProgressBar::chunk { background-color: #ef4444; border-radius: 4px; }
            """)
        else:
            self.val_lbl.setStyleSheet("color: #1f2937; font-size: 15px; font-weight: 700;")
            self.progress.setStyleSheet(f"""
                QProgressBar {{ background-color: #f3f4f6; border: none; border-radius: 4px; }}
                QProgressBar::chunk {{ background-color: {self.accent_color}; border-radius: 4px; }}
            """)


class FormalDashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.bot_ip = "192.168.2.116"      # Jetson Orin IP
        self.udp_port = 8888              # Command port to Jetson
        self.telemetry_port = 8889         # Telemetry reception port
        
        self.is_running = False
        self.simulated_mode = True
        
        self.mq2_lpg = 0.0
        self.mq7_co = 0.0
        self.mq135_smoke = 0.0
        self.bot_x = 0.0
        self.bot_y = 0.0
        
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        self.init_ui()
        self.setup_camera()
        
        self.receiver = TelemetryReceiver(self.telemetry_port)
        self.receiver.data_received.connect(self.handle_telemetry)
        self.receiver.start()
        
        self.sim_timer = QTimer()
        self.sim_timer.timeout.connect(self.simulate_telemetry)
        self.sim_timer.start(1000)
        
        print("\n" + "="*60)
        print("  [+] SHONDHAN BOT - JETSON ORIN COMMAND CENTER [+]")
        print("="*60)
        print(f"Target Bot IP (Jetson)  : {self.bot_ip}")
        print(f"UDP Command Port        : {self.udp_port}")
        print(f"UDP Telemetry Port      : {self.telemetry_port}")
        print("Responsive Layout       : QScrollArea + Dynamic Grid Enabled")
        print("="*60 + "\n")
        
    def init_ui(self):
        self.setWindowTitle("Shondhan Bot")
        self.resize(1200, 820)
        self.setMinimumSize(540, 550)  # Allows smooth scaling down to small screens
        
        self.setStyleSheet("""
            QMainWindow {
                background-color: #f8fafc;
            }
            QWidget {
                font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;
            }
            QScrollArea {
                border: none;
                background-color: #f8fafc;
            }
            QGroupBox {
                font-weight: 700;
                font-size: 13px;
                color: #334155;
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 12px;
                margin-top: 12px;
                padding: 15px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 14px;
                padding: 0 6px;
                background-color: #ffffff;
            }
        """)
        
        # Responsive Scroll Area Wrapper for small screens & phones
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.setCentralWidget(scroll)
        
        main_widget = QWidget()
        scroll.setWidget(main_widget)
        
        root_layout = QVBoxLayout(main_widget)
        root_layout.setContentsMargins(18, 16, 18, 16)
        root_layout.setSpacing(14)
        
        # --- 1. HEADER BAR ---
        header_card = QFrame()
        header_card.setStyleSheet("""
            QFrame {
                background-color: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 14px;
            }
        """)
        header_layout = QHBoxLayout(header_card)
        header_layout.setContentsMargins(16, 12, 16, 12)
        
        title_box = QVBoxLayout()
        app_title = QLabel("Shondhan Command Centre")
        app_title.setStyleSheet("font-size: 18px; font-weight: 800; color: #0f172a;")
        app_subtitle = QLabel("Jetson Orin | ZED 2i Video | Teensy MQ Gas Sensors | Sabertooth Driver")
        app_subtitle.setStyleSheet("font-size: 11px; color: #64748b;")
        title_box.addWidget(app_title)
        title_box.addWidget(app_subtitle)
        header_layout.addLayout(title_box)
        
        header_layout.addStretch()
        
        info_badge = QLabel(f"Jetson IP: {self.bot_ip}:{self.udp_port}")
        info_badge.setStyleSheet("""
            background-color: #f1f5f9;
            color: #475569;
            font-size: 11px;
            font-weight: 600;
            padding: 6px 12px;
            border-radius: 8px;
        """)
        header_layout.addWidget(info_badge)
        
        self.status_pill = QLabel("STATUS: IDLE")
        self.status_pill.setStyleSheet("""
            background-color: #f1f5f9;
            color: #64748b;
            font-size: 12px;
            font-weight: 700;
            padding: 6px 16px;
            border-radius: 16px;
        """)
        header_layout.addWidget(self.status_pill)
        
        root_layout.addWidget(header_card)
        
        # --- 2. MAIN CONTENT BODY (Responsive Flex Grid) ---
        body_layout = QHBoxLayout()
        body_layout.setSpacing(14)
        
        # LEFT COLUMN: ZED 2i Stream & MQ Gas Sensors
        left_col = QVBoxLayout()
        left_col.setSpacing(14)
        
        video_box = QGroupBox("ZED 2i Camera Feed")
        v_layout = QVBoxLayout(video_box)
        v_layout.setContentsMargins(8, 12, 8, 8)
        
        self.video_widget = QVideoWidget()
        self.video_widget.setMinimumHeight(480)
        self.video_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video_widget.setStyleSheet("background-color: #0f172a; border-radius: 8px;")
        v_layout.addWidget(self.video_widget)
        
        self.cam_status_lbl = QLabel("ZED 2i Stream: Connecting...")
        self.cam_status_lbl.setStyleSheet("color: #64748b; font-size: 11px; font-weight: 600;")
        self.cam_status_lbl.setAlignment(Qt.AlignCenter)
        v_layout.addWidget(self.cam_status_lbl)
        
        left_col.addWidget(video_box, 5)
        
        # Gas Sensor Cards Container
        gas_box = QGroupBox("MQ Gas Sensors (Teensy Telemetry)")
        gas_layout = QHBoxLayout(gas_box)
        gas_layout.setSpacing(10)
        
        self.card_lpg = SensorCard("MQ-2 (LPG / Gas)", unit="ppm", max_val=50.0, accent_color="#10b981")
        self.card_co = SensorCard("MQ-7 (Carbon Monoxide)", unit="ppm", max_val=15.0, accent_color="#f59e0b")
        self.card_smoke = SensorCard("MQ-135 (Smoke)", unit="ppm", max_val=30.0, accent_color="#ef4444")
        
        gas_layout.addWidget(self.card_lpg)
        gas_layout.addWidget(self.card_co)
        gas_layout.addWidget(self.card_smoke)
        
        left_col.addWidget(gas_box, 1)
        body_layout.addLayout(left_col, 5)
        
        # RIGHT COLUMN: Sabertooth Controls & Navigation
        right_col = QVBoxLayout()
        right_col.setSpacing(14)
        
        # System Operation
        power_box = QGroupBox("System Operation")
        p_layout = QHBoxLayout(power_box)
        p_layout.setSpacing(10)
        
        self.start_btn = QPushButton("Start")
        self.start_btn.setFixedHeight(40)
        self.start_btn.setCursor(Qt.PointingHandCursor)
        self.start_btn.setStyleSheet("""
            QPushButton {
                background-color: #10b981;
                color: #ffffff;
                font-size: 13px;
                font-weight: 700;
                border: none;
                border-radius: 8px;
            }
            QPushButton:hover { background-color: #059669; }
            QPushButton:disabled { background-color: #cbd5e1; color: #94a3b8; }
        """)
        self.start_btn.clicked.connect(self.start_bot)
        
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setFixedHeight(40)
        self.stop_btn.setEnabled(False)
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background-color: #ef4444;
                color: #ffffff;
                font-size: 13px;
                font-weight: 700;
                border: none;
                border-radius: 8px;
            }
            QPushButton:hover { background-color: #dc2626; }
            QPushButton:disabled { background-color: #cbd5e1; color: #94a3b8; }
        """)
        self.stop_btn.clicked.connect(self.stop_bot)
        
        p_layout.addWidget(self.start_btn)
        p_layout.addWidget(self.stop_btn)
        right_col.addWidget(power_box)
        
        # Sabertooth Movement Controls
        nav_box = QGroupBox("Sabertooth Motor Control")
        nav_layout = QVBoxLayout(nav_box)
        nav_layout.setSpacing(12)
        
        dpad_grid = QGridLayout()
        dpad_grid.setSpacing(6)
        
        btn_u = QPushButton("Forward")
        btn_d = QPushButton("Reverse")
        btn_l = QPushButton("Left")
        btn_r = QPushButton("Right")
        
        dpad_btn_style = """
            QPushButton {
                background-color: #f1f5f9;
                color: #334155;
                font-size: 11px;
                font-weight: 600;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                padding: 10px;
            }
            QPushButton:hover { background-color: #e2e8f0; color: #0f172a; }
            QPushButton:pressed { background-color: #cbd5e1; }
        """
        for b in [btn_u, btn_d, btn_l, btn_r]:
            b.setStyleSheet(dpad_btn_style)
            b.setCursor(Qt.PointingHandCursor)
            
        dpad_grid.addWidget(btn_u, 0, 1)
        dpad_grid.addWidget(btn_l, 1, 0)
        dpad_grid.addWidget(btn_r, 1, 2)
        dpad_grid.addWidget(btn_d, 2, 1)
        
        btn_u.clicked.connect(lambda: self.handle_drive("FORWARD"))
        btn_d.clicked.connect(lambda: self.handle_drive("REVERSE"))
        btn_l.clicked.connect(lambda: self.handle_drive("LEFT"))
        btn_r.clicked.connect(lambda: self.handle_drive("RIGHT"))
        
        nav_layout.addLayout(dpad_grid)
        
        coord_layout = QGridLayout()
        coord_layout.setSpacing(6)
        
        lbl_x = QLabel("Target X:")
        lbl_x.setStyleSheet("color: #475569; font-size: 11px; font-weight: 600;")
        self.input_x = QLineEdit()
        self.input_x.setPlaceholderText("e.g. 100")
        self.input_x.setStyleSheet("""
            QLineEdit {
                background-color: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 6px;
                font-size: 11px;
            }
            QLineEdit:focus { border-color: #3b82f6; background-color: #ffffff; }
        """)
        
        lbl_y = QLabel("Target Y:")
        lbl_y.setStyleSheet("color: #475569; font-size: 11px; font-weight: 600;")
        self.input_y = QLineEdit()
        self.input_y.setPlaceholderText("e.g. -50")
        self.input_y.setStyleSheet("""
            QLineEdit {
                background-color: #f8fafc;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 6px;
                font-size: 11px;
            }
            QLineEdit:focus { border-color: #3b82f6; background-color: #ffffff; }
        """)
        
        coord_layout.addWidget(lbl_x, 0, 0)
        coord_layout.addWidget(self.input_x, 0, 1)
        coord_layout.addWidget(lbl_y, 1, 0)
        coord_layout.addWidget(self.input_y, 1, 1)
        
        self.send_coord_btn = QPushButton("Send Coordinates")
        self.send_coord_btn.setCursor(Qt.PointingHandCursor)
        self.send_coord_btn.setStyleSheet("""
            QPushButton {
                background-color: #3b82f6;
                color: #ffffff;
                font-size: 11px;
                font-weight: 600;
                border: none;
                border-radius: 6px;
                padding: 8px;
            }
            QPushButton:hover { background-color: #2563eb; }
        """)
        self.send_coord_btn.clicked.connect(self.send_custom_coordinates)
        coord_layout.addWidget(self.send_coord_btn, 2, 0, 1, 2)
        
        nav_layout.addLayout(coord_layout)
        right_col.addWidget(nav_box)
        
        # Position & Diagnostics Box
        pos_box = QGroupBox("Bot Status & Diagnostics")
        pos_layout = QVBoxLayout(pos_box)
        pos_layout.setSpacing(8)
        
        pos_info_layout = QHBoxLayout()
        lbl_pos_title = QLabel("Position:")
        lbl_pos_title.setStyleSheet("color: #64748b; font-size: 11px; font-weight: 600;")
        self.pos_val_lbl = QLabel("(X: 0.0, Y: 0.0)")
        self.pos_val_lbl.setStyleSheet("color: #0f172a; font-size: 12px; font-weight: 700;")
        pos_info_layout.addWidget(lbl_pos_title)
        pos_info_layout.addStretch()
        pos_info_layout.addWidget(self.pos_val_lbl)
        pos_layout.addLayout(pos_info_layout)
        
        self.test_gas_btn = QPushButton("Test Gas Hazard Threshold")
        self.test_gas_btn.setCursor(Qt.PointingHandCursor)
        self.test_gas_btn.setStyleSheet("""
            QPushButton {
                background-color: #f59e0b;
                color: #ffffff;
                font-size: 11px;
                font-weight: 700;
                border: none;
                border-radius: 6px;
                padding: 8px;
            }
            QPushButton:hover { background-color: #d97706; }
        """)
        self.test_gas_btn.clicked.connect(self.simulate_gas_spike)
        pos_layout.addWidget(self.test_gas_btn)
        
        right_col.addWidget(pos_box)
        body_layout.addLayout(right_col, 2)
        
        root_layout.addLayout(body_layout)
        
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
                self.cam_status_lbl.setStyleSheet("color: #10b981; font-size: 11px; font-weight: 600;")
                print("[ZED 2i CAMERA] Local capture session connected.")
            else:
                self.cam_status_lbl.setText("ZED 2i Feed: Awaiting Stream from Jetson")
                self.cam_status_lbl.setStyleSheet("color: #f59e0b; font-size: 11px; font-weight: 600;")
                print("[ZED 2i CAMERA] Ready for network stream.")
        except Exception as e:
            print(f"[ZED 2i CAMERA ERROR] {e}")
            self.cam_status_lbl.setText("Camera Stream Error")
            self.cam_status_lbl.setStyleSheet("color: #ef4444; font-size: 11px; font-weight: 600;")

    def start_bot(self):
        self.is_running = True
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        
        self.status_pill.setText("STATUS: ACTIVE")
        self.status_pill.setStyleSheet("""
            background-color: #dcfce7;
            color: #15803d;
            font-size: 12px;
            font-weight: 700;
            padding: 6px 16px;
            border-radius: 16px;
        """)
        
        self.send_udp_command("start")
        print("[ACTION] Start command sent to Jetson Orin.")

    def stop_bot(self):
        self.is_running = False
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        
        self.status_pill.setText("STATUS: IDLE")
        self.status_pill.setStyleSheet("""
            background-color: #f1f5f9;
            color: #64748b;
            font-size: 12px;
            font-weight: 700;
            padding: 6px 16px;
            border-radius: 16px;
        """)
        
        self.send_udp_command("stop")
        print("[ACTION] Stop command sent to Jetson Orin.")

    def handle_drive(self, direction):
        if not self.is_running:
            print("[NOTICE] Click 'Start' first to enable Sabertooth motor control.")
            return
            
        step = 10.0
        v, w = 0.0, 0.0
        if direction == "FORWARD":
            self.bot_y += step
            v = 1.0
        elif direction == "REVERSE":
            self.bot_y -= step
            v = -1.0
        elif direction == "LEFT":
            self.bot_x -= step
            w = -1.0
        elif direction == "RIGHT":
            self.bot_x += step
            w = 1.0
            
        self.update_telemetry_ui()
        self.send_udp_command("move", {
            "x": self.bot_x,
            "y": self.bot_y,
            "v_linear": v,
            "w_angular": w,
            "direction": direction
        })
        print(f"[JETSON COMMAND] Drive: {direction} (v={v}, w={w}) | Target Pos: ({self.bot_x:.1f}, {self.bot_y:.1f})")

    def send_custom_coordinates(self):
        if not self.is_running:
            QMessageBox.information(self, "System Idle", "Please click 'Start' before sending target coordinates.")
            return
            
        try:
            val_x = float(self.input_x.text())
            val_y = float(self.input_y.text())
            
            self.bot_x = val_x
            self.bot_y = val_y
            self.update_telemetry_ui()
            
            self.send_udp_command("move", {"x": self.bot_x, "y": self.bot_y})
            print(f"[JETSON COMMAND] Navigation Target -> X: {self.bot_x:.1f}, Y: {self.bot_y:.1f}")
            
            self.input_x.clear()
            self.input_y.clear()
        except ValueError:
            QMessageBox.warning(self, "Invalid Input", "Please enter valid numeric values for X and Y.")

    def handle_telemetry(self, payload):
        self.simulated_mode = False
        
        self.mq2_lpg = payload.get("lpg", payload.get("mq2", self.mq2_lpg))
        self.mq7_co = payload.get("co", payload.get("mq7", self.mq7_co))
        self.mq135_smoke = payload.get("smoke", payload.get("mq135", self.mq135_smoke))
        self.bot_x = payload.get("x", self.bot_x)
        self.bot_y = payload.get("y", self.bot_y)
        
        self.update_telemetry_ui()

    def simulate_telemetry(self):
        if not self.is_running:
            return
            
        if self.simulated_mode:
            self.mq2_lpg = max(0.0, self.mq2_lpg + random.uniform(-0.3, 0.3))
            self.mq7_co = max(0.0, self.mq7_co + random.uniform(-0.1, 0.1))
            self.mq135_smoke = max(0.0, self.mq135_smoke + random.uniform(-0.2, 0.2))
            
            print(f"[TEENSY SIMULATION] MQ-2 LPG: {self.mq2_lpg:.2f} ppm | MQ-7 CO: {self.mq7_co:.2f} ppm | MQ-135 Smoke: {self.mq135_smoke:.2f} ppm | Pos: ({self.bot_x:.1f}, {self.bot_y:.1f})")
            self.update_telemetry_ui()

    def update_telemetry_ui(self):
        lpg_alert = self.mq2_lpg > 20.0
        co_alert = self.mq7_co > 5.0
        smoke_alert = self.mq135_smoke > 10.0
        
        self.card_lpg.update_value(self.mq2_lpg, is_alert=lpg_alert)
        self.card_co.update_value(self.mq7_co, is_alert=co_alert)
        self.card_smoke.update_value(self.mq135_smoke, is_alert=smoke_alert)
        
        self.pos_val_lbl.setText(f"(X: {self.bot_x:.1f}, Y: {self.bot_y:.1f})")
        
        if lpg_alert or co_alert or smoke_alert:
            self.status_pill.setText("ALERT: GAS HAZARD")
            self.status_pill.setStyleSheet("""
                background-color: #fee2e2;
                color: #b91c1c;
                font-size: 12px;
                font-weight: 800;
                padding: 6px 16px;
                border-radius: 16px;
            """)
        elif self.is_running:
            self.status_pill.setText("STATUS: ACTIVE")
            self.status_pill.setStyleSheet("""
                background-color: #dcfce7;
                color: #15803d;
                font-size: 12px;
                font-weight: 700;
                padding: 6px 16px;
                border-radius: 16px;
            """)

    def simulate_gas_spike(self):
        if not self.is_running:
            print("[NOTICE] Click 'Start' first to test hazard threshold UI.")
            return
        print("[SIMULATION TEST] Triggering MQ gas hazard test values...")
        self.mq2_lpg = 24.8
        self.mq7_co = 6.4
        self.mq135_smoke = 12.1
        self.update_telemetry_ui()

    def send_udp_command(self, command, data=None):
        try:
            payload = {
                "command": command,
                "data": data or {}
            }
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
    dashboard = FormalDashboard()
    dashboard.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()