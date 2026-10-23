"""ML signal layer: features + triple-barrier labels + training, feeding strategy/ml_strategy.py.

The model GENERATES the trade direction (features in -> LONG/SHORT/FLAT out); code still owns stop,
take-profit and size. `features.build_features` is the single function shared by offline training and
live inference (train/serve parity). Heavy deps (scikit-learn/xgboost/joblib) are in the `ml`
dependency group and imported lazily, mirroring the phase2 convention.
"""
