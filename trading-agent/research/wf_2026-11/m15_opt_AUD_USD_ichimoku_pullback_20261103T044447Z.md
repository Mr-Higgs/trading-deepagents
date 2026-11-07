# Walk-forward optimization: AUD_USD ichimoku_pullback

Range 2022-01-01 .. 2026-07-01 (holdout from 2026-07-01 untouched), rolling 12m train / 3m test, objective `sharpe`, 140 trials.

## Concatenated out-of-sample

| metric | value |
|---|--:|
| oos_trades | 188 |
| oos_expectancy | -10.960 |
| oos_expectancy_ci95 | [-25.552, 4.346] |
| oos_sharpe_ann | -0.573 |
| oos_sharpe_ann_ci95 | [-1.750, 0.444] |
| oos_total_pnl | -2060.572 |
| deflated_sharpe | 0.000 |
| pbo_mean | 0.439 |
| n_trials | 140 |
| folds | 14 |

## Folds

| test window | OOS trades | OOS expectancy | OOS Sharpe | train score | plateau score | PBO |
|---|--:|--:|--:|--:|--:|--:|
| 2023-01-01..2023-04-01 | 11 | 1.193 | 0.223 | -0.029 | -1000000.000 | 0.661 |
| 2023-04-01..2023-07-01 | 4 | -24.964 | -1.968 | 0.031 | -1000000.000 | 0.426 |
| 2023-07-01..2023-10-01 | 40 | -13.393 | -2.679 | -0.684 | -1000000.000 | 0.616 |
| 2023-10-01..2024-01-01 | 8 | -97.594 | -3.825 | 0.249 | -1000000.000 | 0.554 |
| 2024-01-01..2024-04-01 | 2 | 20.560 | 1.851 | -0.772 | -1000000.000 | 0.276 |
| 2024-04-01..2024-07-01 | 2 | -70.035 | -2.558 | 0.528 | -1000000.000 | 0.000 |
| 2024-07-01..2024-10-01 | 13 | -4.118 | -0.173 | 0.625 | -1000000.000 | 0.024 |
| 2024-10-01..2025-01-01 | 38 | -16.026 | -1.036 | -0.848 | -1000000.000 | 0.449 |
| 2025-01-01..2025-04-01 | 5 | 53.603 | 1.382 | -0.349 | -1000000.000 | 0.620 |
| 2025-04-01..2025-07-01 | 1 | -100.190 | -1.958 | 0.453 | -1000000.000 | 0.311 |
| 2025-07-01..2025-10-01 | 33 | -45.448 | -3.723 | 0.171 | -1000000.000 | 0.453 |
| 2025-10-01..2026-01-01 | 8 | 64.049 | 1.272 | 0.359 | -1000000.000 | 0.646 |
| 2026-01-01..2026-04-01 | 12 | 46.968 | 1.996 | -0.940 | -1000000.000 | 0.334 |
| 2026-04-01..2026-07-01 | 11 | 32.735 | 1.620 | -0.467 | -1000000.000 | 0.772 |

## Recommended params (last fold's plateau pick)

```json
{
  "rr": 2.75,
  "stop_rule": "kijun",
  "swing_lookback": 16,
  "atr_mult": 2.0,
  "stop_buffer_pips": 5.0,
  "require_tk_momentum": false,
  "require_chikou": true,
  "require_htf_confluence": true,
  "require_regime": false,
  "min_trend_distance_pips": 2.0,
  "min_tk_separation_pips": 8.0,
  "min_kijun_slope_pips": 2.0,
  "min_cloud_thickness_pips": 4.0,
  "min_cloud_slope_pips": 9.0,
  "quality_min_pass": 4,
  "h1_pullback_band": 0.006,
  "fib_lookback": 30,
  "breakeven_enabled": false,
  "breakeven_trigger_pips": 5.0,
  "breakeven_trigger_r": 2.0,
  "trail_mode": "off",
  "trail_distance_pips": 25.0,
  "partial_exit_pct": 0.5
}
```
