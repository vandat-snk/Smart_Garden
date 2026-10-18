"""Recreate baseline artifacts from train/validation only; never reads test scores."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.tree import DecisionTreeClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from ml.data import FEATURES, read_data, partitions, preprocess_fit, windows
from ml.train import select_threshold

df, target = read_data('data/raw/mendeley_original.csv')
train_df, val_df, _ = partitions(df)
prep = preprocess_fit(train_df)
train, val = [windows(p, target, prep, 8) for p in [train_df, val_df]]
for name, models in [
    ('logistic_regression', [LogisticRegression(C=c, class_weight='balanced', max_iter=2000, random_state=42) for c in [.01,.1,1]]),
    ('decision_tree', [DecisionTreeClassifier(max_depth=d, min_samples_leaf=4, class_weight='balanced', random_state=42) for d in [2,3,5]]),
    ('random_forest', [RandomForestClassifier(n_estimators=200, max_depth=d, min_samples_leaf=3, class_weight='balanced', random_state=42, n_jobs=2) for d in [3,6,None]])]:
    candidates=[]
    for model in models:
        model.fit(train[0].reshape(len(train[0]),-1),train[1])
        probability=model.predict_proba(val[0].reshape(len(val[0]),-1))[:,1]
        candidates.append((average_precision_score(val[1],probability),model,probability))
    _,model,p=max(candidates,key=lambda x:x[0])
    joblib.dump({'model':model,'threshold':select_threshold(val[1],p),'preprocessing':prep,'window':8,'features':FEATURES},Path('models/current')/(name+'.joblib'))
    print('Packaged',name)
