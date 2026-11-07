# Walk-forward optimization: AUD_USD ichimoku_pullback

Range 2022-01-01 .. 2026-07-01 (holdout from 2026-07-01 untouched), rolling 12m train / 3m test, objective `sharpe`, 140 trials.

## Concatenated out-of-sample

| metric | value |
|---|--:|
| oos_trades | 198 |
| oos_expectancy | -7.323 |
| oos_expectancy_ci95 | [-15.416, 0.989] |
| oos_sharpe_ann | -0.805 |
| oos_sharpe_ann_ci95 | [-1.761, 0.190] |
| oos_total_pnl | -1449.969 |
| deflated_sharpe | 0.000 |
| pbo_mean | 0.519 |
| n_trials | 140 |
| folds | 14 |

## Folds

| test window | OOS trades | OOS expectancy | OOS Sharpe | train score | plateau score | PBO |
|---|--:|--:|--:|--:|--:|--:|
| 2023-01-01..2023-04-01 | 6 | 9.198 | 2.147 | -0.120 | -1000000.000 | 0.805 |
| 2023-04-01..2023-07-01 | 2 | 0.182 | 2.550 | 0.493 | -1000000.000 | 0.739 |
| 2023-07-01..2023-10-01 | 2 | -100.494 | -2.832 | 0.379 | -1000000.000 | 0.636 |
| 2023-10-01..2024-01-01 | 3 | -101.832 | -2.722 | 0.019 | -1000000.000 | 0.669 |
| 2024-01-01..2024-04-01 | 0 | 0.000 | 0.000 | -0.729 | -1000000.000 | 0.344 |
| 2024-04-01..2024-07-01 | 1 | 1.621 | 1.954 | 0.296 | -1000000.000 | 0.465 |
| 2024-07-01..2024-10-01 | 10 | -10.007 | -1.939 | 0.679 | -1000000.000 | 0.262 |
| 2024-10-01..2025-01-01 | 42 | 10.216 | 1.717 | 0.859 | -1000000.000 | 0.587 |
| 2025-01-01..2025-04-01 | 9 | -52.515 | -3.544 | 0.488 | -1000000.000 | 0.169 |
| 2025-04-01..2025-07-01 | 3 | -99.567 | -2.646 | 0.692 | -1000000.000 | 0.458 |
| 2025-07-01..2025-10-01 | 39 | -17.272 | -2.914 | 0.693 | -1000000.000 | 0.703 |
| 2025-10-01..2026-01-01 | 5 | -31.849 | -2.054 | 0.384 | -1000000.000 | 0.876 |
| 2026-01-01..2026-04-01 | 74 | 3.549 | 1.098 | 1.021 | -1000000.000 | 0.262 |
| 2026-04-01..2026-07-01 | 2 | 5.954 | 1.550 | 0.066 | -1000000.000 | 0.292 |

## Recommended params (last fold's plateau pick)

```json
{
  "rr": 3.5,
  "stop_rule": "swing",
  "swing_lookback": 10,
  "atr_mult": 2.5,
  "stop_buffer_pips": 5.0,
  "require_tk_momentum": false,
  "require_chikou": true,
  "require_htf_confluence": false,
  "require_regime": false,
  "min_trend_distance_pips": 16.0,
  "min_tk_separation_pips": 9.0,
  "min_kijun_slope_pips": 9.0,
  "min_cloud_thickness_pips": 0.0,
  "min_cloud_slope_pips": 9.0,
  "quality_min_pass": 4,
  "h1_pullback_band": 0.002,
  "fib_lookback": 70,
  "breakeven_enabled": true,
  "breakeven_trigger_pips": 15.0,
  "breakeven_trigger_r": 1.75,
  "trail_mode": "kijun",
  "trail_distance_pips": 22.5,
  "partial_exit_pct": 0.75
}
```
