import asyncio
import contextlib

import pytest
from modules.data_feed import DataFeed
from settings import BotConfig, PositionConfig


@pytest.mark.asyncio
async def test_collect_feed_skips_high_slippage():
    config = BotConfig(
        max_slippage_pct=5.0,
        position=PositionConfig(usd_amount=100, leverage=1),
    )
    feed_queue = asyncio.Queue()
    data_feed = DataFeed(
        exchanges=[], markets=[], feed_queue=feed_queue, config=config
    )

    await data_feed.collect_queue.put(
        {
            'exchange': 'binance',
            'market': 'BTC/USDT:USDT',
            'bids': [(100, 0.3), (110, 0.7)],
            'asks': [(101, 0.3), (111, 0.7)],
            'timestamp': 1,
        }
    )

    task = asyncio.create_task(data_feed.collect_feed())
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert feed_queue.empty()


@pytest.mark.asyncio
async def test_collect_feed_accepts_low_slippage():
    config = BotConfig(
        max_slippage_pct=10.0,
        position=PositionConfig(usd_amount=100, leverage=1),
    )
    feed_queue = asyncio.Queue()
    data_feed = DataFeed(
        exchanges=[], markets=[], feed_queue=feed_queue, config=config
    )

    await data_feed.collect_queue.put(
        {
            'exchange': 'binance',
            'market': 'BTC/USDT:USDT',
            'bids': [(100, 1.0)],
            'asks': [(101, 1.0)],
            'timestamp': 1,
        }
    )

    task = asyncio.create_task(data_feed.collect_feed())
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert not feed_queue.empty()


@pytest.mark.asyncio
async def test_collect_feed_skips_low_liquidity():
    config = BotConfig(
        max_slippage_pct=10.0,
        position=PositionConfig(usd_amount=1000, leverage=1),
    )
    feed_queue = asyncio.Queue()
    data_feed = DataFeed(
        exchanges=[], markets=[], feed_queue=feed_queue, config=config
    )

    await data_feed.collect_queue.put(
        {
            'exchange': 'binance',
            'market': 'BTC/USDT:USDT',
            'bids': [(100, 0.5)],
            'asks': [(101, 0.5)],
            'timestamp': 1,
        }
    )

    task = asyncio.create_task(data_feed.collect_feed())
    await asyncio.sleep(0.05)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert feed_queue.empty()
