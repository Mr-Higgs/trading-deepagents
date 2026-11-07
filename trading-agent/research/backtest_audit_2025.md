# 2025 backtest audit: logic fixes + top-down HTF confluence

AUD_USD, 2025-01-01 .. 2026-01-01, OANDA practice BA M15 entries, $10,000 start,
`intraday` stack H1/M15/M15 except the H4/H1/M15 rows (now the default; H4 and H1 both gate confluence), deterministic, LLM gate off. `python -m trading_agent.run backtest --start 2025-01-01 --end 2026-01-01`.

| strategy | run | trades | net P/L | avg R | TP exits avg R |
|---|---|--:|--:|--:|--:|
| pullback | before (main) | 560 | -$7,739 | -0.26 | 0.99 |
| pullback | fixes only | 422 | -$3,504 | -0.10 | - |
| pullback | fixes + HTF confluence | 347 | -$3,182 | -0.10 | 1.81 |
| pullback | fixes + HTF confluence, H4/H1/M15 stack | 281 | -$3,243 | - | - |
| breakout | before (main) | 379 | -$7,231 | -0.34 | 1.04 |
| breakout | fixes only | 126 | -$1,191 | -0.10 | 1.77 |
| breakout | fixes + HTF confluence | 81 | -$1,238 | -0.16 | 1.77 |
| breakout | fixes + HTF confluence, H4/H1/M15 stack | 42 | -$1,371 | - | - |

Days losing more than 3%: before 22 (pullback) / 11 (breakout); after 3 at 3.01-3.02% (rounding) / 0.

Fixes: scale-out legs need a level >= `min_leg_r` (1R) beyond entry; the breakeven stop sits `breakeven_buffer_pips`
(1.0) past the fill; sizing and the `min_rr` floor include `sizing_cost_pips` (1.5); the daily loss cap budgets open
stop risk; backtests liquidate positions open at the last bar; a missing news calendar warns.
HTF confluence (`require_htf_confluence`): on every timeframe above entry, Tenkan/Kijun and close/Kijun agree with the
trade. Both strategies still lose money in 2025.
