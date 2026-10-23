'use strict';
const $ = id => document.getElementById(id);

let token = '', device = 'garden-01', rows = [], metric = 'soil_moisture', polling = false, connected = false;
let lastStatus = null, lastCommands = [], lastRefresh = 0, refreshPromise = null;
let commandPending = false, stopPending = false, exporting = false;

function updateControls() {
  const s = lastStatus, r = s?.latest;
  const fresh = connected && Date.now() - lastRefresh < 15000;
  const activeStart = lastCommands.some(c => c.action === 'start' && ['queued', 'delivered', 'started'].includes(c.status));
  $('start-pump').disabled = !fresh || !s?.online || s.mode !== 'manual' || s.pump_state !== false ||
    !r?.sensor_ok || !r.reservoir_ok || r.soil_moisture == null || r.soil_moisture >= s.safety.soil_stop_threshold ||
    activeStart || commandPending || stopPending;
  $('set-mode').disabled = !fresh || commandPending || stopPending;
  $('mode').disabled = !fresh || commandPending || stopPending;
  $('seconds').disabled = !fresh || s?.mode !== 'manual' || commandPending;
  // Stop stays available while a different command is pending.
  $('stop-pump').disabled = !token || !lastStatus || stopPending;
  $('export').disabled = !fresh || exporting;
  $('refresh').disabled = !token || polling;
  $('connect-btn').disabled = polling || commandPending || stopPending;
}

const units = { soil_moisture: '%', temperature: '°C', humidity: '%', light_intensity: 'lux' };

const statusNames = {
  queued: 'Đang chờ', delivered: 'Đã giao lệnh', started: 'Đã xác nhận bắt đầu',
  completed: 'Đã hoàn thành', stopped: 'Đã dừng', rejected: 'Bị từ chối',
  cancelled: 'Đã hủy', timed_out: 'Hết hạn / thiếu ACK'
};

const sourceNames = {
  manual: 'Thủ công', rule: 'Theo ngưỡng', cnn: 'CNN',
  rule_fallback: 'Ngưỡng dự phòng', operator: 'Người dùng', safety: 'Giới hạn an toàn'
};

const date = value =>
  value ? new Intl.DateTimeFormat('vi-VN', {
    timeZone: 'Asia/Ho_Chi_Minh', day: '2-digit', month: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit'
  }).format(new Date(value)) : '—';

const number = (x, digits = 1) =>
  x == null || !Number.isFinite(x) ? '—' : x.toLocaleString('vi-VN', { maximumFractionDigits: digits });

// ── Message Banner ──────────────────────────────────────────────────────────
function message(text, error = false) {
  const el = $('message');
  el.hidden = false;
  el.className = 'message' + (error ? ' error' : '');
  el.textContent = text;
  clearTimeout(el._timer);
  if (!error) el._timer = setTimeout(() => { el.hidden = true; }, 6000);
}

function errorMessage(error) {
  if (error.name === 'TimeoutError' || error.name === 'AbortError' || /timed out/i.test(error.message)) {
    return 'Máy chủ phản hồi quá chậm. Kiểm tra kết nối và làm mới trạng thái trước khi thử lại.';
  }
  if (error.name === 'TypeError') return 'Không kết nối được máy chủ. Kiểm tra mạng và thử làm mới.';
  return error.message;
}

// ── Badge ───────────────────────────────────────────────────────────────────
function badge(id, text, kind) {
  const el = $(id);
  // Keep dot if present
  const dot = el.querySelector('.badge-dot');
  el.className = 'badge badge-' + kind;
  if (dot) {
    el.textContent = '';
    el.appendChild(dot);
    el.appendChild(document.createTextNode(text));
  } else {
    el.textContent = text;
  }
}

// ── API ──────────────────────────────────────────────────────────────────────
async function api(path, body) {
  if (!token) throw new Error('Nhập khóa quản trị để kết nối.');
  const result = await fetch(path, {
    method: body ? 'POST' : 'GET',
    headers: {
      'Authorization': 'Bearer ' + token,
      ...(body ? { 'Content-Type': 'application/json' } : {})
    },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(8000)
  });
  if (!result.ok) {
    let detail;
    try { detail = (await result.json()).detail; } catch {}
    if (result.status === 401) throw new Error('Khóa quản trị không hợp lệ. Kiểm tra khóa rồi kết nối lại.');
    throw new Error(typeof detail === 'string' ? detail : `API trả về lỗi ${result.status}`);
  }
  return result;
}

function query(path, extra = '') {
  return path + '?device_id=' + encodeURIComponent(device) + extra;
}

// ── Set meter fill width (clamped 0–100%) ────────────────────────────────────
function setMeter(id, value, max = 100) {
  const el = $(id);
  if (el) el.style.width = Math.max(0, Math.min(100, (value / max) * 100)) + '%';
}

// ── Sidebar status indicator ─────────────────────────────────────────────────
function setSidebarStatus(online, simulated) {
  const dot  = $('sidebar-status-dot');
  const text = $('sidebar-status-text');
  if (!dot || !text) return;
  if (!online) {
    dot.className  = 'status-indicator offline';
    text.textContent = simulated ? 'Mô phỏng đã ngoại tuyến' : 'Thiết bị ngoại tuyến';
  } else if (simulated) {
    dot.className  = 'status-indicator simul';
    text.textContent = 'Dữ liệu mô phỏng';
  } else {
    dot.className  = 'status-indicator online';
    text.textContent = 'Thiết bị trực tuyến';
  }
}

// ── Render Status ────────────────────────────────────────────────────────────
function renderStatus(s) {
  connected = true;
  lastStatus = s;
  lastRefresh = Date.now();
  const r = s.latest, p = s.online ? s.prediction : null, m = s.model;

  // Connection badge
  const isOnline = s.online;
  const isSimul  = r?.simulated;
  badge('connection',
    !isOnline ? (isSimul ? 'Mô phỏng ngoại tuyến' : 'Thiết bị ngoại tuyến')
              : isSimul ? 'Dữ liệu mô phỏng' : 'Thiết bị trực tuyến',
    !isOnline ? 'neutral' : isSimul ? 'warn' : 'good'
  );

  setSidebarStatus(isOnline, isSimul);

  // Hero meta
  $('device-label').textContent = s.device_id;
  $('last-update').textContent  = r ? date(r.timestamp) : 'Chưa có dữ liệu';

  // Garden summary
  $('garden-summary').textContent =
    !r            ? 'Hệ thống đã kết nối. Đang chờ bản ghi cảm biến đầu tiên.'
    : !isOnline   ? 'Thiết bị chưa gửi dữ liệu mới. Các chỉ số bên dưới là giá trị ghi nhận gần nhất.'
    : !r.sensor_ok? 'Cảm biến đang báo lỗi. Bơm được yêu cầu dừng để kiểm tra.'
    : isSimul     ? 'Đang chạy thiết bị giả lập để kiểm tra phần mềm. Các chỉ số không phải phép đo trên cây thật.'
    : 'Thiết bị đang gửi dữ liệu. Bạn có thể theo dõi môi trường và kiểm soát từng lần tưới.';

  // Sensor values
  const soilVal = r?.soil_moisture;
  const tempVal = r?.temperature;
  const humVal  = r?.humidity;
  const luxVal  = r?.light_intensity;

  $('soil').textContent        = number(soilVal, 1);
  $('temperature').textContent = number(tempVal, 1);
  $('humidity').textContent    = number(humVal, 1);
  $('light').textContent       = number(luxVal, 0);

  // Meter bars
  setMeter('soil-meter',     soilVal ?? 0, 100);
  setMeter('temp-meter',     tempVal != null ? Math.max(0, tempVal - 0) : 0, 60);   // 0–60°C
  setMeter('humidity-meter', humVal ?? 0, 100);
  setMeter('light-meter', luxVal ?? 0, 65535);

  // Soil note
  $('soil-note').textContent =
    soilVal == null ? 'Chờ dữ liệu cảm biến'
    : soilVal < s.safety.soil_start_threshold ? 'Độ ẩm dưới ngưỡng bắt đầu tưới'
    : soilVal >= s.safety.soil_stop_threshold  ? 'Đạt ngưỡng dừng tưới'
    : 'Theo dõi độ ẩm và thời gian thấm';

  // Mode select
  if (document.activeElement !== $('mode')) $('mode').value = s.mode;

  // AI Prediction
  const hasWater = p?.prediction === 'WATER';
  $('prediction').textContent = p
    ? (hasWater ? '💧 Có gợi ý tưới' : '✓ Chưa cần tưới')
    : s.online ? 'Đang chờ dữ liệu' : 'Đang chờ thiết bị';

  // Color AI card by prediction
  const aiCard = $('ai-prediction-card');
  if (p) {
    aiCard.style.background = hasWater
      ? 'linear-gradient(135deg, rgba(56,189,248,0.08), rgba(56,189,248,0.02))'
      : 'linear-gradient(135deg, rgba(34,197,94,0.08), rgba(34,197,94,0.02))';
    aiCard.style.borderColor = hasWater
      ? 'rgba(56,189,248,0.2)'
      : 'rgba(34,197,94,0.2)';
  } else {
    aiCard.style.background = '';
    aiCard.style.borderColor = '';
  }

  $('prediction-reason').textContent = p
    ? [p.reason, p.blocked_reason].filter(Boolean).join('. ')
    : s.online ? 'Chưa đủ dữ liệu để đưa ra gợi ý.' : 'Thiết bị ngoại tuyến. Chờ cảm biến mới để đưa ra gợi ý tưới.';

  $('probability').textContent     = p?.water_probability == null ? '—' : number(p.water_probability * 100) + '%';
  $('duration').textContent        = p?.duration_seconds == null  ? '—' : number(p.prediction === 'NO WATER' ? 0 : p.duration_seconds, 0) + ' giây';
  $('duration-source').textContent = p?.duration_source === 'cnn_regression' ? 'CNN hồi quy'
    : p?.duration_source === 'configured_pulse' ? 'Xung tưới đã cấu hình' : '—';

  badge('ai-badge', sourceNames[p?.source] || 'CNN 1D', p?.source === 'cnn' ? 'good' : 'neutral');

  $('model-note').textContent =
    (m.available ? `Model ${m.version}. ` : '') +
    (m.reason || '') +
    (m.experimental ? ' Nhãn huấn luyện chưa xác thực nhu cầu tưới tối ưu.' : '') +
    (!m.control_enabled ? ' AI đang bị khóa điều khiển; có thể dùng dự phòng theo ngưỡng.' : '');

  // Pump badge
  badge('pump-badge',
    s.pump_state === null ? 'Bơm: chưa rõ' : s.pump_state ? 'Bơm đang bật' : 'Bơm đang tắt',
    s.pump_state === null ? 'neutral' : s.pump_state ? 'warn' : 'good'
  );

  $('seconds').max = s.safety.max_pump_seconds;
  $('safety-note').textContent = `Tối đa ${s.safety.max_pump_seconds} giây/lần · Nghỉ ${s.safety.cooldown_seconds} giây · Dừng khi đất đạt ${s.safety.soil_stop_threshold}%. Cần hiệu chuẩn thời lượng với bơm và chậu thực tế.`;
}

// ── Render History ───────────────────────────────────────────────────────────
function renderHistory(items) {
  lastCommands = items;
  $('history-count').textContent = items.length + ' lệnh';
  const body = $('history-body');
  body.replaceChildren();

  if (!items.length) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 6;
    td.className = 'empty-row';
    td.innerHTML = `<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><line x1="8" y1="15" x2="16" y2="15"/><line x1="9" y1="9" x2="9.01" y2="9"/><line x1="15" y1="9" x2="15.01" y2="9"/></svg>Chưa có hoạt động tưới`;
    tr.append(td);
    body.append(tr);
    return;
  }

  for (const item of items) {
    const tr = document.createElement('tr');
    const values = [
      date(item.created_at),
      item.action === 'stop' ? 'Dừng bơm' : 'Bật bơm',
      item.actual_duration_seconds == null
        ? `${item.duration_seconds}s yêu cầu`
        : `${number(item.actual_duration_seconds)}s thực tế`,
      sourceNames[item.source] || item.source,
      statusNames[item.status] || item.status,
      item.reason
    ];

    for (const [i, value] of values.entries()) {
      const td = document.createElement('td');
      td.textContent = value;
      // Color status column
      if (i === 4) {
        if (item.status === 'completed') td.style.color = 'var(--green-400)';
        else if (item.status === 'rejected' || item.status === 'timed_out') td.style.color = 'var(--red)';
        else if (item.status === 'started') td.style.color = 'var(--amber)';
      }
      // Color action column
      if (i === 1) {
        td.style.fontWeight = '600';
        td.style.color = item.action === 'stop' ? 'var(--red)' : 'var(--green-400)';
      }
      tr.append(td);
    }
    body.append(tr);
  }
}

// ── Chart Draw ───────────────────────────────────────────────────────────────
const CHART_COLORS = {
  soil_moisture:  { line: '#22c55e', fill1: 'rgba(34,197,94,0.25)',  fill2: 'rgba(34,197,94,0.01)',  grid: 'rgba(34,197,94,0.07)' },
  temperature:    { line: '#f59e0b', fill1: 'rgba(245,158,11,0.2)',  fill2: 'rgba(245,158,11,0.01)', grid: 'rgba(245,158,11,0.06)' },
  humidity:       { line: '#38bdf8', fill1: 'rgba(56,189,248,0.2)',  fill2: 'rgba(56,189,248,0.01)', grid: 'rgba(56,189,248,0.06)' },
  light_intensity:{ line: '#facc15', fill1: 'rgba(250,204,21,0.2)',  fill2: 'rgba(250,204,21,0.01)', grid: 'rgba(250,204,21,0.06)' },
};

function draw() {
  const canvas = $('chart');
  const rect   = canvas.getBoundingClientRect();
  const dpr    = window.devicePixelRatio || 1;
  canvas.width  = rect.width  * dpr;
  canvas.height = rect.height * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  const points = rows.filter(r => r[metric] != null && Number.isFinite(r[metric]));
  $('chart-empty').hidden = points.length > 0;
  $('sample-count').textContent = rows.length + ' mẫu gần nhất';
  if (!points.length) {
    canvas.setAttribute('aria-label', 'Chưa có dữ liệu cảm biến trong khoảng đã chọn');
    return;
  }

  const w = rect.width, h = rect.height;
  const left = 46, right = 14, top = 20, bottom = 32;
  const colors = CHART_COLORS[metric];

  let lo = Math.min(...points.map(p => p[metric]));
  let hi = Math.max(...points.map(p => p[metric]));
  if (metric === 'soil_moisture' || metric === 'humidity') {
    lo = 0; hi = 100;
  } else {
    let margin = Math.max((hi - lo) * 0.18, 1);
    lo = Math.max(metric === 'temperature' ? -40 : 0, lo - margin);
    hi += margin;
  }

  let t0 = new Date(points[0].timestamp).getTime();
  let t1 = new Date(points.at(-1).timestamp).getTime();
  if (t1 === t0) t1 += 1000;

  const xPos = p => left + (new Date(p.timestamp).getTime() - t0) / (t1 - t0) * (w - left - right);
  const yPos = p => top + (hi - p[metric]) / (hi - lo) * (h - top - bottom);

  // Grid lines
  ctx.font = '10px Inter, system-ui, sans-serif';
  ctx.fillStyle = '#94aa9e';
  ctx.strokeStyle = colors.grid;
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const yy = top + i * (h - top - bottom) / 4;
    ctx.beginPath();
    ctx.moveTo(left, yy);
    ctx.lineTo(w - right, yy);
    ctx.stroke();
    ctx.fillText(number(hi - (hi - lo) * i / 4, 0), 2, yy + 3.5);
  }
  ctx.fillStyle = '#94aa9e';
  ctx.fillText(units[metric], 2, 13);

  // Fill gradient area
  ctx.beginPath();
  ctx.moveTo(xPos(points[0]), h - bottom);
  points.forEach(p => ctx.lineTo(xPos(p), yPos(p)));
  ctx.lineTo(xPos(points.at(-1)), h - bottom);
  ctx.closePath();
  const gradient = ctx.createLinearGradient(0, top, 0, h - bottom);
  gradient.addColorStop(0, colors.fill1);
  gradient.addColorStop(1, colors.fill2);
  ctx.fillStyle = gradient;
  ctx.fill();

  // Main line
  ctx.strokeStyle = colors.line;
  ctx.lineWidth   = 2.5;
  ctx.lineJoin    = 'round';
  ctx.lineCap     = 'round';
  ctx.shadowColor = colors.line;
  ctx.shadowBlur  = 6;
  ctx.beginPath();
  points.forEach((p, i) => i ? ctx.lineTo(xPos(p), yPos(p)) : ctx.moveTo(xPos(p), yPos(p)));
  ctx.stroke();
  ctx.shadowBlur = 0;

  // Single point dot
  if (points.length === 1) {
    ctx.beginPath();
    ctx.arc(xPos(points[0]), yPos(points[0]), 5, 0, Math.PI * 2);
    ctx.fillStyle = colors.line;
    ctx.fill();
  }

  // Time labels
  ctx.fillStyle  = '#94aa9e';
  ctx.font       = '10px Inter, system-ui, sans-serif';
  ctx.textAlign  = 'left';
  const fmt = new Intl.DateTimeFormat('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh', hour: '2-digit', minute: '2-digit' });
  ctx.fillText(fmt.format(new Date(t0)), left, h - 6);
  ctx.textAlign = 'right';
  ctx.fillText(fmt.format(new Date(t1)), w - right, h - 6);

  canvas.setAttribute('aria-label', `${points.length} mẫu ${metric}, nhỏ nhất ${number(Math.min(...points.map(p => p[metric])))}, lớn nhất ${number(Math.max(...points.map(p => p[metric])))} ${units[metric]}`);
}

// ── Refresh ──────────────────────────────────────────────────────────────────
function refresh() {
  if (!token) return Promise.resolve();
  if (refreshPromise) return refreshPromise;
  polling = true;
  updateControls();
  refreshPromise = (async () => {
    try {
    const [s, r, h] = await Promise.all([
      api(query('/api/status')).then(r => r.json()),
      api(query('/api/readings', `&hours=${$('hours').value}&limit=5000`)).then(r => r.json()),
      api(query('/api/irrigations')).then(r => r.json())
    ]);
    const recovering = !connected;
    renderStatus(s);
    rows = r;
    draw();
    renderHistory(h);
    if (recovering) message('Đã kết nối. Dữ liệu được cập nhật mỗi 5 giây.');
  } catch (e) {
    connected = false;
    badge('connection', 'Không kết nối được', 'bad');
    setSidebarStatus(false, false);
    badge('pump-badge', 'Bơm: chưa rõ', 'neutral');
    $('prediction').textContent = 'Đang chờ kết nối';
    $('prediction-reason').textContent = 'Chưa có dữ liệu mới để đưa ra gợi ý tưới.';
    for (const id of ['probability', 'duration', 'duration-source']) $(id).textContent = '—';
    $('garden-summary').textContent = 'Không lấy được dữ liệu mới. Kiểm tra kết nối hoặc khóa quản trị; các giá trị đang hiển thị có thể đã cũ.';
    message(errorMessage(e), true);
  } finally {
    polling = false;
    refreshPromise = null;
    updateControls();
  }
  })();
  return refreshPromise;
}

// ── Command helper ───────────────────────────────────────────────────────────
async function command(button, path, body) {
  const isStop = body.action === 'stop';
  if ((isStop && stopPending) || (!isStop && (commandPending || stopPending))) return;
  if (isStop) stopPending = true;
  else commandPending = true;
  updateControls();
  try {
    await (await api(path, body)).json();
    message(
      path === '/api/mode'
        ? 'Đã đổi chế độ. Thiết bị sẽ nhận lệnh dừng trước khi áp dụng lần tưới tiếp theo.'
        : 'Đã ghi nhận lệnh. Kiểm tra xác nhận của thiết bị trong nhật ký.'
    );
    if (refreshPromise) await refreshPromise;
    await refresh();
  } catch (e) {
    message(errorMessage(e), true);
  } finally {
    if (isStop) stopPending = false;
    else commandPending = false;
    updateControls();
  }
}

// ── Events ───────────────────────────────────────────────────────────────────

// Connect form
$('connect-form').addEventListener('submit', e => {
  e.preventDefault();
  if (polling || commandPending || stopPending) return;
  if (!$('token').value.trim() || !$('device').value.trim()) {
    message('Nhập khóa quản trị và mã thiết bị để kết nối.', true);
    return;
  }
  token  = $('token').value.trim();
  device = $('device').value.trim();
  connected = false;
  lastStatus = null;
  rows = [];
  lastCommands = [];
  for (const id of ['soil', 'temperature', 'humidity', 'light', 'probability', 'duration', 'duration-source']) $(id).textContent = '—';
  for (const id of ['soil-meter', 'temp-meter', 'humidity-meter', 'light-meter']) setMeter(id, 0);
  $('prediction').textContent = 'Đang chờ dữ liệu';
  $('prediction-reason').textContent = 'Đang kiểm tra kết nối đến thiết bị.';
  $('model-note').textContent = 'Đang tải thông tin mô hình.';
  $('last-update').textContent = 'Chưa có dữ liệu';
  $('device-label').textContent = device;
  renderHistory([]);
  draw();
  $('message').hidden = true;
  refresh();
});

// Refresh button
$('refresh').onclick = () => refresh();

// Hours select
$('hours').onchange = refresh;

// Chart metric tabs
document.querySelectorAll('[data-metric]').forEach(button =>
  button.onclick = () => {
    metric = button.dataset.metric;
    document.querySelectorAll('[data-metric]').forEach(b => {
      b.classList.toggle('active', b === button);
      b.setAttribute('aria-pressed', String(b === button));
    });
    draw();
  }
);

// Control buttons
$('set-mode').onclick = () => command($('set-mode'), '/api/mode', { device_id: device, mode: $('mode').value });

$('start-pump').onclick = () => {
  const seconds = Number($('seconds').value);
  if (!Number.isInteger(seconds) || seconds < 1 || seconds > Number($('seconds').max)) {
    message('Thời lượng không hợp lệ.', true);
    return;
  }
  command($('start-pump'), '/api/pump', { device_id: device, action: 'start', duration_seconds: seconds });
};

$('stop-pump').onclick = () => command($('stop-pump'), '/api/pump', { device_id: device, action: 'stop' });

// CSV Export
$('export').onclick = async () => {
  if (exporting) return;
  exporting = true;
  updateControls();
  try {
    const res = await api(query('/api/export', '&hours=' + $('hours').value));
    const url = URL.createObjectURL(await res.blob());
    const a   = document.createElement('a');
    a.href     = url;
    a.download = 'smart_garden_sensor_history.csv';
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) {
    message(errorMessage(e), true);
  } finally {
    exporting = false;
    updateControls();
  }
};

// Mobile sidebar toggle
const sidebar     = document.getElementById('sidebar');
const menuToggle  = $('menu-toggle');
const overlay = $('sidebar-overlay');
const mobileQuery = window.matchMedia('(max-width: 960px)');
function setMenu(open, returnFocus = false) {
  sidebar.classList.toggle('open', open);
  document.body.classList.toggle('menu-open', open);
  menuToggle.setAttribute('aria-expanded', String(open));
  menuToggle.setAttribute('aria-label', open ? 'Đóng menu' : 'Mở menu');
  overlay.hidden = !open;
  if (open) sidebar.querySelector('a').focus();
  else if (returnFocus) menuToggle.focus();
}
if (menuToggle && sidebar) {
  menuToggle.onclick = () => setMenu(!sidebar.classList.contains('open'));
  overlay.onclick = () => setMenu(false, true);
  document.addEventListener('keydown', e => {
    if (!sidebar.classList.contains('open')) return;
    if (e.key === 'Escape') setMenu(false, true);
    if (e.key === 'Tab') {
      const links = Array.from(sidebar.querySelectorAll('a'));
      if (e.shiftKey && document.activeElement === links[0]) { e.preventDefault(); links.at(-1).focus(); }
      else if (!e.shiftKey && document.activeElement === links.at(-1)) { e.preventDefault(); links[0].focus(); }
    }
  });
  sidebar.querySelectorAll('a').forEach(link => link.addEventListener('click', () => {
    if (mobileQuery.matches) setMenu(false, true);
  }));
  mobileQuery.addEventListener('change', () => setMenu(false));
}

function updateNavigation() {
  const hash = window.location.hash || '#overview';
  document.querySelectorAll('nav a[href^="#"]').forEach(link => {
    const current = link.getAttribute('href') === hash;
    link.classList.toggle('nav-active', current);
    if (current) { link.setAttribute('aria-current', 'location'); $('current-section').textContent = link.textContent.trim(); }
    else link.removeAttribute('aria-current');
  });
}
window.addEventListener('hashchange', updateNavigation);

// Resize
window.addEventListener('resize', draw);

// Clock
setInterval(() => {
  const el = $('clock');
  if (el) el.textContent = date(new Date().toISOString());
  updateControls();
}, 1000);

// Auto-refresh every 5s
setInterval(refresh, 5000);

// Init
updateControls();
updateNavigation();
draw();
