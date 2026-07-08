//@version=5
strategy(title="WFX Refined Ichimoku Strategy", shorttitle="WFX", overlay=true, precision=2)

// Ichimoku Cloud Inputs
conversionPeriods = input.int(9, minval=1, title="Conversion Line Length")
basePeriods = input.int(26, minval=1, title="Base Line Length")
laggingSpan2Periods = input.int(52, minval=1, title="Lagging Span B Length")

// ATR Inputs
atrLength = input.int(14, title="ATR Length")
atrMultiplier = input.float(2.75, title="ATR Multiplier", step=0.1)

// Supertrend Inputs
superTrendATRPeriod = input.int(7, title="SuperTrend ATR Period")
superTrendMultiplier = input.float(2.75, title="SuperTrend Multiplier", step=0.1)

// Ichimoku Cloud Calculation using Donchian Channels
donchian(len) => math.avg(ta.lowest(len), ta.highest(len))
conversionLine = donchian(conversionPeriods)
baseLine = donchian(basePeriods)
leadLine1 = math.avg(conversionLine, baseLine)
leadLine2 = donchian(laggingSpan2Periods)
chikouSpan = close[basePeriods]

// Determine Cloud Location
aboveCloud = close > math.max(leadLine1, leadLine2)
belowCloud = close < math.min(leadLine1, leadLine2)
inCloud = not aboveCloud and not belowCloud

// SuperTrend Calculation
[__, trend] = ta.supertrend(superTrendMultiplier, superTrendATRPeriod)
longBuySignal = ta.change(trend) < 0
shortSellSignal = ta.change(trend) > 0

// ATR-based Risk Management
atrValue = ta.atr(atrLength)
stopLossDistance = atrValue * atrMultiplier
takeProfitDistance = stopLossDistance * 2.55

// Stop Loss and Take Profit Calculation
longStopLoss = strategy.position_avg_price - stopLossDistance
longTakeProfit = strategy.position_avg_price + takeProfitDistance
shortStopLoss = strategy.position_avg_price + stopLossDistance
shortTakeProfit = strategy.position_avg_price - takeProfitDistance

// Buy and Sell Signals based on Ichimoku Crossovers (moved outside of `if` statements)
buySignal = ta.crossover(conversionLine, baseLine) and aboveCloud
sellSignal = ta.crossunder(conversionLine, baseLine) and belowCloud

// Exit Conditions based on Ichimoku Crosses
exitLongSignal = ta.crossunder(close, baseLine) or ta.crossunder(conversionLine, baseLine)
exitShortSignal = ta.crossover(close, baseLine) or ta.crossover(conversionLine, baseLine)

// Entry Conditions
if buySignal
    strategy.entry("Long", strategy.long)

if sellSignal
    strategy.entry("Short", strategy.short)

// Exit Conditions
if strategy.position_size > 0 // Long Position
    strategy.exit("Exit Long", "Long", stop=longStopLoss, limit=longTakeProfit)
    if exitLongSignal
        strategy.close("Long", comment="Exit by Ichimoku Cross")

if strategy.position_size < 0 // Short Position
    strategy.exit("Exit Short", "Short", stop=shortStopLoss, limit=shortTakeProfit)
    if exitShortSignal
        strategy.close("Short", comment="Exit by Ichimoku Cross")


// Plotting Signals for Visual Confirmation
// plotshape(series=buySignal, title="Buy Signal", location=location.belowbar, color=color.green, style=shape.triangleup, size=size.small)
// plotshape(series=sellSignal, title="Sell Signal", location=location.abovebar, color=color.red, style=shape.triangledown, size=size.small)
plotshape(series=longBuySignal, title="SuperTrend Buy", location=location.belowbar, color=color.green, style=shape.labelup, size=size.small, text="SuperTrend Buy")
plotshape(series=shortSellSignal, title="SuperTrend Sell", location=location.abovebar, color=color.red, style=shape.labeldown, size=size.small, text="SuperTrend Sell")


