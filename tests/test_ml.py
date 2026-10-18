import numpy as np
import pandas as pd
from ml.data import FEATURES, partitions, preprocess_fit, windows
from ml.inference import Predictor


def frame(n=400):
    return pd.DataFrame({'timestamp':pd.date_range('2026-01-01',periods=n,freq='30min'),
        'soil_moisture':np.arange(n)%100,'temperature':29.,'humidity':60.,'light_intensity':100.,'pump_status':np.arange(n)%2})


def test_windows_exclude_target_and_never_cross_splits():
    df=frame();parts=partitions(df);prep=preprocess_fit(parts[0]);splits=[windows(p,'pump_status',prep,8) for p in parts]
    for part, split in zip(parts,splits):
        assert split[3][0]==part.index[0]+8
        assert split[3][-1]==part.index[-1]
    x,y,d,ids=splits[0]
    restored=x[0,0,-1]*prep['std'][0]+prep['mean'][0]
    assert abs(restored-df.loc[ids[0]-1,'soil_moisture'])<1e-4
    assert ids[0] not in range(ids[0]-8,ids[0])


def test_preprocessing_is_train_only():
    df=frame();train,val,test=partitions(df);prep=preprocess_fit(train)
    changed=pd.concat([train,val.assign(temperature=1e9),test.assign(temperature=1e9)])
    assert prep==preprocess_fit(partitions(changed)[0])


def test_gap_windows_are_excluded():
    df=frame();prep=preprocess_fit(df)
    clean=windows(df,'pump_status',prep,8)
    broken=windows(df.drop(index=50),'pump_status',prep,8)
    assert len(broken[0])<len(clean[0])-1
    assert not any(51<=i<=58 for i in broken[3])


def test_missing_checkpoint_fails_closed(tmp_path):
    p=Predictor(tmp_path)
    result=p.predict([])
    assert not result['available'] and result['duration_seconds'] is None


def test_saved_cnn_serving_matches_recorded_test_prediction():
    from pathlib import Path
    from ml.data import read_data
    root=Path(__file__).resolve().parents[1]
    p=Predictor(root/'models/current')
    assert p.available
    df,_=read_data(root/'data/raw/mendeley_original.csv')
    expected=pd.read_csv(root/'models/current/test_predictions.csv').iloc[0]
    target=int(df.index[df.timestamp==pd.Timestamp(expected.timestamp)][0])
    history=df.iloc[target-8:target].copy()
    history['timestamp']=history.timestamp.dt.tz_localize('UTC').astype(str)
    result=p.predict(history.to_dict('records'))
    assert result['available']
    assert abs(result['water_probability']-expected.probability)<1e-6
    assert result['duration_seconds'] is None
    assert not p.predict(history.iloc[:-1].to_dict('records'))['available']


def test_field_preparation_rejects_simulated_rows():
    import pytest
    from ml.prepare_field import prepare
    data=frame(20).assign(simulated=True,sensor_ok=True,reservoir_ok=True,device_id='garden-01')
    labels=pd.DataFrame({'timestamp':data.timestamp,'water_needed':0,'label_source':'independent observation'})
    with pytest.raises(ValueError,match='mô phỏng'):
        prepare(data,labels)
