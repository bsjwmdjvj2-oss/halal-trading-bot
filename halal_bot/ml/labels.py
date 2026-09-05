"""Forward-return labels for the ML entry-signal spike.

Unlike every feature in halal_bot.ml.features (backward-looking only, safe
at any point in time), a label is forward-looking BY CONSTRUCTION — row t's
label needs Close[t+horizon_days], which doesn't exist yet at the moment a
live model would need to score row t. That's fine for a feature (used only
at inference time, never trained on unseen future data) but not for a label:
a training row whose horizon window crosses a train/test split boundary
would let the model see into the test period during training. See
drop_unlabelable_tail() below and scripts/train_ml_model.py, which calls it
per-ticker before concatenating the training table.
"""
from __future__ import annotations

import pandas as pd


def build_labels(df: pd.DataFrame, horizon_days: int = 10, threshold_pct: float = 0.0) -> pd.Series:
    """1 if Close horizon_days trading days ahead is more than threshold_pct
    above today's Close, else 0. NaN for the last horizon_days rows (no
    future close to look at yet) -- those rows are unlabeled, not a 0."""
    forward_return = df["Close"].shift(-horizon_days) / df["Close"] - 1
    return (forward_return > threshold_pct).where(forward_return.notna())


def drop_unlabelable_tail(df: pd.DataFrame, horizon_days: int) -> pd.DataFrame:
    """Drops the last horizon_days rows of df BY POSITION (not calendar
    time). Pass a df already truncated to the train window's end date: its
    last horizon_days rows have a label built from Close horizon_days
    *trading* days ahead, which for those rows falls past the truncation
    point -- into the held-out window. Positional truncation is exact here
    (unlike a calendar-day approximation like BDay, which over/undercounts
    around holidays) since it operates on this ticker's own trading-day
    index. Only meant to be applied to the TRAIN portion of a chronological
    split; the test portion doesn't need this since it isn't used to fit
    anything."""
    if horizon_days <= 0 or len(df) <= horizon_days:
        return df.iloc[0:0]
    return df.iloc[:-horizon_days]
