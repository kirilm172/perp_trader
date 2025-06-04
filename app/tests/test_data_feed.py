import asyncio
import contextlib
from unittest.mock import AsyncMock

import pytest
from modules.data_feed import DataFeed
from settings import BotConfig


@pytest.mark.asyncio
async def test_watch_orderbook_latency(monkeypatch):
    fake_loop_time = 0.0

    class FakeLoop:
        def time(self):
            return fake_loop_time

    fake_loop = FakeLoop()
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: fake_loop)

    exchange = AsyncMock()
    exchange.id = "binance"
    exchange.close = AsyncMock()

    call_idx = 0

    async def side_effect(market, depth):
        nonlocal fake_loop_time, call_idx
        if call_idx == 0:
            fake_loop_time = 0.05
            call_idx += 1
            return {"bids": [[1, 1]], "asks": [[2, 1]], "symbol": market, "timestamp": 1}
        elif call_idx == 1:
            fake_loop_time = 0.25
            call_idx += 1
            return {"bids": [[1, 1]], "asks": [[2, 1]], "symbol": market, "timestamp": 2}
        else:
            raise asyncio.CancelledError

    exchange.watch_order_book.side_effect = side_effect

    config = BotConfig(ws_latency_threshold=0.1, orderbook_depths={"binance": 5})
    data_feed = DataFeed([exchange], ["BTC/USDT:USDT"], asyncio.Queue(), config)

    logged = []
    monkeypatch.setattr(data_feed, "collect_queue", asyncio.Queue())
    monkeypatch.setattr(data_feed.config, "data_feed_retry_seconds", 0)
    monkeypatch.setattr(data_feed.config, "balance_fetch_interval_seconds", 0)
    monkeypatch.setattr(data_feed.config, "status_report_interval_seconds", 0)

    monkeypatch.setattr(
        __import__('settings').console,
        "log",
        lambda msg: logged.append(msg),
    )

    task = asyncio.create_task(
        data_feed.watch_orderbook(exchange, "BTC/USDT:USDT")
    )
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, 0.1)

    assert exchange.close.called
    assert any("WS latency" in msg for msg in logged)
