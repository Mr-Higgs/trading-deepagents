# Walk-forward optimization: AUD_USD ichimoku_breakout

Range 2022-01-01 .. 2026-07-01 (holdout from 2026-07-01 untouched), rolling 12m train / 3m test, objective `sharpe`, 140 trials.

## Concatenated out-of-sample

| metric | value |
|---|--:|
| oos_trades | 13 |
| oos_expectancy | 53.071 |
| oos_expectancy_ci95 | [-29.299, 146.801] |
| oos_sharpe_ann | 0.600 |
| oos_sharpe_ann_ci95 | [-0.632, 1.227] |
| oos_total_pnl | 689.917 |
| deflated_sharpe | 0.000 |
| pbo_mean | 0.429 |
| n_trials | 140 |
| folds | 14 |

## Folds

| test window | OOS trades | OOS expectancy | OOS Sharpe | train score | plateau score | PBO |
|---|--:|--:|--:|--:|--:|--:|
| 2023-01-01..2023-04-01 | 0 | 0.000 | 0.000 | 0.030 | -1000000.000 | 0.276 |
| 2023-04-01..2023-07-01 | 4 | 214.113 | 3.001 | 0.539 | -1000000.000 | 0.096 |
| 2023-07-01..2023-10-01 | 0 | 0.000 | 0.000 | 0.009 | -1000000.000 | 0.430 |
| 2023-10-01..2024-01-01 | 0 | 0.000 | 0.000 | 0.028 | -1000000.000 | 0.937 |
| 2024-01-01..2024-04-01 | 0 | 0.000 | 0.000 | -1.951 | -1000000.000 | 0.306 |
| 2024-04-01..2024-07-01 | 0 | 0.000 | 0.000 | 0.033 | -1000000.000 | 0.500 |
| 2024-07-01..2024-10-01 | 4 | -45.150 | -1.807 | 0.316 | -1000000.000 | 0.304 |
| 2024-10-01..2025-01-01 | 3 | 1.716 | 3.320 | 0.319 | -1000000.000 | 0.440 |
| 2025-01-01..2025-04-01 | 0 | 0.000 | 0.000 | -0.736 | -1000000.000 | 0.577 |
| 2025-04-01..2025-07-01 | 0 | 0.000 | 0.000 | -1000000.000 | -1000000.000 | n/a |
| 2025-07-01..2025-10-01 | 0 | 0.000 | 0.000 | 0.048 | -1000000.000 | 0.780 |
| 2025-10-01..2026-01-01 | 2 | 4.460 | 2.780 | 0.011 | -1000000.000 | 0.688 |
| 2026-01-01..2026-04-01 | 0 | 0.000 | 0.000 | -1.949 | -1000000.000 | 0.229 |
| 2026-04-01..2026-07-01 | 0 | 0.000 | 0.000 | 0.033 | -1000000.000 | 0.013 |

## Recommended params (last fold's plateau pick)

```json
{
  "rr": 3.75,
  "stop_rule": "swing",
  "swing_lookback": 12,
  "atr_mult": 2.25,
  "stop_buffer_pips": 7.0,
  "require_tk_momentum": true,
  "require_chikou": true,
  "require_htf_confluence": false,
  "require_regime": true,
  "min_trend_distance_pips": 10.0,
  "min_tk_separation_pips": 7.0,
  "min_kijun_slope_pips": 1.0,
  "min_cloud_thickness_pips": 16.0,
  "min_cloud_slope_pips": 7.0,
  "quality_min_pass": 3,
  "breakout_trigger": "tk_cross",
  "breakout_require_pattern": false,
  "breakeven_enabled": true,
  "breakeven_trigger_pips": 10.0,
  "breakeven_trigger_r": 1.5,
  "trail_mode": "kijun",
  "trail_distance_pips": 5.0,
  "partial_exit_pct": 0.25
}
```
