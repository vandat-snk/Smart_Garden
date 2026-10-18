import json
import pickle
from pathlib import Path
import numpy as np
import pandas as pd
from ml.data import FEATURES, BOUNDS, transform


class Predictor:
    def __init__(self, model_dir='models/current'):
        self.available, self.experimental, self.version = False, True, None
        self.reason = 'Chưa có checkpoint CNN'
        self.models = []
        try:
            import torch
            from ml.network import GardenCNN
            self.torch = torch
            directory = Path(model_dir)
            self.meta = json.loads((directory / 'metadata.json').read_text(encoding='utf-8'))
            self.version = self.meta['version']
            self.experimental = self.meta.get('experimental', True)
            torch.set_num_threads(2)
            for seed in self.meta['seeds']:
                model = GardenCNN(**self.meta['config'])
                model.load_state_dict(torch.load(directory / f'cnn_seed_{seed}.pt', map_location='cpu', weights_only=True))
                model.eval(); self.models.append(model)
            self.available = bool(self.models)
            self.reason = 'CNN dự báo trạng thái tưới kỳ kế tiếp; cần kiểm chứng trên cây thật'
        except (ImportError, OSError, ValueError, KeyError, RuntimeError, EOFError, pickle.UnpicklingError) as e:
            self.reason = f'CNN chưa sẵn sàng ({type(e).__name__})'

    def predict(self, history):
        result = {'available': False, 'prediction': 'NO WATER', 'water_probability': None,
                  'duration_seconds': None, 'duration_source': 'unavailable', 'model_version': self.version,
                  'reason': self.reason}
        if not self.available:
            return result
        try:
            frame = pd.DataFrame(history)
            if frame.empty:
                return dict(result, reason='Chưa có lịch sử cảm biến')
            frame['timestamp'] = pd.to_datetime(frame.timestamp, utc=True, format='ISO8601')
            frame = frame.sort_values('timestamp').set_index('timestamp')
            for key, (low, high) in zip(FEATURES, BOUNDS):
                frame[key] = pd.to_numeric(frame[key], errors='coerce')
                if not frame[key].between(low, high).all():
                    return dict(result, reason='Có dữ liệu cảm biến ngoài khoảng hợp lệ')
            interval = self.meta['interval_seconds']
            # Right-labelled windows only contain measurements at or before the bin edge.
            # Discard unfinished bin to prevent forecasting on partial/different cadence data.
            end = frame.index.max().floor(f'{interval}s')
            regular = frame[FEATURES].resample(f'{interval}s', closed='right', label='right').last().loc[:end]
            regular = regular.reindex(pd.date_range(end=end, periods=self.meta['window'], freq=f'{interval}s', tz='UTC'))
            if regular.isna().any().any():
                return dict(result, reason=f'Cần {self.meta["window"]} mốc liên tục, cách nhau {interval//60} phút')
            # Do not extrapolate strongly out of the training envelope without a fallback.
            x = transform(regular, self.meta['preprocessing'])
            if (np.abs(x) > 8).any():
                return dict(result, reason='Dữ liệu khác xa miền huấn luyện; dùng ngưỡng')
            tensor = self.torch.tensor(x.T[None], dtype=self.torch.float32)
            with self.torch.no_grad():
                outputs = [m(tensor) for m in self.models]
                probability = float(np.mean([a.sigmoid().item() for a,b in outputs]))
                seconds = float(np.mean([b.item() for a,b in outputs])) if self.meta['duration_trained'] else None
            water = probability >= self.meta['threshold']
            return dict(result, available=True, prediction='WATER' if water else 'NO WATER',
                        water_probability=probability, duration_seconds=seconds if water else (0 if seconds is not None else None),
                        duration_source='cnn_regression' if seconds is not None else 'unavailable',
                        forecast_horizon_seconds=interval, reason=self.reason)
        except (ValueError, KeyError, TypeError, RuntimeError):
            return dict(result, reason='Không thể chuẩn hóa chuỗi cảm biến; dùng ngưỡng')
