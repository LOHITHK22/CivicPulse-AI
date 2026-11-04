"""Metric definitions used by every model. Each is a small, tested function (no hidden averaging)."""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score

# ---------------------------------------------------------------- regression / forecasting


def mae(y: np.ndarray, yhat: np.ndarray) -> float:
    return float(np.mean(np.abs(y - yhat)))


def wape(y: np.ndarray, yhat: np.ndarray) -> float:
    """Weighted absolute percentage error = sum|y - yhat| / sum|y|. Undefined (nan) if sum|y| == 0."""
    denom = float(np.sum(np.abs(y)))
    return float(np.sum(np.abs(y - yhat)) / denom) if denom > 0 else float("nan")


def bias(y: np.ndarray, yhat: np.ndarray) -> float:
    """Mean (yhat - y): positive = over-forecasting."""
    return float(np.mean(yhat - y))


def coverage(y: np.ndarray, lower: np.ndarray, upper: np.ndarray) -> float:
    ok = ~(np.isnan(lower) | np.isnan(upper))
    if not ok.any():
        return float("nan")
    return float(np.mean((y[ok] >= lower[ok]) & (y[ok] <= upper[ok])))


def mean_width(lower: np.ndarray, upper: np.ndarray) -> float:
    ok = ~(np.isnan(lower) | np.isnan(upper))
    return float(np.mean(upper[ok] - lower[ok])) if ok.any() else float("nan")


def relative_improvement(baseline: float, candidate: float) -> float:
    """(baseline - candidate) / baseline for a lower-is-better metric. Positive = candidate better."""
    return (baseline - candidate) / baseline


def paired_bootstrap_wape_diff(
    y: np.ndarray, a: np.ndarray, b: np.ndarray, groups: np.ndarray, n_boot: int = 1000, seed: int = 0
) -> dict[str, float]:
    """Bootstrap CI of WAPE(a) - WAPE(b), resampling whole groups (forecast origins) with replacement.

    Resampling origins (not individual points) respects the dependence between the 7 horizons and
    the series forecast from the same origin.
    """
    rng = np.random.default_rng(seed)
    uniq = np.unique(groups)
    idx_by_group = [np.flatnonzero(groups == g) for g in uniq]
    abs_a = np.abs(y - a)
    abs_b = np.abs(y - b)
    sums = np.array([[abs_a[i].sum(), abs_b[i].sum(), np.abs(y[i]).sum()] for i in idx_by_group])
    diffs = np.empty(n_boot)
    for k in range(n_boot):
        s = sums[rng.integers(0, len(uniq), len(uniq))].sum(axis=0)
        diffs[k] = (s[0] - s[1]) / s[2]
    point = (abs_a.sum() - abs_b.sum()) / np.abs(y).sum()
    return {
        "diff": float(point),
        "ci_low": float(np.quantile(diffs, 0.025)),
        "ci_high": float(np.quantile(diffs, 0.975)),
        "n_groups": len(uniq),
        "n_boot": n_boot,
    }


# ---------------------------------------------------------------- classification


def brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def log_loss(y: np.ndarray, p: np.ndarray, eps: float = 1e-15) -> float:
    q = np.clip(p, eps, 1 - eps)
    return float(-np.mean(y * np.log(q) + (1 - y) * np.log(1 - q)))


def pr_auc(y: np.ndarray, p: np.ndarray) -> float:
    """Average precision (step-wise area under the precision-recall curve)."""
    if y.min() == y.max():
        return float("nan")
    return float(average_precision_score(y, p))


def roc_auc(y: np.ndarray, p: np.ndarray) -> float:
    if y.min() == y.max():
        return float("nan")
    return float(roc_auc_score(y, p))


def reliability(y: np.ndarray, p: np.ndarray, n_bins: int = 10) -> list[dict[str, float]]:
    """Equal-frequency bins of predicted probability: mean predicted vs observed rate per bin."""
    order = np.argsort(p, kind="stable")
    out = []
    for chunk in np.array_split(order, n_bins):
        if len(chunk) == 0:
            continue
        out.append(
            {
                "mean_predicted": float(p[chunk].mean()),
                "observed_rate": float(y[chunk].mean()),
                "p_min": float(p[chunk].min()),
                "p_max": float(p[chunk].max()),
                "n": len(chunk),
            }
        )
    return out


def ece(y: np.ndarray, p: np.ndarray, n_bins: int = 10) -> float:
    """Expected calibration error over equal-frequency bins (weighted mean |predicted - observed|)."""
    bins = reliability(y, p, n_bins)
    n = sum(b["n"] for b in bins)
    return float(sum(b["n"] * abs(b["mean_predicted"] - b["observed_rate"]) for b in bins) / n)


def threshold_metrics(y: np.ndarray, p: np.ndarray, threshold: float) -> dict[str, float]:
    pred = p >= threshold
    tp = float(np.sum(pred & (y == 1)))
    fp = float(np.sum(pred & (y == 0)))
    fn = float(np.sum(~pred & (y == 1)))
    precision = tp / (tp + fp) if tp + fp > 0 else float("nan")
    recall = tp / (tp + fn) if tp + fn > 0 else float("nan")
    f1 = 2 * precision * recall / (precision + recall) if precision + recall > 0 else float("nan")
    return {
        "threshold": float(threshold),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "flagged_share": float(pred.mean()),
    }


def best_f1_threshold(y: np.ndarray, p: np.ndarray) -> float:
    """Threshold maximizing F1 (chosen on VALIDATION data only, then frozen for test)."""
    order = np.argsort(-p, kind="stable")
    ys, ps = y[order], p[order]
    tp = np.cumsum(ys)
    fp = np.cumsum(1 - ys)
    total_pos = ys.sum()
    if total_pos == 0:
        return 0.5
    precision = tp / (tp + fp)
    recall = tp / total_pos
    with np.errstate(invalid="ignore", divide="ignore"):
        f1 = np.where(precision + recall > 0, 2 * precision * recall / (precision + recall), 0)
    # Only consider cut points where the score changes, so the threshold is well defined.
    last_of_run = np.r_[ps[1:] != ps[:-1], True]
    k = int(np.argmax(np.where(last_of_run, f1, -1)))
    return float(ps[k])


def precision_at_top(y: np.ndarray, p: np.ndarray, share: float) -> dict[str, float]:
    k = max(1, round(len(p) * share))
    top = np.argsort(-p, kind="stable")[:k]
    return {"share": share, "k": k, "precision": float(y[top].mean()), "recall": float(y[top].sum() / max(y.sum(), 1))}


def classification_summary(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    return {
        "n": len(y),
        "positive_rate": float(y.mean()) if len(y) else float("nan"),
        "pr_auc": pr_auc(y, p),
        "roc_auc": roc_auc(y, p),
        "brier": brier(y, p),
        "log_loss": log_loss(y, p),
        "ece": ece(y, p) if len(y) >= 10 else float("nan"),
        "mean_predicted": float(p.mean()) if len(p) else float("nan"),
    }
