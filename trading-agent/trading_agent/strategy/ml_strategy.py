"""MLStrategy: an ML model GENERATES the trade direction; code still owns stop / TP / size.

Same Strategy protocol as the deterministic strategies, so it drops into the same `build_strategy`
registry, the same engine, backtest, risk engine and event log - nothing downstream changes. The model
(a scikit-learn / xgboost classifier trained by `ml.train`) predicts LONG / SHORT / FLAT from the
shared `ml.features.build_features` vector; on LONG/SHORT above `prob_threshold` we emit a Signal whose
stop is ATR(atr_length)*atr_mult and TP is rr*stop - the SAME geometry the labels were built with
(`ml.labeling`), so the model trades the setups it was trained to recognize.

Fail-closed, exactly like the LLM gate (agent_strategy.py): a missing/unloadable model, a feature
schema that no longer matches the trained one, a NaN stop, or a sub-threshold / FLAT prediction all
resolve to "no trade" (a skip with a reason), never an exception into the engine. The joblib model is a
pure function of its inputs, so a backtest is deterministic with no verdict-cache equivalent needed.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from trading_agent.config import PairConfig, StrategyConfig
from trading_agent.indicators import atr
from trading_agent.ml.features import build_features
from trading_agent.strategy.base import StrategyResult
from trading_agent.types import Signal

SETUP_ID = "ml_signal"


class MLStrategy:
    def __init__(self, strategy: StrategyConfig, pair: PairConfig) -> None:
        self.cfg = strategy
        self.pair = pair
        self.version = f"{strategy.version}+ml-v1"
        self._artifact: dict | None = None
        self._load_error: str | None = None
        self._loaded = False

    def _load(self) -> None:
        """Lazy-load the joblib artifact once. Any failure is remembered as a fail-closed reason."""
        self._loaded = True
        path = self.cfg.model_path
        if not path:
            self._load_error = "no_model_path"
            return
        try:
            import joblib  # lazy: only the ml path pulls in joblib/sklearn (uv sync --group ml)

            art = joblib.load(path)
            if not all(k in art for k in ("model", "features", "classes")):
                self._load_error = "bad_artifact"
                return
            self._artifact = art
        except FileNotFoundError:
            self._load_error = "model_missing"
        except Exception as e:  # noqa: BLE001 - a broken model must skip trades, never crash the loop
            self._load_error = f"load_failed:{type(e).__name__}"

    def evaluate(self, snapshot: dict[str, pd.DataFrame], now: datetime) -> StrategyResult:
        if not self._loaded:
            self._load()
        if self._artifact is None:
            return StrategyResult(None, f"ml_skip:{self._load_error}")

        feats = build_features(snapshot, self.cfg)
        names = self._artifact["features"]
        if set(feats) != set(names):  # model trained on a different feature schema -> fail closed
            return StrategyResult(None, "ml_skip:schema_mismatch")

        model, classes = self._artifact["model"], list(self._artifact["classes"])
        row = pd.DataFrame([[feats[n] for n in names]], columns=names)
        probs = model.predict_proba(row)[0]
        best = max(range(len(classes)), key=lambda j: probs[j])
        direction, prob = str(classes[best]), float(probs[best])
        if direction not in ("LONG", "SHORT") or prob < self.cfg.prob_threshold:
            return StrategyResult(None, f"ml_skip:{direction.lower()}:{prob:.2f}")

        entry = snapshot.get(self.cfg.entry_tf)
        if entry is None or len(entry) < self.cfg.ichimoku.min_rows + 1:
            return StrategyResult(None, "ml_skip:insufficient_history")
        atr_val = float(atr(entry, self.cfg.atr_length).iloc[-1])
        if not (atr_val > 0):
            return StrategyResult(None, "ml_skip:invalid_stop")
        entry_ref = float(entry["c"].iloc[-1])
        stop_distance = atr_val * self.cfg.atr_mult
        stop = entry_ref - stop_distance if direction == "LONG" else entry_ref + stop_distance
        tp = entry_ref + self.cfg.rr * stop_distance if direction == "LONG" else entry_ref - self.cfg.rr * stop_distance
        reason = f"ml_{direction.lower()}:{prob:.2f}"

        sig = Signal(
            ts=now,
            signal_id=f"{self.cfg.instrument}-{now:%Y%m%dT%H%M%SZ}",
            instrument=self.cfg.instrument,
            direction=direction,
            entry_ref=entry_ref,
            stop=stop,
            tp=tp,
            rr=self.cfg.rr,
            setup_id=SETUP_ID,
            strategy_version=self.version,
            features={
                "ml_class": direction,
                "ml_score": round(prob, 4),
                "ml_probs": {str(classes[j]): round(float(probs[j]), 4) for j in range(len(classes))},
                "stop_rule": "atr",
                "stop_distance": stop_distance,
                "style": self.cfg.style,
                "stack": f"{self.cfg.trend_tf}/{self.cfg.bias_tf}/{self.cfg.entry_tf}",
            },
            reason=reason,
        )
        return StrategyResult(sig, reason)
