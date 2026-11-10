# Disclaimer

**Please read this before using anything in this repository.**

## Educational purposes only

This project is built and shared **for educational and research purposes only**. It is an experiment in
deterministic trading systems and LLM agents. It is not a product, a service, or a recommendation to trade.

## Not financial advice

I am **not a financial advisor**, and nothing in this repository (code, configuration, strategies, backtest
results, documentation, or agent output) is financial, investment, legal, or tax advice. Talk to a licensed
professional before making any financial decision.

## You take the risk

Trading foreign exchange and other leveraged products carries a **high risk of loss**, including losing more
than you put in. Backtests and paper-trading results are simulations and **do not guarantee future results**.

If you use, modify, or run this software, **you do so entirely at your own risk and of your own accord**. The
author accepts **no liability** for any financial loss, damage, or other consequence from using it.

The software is provided **"as is", without warranty of any kind** (see [`LICENSE`](LICENSE)).

## Runs on your own device; no malicious code

- This repository is designed to **run locally on your own machine**. Nothing is hosted for you.
- It contains **no malicious code, malware, or ransomware**. All source code is open, so please read it
  before you run it.
- You supply your own API keys. They stay in your local `.env` file and are sent only to the third-party APIs
  you configure (for example OANDA and Anthropic).
- By default the trading code targets OANDA **practice (demo) accounts only**. A built-in check
  (`trading-agent/trading_agent/oanda_env.py`) refuses to run against a live account.
