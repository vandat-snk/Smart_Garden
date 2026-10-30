'use strict';
const $ = id => document.getElementById(id);

let token = '', device = 'garden-01', rows = [], metric = 'soil_moisture', polling = false, connected = false;
let lastStatus = null, lastCommands = [], lastRefresh = 0, refreshPromise = null;
let commandPending = false, stopPending = false, exporting = false;
let modeDraft = false;
let connectionCollapsed = false;
const modeNames = { manual: 'Thủ công', rule: 'Tự động theo ngưỡng', ai: 'Tự động theo AI' };

function renderSystemHealth() {
  const s = lastStatus, r = s?.latest;
  const fresh = connected && Date.now() - lastRefresh < 15000;
  const online = fresh && s?.online;
  $('health-link').textContent = !fresh ? (token ? 'Chờ kết nối máy chủ' : 'Chưa kết nối')
    : online ? (r?.simulated ? 'Mô phỏng trực tuyến' : 'Thiết bị trực tuyến') : 'Thiết bị ngoại tuyến';
  $('health-link').dataset.tone = online ? 'good' : 'muted';
  const seconds = r ? Math.max(0, Math.floor((Date.now() - new Date(r.timestamp).getTime()) / 1000)) : null;
  $('health-updated').textContent = seconds == null ? 'Chờ bản ghi cảm biến'
    : `Bản ghi ${seconds < 60 ? `${seconds} giây` : seconds < 3600 ? `${Math.floor(seconds / 60)} phút` : `${Math.floor(seconds / 3600)} giờ`} trước`;
  $('health-sensors').textContent = !online ? 'Chưa xác định' : !r?.sensor_ok ? 'Cần kiểm tra cảm biến'
    : !r.reservoir_ok ? 'Nguồn nước chưa sẵn sàng' : 'Thiết bị báo sẵn sàng';
  $('health-sensors').dataset.tone = !online ? 'muted' : r?.sensor_ok && r.reservoir_ok ? 'good' : 'warn';
  $('health-sensor-note').textContent = !online ? 'Cần dữ liệu mới từ thiết bị' : r?.simulated ? 'Trạng thái từ thiết bị mô phỏng' : 'Theo tín hiệu thiết bị; chưa xác nhận dòng nước';
  $('health-mode').textContent = fresh ? modeNames[s?.mode] || 'Chưa xác định' : 'Chưa xác định';
  $('connection-caption').textContent = fresh ? `${s.device_id} · Đã xác thực máy chủ` : token ? 'Đang chờ kết nối lại · Có thể chỉnh thiết lập' : 'Nhập khóa quản trị để bắt đầu theo dõi';
}

function updateControls() {
  renderSystemHealth();
  const s = lastStatus, r = s?.latest;
  const fresh = connected && Date.now() - lastRefresh < 15000;
  const activeStart = lastCommands.some(c => c.action === 'start' && ['queued', 'delivered', 'started'].includes(c.status));
  const lastStart = lastCommands.find(c => c.action === 'start');
  const cooldown = s && lastStart ? Math.max(0, Math.ceil((new Date(lastStart.created_at).getTime() + s.safety.cooldown_seconds * 1000 - Date.now()) / 1000)) : 0;
  $('start-pump').disabled = !fresh || !s?.online || s.mode !== 'manual' || s.pump_state !== false ||
    !r?.sensor_ok || !r.reservoir_ok || r.soil_moisture == null || r.soil_moisture >= s.safety.soil_stop_threshold ||
    activeStart || commandPending || stopPending || cooldown > 0;
  $('set-mode').disabled = !fresh || commandPending || stopPending;
  $('mode').disabled = !fresh || commandPending || stopPending;
  $('seconds').disabled = !fresh || s?.mode !== 'manual' || commandPending;
  // Stop stays available while a different command is pending.
  $('stop-pump').disabled = !token || !lastStatus || stopPending;
  $('export').disabled = !fresh || exporting;
  $('refresh').disabled = !token || polling;
  $('connect-btn').disabled = polling || commandPending || stopPending;
  $('control-hint').textContent = !connected ? 'Kết nối hệ thống để điều khiển tưới.'
    : !fresh ? 'Đang chờ cập nhật trạng thái mới.'
    : !s?.online ? 'Thiết bị ngoại tuyến. Chờ dữ liệu mới trước khi tưới.'
    : !r?.sensor_ok ? 'Cảm biến đang báo lỗi. Kiểm tra thiết bị trước khi tưới.'
    : !r.reservoir_ok ? 'Bình nước chưa sẵn sàng. Kiểm tra nguồn nước.'
    : commandPending || stopPending ? 'Đang gửi yêu cầu. Bạn vẫn có thể yêu cầu dừng bơm.'
    : activeStart || s.pump_state ? 'Đang thực hiện lệnh tưới. Theo dõi xác nhận trong nhật ký.'
    : s.mode !== 'manual' ? 'Hệ thống đang tự động. Chuyển sang Thủ công nếu muốn tưới bằng nút bấm.'
    : r.soil_moisture >= s.safety.soil_stop_threshold ? 'Đất đã đạt ngưỡng đủ ẩm. Chưa cần bật bơm.'
    : cooldown > 0 ? `Đang chờ nước thấm. Có thể tưới tiếp sau ${Math.floor(cooldown / 60)} phút ${cooldown % 60} giây.`
    : 'Sẵn sàng tưới thủ công. Chọn thời lượng phù hợp rồi bấm Tưới ngay.';
}

const units = { soil_moisture: '%', temperature: '°C', humidity: '%', light_intensity: 'lux' };
const metricNames = { soil_moisture: 'độ ẩm đất', temperature: 'nhiệt độ', humidity: 'độ ẩm không khí', light_intensity: 'ánh sáng' };

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
  $('message-text').textContent = text;
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

  // Animate number pop on update
  function setNum(id, val, digits) {
    const el = $(id);
    const newText = number(val, digits);
    if (el.textContent !== newText) {
      el.textContent = newText;
      el.classList.remove('updated');
      void el.offsetWidth; // reflow
      el.classList.add('updated');
    }
  }
  setNum('soil', soilVal, 1);
  setNum('temperature', tempVal, 1);
  setNum('humidity', humVal, 1);
  setNum('light', luxVal, 0);

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
  if (!modeDraft && document.activeElement !== $('mode')) $('mode').value = s.mode;

  // AI Prediction
  const hasWater = p?.prediction === 'WATER';
  $('prediction').textContent = p
    ? (hasWater ? 'Có gợi ý tưới' : 'Chưa cần tưới')
    : s.online ? 'Đang chờ dữ liệu' : 'Đang chờ thiết bị';

  // Color AI card by prediction
  const aiCard = $('ai-prediction-card');
  aiCard.dataset.decision = p ? (hasWater ? 'water' : 'hold') : 'waiting';

  $('prediction-reason').textContent = p
    ? [p.reason, p.blocked_reason].filter(Boolean).join('. ')
    : s.online ? 'Chưa đủ dữ liệu để đưa ra gợi ý.' : 'Thiết bị ngoại tuyến. Chờ cảm biến mới để đưa ra gợi ý tưới.';

  $('probability').textContent     = p?.water_probability == null ? '—' : number(p.water_probability * 100) + '%';
  $('duration').textContent        = p?.duration_seconds == null  ? '—' : number(p.prediction === 'NO WATER' ? 0 : p.duration_seconds, 0) + ' giây';
  $('duration-source').textContent = p?.duration_source === 'cnn_regression' ? 'CNN hồi quy'
    : p?.duration_source === 'configured_pulse' ? 'Xung tưới đã cấu hình' : '—';

  badge('ai-badge', p?.source === 'manual' ? 'Gợi ý theo ngưỡng' : sourceNames[p?.source] || 'Chờ dữ liệu', p?.source === 'cnn' ? 'good' : 'neutral');

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
  const term = $('history-search').value.trim().toLocaleLowerCase('vi');
  const category = $('history-filter').value;
  const groups = { pending: ['queued', 'delivered', 'started'], finished: ['completed', 'stopped', 'cancelled'], issues: ['rejected', 'timed_out'] };
  const visible = items.filter(item => (category === 'all' || groups[category]?.includes(item.status)) &&
    [item.reason, sourceNames[item.source] || item.source, statusNames[item.status] || item.status,
      item.action === 'stop' ? 'Dừng bơm' : 'Bật bơm', date(item.created_at)]
      .join(' ').toLocaleLowerCase('vi').includes(term));
  $('history-result').textContent = `Hiển thị ${visible.length} / ${items.length} lệnh đã tải`;
  const body = $('history-body');
  body.replaceChildren();

  if (!visible.length) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 6;
    td.className = 'empty-row';
    td.textContent = items.length ? 'Không có lệnh phù hợp. Thử từ khóa hoặc trạng thái khác.' : 'Chưa có hoạt động tưới. Các lệnh và xác nhận sẽ xuất hiện tại đây.';
    tr.append(td);
    body.append(tr);
    return;
  }

  for (const item of visible) {
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
        td.className = 'status-cell';
        td.dataset.state = item.status;
        const label = document.createElement('span');
        label.textContent = value;
        td.replaceChildren(label);
      }
      // Color action column
      if (i === 1) {
        td.style.fontWeight = '600';
        td.style.color = item.action === 'stop' ? 'var(--red)' : 'var(--success)';
      }
      tr.append(td);
    }
    body.append(tr);
  }
}

// ── Chart Draw ───────────────────────────────────────────────────────────────
const CHART_COLORS = {
  soil_moisture:  { line: '#16a34a', fill1: 'rgba(34,197,94,0.18)',  fill2: 'rgba(34,197,94,0.01)',  grid: 'rgba(34,197,94,0.10)' },
  temperature:    { line: '#d97706', fill1: 'rgba(217,119,6,0.15)',  fill2: 'rgba(217,119,6,0.01)', grid: 'rgba(217,119,6,0.08)' },
  humidity:       { line: '#0284c7', fill1: 'rgba(2,132,199,0.15)',  fill2: 'rgba(2,132,199,0.01)', grid: 'rgba(2,132,199,0.08)' },
  light_intensity:{ line: '#ca8a04', fill1: 'rgba(202,138,4,0.15)',  fill2: 'rgba(202,138,4,0.01)', grid: 'rgba(202,138,4,0.08)' },
};

function draw() {
  const canvas = $('chart');
  const rect   = canvas.getBoundingClientRect();
  const dpr    = window.devicePixelRatio || 1;
  canvas.width  = rect.width  * dpr;
  canvas.height = rect.height * dpr;
  const ctx = canvas.getContext('2d');
  ctx.scale(dpr, dpr);

  const points = rows.filter(r => r.sensor_ok !== false && r[metric] != null && Number.isFinite(r[metric]));
  $('chart-empty').hidden = points.length > 0;
  $('sample-count').textContent = rows.length + ' mẫu gần nhất';
  $('chart-gap-note').hidden = true;
  if (!points.length) {
    for (const id of ['chart-latest', 'chart-min', 'chart-max']) $(id).textContent = '—';
    canvas.setAttribute('aria-label', 'Chưa có dữ liệu cảm biến trong khoảng đã chọn');
    return;
  }
  const values = points.map(p => p[metric]);
  const formatValue = value => `${number(value, metric === 'light_intensity' ? 0 : 1)} ${units[metric]}`;
  $('chart-latest').textContent = formatValue(points.at(-1)[metric]);
  $('chart-min').textContent = formatValue(Math.min(...values));
  $('chart-max').textContent = formatValue(Math.max(...values));
  const segments = [];
  for (const point of points) {
    const prior = segments.at(-1)?.at(-1);
    if (!prior || new Date(point.timestamp) - new Date(prior.timestamp) > 120000 || Boolean(point.simulated) !== Boolean(prior.simulated)) segments.push([]);
    segments.at(-1).push(point);
  }
  $('chart-gap-note').hidden = segments.length < 2;

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
  ctx.font = '11px Segoe UI, system-ui, sans-serif';
  ctx.fillStyle = '#64796a';
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
  ctx.fillStyle = '#64796a';
  ctx.fillText(units[metric], 2, 13);

  // Fill gradient area
  const gradient = ctx.createLinearGradient(0, top, 0, h - bottom);
  gradient.addColorStop(0, colors.fill1);
  gradient.addColorStop(1, colors.fill2);
  ctx.fillStyle = gradient;
  for (const segment of segments) {
    ctx.beginPath();
    ctx.moveTo(xPos(segment[0]), h - bottom);
    segment.forEach(p => ctx.lineTo(xPos(p), yPos(p)));
    ctx.lineTo(xPos(segment.at(-1)), h - bottom);
    ctx.closePath();
    ctx.fill();
  }

  // Main line
  ctx.strokeStyle = colors.line;
  ctx.lineWidth   = 2.5;
  ctx.lineJoin    = 'round';
  ctx.lineCap     = 'round';
  ctx.beginPath();
  segments.forEach(segment => segment.forEach((p, i) => i ? ctx.lineTo(xPos(p), yPos(p)) : ctx.moveTo(xPos(p), yPos(p))));
  ctx.stroke();

  // Single point dot
  for (const segment of segments.filter(s => s.length === 1)) {
    ctx.beginPath();
    ctx.arc(xPos(segment[0]), yPos(segment[0]), 4, 0, Math.PI * 2);
    ctx.fillStyle = colors.line;
    ctx.fill();
  }

  // Time labels
  ctx.fillStyle  = '#64796a';
  ctx.font       = '11px Segoe UI, system-ui, sans-serif';
  ctx.textAlign  = 'left';
  const fmt = new Intl.DateTimeFormat('vi-VN', { timeZone: 'Asia/Ho_Chi_Minh', hour: '2-digit', minute: '2-digit' });
  ctx.fillText(fmt.format(new Date(t0)), left, h - 6);
  ctx.textAlign = 'right';
  ctx.fillText(fmt.format(new Date(t1)), w - right, h - 6);

  canvas.setAttribute('aria-label', `${points.length} mẫu ${metricNames[metric]}, nhỏ nhất ${formatValue(Math.min(...values))}, lớn nhất ${formatValue(Math.max(...values))}`);
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
    if (!connectionCollapsed) {
      // Return focus before collapsing the form that contains the submit button.
      if ($('connect-form').contains(document.activeElement)) $('connection-settings').querySelector('summary').focus();
      $('connection-settings').open = false;
      connectionCollapsed = true;
    }
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
    const result = await (await api(path, body)).json();
    if (path === '/api/mode') {
      modeDraft = false;
      renderStatus(result);
    }
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
  modeDraft = false;
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
$('mode').onchange = () => { modeDraft = true; };
$('message-dismiss').onclick = () => { $('message').hidden = true; };
$('history-search').oninput = () => renderHistory(lastCommands);
$('history-filter').onchange = () => renderHistory(lastCommands);

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
