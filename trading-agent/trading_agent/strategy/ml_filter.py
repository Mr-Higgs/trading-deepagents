"""MLFilter: meta-labeling. A deterministic primary picks the trade; ML decides whether to TAKE it.

López de Prado meta-labeling. The inner strategy (`filter_inner`, e.g. a LOOSE ichimoku_breakout that
fires often) generates candidate signals with direction + stop + TP; the ML secondary predicts P(this
trade wins) from the shared `ml.features.build_features` vector and keeps the signal only when
P(win) >= `prob_threshold`, otherwise vetoes. Code still owns direction/stop/TP/size — the filter only
subtracts trades, never invents or moves one.

Why this can work where the generator could not (ml_strategy.py): labels are win/loss of REAL trades
(roughly balanced) instead of ~91% FLAT, and the primary already has an edge for the filter to sharpen.

Fail-closed = VETO: a missing/unloadable model or a feature-schema mismatch skips the trade. That is the
safe default here because the inner is a loose candidate generator you do NOT want trading unfiltered.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

import pandas as pd

from trading_agent.config import PairConfig, StrategyConfig
from trading_agent.ml.features import build_features
from trading_agent.strategy.base import StrategyResult


class MLFilter:
    def __init__(self, strategy: StrategyConfig, pair: PairConfig) -> None:
        from trading_agent.strategy import build_strategy  # lazy: avoid an import cycle at package load

        self.cfg = strategy
        self.pair = pair
        self.inner = build_strategy(replace(strategy, name=strategy.filter_inner), pair)
        self.version = f"{self.inner.version}+mlfilter-v1"
        self._artifact: dict | None = None
        self._load_error: str | None = None
        self._loaded = False

    def _load(self) -> None:
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
        except Exception as e:  # noqa: BLE001 - a broken model must veto, never crash the loop
            self._load_error = f"load_failed:{type(e).__name__}"

    def evaluate(self, snapshot: dict[str, pd.DataFrame], now: datetime) -> StrategyResult:
        res = self.inner.evaluate(snapshot, now)
        if res.signal is None:
            return res  # no candidate -> nothing to filter; pass the inner's skip + reason through

        if not self._loaded:
            self._load()
        sig = res.signal
        if self._artifact is None:
            # fail closed: veto, but carry the candidate so the engine still logs its geometry
            return StrategyResult(None, f"ml_filter_unavailable:{self._load_error}", candidate=sig)

        feats = build_features(snapshot, self.cfg)
        names = self._artifact["features"]
        if set(feats) != set(names):
            return StrategyResult(None, "ml_filter_unavailable:schema_mismatch", candidate=sig)

        model, classes = self._artifact["model"], list(self._artifact["classes"])
        row = pd.DataFrame([[feats[n] for n in names]], columns=names)
        probs = model.predict_proba(row)[0]
        pwin = float(probs[classes.index(1)]) if 1 in classes else float(probs[-1])

        if pwin < self.cfg.prob_threshold:
            return StrategyResult(None, f"ml_filter_veto:{pwin:.2f}", candidate=sig)
        kept = replace(sig, features={**sig.features, "ml_pwin": round(pwin, 4)})
        return StrategyResult(kept, f"{res.reason}+ml{pwin:.2f}")
