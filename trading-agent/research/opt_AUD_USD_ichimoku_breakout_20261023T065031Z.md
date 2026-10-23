# Walk-forward optimization: AUD_USD ichimoku_breakout

Range 2022-01-01 .. 2026-07-01 (holdout from 2026-07-01 untouched), rolling 12m train / 3m test, objective `sharpe`, 200 trials.

## Concatenated out-of-sample

| metric | value |
|---|--:|
| oos_trades | 575 |
| oos_expectancy | -29.324 |
| oos_expectancy_ci95 | [-39.880, -16.833] |
| oos_sharpe_ann | -2.391 |
| oos_sharpe_ann_ci95 | [-3.629, -1.309] |
| oos_total_pnl | -16861.512 |
| deflated_sharpe | 0.000 |
| pbo_mean | 0.156 |
| n_trials | 200 |
| folds | 14 |

## Folds

| test window | OOS trades | OOS expectancy | OOS Sharpe | train score | plateau score | PBO |
|---|--:|--:|--:|--:|--:|--:|
| 2023-01-01..2023-04-01 | 48 | -23.468 | -2.839 | -0.173 | -1000000.000 | 0.142 |
| 2023-04-01..2023-07-01 | 19 | -6.271 | -0.408 | -0.923 | -1000000.000 | 0.059 |
| 2023-07-01..2023-10-01 | 42 | 8.794 | 0.625 | -0.100 | -1000000.000 | 0.374 |
| 2023-10-01..2024-01-01 | 79 | -30.577 | -3.762 | -0.516 | -1000000.000 | 0.125 |
| 2024-01-01..2024-04-01 | 80 | -14.274 | -1.487 | -0.558 | -1000000.000 | 0.073 |
| 2024-04-01..2024-07-01 | 29 | -83.698 | -7.607 | 0.553 | -1000000.000 | 0.050 |
| 2024-07-01..2024-10-01 | 75 | -38.525 | -4.329 | -1.990 | -1.990 | 0.048 |
| 2024-10-01..2025-01-01 | 6 | 34.195 | 1.385 | -0.939 | -0.939 | 0.352 |
| 2025-01-01..2025-04-01 | 13 | -72.573 | -6.217 | -0.980 | -1000000.000 | 0.068 |
| 2025-04-01..2025-07-01 | 94 | -37.032 | -4.242 | -1.555 | -1.555 | 0.228 |
| 2025-07-01..2025-10-01 | 63 | -42.602 | -3.804 | -0.890 | -1000000.000 | 0.154 |
| 2025-10-01..2026-01-01 | 4 | -83.847 | -3.497 | -1.253 | -1.253 | 0.054 |
| 2026-01-01..2026-04-01 | 13 | 16.534 | 0.672 | -0.897 | -1000000.000 | 0.001 |
| 2026-04-01..2026-07-01 | 10 | -8.749 | -0.203 | -0.213 | -1000000.000 | 0.457 |

## Recommended params (last fold's plateau pick)

```json
{
  "rr": 4.0,
  "stop_rule": "kijun",
  "atr_mult": 2.75,
  "stop_buffer_pips": 0.0,
  "require_tk_momentum": false,
  "require_chikou": true,
  "min_trend_distance_pips": 10.0,
  "breakout_ref": "base",
  "breakeven_enabled": false,
  "breakeven_trigger_pips": 5.0,
  "breakeven_trigger_r": 2.25,
  "trail_mode": "pips",
  "trail_distance_pips": 20.0,
  "partial_exit_pct": 0.0
}
```
