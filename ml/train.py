"""GPU CNN search; validation selects configuration, seeds ensemble and threshold.

Run: python -m ml.train --device cuda --epochs 160
Test data is evaluated only after all model selection is complete.
"""
import argparse
import copy
import hashlib
import json
import os
import random
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import torch
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (accuracy_score, average_precision_score, confusion_matrix, f1_score,
                             precision_score, recall_score, roc_auc_score, mean_absolute_error, mean_squared_error, r2_score)
from sklearn.tree import DecisionTreeClassifier
from torch import nn

from ml.data import FEATURES, partitions, preprocess_fit, read_data, windows
from ml.network import GardenCNN


def metrics(y, probability, threshold):
    pred = probability >= threshold
    return {'accuracy': float(accuracy_score(y, pred)), 'precision': float(precision_score(y, pred, zero_division=0)),
            'recall': float(recall_score(y, pred, zero_division=0)), 'f1': float(f1_score(y, pred, zero_division=0)),
            'pr_auc': float(average_precision_score(y, probability)),
            'roc_auc': float(roc_auc_score(y, probability)) if len(np.unique(y)) == 2 else None,
            'confusion_matrix': confusion_matrix(y, pred, labels=[0, 1]).tolist(),
            'threshold': float(threshold), 'samples': len(y), 'positives': int(sum(y))}


def select_threshold(y, p):
    # F1, then precision, then threshold: an explicit validation-only criterion.
    ts = np.unique(np.r_[np.linspace(.01, .99, 99), p])
    return float(max(ts, key=lambda t: (f1_score(y, p >= t, zero_division=0), precision_score(y, p >= t, zero_division=0), t)))


def seed_all(seed):
    random.seed(seed); np.random.seed(seed); torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False


def fit_one(train, val, config, seed, device, epochs, duration_enabled):
    seed_all(seed)
    model = GardenCNN(**config).to(device)
    x, y, d = [torch.tensor(a, device=device) for a in train[:3]]
    vx, vy, vd = [torch.tensor(a, device=device) for a in val[:3]]
    pos_weight = torch.tensor([(len(y) - y.sum()) / max(float(y.sum()), 1)], device=device)
    criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    optimizer = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.01)
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer, patience=8, factor=.5, mode='max')
    best_score, best, no_gain, history = -1., None, 0, []
    for epoch in range(epochs):
        model.train()
        order = torch.randperm(len(x), device=device)
        losses = []
        for idx in order.split(64):
            optimizer.zero_grad(set_to_none=True)
            logits, seconds = model(x[idx])
            loss = criterion(logits, y[idx])
            mask = torch.isfinite(d[idx]) & (y[idx] == 1)
            if duration_enabled and mask.any():
                loss = loss + nn.functional.smooth_l1_loss(seconds[mask] / 30, d[idx][mask] / 30)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.)
            optimizer.step(); losses.append(float(loss.detach()))
        model.eval()
        with torch.no_grad():
            logits, seconds = model(vx)
            probability = logits.sigmoid().cpu().numpy()
            ap = float(average_precision_score(val[1], probability))
            mask = torch.isfinite(vd) & (vy == 1)
            mae = float(torch.abs(seconds[mask] - vd[mask]).mean()) if duration_enabled and mask.any() else 0.
        score = ap - mae / 300 if duration_enabled else ap
        history.append({'epoch': epoch + 1, 'train_loss': float(np.mean(losses)), 'validation_ap': ap, 'validation_duration_mae': mae if duration_enabled else None})
        scheduler.step(score)
        if score > best_score + 1e-6:
            best_score, best, no_gain = score, copy.deepcopy(model.state_dict()), 0
        else:
            no_gain += 1
        if no_gain >= 25:
            break
    model.load_state_dict(best)
    model.eval()
    with torch.no_grad():
        probability = model(vx)[0].sigmoid().cpu().numpy()
    return {k: v.cpu() for k, v in best.items()}, probability, best_score, history


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', default='data/raw/mendeley_original.csv')
    parser.add_argument('--out', default='models/current')
    parser.add_argument('--device', choices=['cuda', 'cpu'], default='cuda')
    parser.add_argument('--epochs', type=int, default=160)
    parser.add_argument('--seeds', default='17,42,2026')
    parser.add_argument('--window', type=int, default=8)
    parser.add_argument('--interval-seconds', type=int, default=1800)
    args = parser.parse_args()
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise SystemExit('CUDA unavailable. Install requirements-gpu.txt; no silent CPU fallback.')
    torch.set_num_threads(2)
    device = torch.device(args.device)
    df, target = read_data(args.data)
    pieces = partitions(df)
    prep = preprocess_fit(pieces[0])
    splits = [windows(p, target, prep, args.window, args.interval_seconds) for p in pieces]
    for name, s in zip(['train', 'validation', 'test'], splits):
        if len(np.unique(s[1])) != 2:
            raise ValueError(f'{name} thiếu một lớp. Thu thập thêm dữ liệu, không tráo/random split để làm đẹp kết quả.')
    train, val, test = splits
    duration_enabled = 'duration_seconds' in df
    if duration_enabled:
        for name, s in zip(['train', 'validation', 'test'], splits):
            if np.sum(np.isfinite(s[2]) & (s[1] == 1)) < 5:
                raise ValueError(f'{name} cần >=5 lần tưới có nhãn thời gian độc lập')
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    configs = [{'channels': c, 'dropout': drop} for c, drop in [(8, .1), (16, .2), (32, .3)]]
    seeds = [int(v) for v in args.seeds.split(',')]
    trials, candidates = [], []
    print('DEVICE', torch.cuda.get_device_name(0) if args.device == 'cuda' else 'CPU', flush=True)
    print('SPLITS', [(len(s[1]), int(sum(s[1]))) for s in splits], flush=True)
    for config in configs:
        states, probabilities, scores, histories = [], [], [], []
        for seed in seeds:
            state, prob, score, hist = fit_one(train, val, config, seed, device, args.epochs, duration_enabled)
            states.append(state); probabilities.append(prob); scores.append(score); histories.append(hist)
            trials.append({'config': config, 'seed': seed, 'validation_score': score, 'epochs': len(hist), 'history': hist})
            print('TRIAL', config, seed, 'validation_score', round(score, 5), 'epochs', len(hist), flush=True)
        average = np.mean(probabilities, axis=0)
        candidates.append((float(np.mean(scores)), config, states, average, histories))
    _, config, states, val_p, histories = max(candidates, key=lambda t: t[0])
    threshold = select_threshold(val[1], val_p)
    baselines = []
    for name, options in [
        ('Logistic Regression', [LogisticRegression(C=c, class_weight='balanced', max_iter=2000, random_state=42) for c in [.01, .1, 1]]),
        ('Decision Tree', [DecisionTreeClassifier(max_depth=d, min_samples_leaf=4, class_weight='balanced', random_state=42) for d in [2, 3, 5]]),
        ('Random Forest', [RandomForestClassifier(n_estimators=200, max_depth=d, min_samples_leaf=3, class_weight='balanced', random_state=42, n_jobs=2) for d in [3, 6, None]])]:
        fits = []
        for model in options:
            model.fit(train[0].reshape(len(train[0]), -1), train[1])
            vp = model.predict_proba(val[0].reshape(len(val[0]), -1))[:, 1]
            fits.append((average_precision_score(val[1], vp), model, vp))
        _, model, vp = max(fits, key=lambda t: t[0])
        baselines.append((name, model, select_threshold(val[1], vp), metrics(val[1], vp, select_threshold(val[1], vp))))
    # Selection finished. Only now access test feature tensors/labels for scores.
    test_probs, test_durations = [], []
    for i, state in enumerate(states):
        model = GardenCNN(**config).to(device); model.load_state_dict(state); model.eval()
        torch.save(state, out / f'cnn_seed_{seeds[i]}.pt')
        with torch.no_grad():
            logits, seconds = model(torch.tensor(test[0], device=device))
        test_probs.append(logits.sigmoid().cpu().numpy()); test_durations.append(seconds.cpu().numpy())
    test_p = np.mean(test_probs, axis=0)
    results = {'CNN 1D ensemble': {'validation': metrics(val[1], val_p, threshold), 'test': metrics(test[1], test_p, threshold)}}
    for name, model, t, vm in baselines:
        import joblib
        joblib.dump({'model': model, 'threshold': t, 'preprocessing': prep, 'window': args.window,
                     'features': FEATURES}, out / (name.lower().replace(' ', '_') + '.joblib'))
        tp = model.predict_proba(test[0].reshape(len(test[0]), -1))[:, 1]
        results[name] = {'validation': vm, 'test': metrics(test[1], tp, t)}
    # Rule compares pre-target soil to a fixed domain threshold, not a fitted label.
    rule_prob = (np.array([df.loc[i-1, 'soil_moisture'] for i in test[3]]) < 30).astype(float)
    results['Rule soil < 30%'] = {'test': metrics(test[1], rule_prob, .5)}
    results['Always NO WATER'] = {'test': metrics(test[1], np.zeros(len(test[1])), .5)}
    if duration_enabled:
        mask = np.isfinite(test[2]) & (test[1] == 1)
        duration = np.mean(test_durations, axis=0)
        results['CNN duration'] = {'test': {'mae_seconds': float(mean_absolute_error(test[2][mask], duration[mask])),
            'rmse_seconds': float(np.sqrt(mean_squared_error(test[2][mask], duration[mask]))),
            'r2': float(r2_score(test[2][mask], duration[mask])), 'samples': int(mask.sum())}}
    metadata = {'version': datetime.now(timezone.utc).strftime('cnn-%Y%m%dT%H%M%SZ'),
        'architecture': 'temporal_cnn_1d_ensemble', 'config': config, 'seeds': seeds, 'window': args.window,
        'features': FEATURES, 'preprocessing': prep, 'threshold': threshold, 'interval_seconds': args.interval_seconds,
        'horizon_steps': 1, 'label': target, 'experimental': True, 'duration_trained': duration_enabled,
        'data_sha256': hashlib.sha256(Path(args.data).read_bytes()).hexdigest(),
        'device': str(device), 'gpu': torch.cuda.get_device_name(0) if args.device == 'cuda' else None,
        'torch_version': str(torch.__version__), 'split_rows': {name: {'raw_start': int(p.index[0]), 'raw_end': int(p.index[-1]),
            'windows': len(s[1]), 'positive': int(sum(s[1])), 'first_target': str(df.loc[s[3][0], 'timestamp']),
            'last_target': str(df.loc[s[3][-1], 'timestamp'])} for name, p, s in zip(['train','validation','test'], pieces, splits)},
        'limitations': ['Historical pump status is a proxy action label, not optimal irrigation need.',
            'Target-row sensor values are excluded because they may be post-irrigation.',
            'Small chronological test with few positive events; field validation required.',
            'No duration ground truth in public data; configured pulse is not an AI regression prediction.']}
    (out / 'metadata.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    (out / 'metrics.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    (out / 'search.json').write_text(json.dumps(trials, indent=2), encoding='utf-8')
    import pandas as pd
    pd.DataFrame({'timestamp': [str(df.loc[i,'timestamp']) for i in test[3]], 'target': test[1],
                  'probability': test_p, 'prediction': (test_p >= threshold).astype(int)}).to_csv(out / 'test_predictions.csv', index=False)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for seed, hist in zip(seeds, histories):
        axes[0].plot([h['train_loss'] for h in hist], label=f'seed {seed}')
        axes[1].plot([h['validation_ap'] for h in hist], label=f'seed {seed}')
    axes[0].set(title='Training loss', xlabel='Epoch'); axes[1].set(title='Validation average precision', xlabel='Epoch')
    for ax in axes: ax.legend(); ax.grid(alpha=.2)
    fig.tight_layout(); fig.savefig(out / 'learning_curves.png', dpi=160); plt.close(fig)
    fig, ax = plt.subplots(figsize=(5, 4)); cm = np.array(results['CNN 1D ensemble']['test']['confusion_matrix'])
    ax.imshow(cm, cmap='Greens')
    for (i,j), v in np.ndenumerate(cm): ax.text(j,i,str(v),ha='center',va='center',color='white' if v>cm.max()/2 else 'black',fontsize=18)
    ax.set(xticks=[0,1], yticks=[0,1], xticklabels=['NO WATER','WATER'], yticklabels=['NO WATER','WATER'], xlabel='Predicted', ylabel='Historical action', title='Chronological test, CNN')
    fig.tight_layout(); fig.savefig(out / 'confusion_matrix.png', dpi=160); plt.close(fig)
    print(json.dumps(results, indent=2), flush=True)


if __name__ == '__main__':
    main()
