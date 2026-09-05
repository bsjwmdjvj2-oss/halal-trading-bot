"""Model training/persistence for the ML entry-signal spike.

HistGradientBoostingClassifier: ships in scikit-learn itself (no extra
compiled dependency like xgboost/lightgbm), handles tabular data well at
this dataset size (~140 tickers x a few years daily). First ML dependency
in this repo, so kept to one library.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

import joblib
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from halal_bot.config import ROOT_DIR
from halal_bot.ml.features import FEATURE_COLUMNS

MODEL_PATH = ROOT_DIR / "data" / "ml_model.joblib"
META_PATH = ROOT_DIR / "data" / "ml_model_meta.json"


@dataclass
class ModelMeta:
    trained_at: str
    train_start: str
    train_end: str
    feature_columns: list[str]
    horizon_days: int
    threshold_pct: float
    train_rows: int
    test_auc: float | None = None
    test_precision: float | None = None
    test_recall: float | None = None


def train_model(X: pd.DataFrame, y: pd.Series) -> HistGradientBoostingClassifier:
    model = HistGradientBoostingClassifier(random_state=42)
    model.fit(X[FEATURE_COLUMNS], y)
    return model


def predict_scores(model: HistGradientBoostingClassifier, X: pd.DataFrame) -> pd.Series:
    """Probability of the positive class (forward return > threshold) per
    row. Rows with any NaN feature (indicator warm-up window) score NaN --
    compared against ml_threshold in generate_signals(), NaN > threshold is
    always False, so an unscoreable row just never triggers an entry."""
    feats = X[FEATURE_COLUMNS]
    scores = pd.Series(index=X.index, dtype=float)
    scoreable = feats.notna().all(axis=1)
    if scoreable.any():
        scores.loc[scoreable] = model.predict_proba(feats.loc[scoreable])[:, 1]
    return scores


def save_model(model: HistGradientBoostingClassifier, meta: ModelMeta) -> None:
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, MODEL_PATH)
    with open(META_PATH, "w") as f:
        json.dump(asdict(meta), f, indent=2)


def load_model() -> tuple[HistGradientBoostingClassifier, ModelMeta] | None:
    """None if no model has been trained yet (scripts/train_ml_model.py
    hasn't been run) -- callers must fail closed on this, same discipline
    halal_bot.research.tipranks_context uses for a missing/stale snapshot."""
    if not MODEL_PATH.exists() or not META_PATH.exists():
        return None
    model = joblib.load(MODEL_PATH)
    with open(META_PATH) as f:
        meta = ModelMeta(**json.load(f))
    return model, meta
