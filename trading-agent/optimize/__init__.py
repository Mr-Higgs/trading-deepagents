"""Walk-forward, overfitting-aware parameter search over the deterministic engine (`trading_agent.engine.run`).

Modules: `space` (what may be tuned), `walkforward` (folds + the locked holdout), `evaluate` (one engine
run -> metrics + daily returns), `stats` (DSR, PBO/CSCV, bootstrap CIs, plateau ranking), `study` (Optuna
per fold), `report` (markdown/JSON), `run` (CLI), `holdout` (one-shot final evaluation).
"""
