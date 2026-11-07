# Walk-forward optimization: AUD_USD ichimoku_breakout

Range 2022-01-01 .. 2026-07-01 (holdout from 2026-07-01 untouched), rolling 12m train / 3m test, objective `sharpe`, 140 trials.

## Concatenated out-of-sample

| metric | value |
|---|--:|
| oos_trades | 28 |
| oos_expectancy | 0.840 |
| oos_expectancy_ci95 | [-52.209, 62.031] |
| oos_sharpe_ann | 0.006 |
| oos_sharpe_ann_ci95 | [-1.195, 0.920] |
| oos_total_pnl | 23.508 |
| deflated_sharpe | 0.000 |
| pbo_mean | 0.333 |
| n_trials | 140 |
| folds | 14 |

## Folds

| test window | OOS trades | OOS expectancy | OOS Sharpe | train score | plateau score | PBO |
|---|--:|--:|--:|--:|--:|--:|
| 2023-01-01..2023-04-01 | 7 | -83.977 | -3.482 | -0.415 | -1000000.000 | 0.587 |
| 2023-04-01..2023-07-01 | 0 | 0.000 | 0.000 | -1.352 | -1000000.000 | 0.762 |
| 2023-07-01..2023-10-01 | 1 | -1.220 | -1.969 | 0.117 | -1000000.000 | 0.258 |
| 2023-10-01..2024-01-01 | 2 | 112.669 | 1.305 | -2.795 | -1000000.000 | 0.002 |
| 2024-01-01..2024-04-01 | 1 | -102.096 | -2.028 | -1.999 | -1000000.000 | 0.288 |
| 2024-04-01..2024-07-01 | 3 | -97.934 | -3.444 | -1.972 | -1000000.000 | 0.309 |
| 2024-07-01..2024-10-01 | 1 | -99.999 | -1.939 | 0.111 | -1000000.000 | 0.631 |
| 2024-10-01..2025-01-01 | 2 | -49.618 | -1.924 | 0.354 | -1000000.000 | 0.041 |
| 2025-01-01..2025-04-01 | 4 | 161.448 | 2.888 | 0.791 | -1000000.000 | 0.321 |
| 2025-04-01..2025-07-01 | 2 | 116.783 | 1.290 | -1.969 | -1000000.000 | 0.152 |
| 2025-07-01..2025-10-01 | 0 | 0.000 | 0.000 | 0.074 | -1000000.000 | 0.248 |
| 2025-10-01..2026-01-01 | 0 | 0.000 | 0.000 | 0.033 | -1000000.000 | 0.316 |
| 2026-01-01..2026-04-01 | 5 | 20.601 | 0.629 | -1.409 | -1000000.000 | 0.393 |
| 2026-04-01..2026-07-01 | 0 | 0.000 | 0.000 | 0.103 | -1000000.000 | 0.351 |

## Recommended params (last fold's plateau pick)

```json
{
  "rr": 3.75,
  "stop_rule": "swing",
  "swing_lookback": 16,
  "atr_mult": 3.25,
  "stop_buffer_pips": 4.0,
  "require_tk_momentum": false,
  "require_chikou": true,
  "require_htf_confluence": false,
  "require_regime": true,
  "min_trend_distance_pips": 16.0,
  "min_tk_separation_pips": 8.0,
  "min_kijun_slope_pips": 9.0,
  "min_cloud_thickness_pips": 4.0,
  "min_cloud_slope_pips": 1.0,
  "quality_min_pass": 3,
  "breakout_trigger": "cloud",
  "breakout_require_pattern": true,
  "breakeven_enabled": true,
  "breakeven_trigger_pips": 0.0,
  "breakeven_trigger_r": 1.5,
  "trail_mode": "pips",
  "trail_distance_pips": 27.5,
  "partial_exit_pct": 0.75
}
```
