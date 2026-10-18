"""HTTP telemetry, durable command/ack protocol and Vietnamese dashboard."""
import csv
import io
import secrets
from contextlib import asynccontextmanager
from datetime import timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text

from backend.config import ROOT, Settings
from backend.db import Base, Command, Device, Reading, connect, utcnow
from backend.schemas import Ack, Mode, Pump, Telemetry

ACTIVE = ('queued', 'delivered', 'started')


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def record(obj):
    return {c.name: (aware(getattr(obj, c.name)).isoformat() if c.name.endswith('_at') or c.name == 'timestamp' else getattr(obj, c.name))
            if getattr(obj, c.name) is not None else None for c in obj.__table__.columns}


def create_app(settings=None):
    cfg = settings or Settings()
    engine, sessions = connect(cfg.database_url)
    from ml.inference import Predictor
    predictor = Predictor(ROOT / cfg.model_dir)

    @asynccontextmanager
    async def lifespan(app):
        Base.metadata.create_all(engine)
        with sessions.begin() as db:
            if not db.get(Device, cfg.device_id):
                db.add(Device(id=cfg.device_id))
        yield
        engine.dispose()

    app = FastAPI(title='Smart Garden AIoT', version='1.0.0', lifespan=lifespan)
    app.state.sessions, app.state.settings = sessions, cfg

    def admin(authorization: str = Header(default='')):
        if not secrets.compare_digest(authorization, 'Bearer ' + cfg.admin_token):
            raise HTTPException(401, 'Cần khóa quản trị hợp lệ')

    def device_auth(x_device_key: str = Header(default='')):
        if not secrets.compare_digest(x_device_key, cfg.device_key):
            raise HTTPException(401, 'Khóa thiết bị không hợp lệ')

    def get_device(db, device_id, lock=False):
        if device_id != cfg.device_id:
            raise HTTPException(404, 'Không có thiết bị này')
        stmt = select(Device).where(Device.id == device_id)
        if lock:
            stmt = stmt.with_for_update()
        dev = db.scalar(stmt)
        if not dev:
            raise HTTPException(503, 'Database chưa khởi tạo')
        return dev

    def latest(db, dev):
        return db.scalar(select(Reading).where(Reading.device_id == dev.id).order_by(Reading.timestamp.desc()).limit(1))

    def active(db, dev):
        return list(db.scalars(select(Command).where(Command.device_id == dev.id, Command.status.in_(ACTIVE))))

    def expire(db, dev):
        now = utcnow()
        for c in active(db, dev):
            deadline = aware(c.started_at) + timedelta(seconds=c.duration_seconds + 10) if c.started_at else aware(c.expires_at)
            if now > deadline:
                c.status, c.completed_at = 'timed_out', now

    def queue(db, dev, action, seconds, source, reason):
        now = utcnow()
        if action == 'stop':
            for c in active(db, dev):
                if c.action == 'start':
                    c.status, c.completed_at = 'cancelled', now
            existing = next((c for c in active(db, dev) if c.action == 'stop'), None)
            if existing:
                return existing
        c = Command(id=str(uuid4()), device_id=dev.id, action=action,
                    duration_seconds=seconds if action == 'start' else 0,
                    source=source, reason=reason[:500], expires_at=now + timedelta(seconds=15))
        db.add(c)
        if action == 'start':
            dev.last_water = now
        db.flush()
        return c

    def safe_to_start(db, dev, reading):
        if reading is None or (utcnow() - aware(reading.received_at)).total_seconds() > cfg.stale_seconds or (utcnow() - aware(reading.timestamp)).total_seconds() > cfg.stale_seconds:
            return 'Chưa có dữ liệu cảm biến mới'
        if not reading.sensor_ok or not reading.reservoir_ok:
            return 'Cảm biến lỗi hoặc bình nước không sẵn sàng'
        if reading.soil_moisture is None or reading.soil_moisture >= cfg.soil_stop_threshold:
            return 'Đất đã đạt ngưỡng dừng tưới'
        if reading.pump_on or active(db, dev):
            return 'Bơm đang bật hoặc còn lệnh chưa kết thúc'
        if dev.last_water and (utcnow() - aware(dev.last_water)).total_seconds() < cfg.cooldown_seconds:
            return 'Đang chờ nước thấm theo khoảng nghỉ an toàn'
        return None

    def decision(db, dev, reading):
        if not reading.sensor_ok or not reading.reservoir_ok:
            dev.prediction = {'prediction': 'NO WATER', 'source': 'safety', 'reason': 'Lỗi cảm biến / bình nước', 'duration_seconds': 0}
            queue(db, dev, 'stop', 0, 'safety', 'Cảm biến/bình nước không an toàn')
            return
        result = {'prediction': 'WATER' if reading.soil_moisture < cfg.soil_start_threshold else 'NO WATER',
                  'water_probability': None, 'duration_seconds': cfg.default_pump_seconds,
                  'duration_source': 'configured_pulse', 'source': 'rule', 'model_version': None,
                  'reason': f'Ngưỡng đất khô {cfg.soil_start_threshold:g}%'}
        if dev.mode == 'ai':
            history = list(db.scalars(select(Reading).where(Reading.device_id == dev.id, Reading.sensor_ok.is_(True), Reading.simulated == reading.simulated,
                Reading.timestamp >= utcnow() - timedelta(hours=48)).order_by(Reading.timestamp)))
            pred = predictor.predict([record(r) for r in history])
            if pred['available'] and (cfg.allow_experimental_ai or not predictor.experimental):
                result.update(pred, source='cnn')
                if pred.get('duration_seconds') is None:
                    result.update(duration_seconds=cfg.default_pump_seconds, duration_source='configured_pulse')
            else:
                result['source'] = 'rule_fallback'
                result['reason'] = pred.get('reason', '') if not pred['available'] else 'CNN thử nghiệm chưa được cho phép điều khiển; dùng ngưỡng'
        if reading.soil_moisture >= cfg.soil_stop_threshold:
            result.update(prediction='NO WATER', duration_seconds=0, reason='Đất đủ ẩm, giới hạn dừng ưu tiên')
            if reading.pump_on or any(c.action == 'start' for c in active(db, dev)):
                queue(db, dev, 'stop', 0, 'safety', 'Đất đạt ngưỡng dừng')
        if dev.mode == 'manual':
            result.update(source='manual', reason='Chế độ thủ công; gợi ý ngưỡng không tự bật bơm')
        if result['prediction'] != 'WATER':
            result['duration_seconds'] = 0
        result['duration_seconds'] = max(0, min(cfg.max_pump_seconds, round(result.get('duration_seconds') or 0)))
        dev.prediction = result
        if dev.mode != 'manual' and result['prediction'] == 'WATER':
            blocked = safe_to_start(db, dev, reading)
            if not blocked and result['duration_seconds'] > 0:
                queue(db, dev, 'start', result['duration_seconds'], result['source'], result['reason'])
            elif blocked:
                dev.prediction = dict(result, blocked_reason=blocked)

    def status(db, dev):
        r = latest(db, dev)
        online = bool(dev.last_seen and (utcnow() - aware(dev.last_seen)).total_seconds() < cfg.stale_seconds)
        return {'device_id': dev.id, 'mode': dev.mode, 'online': online,
                'pump_state': r.pump_on if r and online else None, 'latest': record(r) if r else None,
                'prediction': dev.prediction,
                'safety': {'max_pump_seconds': cfg.max_pump_seconds, 'cooldown_seconds': cfg.cooldown_seconds,
                           'soil_stop_threshold': cfg.soil_stop_threshold, 'soil_start_threshold': cfg.soil_start_threshold},
                'model': {'available': predictor.available, 'version': predictor.version,
                          'reason': predictor.reason, 'experimental': predictor.experimental,
                          'control_enabled': cfg.allow_experimental_ai or not predictor.experimental}}

    @app.get('/health')
    def health():
        with sessions() as db:
            db.execute(text('SELECT 1'))
        return {'status': 'ok', 'database': engine.dialect.name}

    @app.post('/api/telemetry', dependencies=[Depends(device_auth)])
    def telemetry(payload: Telemetry):
        now = utcnow()
        age = (now - payload.timestamp).total_seconds()
        if age > 120 or age < -10:
            raise HTTPException(422, 'Timestamp lệch quá giới hạn; đồng bộ NTP')
        with sessions.begin() as db:
            dev = get_device(db, payload.device_id, True)
            prior = db.scalar(select(Reading).where(Reading.device_id == dev.id, Reading.sample_id == payload.sample_id))
            if prior:
                return {'accepted': True, 'duplicate': True, 'id': prior.id}
            last = latest(db, dev)
            if last and payload.timestamp <= aware(last.timestamp):
                raise HTTPException(409, 'Dữ liệu đến sai thứ tự thời gian')
            r = Reading(**payload.model_dump(), received_at=now)
            db.add(r)
            dev.last_seen = now
            db.flush()
            expire(db, dev)
            decision(db, dev, r)
            return {'accepted': True, 'duplicate': False, 'id': r.id}

    @app.get('/api/status', dependencies=[Depends(admin)])
    def get_status(device_id: str = cfg.device_id):
        with sessions.begin() as db:
            dev = get_device(db, device_id, True)
            expire(db, dev)
            return status(db, dev)

    @app.get('/api/readings', dependencies=[Depends(admin)])
    def readings(device_id: str = cfg.device_id, hours: int = Query(24, ge=1, le=720), limit: int = Query(500, ge=1, le=5000)):
        with sessions() as db:
            get_device(db, device_id)
            rows = list(db.scalars(select(Reading).where(Reading.device_id == device_id,
                Reading.timestamp >= utcnow() - timedelta(hours=hours)).order_by(Reading.timestamp.desc()).limit(limit)))
            return [record(r) for r in reversed(rows)]

    @app.get('/api/irrigations', dependencies=[Depends(admin)])
    def irrigations(device_id: str = cfg.device_id, limit: int = Query(50, ge=1, le=500)):
        with sessions() as db:
            get_device(db, device_id)
            return [record(c) for c in db.scalars(select(Command).where(Command.device_id == device_id)
                .order_by(Command.created_at.desc()).limit(limit))]

    @app.post('/api/mode', dependencies=[Depends(admin)])
    def set_mode(payload: Mode):
        with sessions.begin() as db:
            dev = get_device(db, payload.device_id, True)
            expire(db, dev)
            if dev.mode != payload.mode:
                queue(db, dev, 'stop', 0, 'operator', 'Dừng bơm khi đổi chế độ')
            dev.mode = payload.mode
            return status(db, dev)

    @app.post('/api/pump', dependencies=[Depends(admin)])
    def pump(payload: Pump):
        with sessions.begin() as db:
            dev = get_device(db, payload.device_id, True)
            expire(db, dev)
            if payload.action == 'start':
                if dev.mode != 'manual':
                    raise HTTPException(409, 'Chuyển sang thủ công trước khi bật bơm')
                if payload.duration_seconds > cfg.max_pump_seconds:
                    raise HTTPException(422, 'Thời gian vượt giới hạn cấu hình')
                why = safe_to_start(db, dev, latest(db, dev))
                if why:
                    raise HTTPException(409, why)
            return record(queue(db, dev, payload.action, payload.duration_seconds, 'manual', 'Lệnh từ dashboard'))

    @app.get('/api/device/commands', dependencies=[Depends(device_auth)])
    def next_command(device_id: str = cfg.device_id):
        with sessions.begin() as db:
            dev = get_device(db, device_id, True)
            expire(db, dev)
            commands = sorted(active(db, dev), key=lambda c: (c.action != 'stop', c.created_at))
            for c in commands:
                if c.status == 'started':
                    continue
                if c.action == 'start':
                    r = latest(db, dev)
                    if not r or (utcnow() - aware(r.received_at)).total_seconds() > cfg.stale_seconds or (utcnow() - aware(r.timestamp)).total_seconds() > cfg.stale_seconds or not r.sensor_ok or not r.reservoir_ok or r.soil_moisture >= cfg.soil_stop_threshold:
                        c.status, c.completed_at = 'rejected', utcnow()
                        continue
                c.status = 'delivered'
                return {'command': record(c)}
            return {'command': None}

    @app.post('/api/device/commands/{command_id}/ack', dependencies=[Depends(device_auth)])
    def ack(command_id: str, payload: Ack):
        with sessions.begin() as db:
            dev = get_device(db, payload.device_id, True)
            c = db.get(Command, command_id)
            if not c or c.device_id != dev.id:
                raise HTTPException(404, 'Không tìm thấy lệnh')
            if c.status == payload.status:
                return {'accepted': True, 'duplicate': True}
            if c.status not in ACTIVE:
                # Repeated terminal acknowledgements never restart or revive commands.
                return {'accepted': False, 'status': c.status}
            now = utcnow()
            if payload.status == 'started':
                if c.action != 'start' or c.status != 'delivered' or now > aware(c.expires_at):
                    raise HTTPException(409, 'Lệnh chưa giao hoặc đã hết hạn')
                c.started_at = now
            else:
                if payload.status == 'completed' and (c.action != 'start' or c.status != 'started'):
                    raise HTTPException(409, 'Chưa xác nhận bắt đầu bơm')
                c.completed_at = now
                c.actual_duration_seconds = payload.actual_duration_seconds
            c.status = payload.status
            if payload.reason:
                c.reason = (c.reason + '; ' + payload.reason)[:500]
            return {'accepted': True, 'duplicate': False}

    @app.get('/api/export', dependencies=[Depends(admin)])
    def export(device_id: str = cfg.device_id, hours: int = Query(24, ge=1, le=720)):
        with sessions() as db:
            get_device(db, device_id)
            rows = db.scalars(select(Reading).where(Reading.device_id == device_id,
                Reading.timestamp >= utcnow() - timedelta(hours=hours)).order_by(Reading.timestamp))
            stream = io.StringIO()
            fields = [c.name for c in Reading.__table__.columns]
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for r in rows:
                writer.writerow(record(r))
            return Response(stream.getvalue(), media_type='text/csv', headers={'Content-Disposition': 'attachment; filename="sensor_history.csv"'})

    web = ROOT / 'web'
    web.mkdir(exist_ok=True)
    app.mount('/static', StaticFiles(directory=web), name='static')

    @app.get('/', include_in_schema=False)
    def index():
        return FileResponse(web / 'index.html')

    return app
