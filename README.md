# iPhone GPS Web Controller

เครื่องมือ Developer Location Simulation สำหรับทดสอบแอป iOS จาก macOS ผ่าน USB มี FastAPI backend และ Web UI ชื่อ Location Studio ใช้ Leaflet/OpenStreetMap รองรับ Mock Mode โดยไม่ต้องมี iPhone

รองรับ Android ผ่าน ADB Developer Mock Location ด้วย โดยใช้ test provider มาตรฐานของระบบและคืน provider/สิทธิ์เดิมเมื่อ disconnect ไม่มีระบบหลบการตรวจจับ mock location

โปรเจกต์นี้ใช้ข้อกำหนดในไฟล์แนบเป็นหลัก วิดีโอ https://www.youtube.com/watch?v=e6QOF-Ga2mU เป็นข้อมูลอ้างอิงจากผู้ใช้ แต่ไม่ได้คัดลอกแบรนด์หรือ assets และไม่ได้ตรวจสอบภาพภายในวิดีโอ ไม่มีระบบ anti-cheat bypass, anti-detection, jailbreak, binary modification, hooking หรือ certificate bypass

## ติดตั้ง

ต้องมี macOS, Python **3.11+**, และอินเทอร์เน็ตตอนติดตั้ง/โหลด map tiles ทดสอบในสภาพแวดล้อม Apple Silicon; Intel ใช้ได้เมื่อ dependency มี wheel/รองรับเครื่องนั้น

ถ้ามี Homebrew อยู่แล้ว ติดตั้ง Python ได้ด้วย `brew install python@3.11` จากนั้นในโฟลเดอร์โปรเจกต์:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

ถ้า `python3 --version` เป็น 3.11 ขึ้นไป ใช้ `python3` แทน `python3.11` ได้ ไม่ควรใช้ Python 3.10 ที่มากับบาง environment

ใช้ Makefile ได้เช่นกัน:

```bash
make install
make start
make status
make stop
```

`make install` สร้าง `.venv` และสร้าง `.env` จากตัวอย่างเมื่อยังไม่มีไฟล์นั้น ส่วน `make start`, `make status` และ `make stop` อ่านค่าจาก `.env` ทุกครั้ง จึงต้องแก้ค่า provider, port และ device settings ให้ถูกต้องก่อนเริ่ม server. `make start` เขียน log ที่ `.run/server.log` และ `make stop` ตรวจสอบก่อนหยุดว่า process ที่พอร์ตนั้นเป็น `app.py` ของโปรเจกต์นี้

## เริ่มด้วย Mock Mode

```bash
source .venv/bin/activate
GPS_PROVIDER=mock python app.py
```

เปิด http://127.0.0.1:8000 แล้วเลือก **Development iPhone → Connect** โหมดนี้ไม่ได้ส่งคำสั่งไปยัง iPhone จริง แถบด้านซ้ายแสดง MOCK และ connection เป็น MOCK ชัดเจน

หรือใช้ Uvicorn:

```bash
GPS_PROVIDER=mock uvicorn app:app --host 127.0.0.1 --port 8000 --ws-max-size 4096
```

ใช้ **worker เดียว** เท่านั้น เพราะ developer session, movement worker และ WebSocket state อยู่ใน process เดียว ไม่ใช้ `--workers 2` ขึ้นไป และไม่ใช้ `--reload` กับ device จริงที่กำลัง simulation

## BlueStacks 5 บน Windows

เปิด BlueStacks แล้วไปที่ **Settings → Advanced → Android Debug Bridge**, เปิด ADB และจดพอร์ตที่แสดง (ตัวอย่างทั่วไปคือ `5555`) จากนั้นตั้ง `.env` ดังนี้:

```env
GPS_PROVIDER=android
ADB_PATH=C:/Program Files/BlueStacks_nxt/HD-Adb.exe
ADB_ENDPOINTS=127.0.0.1:5555
```

เปิด PowerShell ในโฟลเดอร์โปรเจกต์แล้วรัน:

```powershell
.\scripts\windows\install.ps1
.\scripts\windows\start.ps1
.\scripts\windows\status.ps1
.\scripts\windows\logs.ps1
.\scripts\windows\stop.ps1
```

ติดตาม log แบบสดได้ด้วย:

```powershell
.\scripts\windows\logs.ps1 -Follow
```

ตรวจ ADB แยกต่างหากได้ด้วย:

```powershell
& "C:\Program Files\BlueStacks_nxt\HD-Adb.exe" connect 127.0.0.1:5555
& "C:\Program Files\BlueStacks_nxt\HD-Adb.exe" devices -l
```

หาก PowerShell ไม่อนุญาตให้รัน script ให้ใช้ `Set-ExecutionPolicy -Scope Process Bypass` ในหน้าต่าง PowerShell นั้นก่อน แล้วรันคำสั่งข้างต้นใหม่

## เชื่อมต่อ iPhone จริง

1. ต่อ iPhone ด้วยสาย USB ที่ส่งข้อมูลได้ ปลดล็อกหน้าจอ
2. กด **Trust This Computer** และใส่ passcode บน iPhone
3. เปิด **Settings → Privacy & Security → Developer Mode** แล้วทำตามขั้นตอน reboot/ยืนยันบนอุปกรณ์
4. ใช้ Xcode ที่รองรับ iOS ของเครื่องเพื่อเตรียม developer support หากยังไม่พร้อม ให้เปิด **Window → Devices and Simulators** แล้วรอเตรียมเครื่อง หรือใช้คำสั่ง `python -m pymobiledevice3 mounter auto-mount` ตาม dependency ที่ติดตั้ง ซึ่งอาจต้องดาวน์โหลด Developer Disk Image
5. เริ่มเว็บ:

```bash
GPS_PROVIDER=iphone IOS_TRANSPORT=native python app.py
```

6. เลือก iPhone ใน sidebar แล้วกด Connect ต้องขึ้น CONNECTED ก่อนสั่งตำแหน่ง

**ค่าเริ่มต้นบน macOS คือ native tunnel** ผ่าน `NativeRemotedTunnel` ของ pymobiledevice3 11.3.1 ใช้ tunnel ของ Apple ที่มีอยู่ ไม่ต้องรันเว็บด้วย sudo เก็บ native tunnel, RSD, DVT และ LocationSimulation session ไว้จน disconnect ไม่มีการสร้าง CLI process ต่อ GPS tick

หากยังไม่ได้ pair สามารถตรวจ/เริ่ม pairing จาก Terminal ด้วย:

```bash
python -m pymobiledevice3 usbmux list
python -m pymobiledevice3 lockdown pair
python -m pymobiledevice3 mounter query-developer-mode-status
```

### ทางเลือก: tunneld

สำหรับเครื่องที่ native tunnel ใช้ไม่ได้ สามารถเลือก tunnel daemon แยกต่างหาก เปิด Terminal หนึ่งจากโฟลเดอร์โปรเจกต์:

```bash
sudo .venv/bin/python -m pymobiledevice3 remote tunneld --host 127.0.0.1 --port 49151 --no-wifi --no-mobdev2
```

ปล่อย Terminal นี้ทำงาน แล้วเปิดอีก Terminal:

```bash
source .venv/bin/activate
GPS_PROVIDER=iphone IOS_TRANSPORT=tunneld python app.py
```

เว็บอ่าน endpoint ของ UDID ที่เลือกจาก `127.0.0.1:49151` และสร้าง async RSD session ใช้ซ้ำ ค่า port นี้เป็นของ tunneld ไม่ใช่ HTTP UI ไม่เปิด tunneld ออก LAN

## เชื่อมต่อ Android

ต้องมี Android Platform Tools (`adb`) และ Android 11+ แนะนำให้เชื่อม USB โดยเปิด Developer options, USB debugging และยืนยัน RSA prompt ก่อน ตรวจด้วย `adb devices -l` ต้องแสดงสถานะ `device`

```bash
GPS_PROVIDER=android python app.py
```

เลือก Android ใน sidebar แล้วกด Connect ระบบจะขอสิทธิ์ `android:mock_location` สำหรับ ADB shell และสร้าง Android test provider เมื่อ Teleport ครั้งแรก ปุ่ม Restore จะลบ test provider เพื่อกลับ Location providers จริง ส่วน Disconnect/shutdown จะคืน app-op เดิมด้วย การเปลี่ยนนี้ใช้ Android Developer Mock Location จึงอาจถูกแอปที่ไม่อนุญาตตำแหน่งจำลองปฏิเสธ

### BlueStacks Air บน macOS

BlueStacks Air ต้องใช้ `hd-adb` ที่ติดมากับแอปและ endpoint ที่แสดงใน BlueStacks (โดยทั่วไป `127.0.0.1:5555`) เพื่อไม่ให้ ADB shell ถูกตัด:

```bash
ADB_PATH="/Applications/BlueStacks.app/Contents/MacOS/hd-adb" \
ADB_ENDPOINTS=127.0.0.1:5555 \
GPS_PROVIDER=android python app.py
```

ระบบจะแสดง connection เป็น `ADB EMULATOR` และตัด alias ซ้ำของ instance เดียวกันออก BlueStacks ต้องเปิดอยู่ก่อนเริ่มเว็บ หากเปลี่ยน port ให้แก้ `ADB_ENDPOINTS` ตามค่าที่ BlueStacks แสดง

## วิธีใช้งาน

- **Teleport:** คลิกแผนที่หรือค้นหาพิกัด เช่น `13.7563, 100.5018` แล้วกด Teleport here ลาก destination marker เพื่อปรับตำแหน่งได้ คลิก marker เพื่อเปิดเมนู Teleport/Add to route/Add favorite ปุ่ม ◎ ด้านขวาใช้ตำแหน่งที่ browser บน Mac รายงานเพื่อเลือก destination (ต้องอนุญาต Location และไม่ใช่ GPS ที่อ่านจาก iPhone)
- **Joystick:** ตั้งตำแหน่งเริ่มต้นด้วย Teleport ก่อน จากนั้นลาก joystick ได้ 360° หรือกด WASD/ลูกศร กดสองปุ่มเพื่อเคลื่อนแนวทแยง ไม่มีการขยับขณะพิมพ์ในช่อง input
- **Speed:** Walk 5, Run 10, Bike 15 km/h หรือ Custom/slider 0.1–200 km/h แสดง m/s ด้วย ค่า speed อยู่ใน session ของอุปกรณ์
- **Two Spot:** เลือก A → Add destination → เลือก B → Add destination → Start route
- **Multi Spot:** เพิ่มจุดตามลำดับ เลือกจำนวนรอบ แล้ว Start/Pause/Resume/Stop
- **Multi Spot JSON:** ใช้ Export JSON เพื่อสำรอง waypoint และจำนวนรอบ หรือ Import JSON เพื่อแทนที่แผนปัจจุบัน รองรับสูงสุด 10,000 จุดและตรวจช่วง latitude/longitude ก่อนนำเข้า ทดลองได้ด้วย `examples/bangkok-waypoints.json`
- **Start route จะตั้งตำแหน่งไปที่ A หนึ่งครั้ง** แล้วเคลื่อนระหว่าง waypoint ด้วย geodesic ไม่มี teleport ระหว่างจุด เมื่อวนซ้ำจะเดินจากจุดสุดท้ายกลับ A ก่อนรอบถัดไป
- **GPX:** เลือกไฟล์ `.gpx` ไม่เกิน 2 MB ดูเส้นทางก่อนกด Start ใช้ `examples/bangkok-walk.gpx` ทดลองได้ รองรับ namespace, trkpt/rtept/wpt โดยเลือก track ก่อน route ก่อน standalone waypoints เพื่อไม่เอาจุด POI มาปน track สูงสุด 10,000 จุด
- **Favorites:** กด ☆ ตั้งชื่อ ข้อมูลเก็บ SQLite สามารถ Teleport, วางแผน route จากตำแหน่งปัจจุบันไป favorite และ Delete
- **History:** เก็บเฉพาะ teleport และ start route แบ่ง Today/Yesterday/Older ตามเวลาเครื่อง ไม่บันทึกทุก movement tick แสดงล่าสุด 500 รายการ
- **Restore Real Location:** ส่ง `LocationSimulation.clear()` แล้วล้าง marker จำลอง แอปปลายทางอาจใช้เวลารับตำแหน่งจริงรอบใหม่

การปล่อย joystick, window blur, ซ่อน tab หรือ WebSocket ของผู้ควบคุมหลุดจะหยุด manual movement มี heartbeat 200 ms และ backend timeout 1 วินาที หากไม่มี browser เหลือจะหยุด route ด้วย การใช้ joystick จะหยุด route เดิม

เมื่อ USB หลุด ระบบตรวจทุกประมาณ 3 วินาทีและหยุดคำสั่ง เมื่อเสียบกลับจะ reconnect และ clear ตำแหน่งที่อาจค้างก่อนเปิดรับคำสั่งใหม่ **ไม่ resume movement อัตโนมัติ** ตอนปิดด้วย Ctrl+C จะ attempt clear และปิด session เสมอ หากอุปกรณ์หลุด/ระบบ kill process จะรับประกัน clear ไม่ได้ ให้ reconnect แล้ว Restore หรือ restart iPhone

## Configuration

คัดลอก `.env.example` เป็น `.env` หรือกำหนด environment variables ค่า environment มีลำดับสูงกว่าไฟล์ `.env`

| ตัวแปร | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| HOST | 127.0.0.1 | HTTP bind address เมื่อใช้ `python app.py` |
| PORT | 8000 | HTTP port |
| GPS_PROVIDER | iphone | iphone, android หรือ mock |
| IOS_TRANSPORT | native บน macOS | native หรือ tunneld |
| MOVEMENT_HZ | 10 | 5–20 Hz |
| DEFAULT_SPEED_KMH | 5 | 0.1–200 km/h |
| LOG_LEVEL | INFO | `DEBUG`, `INFO`, `WARNING`, `ERROR` หรือ `CRITICAL`; DEBUG แสดง HTTP method/path/status และ log การเชื่อมต่อ โดยไม่แสดง request body |
| DATABASE_PATH | data/app.db | SQLite path |
| ALLOWED_HOSTS | ว่าง | hostname เพิ่มเติม คั่น comma |

ค่า HOST/PORT ไม่มีผลเมื่อระบุ `uvicorn ... --host ... --port ...` เอง ค่าเริ่มต้นรับเฉพาะ localhost, ตรวจ Host และ same-origin สำหรับคำสั่ง HTTP/WebSocket ไม่มี authentication สำหรับการเปิดออก network ถ้าจะใช้ LAN ต้องตั้ง HOST/ALLOWED_HOSTS และเพิ่มการควบคุมการเข้าถึงของตนเองก่อน

## Architecture และไฟล์

```text
Browser (Leaflet + ES modules)
  → REST / WebSocket → FastAPI
    → Controller + AppState (asyncio.Lock)
      → Movement/Route engine
      → LocationProvider
        → MockLocationProvider
        → PymobiledeviceLocationProvider
          → native tunnel หรือ tunneld RSD → DVT → LocationSimulation
    → SQLite favorites/history
```

| ไฟล์/โฟลเดอร์ | หน้าที่ |
|---|---|
| app.py | app factory, lifecycle, static files, origin/host checks |
| api/endpoints.py | REST และ WebSocket validation/control |
| models/schemas.py, state.py | Pydantic inputs และ shared async state |
| services/controller.py | movement worker เดียว, USB monitor, reconnect, shutdown |
| services/device_manager.py | USB discovery และ metadata |
| services/ios_location.py | provider interface, mock และ persistent iPhone adapter |
| services/ios_transport.py | compatibility ของ native/tunneld RSD |
| services/android_location.py | Android ADB test-provider lifecycle และ cleanup |
| services/movement_engine.py | geodesic distance, bearing, destination, R=6,371,000 m |
| services/route_engine.py | segment progression, loops, pause/resume state |
| services/gpx_parser.py | XML/GPX validation โดย defusedxml |
| database/database.py | SQLite parameterized statements |
| static/index.html, css/app.css | responsive desktop-first UI |
| static/js/ | app, map, joystick, route, WebSocket modules |
| static/vendor/ | Leaflet 1.9.4 และ license |
| tests/ | unit, API, WebSocket และ recovery tests |

เลือกได้หลาย iPhone แต่ควบคุม **หนึ่งเครื่องในเวลาเดียวกัน** การเปลี่ยนเครื่องจะ clear เครื่องเดิมก่อน State/session สร้างต่อ app instance ไม่มี mutable singleton ของ device ใน module

WebSocket ส่ง bearing/active จาก browser; Python คำนวณ `speed_mps × elapsed_seconds` ทุก tick ใช้ monotonic clock และจำกัด dt หลัง stall เพื่อไม่กระโดดไกล มี asyncio.Lock ครอบคำสั่งต่อ device และ bounded queue ต่อ WebSocket เพื่อไม่ให้ client ช้าหน่วง movement

### API

เอกสาร interactive: http://127.0.0.1:8000/docs

- `GET /api/devices`, `POST /api/devices/connect` (`{"udid":"..."}`), `POST /api/devices/disconnect`
- `GET /api/state`
- `POST /api/location/set` (`{"latitude":13.7563,"longitude":100.5018}`), `POST /api/location/clear`
- `POST /api/routes/start` (`{"points":[...],"loops":1}`); loops 0 = infinite
- `POST /api/routes/pause`, `/resume`, `/stop`
- `POST /api/gpx/import` (`{"xml":"<gpx>...</gpx>"}`) คืน preview points ไม่เริ่มเดิน
- `GET/POST /api/favorites`, `DELETE /api/favorites/{id}`, `GET /api/history`
- `/ws`: รับ `movement`, `speed`; ส่ง `device_state`, `location_state`, `route_state`, `error`

## Tests

```bash
python -m pip install -r requirements-dev.txt
pytest -q
ruff check .
python -m compileall -q app.py api services models database tests
```

ทดสอบ coordinates รวม NaN/Infinity, movement ระยะ 1.3889 m/วินาทีที่ 5 km/h, bearing ข้ามเส้น date line, route completion/loops/pause/resume, GPX, SQLite persistence, API, WebSocket dead-man timeout, shutdown clear และ reconnect โดย MockLocationProvider ดูผลที่ตรวจจริงใน `docs/verification.md`

ถ้าต้องการรัน browser smoke test เพิ่มเติม:

```bash
python -m pip install -r requirements-ui.txt
# เปิด Mock Mode server ที่ port 8000 ใน Terminal แรก
python tests/ui_smoke.py
```

## Troubleshooting

| อาการ | วิธีตรวจ |
|---|---|
| ไม่พบ iPhone | ตรวจสาย USB, ปลดล็อก, Trust, `python -m pymobiledevice3 usbmux list` |
| Developer Mode disabled | เปิดบน iPhone แล้ว reboot/ยืนยัน |
| Native tunnel unavailable | ตรวจ Trust/Developer Mode, เสียบ USB ใหม่, ลอง transport=tunneld |
| DVT/developer service unavailable | ใช้ Xcode ที่รองรับ iOS หรือ `mounter auto-mount`; ตรวจว่า DDI mount สำเร็จ |
| Tunnel connection refused | สำหรับ tunneld ให้เปิด daemon ที่ 127.0.0.1:49151 |
| Restore pending | ต่อ iPhone เครื่องเดิมอีกครั้ง รอ reconnect แล้ว Restore |
| WASD ไม่เดิน | Connect + Teleport ก่อน, focus นอกช่องกรอก, ตรวจ Live connection |
| ภาพ map ไม่มา | tile.openstreetmap.org ต้องเข้าถึงได้; ยังสั่งพิกัดและใช้ controls ได้ |
| ค้นชื่อสถานที่ไม่ได้ | ตรวจอินเทอร์เน็ตและลองระบุชื่อเมือง/ประเทศให้ชัดเจน Nominatim เป็นบริการ best-effort และอาจปฏิเสธคำขอชั่วคราว |
| Port 8000 ถูกใช้ | ตั้ง `PORT=8001 python app.py` |
| Safari/WebSocket reconnect | ใช้ URL/port เดียวกับเว็บ ไม่เปิด HTML จาก file:// |

## ข้อจำกัด

- Developer Location Simulation ขึ้นอยู่กับ iOS, DDI, macOS และ pymobiledevice3 ไม่ใช่การเปลี่ยน hardware GPS และไม่รับประกันว่าแอปทุกตัวจะใช้ตำแหน่งจำลอง
- ล็อก **pymobiledevice3 11.3.1** ตาม source/signature ที่ตรวจจริง เวอร์ชันเก่าที่เป็น sync API ไม่รองรับด้วย adapter นี้
- เส้นทางเป็น geodesic ระหว่างจุด ไม่ใช่ road routing และไม่ตรวจถนน/อาคาร/ภูมิประเทศ
- การค้นชื่อส่งคำค้นที่ผู้ใช้กดค้นหาไปยัง Nominatim ของ OpenStreetMap และมี cache/จำกัดรวมไม่เกิน 1 request ต่อวินาที ไม่มี autocomplete ตาม usage policy เปลี่ยนผู้ให้บริการได้ด้วย `GEOCODER_URL` พิกัดแบบตัวเลขยังค้นในเครื่องโดยไม่ส่งออก
- Map tiles ใช้ OpenStreetMap มี attribution; ไม่ดาวน์โหลด bulk/offline tiles ตำแหน่ง viewport ทำให้มี tile requests ไปผู้ให้บริการ
- GPX หลาย track/segment รวมตามลำดับเอกสาร ช่องว่างระหว่าง segment จะถูกเชื่อมด้วยเส้นทางตรง
- Apple Developer Location Simulation และ pymobiledevice3 11.3.1 มีคำสั่ง set/clear แต่ไม่มี API อ่าน CLLocation จริงจาก iPhone แอปจึงแสดงพิกัดเป็น — จนกว่าจะตั้ง simulation ปุ่มตำแหน่ง Mac เป็นเพียงจุดเริ่มต้นโดยประมาณและความแม่นยำขึ้นกับ Location Services ของ macOS
- หยุด route ไม่ได้ clear simulation; ใช้ Restore แยกต่างหาก
- ยังไม่มี auth/ผู้ใช้หลายบัญชี เหมาะกับ local developer workstation

### Automatic 10 km / 60 min speed

Under **MOVEMENT SPEED**, select **10 km / 60 min · Auto** and start a route
of at least 10 km (or use route looping). The server gradually increases speed from 5 to 15 km/h over 30 seconds,
then decreases back to 5 over the next 30 seconds, averaging 10 km/h. The schedule counts active movement time, pauses while idle or the route
is paused, and continues automatically beyond 60 active minutes (10 km per active hour). A shorter route still
ends at its endpoint. Manual speed changes cancel the schedule; restoring the
location or switching devices resets it. Browser background timers do not control
this mode. Device delays, disconnections, and pauses can extend wall-clock time.

### Simulated distance counter

MOVEMENT SPEED displays cumulative simulated movement in km and meters. It counts
joystick movement and actual route segments, including loops, only after the
device provider accepts the location update. Teleports and jumps to a route start
are excluded. Pausing, stopping, restoring location, and refreshing the page retain
the counter. Use **รีเซ็ตระยะ** to start a fresh comparison with the game; this
does not reset the Auto speed timer. Connecting a different device or restarting
the server starts a new counter. This is the simulator's distance, not a reading
from the game.

### Run to a map point

Set an initial simulated location using Teleport, then select **Run** and click a map point.
Movement starts from the current controlled position and follows a straight segment at the selected
movement speed (including an active automatic speed schedule). Clicking another point redirects
movement from the latest position without teleporting. Pause, Resume and Stop control the movement;
it stops on arrival. Coordinate search and dragging the destination also select a new Run destination.
