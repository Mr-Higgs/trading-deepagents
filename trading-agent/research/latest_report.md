# Backtest analysis — loop-2025-01-01-2026-01-01 (v2.0.0)

| metric | value |
|---|--:|
| trades | 306 |
| **expectancy** ($/trade) | **-20.47** |
| profit factor | 0.34 |
| win rate | 51.3% |
| avg win (R) | 0.34 |
| avg loss (R) | -1.11 |
| total P/L | -6264.48 |
| max drawdown | 6334.05 |
| P/L ÷ drawdown | -0.99 |

**MAE / MFE (avg R while open)**

| | MAE (adverse) | MFE (favorable) |
|---|--:|--:|
| winners | 0.47 | 1.80 |
| losers | 1.10 | 0.54 |

**By setup**

| bucket | n | win% | expectancy | total P/L |
|---|--:|--:|--:|--:|
| ichimoku_breakout | 306 | 51% | -20.47 | -6264.48 |

**By exit reason**

| bucket | n | win% | expectancy | total P/L |
|---|--:|--:|--:|--:|
| STOP | 277 | 45% | -31.73 | -8788.11 |
| TP | 29 | 100% | 87.02 | 2523.62 |

**By direction**

| bucket | n | win% | expectancy | total P/L |
|---|--:|--:|--:|--:|
| LONG | 175 | 49% | -25.04 | -4381.69 |
| SHORT | 131 | 55% | -14.37 | -1882.79 |

**By hour (UTC, entry)**

| bucket | n | win% | expectancy | total P/L |
|---|--:|--:|--:|--:|
| 00h | 28 | 54% | -24.82 | -694.83 |
| 01h | 24 | 57% | -23.28 | -558.68 |
| 02h | 24 | 63% | -7.97 | -191.20 |
| 03h | 36 | 56% | -16.81 | -605.25 |
| 04h | 19 | 39% | -36.62 | -695.86 |
| 05h | 16 | 36% | -26.95 | -431.20 |
| 06h | 12 | 100% | 15.34 | 184.11 |
| 07h | 14 | 42% | -12.56 | -175.82 |
| 08h | 20 | 32% | -45.49 | -909.77 |
| 09h | 20 | 41% | -36.20 | -723.97 |
| 10h | 16 | 43% | -34.83 | -557.36 |
| 11h | 17 | 38% | -21.84 | -371.21 |
| 12h | 12 | 20% | -40.81 | -489.77 |
| 13h | 17 | 77% | 16.69 | 283.70 |
| 14h | 19 | 69% | -13.23 | -251.41 |
| 15h | 12 | 64% | -6.33 | -75.98 |

**Why candidates were dropped (SKIP/REJECT/HALT)**

- no_tk_cross: 13119
- session_closed: 8785
- in_cloud: 2346
- daily_loss_breached: 166
- max_concurrent: 69
- insufficient_history: 64
- below_min_stop: 8
- max_trades_reached: 6
