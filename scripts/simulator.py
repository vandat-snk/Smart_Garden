"""Explicit software-only device simulator. Never controls GPIO or physical pumps."""
import argparse
from datetime import datetime, timezone
import math
import time
import uuid
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from backend.config import Settings


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--url', default='http://127.0.0.1:8000')
    p.add_argument('--steps', type=int, default=120)
    p.add_argument('--interval', type=float, default=5)
    args = p.parse_args(); cfg = Settings()
    print('SIMULATED readings only. Disconnect physical ESP32 with this device ID before running.')
    pump_end, current, moisture = 0., None, 27.
    with httpx.Client(base_url=args.url, headers={'X-Device-Key': cfg.device_key}, timeout=5) as client:
        def ack(cid, status, elapsed=None):
            body = {'device_id': cfg.device_id, 'status': status, 'reason': 'Software simulator', 'actual_duration_seconds': elapsed}
            r = client.post(f'/api/device/commands/{cid}/ack', json=body); r.raise_for_status()
        for i in range(args.steps):
            now = time.monotonic()
            if current and now >= pump_end:
                ack(current, 'completed', min(duration, 30)); current = None
            if current: moisture = min(70, moisture + 3)
            else: moisture = max(20, moisture - .06)
            payload = {'device_id': cfg.device_id, 'sample_id': 'sim-'+uuid.uuid4().hex, 'simulated': True,
                'timestamp': datetime.now(timezone.utc).isoformat(), 'soil_moisture': round(moisture, 2),
                'temperature': round(29 + math.sin(i/12)*2, 2), 'humidity': round(68+math.cos(i/10)*5, 2),
                'light_intensity': round(420+math.sin(i/8)*60, 1), 'pump_on': bool(current), 'sensor_ok': True, 'reservoir_ok': True}
            r = client.post('/api/telemetry', json=payload); r.raise_for_status()
            command = client.get('/api/device/commands', params={'device_id': cfg.device_id}); command.raise_for_status()
            c = command.json()['command']
            if c:
                if c['action'] == 'stop':
                    if current: ack(current, 'stopped', min(duration, 30)); current = None
                    ack(c['id'], 'stopped', 0)
                elif not current:
                    duration = c['duration_seconds']; ack(c['id'], 'started')
                    current, pump_end = c['id'], time.monotonic()+duration
            print(f'{i+1}/{args.steps} soil={moisture:.1f} simulated_pump={bool(current)}', flush=True)
            time.sleep(args.interval)
        if current:
            ack(current, 'stopped', 0)


if __name__ == '__main__':
    main()
