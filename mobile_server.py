# mobile_server.py
import os
import sys
import json
import socket
import threading
import time
import random
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

BOT_IP = "192.168.2.116"
UDP_PORT = 8888
TELEMETRY_PORT = 8889
SERVER_PORT = 5000

state = {
    "is_running": False,
    "mq2_lpg": 0.0,
    "mq7_co": 0.0,
    "mq135_smoke": 0.0,
    "bot_x": 0.0,
    "bot_y": 0.0,
    "status": "IDLE"
}

udp_cmd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def udp_telemetry_listener():
    recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    recv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        recv_sock.bind(("0.0.0.0", TELEMETRY_PORT))
        print(f"[MOBILE SERVER] Listening for Jetson telemetry on UDP port {TELEMETRY_PORT}")
    except Exception as e:
        print(f"[MOBILE SERVER] UDP Bind warning: {e}")
        return

    recv_sock.settimeout(0.5)
    while True:
        try:
            data, addr = recv_sock.recvfrom(2048)
            payload = json.loads(data.decode('utf-8'))
            state["mq2_lpg"] = payload.get("lpg", payload.get("mq2", state["mq2_lpg"]))
            state["mq7_co"] = payload.get("co", payload.get("mq7", state["mq7_co"]))
            state["mq135_smoke"] = payload.get("smoke", payload.get("mq135", state["mq135_smoke"]))
            state["bot_x"] = payload.get("x", state["bot_x"])
            state["bot_y"] = payload.get("y", state["bot_y"])
        except socket.timeout:
            pass
        except Exception:
            pass

def telemetry_simulator():
    while True:
        if state["is_running"]:
            state["mq2_lpg"] = max(0.0, state["mq2_lpg"] + random.uniform(-0.3, 0.3))
            state["mq7_co"] = max(0.0, state["mq7_co"] + random.uniform(-0.1, 0.1))
            state["mq135_smoke"] = max(0.0, state["mq135_smoke"] + random.uniform(-0.2, 0.2))
            
            if state["mq2_lpg"] > 20.0 or state["mq7_co"] > 5.0 or state["mq135_smoke"] > 10.0:
                state["status"] = "ALERT: GAS HAZARD"
            else:
                state["status"] = "ACTIVE"
        time.sleep(1)

# Start background threads
t_udp = threading.Thread(target=udp_telemetry_listener, daemon=True)
t_udp.start()

t_sim = threading.Thread(target=telemetry_simulator, daemon=True)
t_sim.start()

def send_udp_command(cmd, data=None):
    try:
        msg = json.dumps({"command": cmd, "data": data or {}})
        udp_cmd_sock.sendto(msg.encode('utf-8'), (BOT_IP, UDP_PORT))
        print(f"[SENT TO JETSON] Command: {cmd} | Payload: {data}")
    except Exception as e:
        print(f"[UDP ERROR] {e}")

HTML_MOBILE_UI = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Shondhan Mobile Command</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; -webkit-tap-highlight-color: transparent; }
        body { background-color: #f8fafc; color: #0f172a; padding: 12px; max-width: 600px; margin: 0 auto; }
        .card { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 14px; padding: 14px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }
        .header { display: flex; justify-content: space-between; align-items: center; }
        .title { font-size: 18px; font-weight: 800; }
        .subtitle { font-size: 10px; color: #64748b; margin-top: 2px; }
        .pill { font-size: 11px; font-weight: 700; padding: 6px 12px; border-radius: 16px; background: #f1f5f9; color: #64748b; }
        .pill.active { background: #dcfce7; color: #15803d; }
        .pill.alert { background: #fee2e2; color: #b91c1c; }
        
        .video-container { background: #0f172a; width: 100%; min-height: 260px; aspect-ratio: 16 / 9; border-radius: 10px; display: flex; flex-direction: column; align-items: center; justify-content: center; color: #94a3b8; font-size: 12px; margin-top: 10px; overflow: hidden; position: relative; }
        .cam-status { position: absolute; bottom: 8px; font-size: 10px; font-weight: 600; color: #10b981; background: rgba(15,23,42,0.8); padding: 4px 8px; border-radius: 4px; }
        
        .gas-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 10px; }
        .sensor-card { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 10px; text-align: center; }
        .sensor-lbl { font-size: 9px; font-weight: 700; color: #64748b; text-transform: uppercase; }
        .sensor-val { font-size: 14px; font-weight: 800; color: #0f172a; margin-top: 4px; }
        .progress-bg { background: #e2e8f0; height: 6px; border-radius: 3px; margin-top: 6px; overflow: hidden; }
        .progress-fill { background: #3b82f6; height: 100%; width: 0%; transition: width 0.3s ease; }
        
        .btn-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 10px; }
        .btn { padding: 14px; border-radius: 10px; border: none; font-weight: 700; font-size: 13px; color: white; cursor: pointer; text-align: center; }
        .btn-start { background: #10b981; }
        .btn-start:active { background: #059669; }
        .btn-stop { background: #ef4444; }
        .btn-stop:active { background: #dc2626; }
        .btn-blue { background: #3b82f6; }
        .btn-warn { background: #f59e0b; }
        
        .dpad { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; width: 220px; margin: 12px auto; }
        .dpad-btn { padding: 16px; background: #f1f5f9; border: 1px solid #cbd5e1; border-radius: 10px; font-weight: 700; font-size: 14px; color: #334155; text-align: center; user-select: none; }
        .dpad-btn:active { background: #cbd5e1; transform: scale(0.96); }
        
        .flex-row { display: flex; gap: 8px; margin-top: 10px; }
        input { flex: 1; padding: 10px; border: 1px solid #cbd5e1; border-radius: 8px; font-size: 12px; background: #f8fafc; }
    </style>
</head>
<body>

    <div class="card">
        <div class="header">
            <div>
                <div class="title">Shondhan Command</div>
                <div class="subtitle">Jetson Orin | ZED 2i | Teensy | Sabertooth</div>
            </div>
            <div id="status-pill" class="pill">STATUS: IDLE</div>
        </div>
    </div>

    <!-- Video Feed Card -->
    <div class="card">
        <div style="font-size:12px; font-weight:700;">ZED 2i Camera Feed</div>
        <div class="video-container">
            <div>ZED 2i Stream Active</div>
            <div class="cam-status">Connected to Jetson Orin</div>
        </div>
    </div>

    <!-- MQ Gas Sensor Cards -->
    <div class="card">
        <div style="font-size:12px; font-weight:700;">MQ Gas Sensors (Teensy)</div>
        <div class="gas-grid">
            <div class="sensor-card">
                <div class="sensor-lbl">MQ-2 LPG</div>
                <div id="lpg-val" class="sensor-val">0.0 ppm</div>
                <div class="progress-bg"><div id="lpg-bar" class="progress-fill" style="background:#10b981;"></div></div>
            </div>
            <div class="sensor-card">
                <div class="sensor-lbl">MQ-7 CO</div>
                <div id="co-val" class="sensor-val">0.0 ppm</div>
                <div class="progress-bg"><div id="co-bar" class="progress-fill" style="background:#f59e0b;"></div></div>
            </div>
            <div class="sensor-card">
                <div class="sensor-lbl">MQ-135 Smoke</div>
                <div id="smoke-val" class="sensor-val">0.0 ppm</div>
                <div class="progress-bg"><div id="smoke-bar" class="progress-fill" style="background:#ef4444;"></div></div>
            </div>
        </div>
    </div>

    <!-- System Operation -->
    <div class="card">
        <div style="font-size:12px; font-weight:700;">System Operation</div>
        <div class="btn-grid">
            <button class="btn btn-start" onclick="sendCmd('start')">START</button>
            <button class="btn btn-stop" onclick="sendCmd('stop')">STOP</button>
        </div>
    </div>

    <!-- Sabertooth Motor Driver Touch Controls -->
    <div class="card">
        <div style="font-size:12px; font-weight:700; text-align:center;">Sabertooth Motor Control</div>
        <div class="dpad">
            <div></div>
            <div class="dpad-btn" onclick="drive('FORWARD')">▲</div>
            <div></div>
            <div class="dpad-btn" onclick="drive('LEFT')">◀</div>
            <div></div>
            <div class="dpad-btn" onclick="drive('RIGHT')">▶</div>
            <div></div>
            <div class="dpad-btn" onclick="drive('REVERSE')">▼</div>
            <div></div>
        </div>

        <div class="flex-row">
            <input type="number" id="inp-x" placeholder="X Target">
            <input type="number" id="inp-y" placeholder="Y Target">
            <button class="btn btn-blue" style="padding:8px 12px;" onclick="sendCoords()">Send</button>
        </div>
    </div>

    <div class="card">
        <button class="btn btn-warn" style="width:100%;" onclick="testHazard()">Test Gas Alert UI</button>
    </div>

    <script>
        function updateState() {
            fetch('/api/state')
                .then(res => res.json())
                .then(data => {
                    document.getElementById('lpg-val').innerText = data.mq2_lpg.toFixed(1) + ' ppm';
                    document.getElementById('co-val').innerText = data.mq7_co.toFixed(1) + ' ppm';
                    document.getElementById('smoke-val').innerText = data.mq135_smoke.toFixed(1) + ' ppm';

                    document.getElementById('lpg-bar').style.width = Math.min(100, (data.mq2_lpg / 50) * 100) + '%';
                    document.getElementById('co-bar').style.width = Math.min(100, (data.mq7_co / 15) * 100) + '%';
                    document.getElementById('smoke-bar').style.width = Math.min(100, (data.mq135_smoke / 30) * 100) + '%';

                    const pill = document.getElementById('status-pill');
                    pill.innerText = 'STATUS: ' + data.status;
                    if (data.status.includes('ALERT')) {
                        pill.className = 'pill alert';
                    } else if (data.status === 'ACTIVE') {
                        pill.className = 'pill active';
                    } else {
                        pill.className = 'pill';
                    }
                });
        }
        setInterval(updateState, 1000);

        function sendCmd(cmd) {
            fetch('/api/command', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({command: cmd})
            });
        }

        function drive(dir) {
            fetch('/api/drive', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({direction: dir})
            });
        }

        function sendCoords() {
            const x = parseFloat(document.getElementById('inp-x').value) || 0;
            const y = parseFloat(document.getElementById('inp-y').value) || 0;
            fetch('/api/command', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({command: 'move', data: {x: x, y: y}})
            });
        }

        function testHazard() {
            fetch('/api/testhazard', {method: 'POST'});
        }
    </script>
</body>
</html>"""

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle HTTP requests concurrently."""

class MobileRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _set_headers(self, status=200, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_OPTIONS(self):
        self._set_headers(200)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            self._set_headers(200, "text/html")
            self.wfile.write(HTML_MOBILE_UI.encode("utf-8"))
        elif self.path == "/api/state":
            self._set_headers(200, "application/json")
            self.wfile.write(json.dumps(state).encode("utf-8"))
        else:
            self._set_headers(404, "application/json")
            self.wfile.write(json.dumps({"error": "Not Found"}).encode("utf-8"))

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length)
        data = json.loads(body.decode('utf-8')) if body else {}

        if self.path == "/api/command":
            cmd = data.get('command')
            payload_data = data.get('data', {})
            if cmd == 'start':
                state['is_running'] = True
                state['status'] = 'ACTIVE'
            elif cmd == 'stop':
                state['is_running'] = False
                state['status'] = 'IDLE'
            send_udp_command(cmd, payload_data)
            self._set_headers(200)
            self.wfile.write(json.dumps({"status": "ok"}).encode("utf-8"))

        elif self.path == "/api/drive":
            if not state['is_running']:
                self._set_headers(400)
                self.wfile.write(json.dumps({"status": "error", "msg": "Bot is not running"}).encode("utf-8"))
                return

            dir_str = data.get('direction')
            step = 10.0
            v, w = 0.0, 0.0
            if dir_str == "FORWARD":
                state["bot_y"] += step
                v = 1.0
            elif dir_str == "REVERSE":
                state["bot_y"] -= step
                v = -1.0
            elif dir_str == "LEFT":
                state["bot_x"] -= step
                w = -1.0
            elif dir_str == "RIGHT":
                state["bot_x"] += step
                w = 1.0

            send_udp_command("move", {
                "x": state["bot_x"],
                "y": state["bot_y"],
                "v_linear": v,
                "w_angular": w,
                "direction": dir_str
            })
            self._set_headers(200)
            self.wfile.write(json.dumps({"status": "ok"}).encode("utf-8"))

        elif self.path == "/api/testhazard":
            if state['is_running']:
                state['mq2_lpg'] = 24.8
                state['mq7_co'] = 6.4
                state['mq135_smoke'] = 12.1
                state['status'] = 'ALERT: GAS HAZARD'
            self._set_headers(200)
            self.wfile.write(json.dumps({"status": "ok"}).encode("utf-8"))

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def run_server():
    local_ip = get_local_ip()
    server_address = ("0.0.0.0", SERVER_PORT)
    httpd = ThreadedHTTPServer(server_address, MobileRequestHandler)
    
    print("\n" + "="*65)
    print("  [+] SHONDHAN MOBILE WEB COMMAND SERVER ACTIVE [+]")
    print("="*65)
    print("  Open this link on your iPhone or Android browser (Wi-Fi):")
    print(f"  -> http://{local_ip}:{SERVER_PORT}")
    print(f"  -> http://localhost:{SERVER_PORT}")
    print("="*65 + "\n")
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[MOBILE SERVER] Server shutting down.")
        httpd.server_close()

if __name__ == "__main__":
    run_server()
