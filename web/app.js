'use strict';
const $ = id => document.getElementById(id);
let token = '', device = 'garden-01', rows = [], metric = 'soil_moisture', polling = false, connected = false;
const units = {soil_moisture:'%', temperature:'°C',humidity:'%',light_intensity:'lux'};
const statusNames = {queued:'Đang chờ',delivered:'Đã giao lệnh',started:'Đã xác nhận bắt đầu',completed:'Đã hoàn thành',stopped:'Đã dừng',rejected:'Bị từ chối',cancelled:'Đã hủy',timed_out:'Hết hạn / thiếu ACK'};
const sourceNames = {manual:'Thủ công',rule:'Theo ngưỡng',cnn:'CNN',rule_fallback:'Ngưỡng dự phòng',operator:'Người dùng',safety:'Giới hạn an toàn'};
const date = value => value ? new Intl.DateTimeFormat('vi-VN',{timeZone:'Asia/Ho_Chi_Minh',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit'}).format(new Date(value)) : '—';
const number = (x,digits=1) => x == null || !Number.isFinite(x) ? '—' : x.toLocaleString('vi-VN',{maximumFractionDigits:digits});
function message(text,error=false){$('message').hidden=false;$('message').className='message'+(error?' error':'');$('message').textContent=text;}
function badge(id,text,kind){$(id).textContent=text;$(id).className='badge '+kind;}
async function api(path,body){
  if(!token) throw new Error('Nhập khóa quản trị để kết nối.');
  const result=await fetch(path,{method:body?'POST':'GET',headers:{'Authorization':'Bearer '+token,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined,signal:AbortSignal.timeout(8000)});
  if(!result.ok){let detail;try{detail=(await result.json()).detail;}catch{}throw new Error(typeof detail==='string'?detail:`API trả về lỗi ${result.status}`);}
  return result;
}
function query(path,extra=''){return path+'?device_id='+encodeURIComponent(device)+extra;}
function renderStatus(s){
  connected=true; const r=s.latest,p=s.prediction,m=s.model;
  badge('connection',!s.online?(r?.simulated?'Mô phỏng đã ngoại tuyến':'Thiết bị ngoại tuyến'):r?.simulated?'Dữ liệu mô phỏng':'Thiết bị trực tuyến',!s.online?'neutral':r?.simulated?'warn':'good');
  $('device-label').textContent=s.device_id;$('last-update').textContent=r?date(r.timestamp):'Chưa có dữ liệu';
  $('garden-summary').textContent=!r?'Hệ thống đã kết nối. Đang chờ bản ghi cảm biến đầu tiên.':!s.online?'Thiết bị chưa gửi dữ liệu mới. Các chỉ số bên dưới là giá trị ghi nhận gần nhất.':!r.sensor_ok?'Cảm biến đang báo lỗi. Bơm được yêu cầu dừng để kiểm tra.':r.simulated?'Đang chạy thiết bị giả lập để kiểm tra phần mềm. Các chỉ số không phải phép đo trên cây thật.':'Thiết bị đang gửi dữ liệu. Bạn có thể theo dõi môi trường và kiểm soát từng lần tưới.';
  for(const [id,key] of [['soil','soil_moisture'],['temperature','temperature'],['humidity','humidity'],['light','light_intensity']]) $(id).textContent=number(r?.[key],id==='light'?0:1);
  $('soil-meter').style.width=Math.max(0,Math.min(100,r?.soil_moisture||0))+'%';
  $('soil-note').textContent=r?.soil_moisture==null?'Chờ dữ liệu cảm biến':r.soil_moisture<s.safety.soil_start_threshold?'Độ ẩm dưới ngưỡng bắt đầu tưới':r.soil_moisture>=s.safety.soil_stop_threshold?'Đạt ngưỡng dừng tưới':'Theo dõi độ ẩm và thời gian thấm';
  if(document.activeElement!==$('mode')) $('mode').value=s.mode;
  $('prediction').textContent=p?(p.prediction==='WATER'?'Có gợi ý tưới':'Chưa cần tưới'):'Đang chờ dữ liệu';
  $('prediction-reason').textContent=p?[p.reason,p.blocked_reason].filter(Boolean).join('. '):'Chưa đủ dữ liệu để đưa ra gợi ý.';
  $('probability').textContent=p?.water_probability==null?'—':number(p.water_probability*100)+'%';
  $('duration').textContent=p?.duration_seconds==null?'—':number(p.prediction==='NO WATER'?0:p.duration_seconds,0)+' giây';
  $('duration-source').textContent=p?.duration_source==='cnn_regression'?'CNN hồi quy':p?.duration_source==='configured_pulse'?'Xung tưới đã cấu hình':'—';
  badge('ai-badge',sourceNames[p?.source]||'CNN 1D',p?.source==='cnn'?'good':'neutral');
  $('model-note').textContent=(m.available?`Model ${m.version}. `:'')+(m.reason||'')+(m.experimental?' Nhãn huấn luyện chưa xác thực nhu cầu tưới tối ưu.':'')+(!m.control_enabled?' AI đang bị khóa điều khiển; có thể dùng dự phòng theo ngưỡng.':'');
  badge('pump-badge',s.pump_state===null?'Bơm: chưa rõ':s.pump_state?'Bơm đang bật':'Bơm đang tắt',s.pump_state===null?'neutral':s.pump_state?'warn':'good');
  $('start-pump').disabled=!s.online||s.mode!=='manual'||s.pump_state!==false;
  $('seconds').max=s.safety.max_pump_seconds;
  $('safety-note').textContent=`Tối đa ${s.safety.max_pump_seconds} giây/lần · Khoảng nghỉ ${s.safety.cooldown_seconds} giây · Dừng khi đất đạt ${s.safety.soil_stop_threshold}%. Thời lượng cấu hình cần được hiệu chuẩn với bơm và chậu thực tế.`;
}
function renderHistory(items){
  $('history-count').textContent=items.length+' lệnh';const body=$('history-body');body.replaceChildren();
  if(!items.length){const tr=document.createElement('tr'),td=document.createElement('td');td.colSpan=6;td.className='empty-row';td.textContent='Chưa có hoạt động tưới.';tr.append(td);body.append(tr);return;}
  for(const item of items){const tr=document.createElement('tr');for(const value of [date(item.created_at),item.action==='stop'?'Dừng bơm':'Bật bơm',item.actual_duration_seconds==null?`${item.duration_seconds}s yêu cầu`:`${number(item.actual_duration_seconds)}s thực tế`,sourceNames[item.source]||item.source,statusNames[item.status]||item.status,item.reason]){const td=document.createElement('td');td.textContent=value;tr.append(td);}body.append(tr);}
}
function draw(){
  const canvas=$('chart'),rect=canvas.getBoundingClientRect(),dpr=window.devicePixelRatio||1;
  canvas.width=rect.width*dpr;canvas.height=rect.height*dpr;const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);
  const points=rows.filter(r=>r[metric]!=null&&Number.isFinite(r[metric]));$('chart-empty').hidden=points.length>0;
  $('sample-count').textContent=rows.length+' mẫu gần nhất';if(!points.length)return;
  const w=rect.width,h=rect.height,left=43,right=13,top=18,bottom=29;
  let lo=Math.min(...points.map(p=>p[metric])),hi=Math.max(...points.map(p=>p[metric]));
  if(metric==='soil_moisture'||metric==='humidity'){lo=0;hi=100;}else{let margin=Math.max((hi-lo)*.15,1);lo=Math.max(metric==='temperature'?-40:0,lo-margin);hi+=margin;}
  let t0=new Date(points[0].timestamp).getTime(),t1=new Date(points.at(-1).timestamp).getTime();if(t1===t0)t1+=1000;
  const x=p=>left+(new Date(p.timestamp).getTime()-t0)/(t1-t0)*(w-left-right),y=p=>top+(hi-p[metric])/(hi-lo)*(h-top-bottom);
  ctx.font='10px Segoe UI';ctx.fillStyle='#9ba88f';ctx.strokeStyle='#edf1e7';ctx.lineWidth=1;
  for(let i=0;i<=4;i++){let yy=top+i*(h-top-bottom)/4;ctx.beginPath();ctx.moveTo(left,yy);ctx.lineTo(w-right,yy);ctx.stroke();ctx.fillText(number(hi-(hi-lo)*i/4,0),3,yy+3);}
  ctx.fillText(units[metric],4,9);
  ctx.beginPath();ctx.moveTo(x(points[0]),h-bottom);points.forEach(p=>ctx.lineTo(x(p),y(p)));ctx.lineTo(x(points.at(-1)),h-bottom);ctx.closePath();const gradient=ctx.createLinearGradient(0,top,0,h-bottom);gradient.addColorStop(0,'#a4bf773d');gradient.addColorStop(1,'#a4bf7703');ctx.fillStyle=gradient;ctx.fill();
  ctx.strokeStyle='#86a764';ctx.lineWidth=2;ctx.beginPath();points.forEach((p,i)=>i?ctx.lineTo(x(p),y(p)):ctx.moveTo(x(p),y(p)));ctx.stroke();
  if(points.length===1){ctx.beginPath();ctx.arc(x(points[0]),y(points[0]),4,0,Math.PI*2);ctx.fillStyle='#86a764';ctx.fill();}
  ctx.fillStyle='#a0aa97';const fmt=new Intl.DateTimeFormat('vi-VN',{timeZone:'Asia/Ho_Chi_Minh',hour:'2-digit',minute:'2-digit'});
  ctx.fillText(fmt.format(new Date(t0)),left,h-6);ctx.textAlign='right';ctx.fillText(fmt.format(new Date(t1)),w-right,h-6);
  canvas.setAttribute('aria-label',`${points.length} mẫu ${metric}, nhỏ nhất ${number(Math.min(...points.map(p=>p[metric])))}, lớn nhất ${number(Math.max(...points.map(p=>p[metric])))} ${units[metric]}`);
}
async function refresh(){
  if(!token||polling)return;polling=true;
  try{const [s,r,h]=await Promise.all([api(query('/api/status')).then(r=>r.json()),api(query('/api/readings',`&hours=${$('hours').value}&limit=5000`)).then(r=>r.json()),api(query('/api/irrigations')).then(r=>r.json())]);renderStatus(s);rows=r;draw();renderHistory(h);}
  catch(e){connected=false;badge('connection','Không kết nối được','bad');$('start-pump').disabled=true;message(e.message,true);}finally{polling=false;}
}
$('connect-form').addEventListener('submit',e=>{e.preventDefault();token=$('token').value.trim();device=$('device').value.trim();$('message').hidden=true;refresh();});
$('refresh').onclick=()=>refresh();$('hours').onchange=refresh;
document.querySelectorAll('[data-metric]').forEach(button=>button.onclick=()=>{metric=button.dataset.metric;document.querySelectorAll('[data-metric]').forEach(b=>b.classList.toggle('active',b===button));draw();});
async function command(button,path,body){button.disabled=true;try{await(await api(path,body)).json();message(path==='/api/mode'?'Đã đổi chế độ. Thiết bị sẽ nhận lệnh dừng trước khi áp dụng lần tưới tiếp theo.':'Đã ghi nhận lệnh. Kiểm tra xác nhận của thiết bị trong nhật ký.');await refresh();}catch(e){message(e.message,true);}finally{if(button!==$('start-pump'))button.disabled=false;}}
$('set-mode').onclick=()=>command($('set-mode'),'/api/mode',{device_id:device,mode:$('mode').value});
$('start-pump').onclick=()=>{const seconds=Number($('seconds').value);if(!Number.isInteger(seconds)||seconds<1||seconds>Number($('seconds').max)){message('Thời lượng không hợp lệ.',true);return;}command($('start-pump'),'/api/pump',{device_id:device,action:'start',duration_seconds:seconds});};
$('stop-pump').onclick=()=>command($('stop-pump'),'/api/pump',{device_id:device,action:'stop'});
$('export').onclick=async()=>{try{const res=await api(query('/api/export','&hours='+$('hours').value));const url=URL.createObjectURL(await res.blob()),a=document.createElement('a');a.href=url;a.download='smart_garden_sensor_history.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){message(e.message,true);}};
window.addEventListener('resize',draw);setInterval(refresh,5000);setInterval(()=>$('clock').textContent=date(new Date().toISOString()),1000);$('start-pump').disabled=true;draw();
