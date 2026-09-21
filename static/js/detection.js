import { detectStops } from './stop_detector.js';
import { SpinWorkflow, analyzeScreen, chooseCandidate } from './spin_workflow.js';
const $ = s => document.querySelector(s), canvas = $('#screen'), ctx = canvas.getContext('2d');
let frame = null, busy = false, timer = null, frameLabel = '';
const status = text => { $('#status').textContent = text; };
async function request(url) {
  const response = await fetch(url, { cache: 'no-store' });
  if (!response.ok) { const error = await response.json(); throw new Error(error.detail || 'Request failed'); }
  return response;
}
async function loadAppVersion() {
  try {
    const info = await (await request('/api/version')).json();
    const label = `v${info.version}`;
    document.querySelectorAll('.app-version').forEach(el => { el.textContent = label; });
    document.title = `Screen Detection ${label} · Pokemon GO Controller`;
  } catch (_) {}
}
loadAppVersion();
function stopLive() { $('#live').checked = false; clearTimeout(timer); }
let currentBoxes = null;

async function detect(imageElement) {
  const engine = $('#engine')?.value || 'ai';
  const device = $('#device')?.value;
  
  if (engine === 'ai' && device) {
    try {
      const res = await (await request(`/api/screen/detect_stops?serial=${encodeURIComponent(device)}&engine=ai`)).json();
      if (res.ok && res.boxes) {
        return res.boxes;
      }
    } catch (e) {
      console.warn('AI detect fallback to local:', e);
    }
  }

  const work = document.createElement('canvas');
  work.width = Math.min(900, frame.width); work.height = Math.round(frame.height * work.width / frame.width);
  const wc = work.getContext('2d', { willReadFrequently: true }); wc.drawImage(frame, 0, 0, work.width, work.height);
  const boxes = detectStops(wc.getImageData(0, 0, work.width, work.height), Number($('#sensitivity').value));
  const sx = frame.width / work.width, sy = frame.height / work.height;
  return boxes.map(b => ({
    ...b,
    x: Math.round(b.x * sx),
    y: Math.round(b.y * sy),
    width: Math.round(b.width * sx),
    height: Math.round(b.height * sy),
    targetX: Math.round(b.targetX * sx),
    targetY: Math.round(b.targetY * sy)
  }));
}

async function draw() {
  if (!frame) return;
  canvas.width = frame.width; canvas.height = frame.height;
  ctx.drawImage(frame, 0, 0); canvas.hidden = false; $('#empty').hidden = true;
  
  const boxes = await detect(frame);
  currentBoxes = boxes;
  const sx = frame.width / 900.0;
  const threshold = Number($('#threshold').value);
  ctx.lineWidth = Math.max(2, sx * 3); ctx.font = `bold ${Math.round(13 * Math.max(1, sx))}px sans-serif`;
  $('#results').replaceChildren();
  const visible = boxes.filter(box => box.kind === 'ring' || box.eligible || $('#show-rejected').checked).sort((a,b) => b.score-a.score);
  visible.forEach((box, index) => {
    const x = Math.max(0, box.x - 4), y = Math.max(0, box.y - 4), w = box.width + 8, h = box.height + 8;
    const color = box.kind === 'solid' ? '#c2c9ce' : box.color === 'purple' ? '#c88aff' : box.score >= threshold ? '#49ef88' : '#ffdb38';
    ctx.strokeStyle = color; ctx.strokeRect(x, y, w, h);
    const tagW = Math.max(90, 110 * sx);
    ctx.fillStyle = color; ctx.fillRect(x, Math.max(0, y - 20 * sx), tagW, 20 * sx);
    ctx.fillStyle = '#18251d'; ctx.fillText(`#${index + 1} ${box.class_name ? box.class_name + ' ' : ''}${box.score}%`, x + 4, Math.max(15 * sx, y - 4 * sx));
    const item = document.createElement('li'); item.textContent = `${box.score}% · ${box.kind === "ring" ? (box.color === "cyan" ? "วงแหวนฟ้า" : "วงแหวนม่วง — ข้าม") : "ไม่ผ่านเกณฑ์"} · ${box.reason}`;
    $('#results').append(item);
  });
  const eligibleCount = boxes.filter(b => b.eligible && b.score >= threshold).length;
  $('#summary').textContent = `${eligibleCount} เสาฟ้าผ่านเกณฑ์ / ${boxes.length} จุด · ${frame.width}×${frame.height} px`;
  $('#download').disabled = false;
  status(`${frameLabel} · พบเสาพร้อมหมุน ${eligibleCount} จุด · ทั้งหมด ${boxes.length} จุด (${$('#engine')?.value === 'ai' ? 'โหมด AI' : 'โหมด Heuristic'})`);
}

async function show(blob, label) {
  const next = await createImageBitmap(blob);
  frame?.close(); frame = next; frameLabel = label; draw();
}
async function capture() {
  if (busy || workflow.running) return;
  clearTimeout(timer); busy = true; $('#capture').disabled = true; $('#upload').disabled = true;
  try {
    if (!$('#device').value) throw new Error('ไม่พบอุปกรณ์ Android ที่พร้อมใช้งาน');
    status('กำลังจับภาพ…');
    const response = await request(`/api/screen/capture?serial=${encodeURIComponent($('#device').value)}`);
    await show(await response.blob(), `จับภาพ ${new Date().toLocaleTimeString()}`);
  } catch (error) { stopLive(); status(`${error.message} · ภาพที่แสดงเป็นภาพก่อนหน้า (ถ้ามี)`); }
  finally { busy = false; $('#capture').disabled = !$('#device').value; $('#upload').disabled = false;
    if ($('#live').checked && !document.hidden) timer = setTimeout(capture, 3000);
  }
}
async function devices() {
  stopLive();
  try {
    const previous = $('#device').value;
    const list = await (await request('/api/screen/devices')).json();
    $('#device').replaceChildren(...list.map(serial => new Option(serial, serial)));
    if (list.includes(previous)) $('#device').value = previous;
    $('#capture').disabled = !list.length;
    status(list.length ? 'พร้อมจับภาพ — เปิดหน้าแผนที่เกมบนอุปกรณ์ก่อน' : 'ไม่พบ Android ที่เชื่อมต่อ ให้เปิด ADB ใน emulator หรือเปิดไฟล์ภาพ');
  } catch (error) { $('#capture').disabled = true; status(error.message); }
}
$('#capture').onclick = capture; $('#refresh').onclick = devices;
$('#live').onchange = () => $('#live').checked ? capture() : stopLive();
$('#device').onchange = () => { stopLive(); status('เปลี่ยนอุปกรณ์แล้ว กดจับภาพเพื่ออัปเดตภาพ'); };
$('#show-rejected').onchange = draw;
$('#engine').onchange = draw;
$('#threshold').oninput = () => { $('#threshold-value').value = $('#threshold').value; draw(); };
$('#sensitivity').oninput = () => { $('#sensitivity-value').value = $('#sensitivity').value; draw(); };
$('#upload').onchange = async event => {
  stopLive(); const file = event.target.files[0]; if (!file) return;
  try { await show(file, `ไฟล์ ${file.name}`); } catch { status('อ่านภาพไม่ได้ กรุณาเลือก PNG หรือ JPG'); }
};
$('#download').onclick = () => canvas.toBlob(blob => {
  if (!blob) return;
  const url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href = url; link.download = 'pokestop-candidates.png'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
});
document.addEventListener('visibilitychange', () => { if (document.hidden) { stopLive(); workflow.stop(); } });
window.addEventListener('pagehide', () => { stopLive(); workflow.stop(); });
function pixels() {
  const work = document.createElement('canvas'); work.width = Math.min(900, frame.width); work.height = Math.round(frame.height * work.width/frame.width);
  const context = work.getContext('2d'); context.drawImage(frame,0,0,work.width,work.height);
  return context.getImageData(0,0,work.width,work.height);
}
let workflowSerial = '';
const workflow = new SpinWorkflow({
  capture: async () => {
    const response = await request(`/api/screen/capture?serial=${encodeURIComponent(workflowSerial)}`);
    await show(await response.blob(), `ภาพลำดับงาน ${new Date().toLocaleTimeString()}`);
    return pixels();
  },
  input: async command => {
    const response = await fetch('/api/screen/input', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({serial:workflowSerial,...command})});
    if (!response.ok) throw new Error((await response.json()).detail || 'ส่งคำสั่งไม่สำเร็จ');
  },
  analyze: analyzeScreen,
  candidate: image => chooseCandidate(image, detectStops(image, Number($('#sensitivity').value)).filter(box => box.eligible && box.score >= Number($('#threshold').value))),
  report: (step, message) => {
    $('#workflow-status').textContent = `${step} · ${message}`;
    const entry = document.createElement('li'); entry.textContent = `${new Date().toLocaleTimeString()} · ${message}`;
    $('#workflow-log').append(entry);
  },
});
$('#start-workflow').onclick = async () => {
  if (busy || workflow.running || !$('#device').value) return;
  stopLive(); workflowSerial = $('#device').value; $('#workflow-log').replaceChildren();
  const controls = ['device','refresh','capture','upload','live','sensitivity','threshold','start-workflow'];
  controls.forEach(id => $('#'+id).disabled = true); $('#stop-workflow').disabled = false;
  try { await workflow.run(); }
  finally { controls.forEach(id => $('#'+id).disabled = false); $('#stop-workflow').disabled = true; }
};
$('#stop-workflow').onclick = () => { workflow.stop(); $('#workflow-status').textContent = 'กำลังหยุด · คำสั่งที่ส่งไปแล้วอาจจบก่อนหยุด'; };
devices();
