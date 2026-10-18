"""Chronological preprocessing. No target-row sensors or pump status enter X."""
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd

FEATURES = ['soil_moisture', 'temperature', 'humidity', 'light_intensity']
BOUNDS = [(0, 100), (-40, 80), (0, 100), (0, 65535)]
PUBLIC_SHA = '840cb77e743ca6846d6d097cf054cb67c33f2286c547ab1a05adfc78be222686'


def read_data(path):
    df = pd.read_csv(path).rename(columns={'Timestamp': 'timestamp', 'Soil_Moisture_pct': 'soil_moisture',
        'Temperature_C': 'temperature', 'Humidity_pct': 'humidity', 'Light_Intensity': 'light_intensity',
        'Pump_Status': 'pump_status'})
    target = 'water_needed' if 'water_needed' in df else 'pump_status'
    required = ['timestamp', target] + FEATURES
    if not set(required).issubset(df.columns):
        raise ValueError('Thiếu cột: ' + ', '.join(set(required) - set(df.columns)))
    df['timestamp'] = pd.to_datetime(df.timestamp, errors='raise')
    df = df.sort_values('timestamp').reset_index(drop=True)
    if df.timestamp.duplicated().any():
        raise ValueError('Timestamp trùng; hợp nhất/xác minh ở nguồn trước khi train')
    df[target] = pd.to_numeric(df[target], errors='raise')
    if not df[target].isin([0, 1]).all():
        raise ValueError('Nhãn phải là 0/1, không được thiếu')
    for key, (low, high) in zip(FEATURES, BOUNDS):
        df[key] = pd.to_numeric(df[key], errors='coerce')
        df.loc[~df[key].between(low, high), key] = np.nan
    if len(df) < 200:
        raise ValueError('Cần ít nhất 200 bản ghi; dữ liệu ít chưa đủ chia thời gian')
    if 'duration_seconds' in df:
        if target != 'water_needed':
            raise ValueError('Hồi quy cần nhãn water_needed đã xác minh, không ghép duration với pump_status')
        df['duration_seconds'] = pd.to_numeric(df.duration_seconds, errors='raise')
        if not df.duration_seconds.dropna().between(0, 30).all():
            raise ValueError('duration_seconds phải trong [0,30], từ thực nghiệm độc lập')
    return df, target


def preprocess_fit(df):
    values = df[FEATURES].to_numpy(dtype=np.float32)
    median = np.nanmedian(values, axis=0)
    if not np.isfinite(median).all():
        raise ValueError('Có cảm biến không có giá trị hợp lệ trong tập train')
    filled = np.where(np.isnan(values), median, values)
    return {'median': median.tolist(), 'mean': filled.mean(0).tolist(), 'std': np.maximum(filled.std(0), 1e-5).tolist()}


def transform(df, prep):
    x = df[FEATURES].to_numpy(dtype=np.float32)
    x = np.where(np.isfinite(x), x, prep['median'])
    return ((x - np.array(prep['mean'])) / np.array(prep['std'])).astype(np.float32)


def windows(df, target, prep, window, interval_seconds=1800):
    x, y, duration, target_rows = [], [], [], []
    normalized = transform(df, prep)
    # pandas may store datetime64[us]; use timedeltas for an unambiguous unit.
    gaps = df.timestamp.diff().dt.total_seconds().to_numpy()
    for i in range(window, len(df)):
        # Window ends at t-1. t's sensors may reflect irrigation and would leak.
        if not np.all(np.abs(gaps[i-window+1:i+1] - interval_seconds) <= 1):
            continue
        x.append(normalized[i-window:i].T)
        y.append(df.iloc[i][target])
        duration.append(df.iloc[i].get('duration_seconds', np.nan))
        target_rows.append(int(df.index[i]))
    if not x:
        raise ValueError('Không có cửa sổ liên tục đúng tần suất lấy mẫu')
    return np.array(x, dtype=np.float32), np.array(y, dtype=np.float32), np.array(duration, dtype=np.float32), target_rows


def partitions(df):
    n = len(df)
    return [df.iloc[:int(n*.6)].copy(), df.iloc[int(n*.6):int(n*.8)].copy(), df.iloc[int(n*.8):].copy()]
