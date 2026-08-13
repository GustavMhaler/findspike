import json
from datetime import timezone
from unittest import mock

import pytest

from shanzhai.binance_api import BinancePublicClient

SAMPLE_KLINE = [
    1704067200000,  # open time ms
    "100.0",  # open
    "105.0",  # high
    "99.0",  # low
    "104.0",  # close
    "1234.5",  # volume
    1704067199999,  # close time ms
    "128388.0",  # quote volume
    42,  # trades
    "600.0",  # taker buy base
    "62400.0",  # taker buy quote
    "0",  # ignore
]


def _fake_urlopen(payload):
    response = mock.Mock()
    response.__enter__ = mock.Mock(return_value=response)
    response.__exit__ = mock.Mock(return_value=False)
    response.read = mock.Mock(return_value=json.dumps(payload).encode())
    return response


class TestBinanceClient:
    def test_candles_parses_binance_kline(self):
        with mock.patch("shanzhai.binance_api.urllib.request.urlopen", return_value=_fake_urlopen([SAMPLE_KLINE])):
            candles = BinancePublicClient().candles("BTCUSDT", "1d", 1)
        assert len(candles) == 1
        c = candles[0]
        assert c.open_time.tzinfo == timezone.utc
        assert c.close_time.tzinfo == timezone.utc
        assert c.open == 100.0 and c.high == 105.0 and c.low == 99.0 and c.close == 104.0
        assert c.volume == 1234.5 and c.quote_volume == 128388.0

    def test_usdt_symbols_filters(self):
        info = {
            "symbols": [
                {"symbol": "BTCUSDT", "status": "TRADING", "quoteAsset": "USDT", "isSpotTradingAllowed": True},
                {"symbol": "BTCBTC", "status": "TRADING", "quoteAsset": "BTC", "isSpotTradingAllowed": True},
                {"symbol": "ETHUSDT", "status": "BREAK", "quoteAsset": "USDT", "isSpotTradingAllowed": True},
                {"symbol": "XRPUSDT", "status": "TRADING", "quoteAsset": "USDT", "isSpotTradingAllowed": False},
                {"symbol": "SOLUSDT", "status": "TRADING", "quoteAsset": "USDT", "isSpotTradingAllowed": True},
            ]
        }
        with mock.patch("shanzhai.binance_api.urllib.request.urlopen", return_value=_fake_urlopen(info)):
            symbols = BinancePublicClient().usdt_symbols()
        assert symbols == ["BTCUSDT", "SOLUSDT"]

    def test_retries_then_raises(self):
        with mock.patch("shanzhai.binance_api.time.sleep") as sleep, mock.patch(
            "shanzhai.binance_api.urllib.request.urlopen", side_effect=ConnectionError("boom")
        ):
            with pytest.raises(RuntimeError, match="Binance request failed"):
                BinancePublicClient(timeout=1).candles("BTCUSDT", "1d", 1)
        assert sleep.call_count == 2

    def test_succeeds_after_retry(self):
        failing = mock.Mock(side_effect=ConnectionError("boom"))
        ok = _fake_urlopen([SAMPLE_KLINE])

        def flaky(*args, **kwargs):
            if failing.side_effect:
                failing.side_effect = None
                raise ConnectionError("boom")
            return ok

        with mock.patch("shanzhai.binance_api.urllib.request.urlopen", side_effect=flaky):
            candles = BinancePublicClient(timeout=1).candles("BTCUSDT", "1d", 1)
        assert len(candles) == 1
