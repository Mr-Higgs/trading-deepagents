# Hypotheses backlog (strategy-config levers the loop can tune)

Seeded from the first analysis (2025 in-sample, ichimoku_breakout v2.0.0: 51% win rate but -20.47
expectancy — losers give back ~1.1R, winners bank ~0.34R; only 29/306 reached the 3R target).

- rr is too far: only ~9% of trades reach the 3R TP. Test rr in {2.0, 2.5} (risk.yaml min_rr=2.0 rejects anything below 2.0 -> 0 trades).
- Stops are wide (atr_mult 2.75): test atr_mult in {1.5, 2.0} or stop_rule "cloud"/"kijun".
- Asymmetry (win 0.34R / loss 1.1R): a nearer TP or tighter stop should rebalance expectancy.
- Trend filter: raise min_trend_distance_pips to skip thin-cloud chop.
- Momentum quality: try require_tk_momentum true / require_chikou true to cut weak entries.
- Try the new cloud-breakout retest setup: name "ichimoku_cloud_breakout" (tune retest_band_pips, breakout_lookback).
- (Manual lever, not loop-tunable yet: breakeven / scale-out in risk.yaml — MFE shows losers reach 0.54R in favor first.)
