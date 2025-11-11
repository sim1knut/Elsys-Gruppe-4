#!/usr/bin/env python3
import os
import sys
import time
import struct
import threading
from collections import deque
from datetime import datetime

from flask import Flask, jsonify, request, render_template_string

try:
    from bluetooth import discover_devices, BluetoothSocket, RFCOMM, find_service
except Exception as e:
    print("Klarte ikke å importere PyBluez (modulen 'bluetooth').")
    raise

# ---------------- KONFIG ----------------
TARGET_NAME = os.getenv("TARGET_NAME", "ESP32_GRUPPE4_01")
TARGET_ADDR = os.getenv("TARGET_ADDR", "6C:C8:40:5D:32:76") 
# ESP01: 44:1D:64:E2:C4:F2
# ESP  : 6C:C8:40:5D:32:76  LODDET PÅ SENSOR
HISTORY_SIZE = int(os.getenv("HISTORY_SIZE", "500"))
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "0.1"))
HEADER = b"\xAA\x55"
TYPE_TELEMETRY = 0x01

data_lock = threading.Lock()
history = deque(maxlen=HISTORY_SIZE)
latest = None
# Tare offset in grams (display adjustment only)
tare_offset = 0

# ---------------- BLUETOOTH ----------------
def xor_checksum(b: bytes) -> int:
    chk = 0
    for x in b:
        chk ^= x
    return chk & 0xFF

def find_esp32_address():
    if TARGET_ADDR:
        return TARGET_ADDR
    print("Søker etter enheter...")
    devices = discover_devices(duration=8, lookup_names=True)
    for addr, name in devices:
        print(f"Fant: {name} [{addr}]")
        if name == TARGET_NAME:
            return addr
    return None

def connect(addr):
    services = find_service(address=addr)
    channel = None
    for svc in services:
        if ("serial" in (svc.get("name","") or "").lower()) or (svc.get("protocol") == "RFCOMM"):
            channel = svc.get("port")
            break
    sock = BluetoothSocket(RFCOMM)
    if channel is not None:
        print(f"Kobler til {addr} på RFCOMM kanal {channel}...")
        sock.connect((addr, channel))
    else:
        print(f"Fant ikke annonsert kanal – prøver kanal 1 på {addr}...")
        sock.connect((addr, 1))
    sock.settimeout(5.0)
    print("Tilkoblet.")
    return sock

def packet_stream(sock):
    buffer = b""
    while True:
        try:
            chunk = sock.recv(256)
            if not chunk:
                raise ConnectionError("Forbindelsen ble brutt.")
            buffer += chunk
        except Exception:
            time.sleep(POLL_INTERVAL)
            continue

        while True:
            idx = buffer.find(HEADER)
            if idx < 0:
                buffer = buffer[-1:] if len(buffer) else b""
                break
            if idx > 0:
                buffer = buffer[idx:]
            if len(buffer) < 6:
                break
            typ = buffer[2]
            length = (buffer[3] << 8) | buffer[4]
            total_len = 2 + 1 + 2 + length + 1
            if len(buffer) < total_len:
                break
            frame = buffer[:total_len]
            buffer = buffer[total_len:]
            payload = frame[5:-1]
            chk = frame[-1]
            calc = xor_checksum(frame[2:-1])
            if chk != calc:
                continue
            yield typ, payload

def parse_telemetry(payload: bytes):
    if len(payload) != 8:
        return None
    try:
        counter, weight_g = struct.unpack("<Ii", payload)
        return int(counter), int(weight_g)
    except struct.error:
        return None

def reader_thread():
    global latest
    backoff = 2.0
    while True:
        try:
            addr = find_esp32_address()
            if not addr:
                time.sleep(backoff)
                backoff = min(30.0, backoff * 1.5)
                continue
            sock = connect(addr)
            backoff = 2.0
            for typ, payload in packet_stream(sock):
                if typ == TYPE_TELEMETRY:
                    parsed = parse_telemetry(payload)
                    if not parsed:
                        continue
                    counter, weight_g = parsed
                    item = {
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "counter": counter,
                        "weight_g": weight_g,
                        "weight_kg": round(weight_g / 1000.0, 3),
                    }
                    with data_lock:
                        latest = item
                        history.append(item)
        except Exception as e:
            print(f"[reader] Feil: {e}")
            try:
                sock.close()
            except:
                pass
            time.sleep(backoff)
            backoff = min(30.0, backoff * 1.5)

# ---------------- FLASK ----------------
app = Flask(__name__)

INDEX_HTML = """
<!doctype html>
<html lang="no">
<head>
<meta charset="utf-8">
<title>ESP32 Vekt</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root { font-family: system-ui, sans-serif; }
  body { margin: 0; padding: 0; background: #fafafa; display: flex; flex-direction: column; min-height: 100vh; }
  .main {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-direction: column;
    padding: 2rem;
  }
  .weight {
    font-size: 6rem;
    font-weight: 800;
    color: #222;
  }
  .unit {
    font-size: 2rem;
    color: #666;
  }
  .timestamp {
    margin-top: 0.5rem;
    font-size: 1rem;
    color: #888;
  }
  .status {
    margin-top: 0.5rem;
    font-size: 0.9rem;
    color: #888;
  }
  .history {
    width: 100%;
    max-width: 700px;
    margin: 2rem auto;
    background: white;
    border-radius: 12px;
    box-shadow: 0 2px 6px rgba(0,0,0,0.1);
    overflow: hidden;
  }
  table {
    width:100%;
    border-collapse: collapse;
  }
  th, td {
    padding: 10px 12px;
    border-bottom: 1px solid #eee;
    text-align: left;
  }
  th { background: #f3f3f3; }
</style>
</head>
<body>
  <div class="main">
    <div id="weightVal" class="weight">—</div>
    <div class="unit">gram</div>
    <div class="timestamp" id="timestamp">Ingen data</div>
    <div class="status" id="status">kobler...</div>
    <div style="margin-top:1rem;">
      <button id="tareBtn" onclick="tare()" style="padding:0.6rem 1rem; font-size:1rem; border-radius:8px; border:1px solid #ccc; background:#fff; cursor:pointer;">Tare</button>
    </div>
  </div>

  <div class="history">
    <table>
      <thead><tr><th>Tid</th><th>Teller</th><th>Vekt (g)</th><th>Vekt (kg)</th></tr></thead>
      <tbody id="rows"></tbody>
    </table>
  </div>

<script>
let timer=null;

async function fetchLatest() {
  try {
    const r = await fetch('/api/latest');
    const j = await r.json();
    const weight = document.getElementById('weightVal');
    const ts = document.getElementById('timestamp');
    const st = document.getElementById('status');
    if (j && j.timestamp) {
      weight.textContent = j.weight_g != null ? j.weight_g : '—';
      ts.textContent = j.timestamp;
      st.textContent = 'tilkoblet';
      st.style.color = '#0a7';
    } else {
      weight.textContent = '—';
      ts.textContent = 'Venter på data';
      st.textContent = 'venter...';
      st.style.color = '#888';
    }
  } catch(e) {
    document.getElementById('status').textContent = 'frakoblet';
    document.getElementById('status').style.color = '#c30';
  }
}

async function fetchTable() {
  try {
    const r = await fetch('/api/history?n=50');
    const j = await r.json();
    const tb = document.getElementById('rows');
    tb.innerHTML = '';
    (j || []).slice().reverse().forEach(d => {
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${d.timestamp}</td><td>${d.counter}</td><td>${d.weight_g}</td><td>${Number(d.weight_kg).toFixed(3)}</td>`;
      tb.appendChild(tr);
    });
  } catch(e) {}
}

function tick() {
  fetchLatest();
  fetchTable();
}

tick();
timer = setInterval(tick, 1000);

async function tare() {
  const st = document.getElementById('status');
  try {
    const r = await fetch('/api/tare', { method: 'POST' });
    const j = await r.json();
    if (!r.ok || !j.ok) {
      st.textContent = (j && j.message) ? j.message : 'tare feilet';
      st.style.color = '#c30';
      return;
    }
    st.textContent = 'taret';
    st.style.color = '#0a7';
    // Force immediate refresh
    tick();
    setTimeout(()=>{ st.textContent = 'tilkoblet'; st.style.color = '#0a7'; }, 800);
  } catch(e) {
    st.textContent = 'tare feilet';
    st.style.color = '#c30';
  }
}
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return render_template_string(INDEX_HTML)

@app.get("/api/latest")
def api_latest():
    with data_lock:
        if not latest:
            return jsonify({})
        # Return adjusted (tared) values without mutating stored raw data
        g = int(latest.get("weight_g", 0)) - int(tare_offset)
        item = {
            "timestamp": latest.get("timestamp"),
            "counter": latest.get("counter"),
            "weight_g": g,
            "weight_kg": round(g / 1000.0, 3),
        }
        return jsonify(item)

@app.get("/api/history")
def api_history():
    try:
        n = int(request.args.get("n", "50"))
    except ValueError:
        n = 50
    with data_lock:
        items = list(history)[-n:]
        # Return adjusted (tared) copies
        adjusted = []
        for it in items:
            g = int(it.get("weight_g", 0)) - int(tare_offset)
            adjusted.append({
                "timestamp": it.get("timestamp"),
                "counter": it.get("counter"),
                "weight_g": g,
                "weight_kg": round(g / 1000.0, 3),
            })
    return jsonify(adjusted)

@app.post("/api/tare")
def api_tare():
    global tare_offset
    with data_lock:
        # Set tare to current raw weight so displayed becomes zero
        current = latest.get("weight_g") if latest else None
        if current is None:
            # No data yet; do not change offset
            return jsonify({"ok": False, "message": "Ingen data å tare.", "tare_offset": tare_offset}), 400
        tare_offset = int(current)
        return jsonify({"ok": True, "tare_offset": tare_offset})

def start_reader():
    th = threading.Thread(target=reader_thread, daemon=True)
    th.start()

if __name__ == "__main__":
    start_reader()
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    print(f"Starter Flask på http://{host}:{port}")
    app.run(host=host, port=port, threaded=True)
