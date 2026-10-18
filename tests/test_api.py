from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import pytest
from backend.db import Command, Device, Reading


def reading(**changes):
    data = {'device_id':'garden-01', 'sample_id':uuid4().hex, 'timestamp':datetime.now(timezone.utc).isoformat(),
        'soil_moisture':25, 'temperature':29, 'humidity':65, 'light_intensity':400, 'pump_on':False,
        'sensor_ok':True, 'reservoir_ok':True}
    data.update(changes)
    return data


def start(client, admin, seconds=5):
    return client.post('/api/pump',headers=admin,json={'device_id':'garden-01','action':'start','duration_seconds':seconds})


def ack(client, device, cid, status):
    return client.post(f'/api/device/commands/{cid}/ack',headers=device,json={'device_id':'garden-01','status':status,'actual_duration_seconds':5 if status=='completed' else None})


def test_auth_separates_admin_and_device(client,admin,device):
    assert client.get('/api/status').status_code==401
    assert client.get('/api/status',headers=device).status_code==401
    assert client.post('/api/telemetry',headers=admin,json=reading()).status_code==401
    assert client.get('/api/status',headers=admin).status_code==200
    assert client.get('/health').json()['status']=='ok'


def test_round_trip_command_ack_history(client,admin,device):
    assert client.post('/api/telemetry',headers=device,json=reading()).status_code==200
    c=start(client,admin).json()
    assert c['status']=='queued'
    delivered=client.get('/api/device/commands',headers=device).json()['command']
    assert delivered['id']==c['id']
    assert ack(client,device,c['id'],'started').json()['accepted']
    assert client.get('/api/device/commands',headers=device).json()['command'] is None
    assert ack(client,device,c['id'],'completed').json()['accepted']
    assert ack(client,device,c['id'],'completed').json()['duplicate']
    history=client.get('/api/irrigations',headers=admin).json()
    assert history[0]['actual_duration_seconds']==5
    assert history[0]['status']=='completed'
    # ACK alone does not invent a physical pump reading.
    assert client.get('/api/status',headers=admin).json()['pump_state'] is False


def test_duplicate_telemetry_no_duplicate_action(client,admin,device,app):
    with app.state.sessions.begin() as db: db.get(Device,'garden-01').mode='rule'
    payload=reading()
    a=client.post('/api/telemetry',headers=device,json=payload).json()
    b=client.post('/api/telemetry',headers=device,json=payload).json()
    assert a['id']==b['id'] and b['duplicate']
    assert len(client.get('/api/irrigations',headers=admin).json())==1


@pytest.mark.parametrize('changes',[
    {'soil_moisture':101}, {'humidity':-1}, {'temperature':90}, {'light_intensity':-10},
    {'timestamp':'2026-01-01T00:00:00'}, {'soil_moisture':None},
    {'timestamp':(datetime.now(timezone.utc)-timedelta(days=1)).isoformat()},
    {'timestamp':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()},
])
def test_invalid_readings_rejected(client,device,changes):
    assert client.post('/api/telemetry',headers=device,json=reading(**changes)).status_code==422


def test_start_requires_fresh_safe_reading(client,admin,device):
    assert start(client,admin).status_code==409


def test_delayed_observation_cannot_start_pump(client,admin,device):
    old=(datetime.now(timezone.utc)-timedelta(seconds=90)).isoformat()
    assert client.post('/api/telemetry',headers=device,json=reading(timestamp=old)).status_code==200
    assert start(client,admin).status_code==409
    client.post('/api/telemetry',headers=device,json=reading(soil_moisture=80))
    assert start(client,admin).status_code==409


def test_cooldown_and_duration_limit(client,admin,device):
    client.post('/api/telemetry',headers=device,json=reading())
    assert start(client,admin,31).status_code==422
    c=start(client,admin).json()
    client.get('/api/device/commands',headers=device)
    ack(client,device,c['id'],'started');ack(client,device,c['id'],'completed')
    response=start(client,admin)
    assert response.status_code==409 and 'khoảng nghỉ' in response.json()['detail']


def test_safety_stop_cancels_pending_start(client,admin,device):
    client.post('/api/telemetry',headers=device,json=reading())
    original=start(client,admin).json()
    client.post('/api/telemetry',headers=device,json=reading(sensor_ok=False,soil_moisture=None))
    c=client.get('/api/device/commands',headers=device).json()['command']
    assert c['action']=='stop'
    assert ack(client,device,original['id'],'started').json()['accepted'] is False
    assert ack(client,device,c['id'],'stopped').json()['accepted']


def test_mode_change_queues_stop(client,admin,device):
    response=client.post('/api/mode',headers=admin,json={'device_id':'garden-01','mode':'ai'})
    assert response.json()['mode']=='ai'
    c=client.get('/api/device/commands',headers=device).json()['command']
    assert c['action']=='stop'
    assert start(client,admin).status_code==409


def test_ai_missing_uses_rule_fallback(client,admin,device,app):
    with app.state.sessions.begin() as db: db.get(Device,'garden-01').mode='ai'
    client.post('/api/telemetry',headers=device,json=reading())
    s=client.get('/api/status',headers=admin).json()
    assert s['prediction']['source']=='rule_fallback'
    assert s['prediction']['duration_source']=='configured_pulse'
    assert not s['model']['available']


def test_no_water_never_suggests_a_positive_duration(client,admin,device,app):
    with app.state.sessions.begin() as db: db.get(Device,'garden-01').mode='rule'
    client.post('/api/telemetry',headers=device,json=reading(soil_moisture=45))
    prediction=client.get('/api/status',headers=admin).json()['prediction']
    assert prediction['prediction']=='NO WATER' and prediction['duration_seconds']==0
    assert client.get('/api/device/commands',headers=device).json()['command'] is None


def test_expired_command_is_never_delivered(client,admin,device,app):
    client.post('/api/telemetry',headers=device,json=reading())
    c=start(client,admin).json()
    with app.state.sessions.begin() as db: db.get(Command,c['id']).expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)
    assert client.get('/api/device/commands',headers=device).json()['command'] is None
    assert ack(client,device,c['id'],'started').json()['accepted'] is False


def test_ack_cannot_skip_delivery(client,admin,device):
    client.post('/api/telemetry',headers=device,json=reading())
    c=start(client,admin).json()
    assert ack(client,device,c['id'],'started').status_code==409
    assert ack(client,device,c['id'],'completed').status_code==409


def test_stale_telemetry_reports_unknown_pump(client,admin,device,app):
    client.post('/api/telemetry',headers=device,json=reading())
    with app.state.sessions.begin() as db:
        db.get(Device,'garden-01').last_seen=datetime.now(timezone.utc)-timedelta(minutes=5)
        db.get(Reading,1).received_at=datetime.now(timezone.utc)-timedelta(minutes=5)
    s=client.get('/api/status',headers=admin).json()
    assert not s['online'] and s['pump_state'] is None
    assert start(client,admin).status_code==409


def test_export_and_dashboard(client,admin,device):
    client.post('/api/telemetry',headers=device,json=reading(simulated=True))
    assert 'text/csv' in client.get('/api/export',headers=admin).headers['content-type']
    assert 'soil_moisture' in client.get('/api/export',headers=admin).text
    assert client.get('/').status_code==200
    assert client.get('/static/app.js').status_code==200
    assert client.get('/api/readings?limit=0',headers=admin).status_code==422
    assert client.get('/api/status?device_id=other',headers=admin).status_code==404


def test_postgres_concurrent_starts_create_one_command(client,admin,device,app):
    if not app.state.settings.database_url.startswith('postgresql'):
        pytest.skip('Row-lock concurrency is tested on PostgreSQL')
    client.post('/api/telemetry',headers=device,json=reading())
    with ThreadPoolExecutor(max_workers=4) as pool:
        codes=list(pool.map(lambda _:start(client,admin).status_code,range(4)))
    assert sorted(codes)==[200,409,409,409]


def test_real_checkpoint_can_queue_ai_command_without_mixing_simulator_history(app,admin,device):
    from pathlib import Path
    import pandas as pd
    from fastapi.testclient import TestClient
    from backend.main import create_app
    from ml.data import read_data
    root=Path(__file__).resolve().parents[1]
    data,_=read_data(root/'data/raw/mendeley_original.csv')
    predictions=pd.read_csv(root/'models/current/test_predictions.csv')
    expected=predictions.loc[predictions.prediction==1].iloc[0]
    target=int(data.index[data.timestamp==pd.Timestamp(expected.timestamp)][0])
    window=data.iloc[target-8:target]
    now=datetime.now(timezone.utc)
    end=pd.Timestamp(now).floor('30min').to_pydatetime()
    configured=app.state.settings.model_copy(update={'model_dir':'models/current','allow_experimental_ai':True})
    integrated=create_app(configured)
    with TestClient(integrated) as api:
        with integrated.state.sessions.begin() as db:
            db.get(Device,'garden-01').mode='ai'
            for index,(_,row) in enumerate(window.iterrows()):
                timestamp=end-timedelta(minutes=30*(7-index))
                values=reading(timestamp=timestamp,simulated=False,
                    soil_moisture=float(row.soil_moisture),temperature=float(row.temperature),
                    humidity=float(row.humidity),light_intensity=float(row.light_intensity))
                db.add(Reading(**values,received_at=timestamp))
                # A simulator sample in the same bin must not replace real history.
                fake=reading(timestamp=timestamp,simulated=True,soil_moisture=5,light_intensity=150000)
                db.add(Reading(**fake,received_at=timestamp))
        response=api.post('/api/telemetry',headers=device,json=reading(soil_moisture=45))
        assert response.status_code==200
        state=api.get('/api/status',headers=admin).json()
        prediction=state['prediction']
        assert prediction['source']=='cnn' and prediction['prediction']=='WATER', prediction['reason']
        assert abs(prediction['water_probability']-expected.probability)<1e-6
        command=api.get('/api/device/commands',headers=device).json()['command']
        assert command['action']=='start' and command['source']=='cnn'
        assert command['duration_seconds']==configured.default_pump_seconds
