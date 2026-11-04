"""Delay risk: target definition, maturity, chronological splits, feature availability, serialization."""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl
import pytest
from modeling_fixtures import fast_cfg, synthetic_fact, synthetic_paths, write_warehouse  # noqa: F401

from civicpulse_modeling import delay_risk as D


@pytest.fixture(scope="module")
def frame(synthetic_paths):  # noqa: F811
    return D.load_frame(synthetic_paths, 7)


def _one_request_fact(closed_offsets: list[dt.timedelta | None], invalid: bool = False) -> pl.DataFrame:
    created = dt.datetime(2025, 3, 3, 10, 0)
    n = len(closed_offsets)
    return pl.DataFrame(
        {
            "unique_key": [str(i) for i in range(n)],
            "created_at": [created] * n,
            "closed_at": [None if o is None else created + o for o in closed_offsets],
            "created_date": [created.date()] * n,
            "created_hour": [10] * n,
            "created_dow": [1] * n,
            "created_month": [3] * n,
            "category_key": ["heat_hot_water"] * n,
            "descriptor": ["A"] * n,
            "borough": ["BRONX"] * n,
            "community_board": ["01 BRONX"] * n,
            "channel": ["PHONE"] * n,
            "location_type": ["X"] * n,
            "dq_closed_before_created": [invalid] * n,
            "dq_closed_in_future": [False] * n,
            "dq_closed_status_without_date": [False] * n,
        },
        schema_overrides={"created_hour": pl.Int8, "created_dow": pl.Int8, "created_month": pl.Int8},
    )


def test_target_definition_edges(tmp_path):
    offsets = [dt.timedelta(hours=1), dt.timedelta(days=7), dt.timedelta(days=7, seconds=1), None]
    paths = write_warehouse(tmp_path / "d", _one_request_fact(offsets))
    df = D.load_frame(paths, 7).sort("unique_key")
    assert df["y"].to_list() == [0, 0, 1, 1]  # closed at exactly 7 x 24 h counts as within 7 days
    assert df["label_unknown"].to_list() == [False] * 4


def test_invalid_closure_is_label_unknown_and_excluded(tmp_path, fast_cfg):  # noqa: F811
    paths = write_warehouse(tmp_path / "d", _one_request_fact([dt.timedelta(hours=1)], invalid=True))
    df = D.load_frame(paths, 7)
    snap = D.Snapshot(dt.datetime(2026, 1, 1), dt.datetime(2025, 12, 25), 7)
    assert D.assign_splits(df, fast_cfg.delay_risk, snap)["split"].to_list() == ["label_unknown"]


def test_snapshot_maturity_uses_earliest_observation_time():
    snap = D.snapshot_from_data("2026-11-01 02:04:40", "2026-10-31T12:00:00+00:00", 7)
    # 12:00 UTC = 08:00 NYC (EDT) on Oct 31, earlier than the last created_at -> it wins.
    assert snap.observed_until == dt.datetime(2026, 10, 31, 8, 0)
    assert snap.maturity_cutoff == dt.datetime(2026, 10, 24, 8, 0)


def test_splits_are_chronological_with_embargo_and_maturity(frame, fast_cfg):  # noqa: F811
    cfg = fast_cfg.delay_risk
    snap = D.Snapshot(dt.datetime(2026, 10, 31, 23, 0), dt.datetime(2026, 10, 24, 23, 0), 7)
    df = D.assign_splits(frame, cfg, snap)
    rng = {s: (g["created_at"].min(), g["created_at"].max()) for (s,), g in df.group_by(["split"])}
    emb = dt.timedelta(days=cfg.embargo_days)
    assert rng["train"][1] < rng["validation"][0] - emb + dt.timedelta(days=1)
    assert rng["train"][1] < dt.datetime.combine(cfg.validation.start, dt.time()) - emb
    assert rng["validation"][1] < dt.datetime.combine(cfg.test.start, dt.time()) - emb
    assert rng["test"][1] <= snap.maturity_cutoff
    assert rng["immature"][0] > snap.maturity_cutoff
    assert "embargo" in rng
    # Every row lands in exactly one split.
    assert df["split"].null_count() == 0


def test_features_are_creation_time_only(synthetic_fact, tmp_path):  # noqa: F811
    assert not set(D.FEATURES) & D.FORBIDDEN_FEATURES
    target_day = dt.date(2025, 6, 15)
    base = D.load_frame(write_warehouse(tmp_path / "a", synthetic_fact), 7)
    # Change outcomes that were NOT knowable on target_day: closures of requests created in the
    # last 7 days before it, and of everything created on/after it.
    hidden = pl.col("created_date") >= target_day - dt.timedelta(days=7)
    changed = synthetic_fact.with_columns(
        pl.when(hidden)
        .then(pl.col("created_at") + dt.timedelta(days=30))
        .otherwise(pl.col("closed_at"))
        .alias("closed_at")
    )
    alt = D.load_frame(write_warehouse(tmp_path / "b", changed), 7)
    cols = list(D.FEATURES)
    a = base.filter(pl.col("created_date") == target_day).sort("unique_key").select(cols)
    b = alt.filter(pl.col("created_date") == target_day).sort("unique_key").select(cols)
    assert a.height > 0 and a.equals(b)
    # Sanity: the change did alter later labels and later trailing rates (test has power).
    later = dt.date(2025, 7, 10)
    assert (
        not base.filter(pl.col("created_date") == later)
        .sort("unique_key")["trailing_delay_rate_borough"]
        .equals(alt.filter(pl.col("created_date") == later).sort("unique_key")["trailing_delay_rate_borough"])
    )


def test_load_features_count_only_earlier_requests(frame):
    day = frame.filter(pl.col("created_date") == dt.date(2025, 5, 20))
    for (_cat, _bor), g in day.group_by(["category_key", "borough"]):
        g = g.sort(["created_at", "unique_key"])
        assert g["load_same_day_before"].to_list() == list(range(g.height))
    # Trailing rates have no history at the very start of the data.
    first = frame.filter(pl.col("created_date") == frame["created_date"].min())
    assert first["trailing_delay_rate_borough"].null_count() == first.height


def test_encoder_uses_training_statistics_only(frame):
    train = frame.filter(pl.col("created_date") < dt.date(2024, 1, 1))
    test = frame.filter(pl.col("created_date") >= dt.date(2026, 1, 1)).with_columns(
        pl.lit("NEVER_SEEN").alias("descriptor"), pl.lit(None, dtype=pl.Float64).alias("trailing_delay_rate_cb")
    )
    enc = D.Encoder.fit(train, min_count=20)
    assert enc.medians["trailing_delay_rate_cb"] == pytest.approx(train["trailing_delay_rate_cb"].median())
    codes = enc.categorical_codes(test)
    assert (codes[:, D.CATEGORICAL.index("descriptor")] == 0).all()  # unseen -> __other__
    num = enc.numeric(test, impute=True)
    assert (num[:, D.NUMERIC.index("trailing_delay_rate_cb")] == enc.medians["trailing_delay_rate_cb"]).all()


def test_models_train_serialize_and_predict_valid_probabilities(frame, fast_cfg):  # noqa: F811
    cfg = fast_cfg.delay_risk
    train = frame.filter(pl.col("created_date") < dt.date(2025, 1, 1))
    valid = frame.filter(pl.col("created_date").is_between(dt.date(2025, 1, 10), dt.date(2025, 6, 30)))
    enc = D.Encoder.fit(train, cfg.min_category_count)
    lr = D.LogisticModel.fit(enc, train, cfg.logreg, 0)
    p = lr.predict_proba(valid)
    assert ((p > 0) & (p < 1)).all()
    # JSON round trip is exact.
    import json

    lr2 = D.LogisticModel.from_dict(json.loads(json.dumps(lr.to_dict())))
    assert np.allclose(lr2.predict_proba(valid), p)
    # Contributions sum to the logit.
    z = lr.contributions(valid).sum(axis=1) + lr.intercept
    assert np.allclose(1 / (1 + np.exp(-z)), p)

    booster = D.train_lgbm(enc, train, valid, cfg.lgbm, 0)
    q = np.asarray(booster.predict(enc.lgb_matrix(valid)))
    assert ((q >= 0) & (q <= 1)).all()
    import lightgbm as lgb

    loaded = lgb.Booster(model_str=booster.model_to_string())
    assert np.allclose(loaded.predict(enc.lgb_matrix(valid)), q)
    contrib = D.lgbm_contributions(booster, enc, valid)
    assert np.allclose(1 / (1 + np.exp(-contrib.sum(axis=1))), q, atol=1e-6)
    # The synthetic data has a real signal (slow-closure rate rises by category/borough).
    from civicpulse_modeling import metrics as M

    assert M.roc_auc(valid["y"].to_numpy(), q) > 0.55


def test_logistic_regression_matches_sklearn(frame, fast_cfg):  # noqa: F811
    """The hand-rolled JSON predictor must equal sklearn on the same design matrix."""
    from scipy import sparse
    from sklearn.linear_model import LogisticRegression

    train = frame.filter(pl.col("created_date") < dt.date(2024, 6, 1))
    enc = D.Encoder.fit(train, 20)
    lr = D.LogisticModel.fit(enc, train, {"C": 1.0, "max_iter": 300}, 0)
    codes = enc.categorical_codes(train)
    offsets = np.cumsum([0, *[len(enc.vocab[c]) for c in D.CATEGORICAL]])
    rows = np.repeat(np.arange(train.height), len(D.CATEGORICAL))
    x_cat = sparse.csr_matrix(
        (np.ones(rows.size), (rows, (codes + offsets[:-1]).ravel())), shape=(train.height, offsets[-1])
    )
    x_num = D.LogisticModel._numeric_transform(enc, train)
    mean, scale = x_num.mean(axis=0), x_num.std(axis=0)
    scale[scale == 0] = 1
    X = sparse.hstack([x_cat, sparse.csr_matrix((x_num - mean) / scale)]).tocsr()
    sk = LogisticRegression(C=1.0, max_iter=300, solver="lbfgs", random_state=0).fit(X, train["y"].to_numpy())
    assert np.allclose(sk.predict_proba(X)[:, 1], lr.predict_proba(train), atol=1e-8)
