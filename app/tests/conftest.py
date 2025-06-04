import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from main import SpreadData, Strategy


@pytest.fixture
def mock_exchange():
    """Create a mock exchange for testing."""
    exchange = AsyncMock()
    exchange.id = 'binance'
    exchange.markets = {
        'BTC/USDT:USDT': {'taker': 0.001, 'limits': {'cost': {'min': 5}}},
        'ETH/USDT:USDT': {'taker': 0.001, 'limits': {'cost': {'min': 5}}},
    }
    exchange.amount_to_precision = MagicMock(
        side_effect=lambda market, amount: amount
    )
    exchange.fetch_balance = AsyncMock(return_value={'free': {'USDT': 1000.0}})
    return exchange


@pytest.fixture
def mock_exchanges(mock_exchange):
    """Create multiple mock exchanges."""
    exchange1 = AsyncMock()
    exchange1.id = 'binance'
    exchange1.markets = {
        'BTC/USDT:USDT': {'taker': 0.001},
        'ETH/USDT:USDT': {'taker': 0.001},
    }
    exchange1.amount_to_precision = MagicMock(
        side_effect=lambda market, amount: amount
    )
    exchange1.fetch_balance = AsyncMock(
        return_value={'free': {'USDT': 1000.0}}
    )

    exchange2 = AsyncMock()
    exchange2.id = 'bybit'
    exchange2.markets = {
        'BTC/USDT:USDT': {'taker': 0.0015},
        'ETH/USDT:USDT': {'taker': 0.0015},
    }
    exchange2.amount_to_precision = MagicMock(
        side_effect=lambda market, amount: amount
    )
    exchange2.fetch_balance = AsyncMock(
        return_value={'free': {'USDT': 1000.0}}
    )

    return {'binance': exchange1, 'bybit': exchange2}


@pytest.fixture
def strategy_config():
    """Basic strategy configuration."""
    return {
        'open_position_net_spread_threshold': 0.1,
        'close_position_raw_spread_threshold': 0.02,
        'close_position_after_seconds': 300,
        'position_usd_amount': 100.0,
        'position_leverage': 1,
        'base_currency': 'USDT',
    }


@pytest.fixture
def strategy(mock_exchanges, strategy_config):
    """Create a Strategy instance for testing."""
    feed_queue = asyncio.Queue()
    render_queue = asyncio.Queue()

    return Strategy(
        feed_queue=feed_queue,
        render_queue=render_queue,
        exchange_by_id=mock_exchanges,
        **strategy_config,
    )


@pytest.fixture
def sample_exchange_data():
    """Sample exchange data for testing arbitrage analysis."""
    return {
        'binance': {
            'BTC/USDT:USDT': {
                'ask': 50000.0,
                'bid': 49950.0,
                'timestamp': 1640995200000,
            },
            'ETH/USDT:USDT': {
                'ask': 4000.0,
                'bid': 3995.0,
                'timestamp': 1640995200000,
            },
        },
        'bybit': {
            'BTC/USDT:USDT': {
                'ask': 50100.0,
                'bid': 50050.0,
                'timestamp': 1640995200100,
            },
            'ETH/USDT:USDT': {
                'ask': 4020.0,
                'bid': 4015.0,
                'timestamp': 1640995200100,
            },
        },
    }


@pytest.fixture
def sample_spread_data():
    """Sample SpreadData for testing."""
    return SpreadData(
        market='BTC/USDT:USDT',
        buy_exchange_id='binance',
        buy_price=50000.0,
        sell_exchange_id='bybit',
        sell_price=50050.0,
        raw_spread=0.1,
        commission=0.05,
        net_spread=0.05,
        min_timestamp=100.0,
    )
