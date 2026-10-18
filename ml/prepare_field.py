"""Join independently annotated decisions to real sensor observations.

Labels must align to sampling-bin edges. Missing labels remain missing: never
interpret an unannotated decision as NO WATER. Gaps are excluded by the trainer.
"""
import argparse
from pathlib import Path
import pandas as pd
from ml.data import FEATURES


def prepare(readings, labels, interval_seconds=1800):
    readings = readings.copy(); labels = labels.copy()
    if 'device_id' in readings and readings.device_id.nunique() != 1:
        raise ValueError('Chỉ chuẩn bị một thiết bị/chậu mỗi lần; không nối chuỗi chậu khác nhau')
    if 'simulated' not in readings:
        raise ValueError('Thiếu cột simulated; cần file xuất từ API để xác minh nguồn')
    simulated = readings.simulated.astype(str).str.lower().isin(['true','1'])
    valid = ~simulated
    for field in ['sensor_ok','reservoir_ok']:
        if field in readings:
            valid &= readings[field].astype(str).str.lower().isin(['true','1'])
    readings = readings.loc[valid]
    if readings.empty:
        raise ValueError('Không có dữ liệu thực hợp lệ; dữ liệu mô phỏng đã bị loại')
    readings['timestamp'] = pd.to_datetime(readings.timestamp, utc=True, format='ISO8601')
    labels['timestamp'] = pd.to_datetime(labels.timestamp, utc=True, format='ISO8601')
    if labels.timestamp.duplicated().any():
        raise ValueError('Nhãn timestamp trùng')
    if not labels.water_needed.isin([0,1]).all():
        raise ValueError('water_needed phải là 0 hoặc 1 đã được xác minh')
    if not (labels.timestamp == labels.timestamp.dt.floor(f'{interval_seconds}s')).all():
        raise ValueError('Timestamp nhãn phải đúng biên bin lấy mẫu')
    if 'label_source' not in labels or labels.label_source.fillna('').str.strip().eq('').any():
        raise ValueError('Mỗi nhãn cần label_source mô tả căn cứ độc lập')
    labels = labels.set_index('timestamp')
    observations = readings.sort_values('timestamp').set_index('timestamp')[FEATURES]
    regular = observations.resample(f'{interval_seconds}s',closed='right',label='right').last()
    regular = regular.loc[:observations.index.max().floor(f'{interval_seconds}s')]
    result = regular.join(labels,how='inner').dropna(subset=FEATURES+['water_needed'])
    if result.empty:
        raise ValueError('Không có cảm biến khớp nhãn; kiểm tra UTC và khoảng lấy mẫu')
    # Rows without labels are absent, creating gaps which training will reject.
    return result.reset_index()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--readings',required=True);p.add_argument('--labels',required=True)
    p.add_argument('--out',required=True);p.add_argument('--interval-seconds',type=int,default=1800)
    a=p.parse_args()
    result=prepare(pd.read_csv(a.readings),pd.read_csv(a.labels),a.interval_seconds)
    dest=Path(a.out);dest.parent.mkdir(parents=True,exist_ok=True);result.to_csv(dest,index=False)
    print(f'{len(result)} annotated real observations saved. Train requires contiguous labelled windows.')


if __name__=='__main__':
    main()
