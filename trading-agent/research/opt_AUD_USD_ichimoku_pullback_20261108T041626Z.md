# Walk-forward optimization: AUD_USD ichimoku_pullback

Range 2022-01-01 .. 2026-07-01 (holdout from 2026-07-01 untouched), rolling 12m train / 3m test, objective `sharpe`, 200 trials.

## Concatenated out-of-sample

| metric | value |
| --- |--: |
| oos_trades | 202 |
| oos_expectancy | 5.297 |
| oos_expectancy_ci95 | [-8.052, 18.127] |
| oos_sharpe_ann | 0.325 |
| oos_sharpe_ann_ci95 | [-0.864, 1.249] |
| oos_total_pnl | 1070.002 |
| deflated_sharpe | 0.000 |
| pbo_mean | 0.416 |
| n_trials | 200 |
| folds | 14 |

## Folds

| test window | OOS trades | OOS expectancy | OOS Sharpe | train score | plateau score | PBO |
| --- |--: |--:|--: |--:|--:|--:|
| 2023-01-01..2023-04-01 | 21 | 1.067 | 0.401 | 1.317 | -1000000.000 | 0.465 |
| 2023-04-01..2023-07-01 | 4 | -100.517 | -2.536 | 0.977 | -1000000.000 | 0.328 |
| 2023-07-01..2023-10-01 | 11 | 9.521 | 2.935 | 0.252 | -1000000.000 | 0.023 |
| 2023-10-01..2024-01-01 | 27 | 23.356 | 1.175 | -2.165 | -2.524 | 0.107 |
| 2024-01-01..2024-04-01 | 5 | 41.998 | 2.654 | 0.765 | -1000000.000 | 0.408 |
| 2024-04-01..2024-07-01 | 7 | -41.443 | -1.881 | -0.488 | -1000000.000 | 0.345 |
| 2024-07-01..2024-10-01 | 40 | 11.310 | 0.849 | -0.522 | -1000000.000 | 0.344 |
| 2024-10-01..2025-01-01 | 12 | 65.456 | 3.017 | 0.424 | -1000000.000 | 0.755 |
| 2025-01-01..2025-04-01 | 14 | -6.734 | -0.841 | 0.939 | -1000000.000 | 0.546 |
| 2025-04-01..2025-07-01 | 1 | -99.793 | -1.954 | 0.510 | 0.505 | 0.441 |
| 2025-07-01..2025-10-01 | 17 | -17.084 | -3.260 | 0.530 | 0.530 | 0.610 |
| 2025-10-01..2026-01-01 | 2 | -3.007 | -1.954 | -1.323 | -1.671 | 0.515 |
| 2026-01-01..2026-04-01 | 32 | -2.558 | -0.267 | -0.830 | -1000000.000 | 0.566 |
| 2026-04-01..2026-07-01 | 9 | 14.325 | 0.696 | -0.374 | -1000000.000 | 0.365 |

## Recommended params (last fold's plateau pick)

```json
{
  "rr": 2.75,
  "stop_rule": "cloud",
  "swing_lookback": 16,
  "atr_mult": 1.5,
  "stop_buffer_pips": 1.0,
  "require_tk_momentum": false,
  "require_chikou": false,
  "require_htf_confluence": false,
  "require_regime": false,
  "min_trend_distance_pips": 20.0,
  "min_tk_separation_pips": 2.0,
  "min_kijun_slope_pips": 0.0,
  "min_cloud_thickness_pips": 4.0,
  "min_cloud_slope_pips": 7.0,
  "quality_min_pass": 2,
  "h1_pullback_band": 0.007,
  "fib_lookback": 80,
  "breakeven_enabled": false,
  "breakeven_trigger_pips": 0.0,
  "breakeven_trigger_r": 0.75,
  "trail_mode": "kijun",
  "trail_distance_pips": 7.5,
  "partial_exit_pct": 0.25
}
```
