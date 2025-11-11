import os, asyncio, json, time, threading
from collections import defaultdict
from flask import Flask, jsonify, render_template_string
from typing import Dict, Optional

# ==== KONFIG ====
NAME_PREFIX = os.getenv("NAME_PREFIX", "ESP-SCALE-01")
SERVICE_UUID = os.getenv("SERVICE_UUID", "6E400001-B5A3-F393-E0A9-E50E24DCCA9E").lower()
CHAR_TX_UUID = os.getenv("CHAR_TX_UUID", "6E400003-B5A3-F393-E0A9-E50E24DCCA9E").lower()  # notify
SCAN_INTERVAL = float(os.getenv("SCAN_INTERVAL", "10"))  # sek
RECONNECT_DELAY = float(os.getenv("RECONNECT_DELAY", "5"))
MOCK = os.getenv("MOCK", "0") == "1"  # sett MOCK=1 for å simulere uten BLE

# Delt state: siste måling pr. enhet
latest: Dict[str, dict] = {}
latest_lock = threading.Lock()

# ===== BLE DEL =====
if not MOCK:
    from bleak import BleakScanner, BleakClient

async def handle_device(address: str, name: str):
    """Koble til én enhet, abonner på notificiations, reconnect ved behov."""
    if MOCK:
        return
    while True:
        try:
            async with BleakClient(address, timeout=15.0) as client:
                # Sjekk at tjenesten finnes (kan droppes for fart)
                svcs = await client.get_services()
                svcuuids = [str(s.uuid).lower() for s in svcs]
                if SERVICE_UUID not in svcuuids:
                    print(f"[{name}] Service UUID ikke funnet, men prøver notifications likevel.")

                buffer = bytearray()

                def handle_notify(_, data: bytearray):
                    nonlocal buffer
                    buffer.extend(data)
                    # Del opp på linjeskift (ESP32 bør sende '\n' per melding)
                    while b"\n" in buffer:
                        line, _, rest = buffer.partition(b"\n")
                        buffer = bytearray(rest)
                        try:
                            msg = json.loads(line.decode("utf-8").strip())
                            # Forventet: {"id":"ESP-SCALE-01","grams":123.4,"ts":...}
                            dev_id = msg.get("id") or name
                            grams = float(msg.get("grams"))
                            ts = float(msg.get("ts", time.time()))
                            with latest_lock:
                                latest[dev_id] = {"grams": grams, "ts": ts, "name": name, "address": address}
                        except Exception as e:
                            print(f"[{name}] Parse-feil: {e} | line={line!r}")

                await client.start_notify(CHAR_TX_UUID, handle_notify)
                print(f"[{name}] Tilkoblet og lytter på notifications.")
                while True:
                    await asyncio.sleep(1)
        except Exception as e:
            print(f"[{name}] Frakoblet/feil: {e}. Reconnect om {RECONNECT_DELAY}s.")
            await asyncio.sleep(RECONNECT_DELAY)

async def ble_manager():
    if MOCK:
        return
    tasks = {}
    TARGET_UUID = SERVICE_UUID
    while True:
        try:
            devices = await BleakScanner.discover(timeout=5.0)
            for d in devices:
                name = d.name or ""
                adv = getattr(d, "metadata", {}).get("uuids", []) or []
                adv = [str(u).lower() for u in adv]
                match_name = name.startswith(NAME_PREFIX)
                match_uuid = (TARGET_UUID in adv)
                if match_name or match_uuid:
                    if d.address not in tasks:
                        print(f"Fant {name or 'ukjent'} @ {d.address} – starter tilkobling.")
                        tasks[d.address] = asyncio.create_task(handle_device(d.address, name or d.address))
        except Exception as e:
            print(f"[SCAN] Feil under scanning: {e}")
        await asyncio.sleep(SCAN_INTERVAL)


def start_ble_loop_in_background():
    if MOCK:
        return
    loop = asyncio.new_event_loop()
    def runner():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(ble_manager())
    t = threading.Thread(target=runner, daemon=True)
    t.start()

# ===== MOCK (uten BLE/ESP32) =====
def start_mock_producer():
    import random
    ids = [f"{NAME_PREFIX}{i:02d}" for i in range(1, 4)]
    def loop():
        while True:
            now = time.time()
            for i, dev_id in enumerate(ids, start=1):
                grams = max(0.0, 1000 + 100 * i + (random.random() - 0.5) * 20)
                with latest_lock:
                    latest[dev_id] = {"grams": round(grams, 1), "ts": now, "name": dev_id, "address": f"MOCK-{i}"}
            time.sleep(0.2)
    threading.Thread(target=loop, daemon=True).start()

# ===== WEB (Flask + UI) =====
app = Flask(__name__)

HTML = """
<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vekter</title>
<style>
  html,body{margin:0;background:#0b0f14;color:#e6eef8;font-family:system-ui,sans-serif}
  .wrap{display:grid;gap:16px;padding:24px;grid-template-columns:repeat(auto-fill, minmax(260px, 1fr))}
  .card{background:#131a22;border-radius:16px;padding:16px;box-shadow:0 8px 24px rgba(0,0,0,.35)}
  .id{font-size:1rem;opacity:.8}
  .val{font-size:3.2rem;font-weight:800;line-height:1;margin-top:8px}
  .meta{opacity:.6;font-size:.9rem;margin-top:6px}
  .header{display:flex;justify-content:space-between;align-items:center;margin:0 24px 8px}
  .title{font-size:1.25rem;opacity:.9}
  button{background:#1a2430;color:#e6eef8;border:none;padding:8px 12px;border-radius:10px;cursor:pointer}
  button:active{transform:scale(.98)}
</style>
<div class="header">
  <div class="title">Vekter (BLE) – <span id="count">0</span> enheter</div>
  <div>
    <button onclick="refresh()">Oppdater</button>
  </div>
</div>
<div class="wrap" id="grid"></div>
<script>
async function load(){
  const r = await fetch('/api/devices');
  const j = await r.json();
  const grid = document.getElementById('grid');
  grid.innerHTML = '';
  document.getElementById('count').textContent = j.devices.length;
  for(const d of j.devices){
    const div = document.createElement('div');
    div.className = 'card';
    div.innerHTML = `
      <div class="id">${d.name || d.id}</div>
      <div class="val">${(d.grams ?? 0).toFixed(1)}<span style="font-size:1.2rem;opacity:.7"> g</span></div>
      <div class="meta">Sist: ${new Date(d.ts*1000).toLocaleTimeString()} • ${d.address}</div>
    `;
    grid.appendChild(div);
  }
}
function refresh(){ load(); }
setInterval(load, 500); load();
</script>
"""

@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML)

@app.route("/api/devices", methods=["GET"])
def api_devices():
    with latest_lock:
        arr = []
        for dev_id, d in latest.items():
            arr.append({
                "id": dev_id,
                "name": d.get("name", dev_id),
                "grams": d.get("grams"),
                "ts": d.get("ts", time.time()),
                "address": d.get("address", ""),
            })
    arr.sort(key=lambda x: x["id"])
    return jsonify({"devices": arr, "count": len(arr), "mock": MOCK})

def main():
    if MOCK:
        print("[MOCK] Kjører uten BLE – genererer testdata.")
        start_mock_producer()
    else:
        start_ble_loop_in_background()
    app.run(host="0.0.0.0", port=5000, debug=False)

if __name__ == "__main__":
    main()
