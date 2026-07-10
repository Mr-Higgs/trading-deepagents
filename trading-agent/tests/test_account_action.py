"""account_action: tradeSpecifier resolution, price precision, and input validation (no network)."""

from trading_agent.account_action_graph import _px, _spec, act


def test_spec_numeric_ticket_vs_client_id():
    assert _spec("71") == "71"  # OANDA ticket used as-is
    assert _spec("@abc") == "@abc"  # already a client-id specifier
    assert _spec("AUD_USD-20260709T001500Z-t1") == "@AUD_USD-20260709T001500Z-t1"  # our client id → @prefixed


def test_px_precision_by_instrument():
    assert _px(0.69263, "AUD_USD") == "0.69263"  # 5dp for non-JPY
    assert _px(157.1, "USD_JPY") == "157.100"  # 3dp for JPY


def test_act_validates_before_network():
    # missing action/trade_id and modify-without-levels short-circuit before any OANDA credential is read
    assert act({})["error"]
    assert act({"action": "modify", "trade_id": "71"})["error"] == "modify requires stop and/or tp"
    assert act({"action": "frobnicate", "trade_id": "71"})["ok"] is False
