// Plain-language explanations for the trading jargon the UI shows. One place to edit the copy.
// ponytail: native `title` tooltips (hover / long-press); swap for the Radix tooltip if styling matters.
import type { ReactNode } from "react";

export const GLOSSARY = {
  tenkan: "Tenkan-sen (conversion line): midpoint of the high/low over the last N bars (default 9). The fast Ichimoku line.",
  kijun: "Kijun-sen (base line): midpoint of the high/low over the last N bars (default 26). The slow line; often used for stops.",
  senkou_b: "Senkou Span B: midpoint of the high/low over the last N bars (default 52), shifted forward. One edge of the cloud.",
  displacement: "How many bars the cloud is shifted forward (and Chikou backward). Default 26.",
  chikou: "Chikou Span (lagging line): today's close plotted 26 bars back. Confirms trend when it is clear of past price.",
  pullback_band: "How close (in price) the H1 candle must come back to the Kijun line to count as a pullback. 0.0010 = 10 pips on most pairs.",
  min_trend_distance: "Minimum gap, in pips, between price and the cloud on the trend timeframe before a trend counts.",
  stop_buffer: "Extra pips placed beyond the stop level so normal noise does not stop the trade out.",
  rr: "Reward-to-risk: how far the take-profit is, as a multiple of the stop distance. 2 = target is twice the risk.",
  gate: "The LLM's verdict on this signal: approve, veto or modify, with its confidence. Hover the verdict for its reasoning.",
  is: "In-sample: the older slice of history the agent is allowed to look at while tuning.",
  oos: "Out-of-sample: a later slice of history held back for testing, so a change has to work on data it was not tuned on.",
  expectancy: "Average profit or loss per trade. Above zero means the strategy made money on average.",
  pf: "Profit factor: gross profit divided by gross loss. Above 1 is profitable; above 1.5 is solid.",
  r: "R = one unit of risk (the stop distance). +2R means the trade made twice what it risked.",
} as const;

export function Term({ k, children }: { k: keyof typeof GLOSSARY; children: ReactNode }) {
  return (
    <span title={GLOSSARY[k]} className="cursor-help underline decoration-muted-foreground/50 decoration-dotted underline-offset-2">
      {children}
    </span>
  );
}
