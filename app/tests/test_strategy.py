import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from main import SpreadData


class TestStrategy:
    """Test cases for the Strategy class."""

    def test_initialization(self, strategy, mock_exchanges, strategy_config):
        """Test Strategy initialization."""
        assert strategy.exchange_by_id == mock_exchanges
        assert strategy.open_position_net_spread_threshold == 0.1
        assert strategy.close_position_raw_spread_threshold == 0.02
        assert strategy.close_position_after_seconds == 300
        assert strategy.position_usd_amount == 100.0
        assert strategy.position_leverage == 1
        assert strategy.base_currency == 'USDT'
        assert strategy.positions == {}

    def test_analyze_arbitrage_basic(self, strategy, sample_exchange_data):
        """Test basic arbitrage analysis."""
        spreads = strategy.analyze_arbitrage(sample_exchange_data)

        # Should have 4 spreads (2 markets x 2 exchange pairs)
        assert len(spreads) == 4

        # Check specific spread (buying on binance, selling on bybit for BTC)
        btc_key = ('BTC/USDT:USDT', 'binance', 'bybit')
        assert btc_key in spreads

        btc_spread = spreads[btc_key]
        assert btc_spread.market == 'BTC/USDT:USDT'
        assert btc_spread.buy_exchange_id == 'binance'
        assert btc_spread.sell_exchange_id == 'bybit'
        assert btc_spread.buy_price == 50000.0
        assert btc_spread.sell_price == 50050.0

    def test_analyze_arbitrage_calculations(
        self, strategy, sample_exchange_data
    ):
        """Test arbitrage spread calculations are correct."""
        spreads = strategy.analyze_arbitrage(sample_exchange_data)

        btc_key = ('BTC/USDT:USDT', 'binance', 'bybit')
        btc_spread = spreads[btc_key]

        # Calculate expected values
        price_diff = 50050.0 - 50000.0  # 50
        mid_price = (50050.0 + 50000.0) / 2  # 50025
        expected_raw_spread = (price_diff / mid_price) * 100  # ~0.1%

        # Commission: (0.001 + 0.0015) * 2 * 100 * 1 = 0.5%
        expected_commission = (0.001 + 0.0015) * 2 * 100 * 1
        expected_net_spread = expected_raw_spread - expected_commission

        assert abs(btc_spread.raw_spread - expected_raw_spread) < 0.001
        assert abs(btc_spread.commission - expected_commission) < 0.001
        assert abs(btc_spread.net_spread - expected_net_spread) < 0.001

    def test_analyze_arbitrage_empty_data(self, strategy):
        """Test arbitrage analysis with empty exchange data."""
        spreads = strategy.analyze_arbitrage({})
        assert len(spreads) == 0

    def test_analyze_arbitrage_single_exchange(self, strategy):
        """Test arbitrage analysis with single exchange (should return no spreads)."""
        single_exchange_data = {
            'binance': {
                'BTC/USDT:USDT': {
                    'ask': 50000.0,
                    'bid': 49950.0,
                    'timestamp': 1640995200000,
                }
            }
        }
        spreads = strategy.analyze_arbitrage(single_exchange_data)
        assert len(spreads) == 0

    def test_analyze_arbitrage_no_common_markets(self, strategy):
        """Test arbitrage analysis with no common markets."""
        no_common_data = {
            'binance': {
                'BTC/USDT:USDT': {
                    'ask': 50000.0,
                    'bid': 49950.0,
                    'timestamp': 1640995200000,
                }
            },
            'bybit': {
                'ETH/USDT:USDT': {
                    'ask': 4020.0,
                    'bid': 4015.0,
                    'timestamp': 1640995200100,
                }
            },
        }
        spreads = strategy.analyze_arbitrage(no_common_data)
        assert len(spreads) == 0

    @patch('asyncio.get_running_loop')
    def test_have_to_open_position_true(
        self, mock_loop, strategy, sample_spread_data
    ):
        """Test position opening condition when conditions are met."""
        mock_loop.return_value.time.return_value = (
            1640995200.0  # Current time in seconds
        )

        # High net spread and low time diff - should open
        spread_data = SpreadData(
            market='BTC/USDT:USDT',
            buy_exchange_id='binance',
            buy_price=50000.0,
            sell_exchange_id='bybit',
            sell_price=50050.0,
            raw_spread=0.5,
            commission=0.3,
            net_spread=0.3,  # Above threshold (0.1)
            min_timestamp=300.0,  # Below threshold (350)
        )

        assert strategy.have_to_open_position(spread_data) is True

    @patch('asyncio.get_running_loop')
    def test_have_to_open_position_false_spread(self, mock_loop, strategy):
        """Test position opening condition when net spread is too low."""
        mock_loop.return_value.time.return_value = (
            1640995200.0  # Current time in seconds
        )

        spread_data = SpreadData(
            market='BTC/USDT:USDT',
            buy_exchange_id='binance',
            buy_price=50000.0,
            sell_exchange_id='bybit',
            sell_price=50050.0,
            raw_spread=0.1,
            commission=0.08,
            net_spread=0.02,  # Below threshold (0.1)
            min_timestamp=300.0,
        )

        assert strategy.have_to_open_position(spread_data) is False

    @patch('asyncio.get_running_loop')
    def test_have_to_open_position_false_time(self, mock_loop, strategy):
        """Test position opening condition when time diff is too high."""
        mock_loop.return_value.time.return_value = (
            1640995200.0  # Current time in seconds
        )

        spread_data = SpreadData(
            market='BTC/USDT:USDT',
            buy_exchange_id='binance',
            buy_price=50000.0,
            sell_exchange_id='bybit',
            sell_price=50050.0,
            raw_spread=0.5,
            commission=0.3,
            net_spread=0.2,  # Above threshold
            min_timestamp=600.0,  # Above threshold (500ms)
        )

        assert strategy.have_to_open_position(spread_data) is False

    @patch('asyncio.get_running_loop')
    def test_have_to_close_position_raw_spread(
        self, mock_loop, strategy, mock_exchanges
    ):
        """Test position closing condition based on raw spread."""
        mock_loop.return_value.time.return_value = 1000.0

        # Create a mock position
        position = MagicMock()
        position.opened_at = 500.0  # Well within time limit
        position.market = 'BTC/USDT:USDT'

        # Low raw spread - should close
        spread_data = SpreadData(
            market='BTC/USDT:USDT',
            buy_exchange_id='binance',
            buy_price=50000.0,
            sell_exchange_id='bybit',
            sell_price=50050.0,
            raw_spread=0.01,  # Below threshold (0.02)
            commission=0.05,
            net_spread=-0.04,
            min_timestamp=300.0,
        )

        assert strategy.have_to_close_position(position, spread_data) is True

    @patch('asyncio.get_running_loop')
    def test_have_to_close_position_timeout(
        self, mock_loop, strategy, mock_exchanges
    ):
        """Test position closing condition based on timeout."""
        mock_loop.return_value.time.return_value = 1000.0

        # Create a mock position that should timeout
        position = MagicMock()
        position.opened_at = 600.0  # 1000 - 600 = 400s > 300s threshold
        position.market = 'BTC/USDT:USDT'

        # High raw spread but timeout - should still close
        spread_data = SpreadData(
            market='BTC/USDT:USDT',
            buy_exchange_id='binance',
            buy_price=50000.0,
            sell_exchange_id='bybit',
            sell_price=50050.0,
            raw_spread=0.1,  # Above threshold
            commission=0.05,
            net_spread=0.05,
            min_timestamp=300.0,
        )

        assert strategy.have_to_close_position(position, spread_data) is True

    @patch('asyncio.get_running_loop')
    def test_have_to_close_position_false(
        self, mock_loop, strategy, mock_exchanges
    ):
        """Test position closing condition when should not close."""
        mock_loop.return_value.time.return_value = 1000.0

        # Create a mock position
        position = MagicMock()
        position.opened_at = 800.0  # Recent position
        position.market = 'BTC/USDT:USDT'

        # High raw spread and no timeout - should not close
        spread_data = SpreadData(
            market='BTC/USDT:USDT',
            buy_exchange_id='binance',
            buy_price=50000.0,
            sell_exchange_id='bybit',
            sell_price=50050.0,
            raw_spread=0.1,  # Above threshold
            commission=0.05,
            net_spread=0.05,
            min_timestamp=300.0,
        )

        assert strategy.have_to_close_position(position, spread_data) is False

    @patch('asyncio.get_running_loop')
    def test_have_to_close_position_high_time_diff(
        self, mock_loop, strategy, mock_exchanges
    ):
        """Test position closing condition fails with high time diff."""
        mock_loop.return_value.time.return_value = 1000.0

        position = MagicMock()
        position.opened_at = 600.0  # Should timeout
        position.market = 'BTC/USDT:USDT'

        # Timeout but high time diff - should not close
        spread_data = SpreadData(
            market='BTC/USDT:USDT',
            buy_exchange_id='binance',
            buy_price=50000.0,
            sell_exchange_id='bybit',
            sell_price=50050.0,
            raw_spread=0.01,
            commission=0.05,
            net_spread=-0.04,
            min_timestamp=400.0,  # Above 350ms threshold
        )

        assert strategy.have_to_close_position(position, spread_data) is False

    @pytest.mark.asyncio
    async def test_process_positions_no_spreads(self, strategy):
        """Test process_positions with empty spreads."""
        await strategy.process_positions({})
        assert len(strategy.positions) == 0

    @pytest.mark.asyncio
    async def test_process_positions_close_position(
        self, strategy, mock_exchanges
    ):
        """Test closing positions through process_positions."""
        # Create a mock position
        mock_position = AsyncMock()
        mock_position.key = ('BTC/USDT:USDT', 'binance', 'bybit')
        mock_position.market = 'BTC/USDT:USDT'
        mock_position.close = AsyncMock()

        strategy.positions = {'BTC/USDT:USDT': mock_position}

        # Create spread data that should trigger close
        spread_data = SpreadData(
            market='BTC/USDT:USDT',
            buy_exchange_id='binance',
            buy_price=50000.0,
            sell_exchange_id='bybit',
            sell_price=50050.0,
            raw_spread=0.01,  # Low spread - should close
            commission=0.05,
            net_spread=-0.04,
            min_timestamp=300.0,
        )

        spreads = {mock_position.key: spread_data}

        with patch.object(
            strategy, 'have_to_close_position', return_value=True
        ):
            await strategy.process_positions(spreads)

        # Position should be closed and removed
        mock_position.close.assert_called_once()
        assert 'BTC/USDT:USDT' not in strategy.positions

    @pytest.mark.asyncio
    async def test_process_positions_open_position(
        self, strategy, mock_exchanges
    ):
        """Test opening new positions through process_positions."""
        # Set up sufficient balance for the strategy
        strategy.exchange_balances = {'binance': 1000.0, 'bybit': 1000.0}

        # Create spread data that should trigger open
        spread_data = SpreadData(
            market='BTC/USDT:USDT',
            buy_exchange_id='binance',
            buy_price=50000.0,
            sell_exchange_id='bybit',
            sell_price=50050.0,
            raw_spread=0.5,
            commission=0.3,
            net_spread=0.2,  # High net spread - should open
            min_timestamp=300.0,
        )

        spreads = {spread_data.key: spread_data}

        with (
            patch.object(strategy, 'have_to_open_position', return_value=True),
            patch('main.ArbitragePosition') as mock_position_class,
        ):
            mock_position = AsyncMock()
            mock_position.market = 'BTC/USDT:USDT'
            mock_position.buy_exchange.id = 'binance'
            mock_position.sell_exchange.id = 'bybit'
            mock_position.usd_amount = 100.0
            mock_position.open = AsyncMock(return_value=True)
            mock_position_class.return_value = mock_position

            await strategy.process_positions(spreads)

            # Position should be created and opened
            mock_position.open.assert_called_once()
            assert 'BTC/USDT:USDT' in strategy.positions

    @pytest.mark.asyncio
    async def test_process_positions_insufficient_balance(
        self, strategy, mock_exchanges
    ):
        """Test position opening fails with insufficient balance."""
        # Mock insufficient balance
        for exchange in mock_exchanges.values():
            exchange.fetch_balance.return_value = {
                'free': {'USDT': 50.0}
            }  # Insufficient

        spread_data = SpreadData(
            market='BTC/USDT:USDT',
            buy_exchange_id='binance',
            buy_price=50000.0,
            sell_exchange_id='bybit',
            sell_price=50050.0,
            raw_spread=0.5,
            commission=0.3,
            net_spread=0.2,
            min_timestamp=300.0,
        )

        spreads = {spread_data.key: spread_data}

        with patch.object(
            strategy, 'have_to_open_position', return_value=True
        ):
            await strategy.process_positions(spreads)

        # No position should be opened due to insufficient balance
        assert len(strategy.positions) == 0

    @pytest.mark.asyncio
    async def test_process_feed_basic(self, strategy):
        """Test basic feed processing."""
        # Mock the analyze_arbitrage method
        mock_spreads = {
            ('BTC/USDT:USDT', 'binance', 'bybit'): SpreadData(
                market='BTC/USDT:USDT',
                buy_exchange_id='binance',
                buy_price=50000.0,
                sell_exchange_id='bybit',
                sell_price=50050.0,
                raw_spread=0.1,
                commission=0.05,
                net_spread=0.05,
                min_timestamp=300.0,
            )
        }

        feed_data = {
            'feed': {
                'binance': {
                    'BTC/USDT:USDT': {
                        'ask': 50000.0,
                        'bid': 49950.0,
                        'timestamp': 1640995200000,
                    }
                },
                'bybit': {
                    'BTC/USDT:USDT': {
                        'ask': 50100.0,
                        'bid': 50050.0,
                        'timestamp': 1640995200100,
                    }
                },
            },
            'changed_markets': {'BTC/USDT:USDT'},
        }

        # Put data in queue
        await strategy.feed_queue.put(feed_data)

        with (
            patch.object(
                strategy, 'analyze_arbitrage', return_value=mock_spreads
            ),
            patch.object(
                strategy, 'process_positions'
            ) as mock_process_positions,
        ):
            # Process one iteration
            feed_task = asyncio.create_task(strategy.process_feed())

            # Give it a moment to process
            await asyncio.sleep(0.1)

            # Cancel the task
            feed_task.cancel()

            with contextlib.suppress(asyncio.CancelledError):
                await feed_task

            # Verify process_positions was called
            mock_process_positions.assert_called_once_with(mock_spreads)

    def test_spread_data_key_property(self):
        """Test SpreadData key property."""
        spread = SpreadData(
            market='BTC/USDT:USDT',
            buy_exchange_id='binance',
            buy_price=50000.0,
            sell_exchange_id='bybit',
            sell_price=50050.0,
            raw_spread=0.1,
            commission=0.05,
            net_spread=0.05,
            min_timestamp=300.0,
        )

        expected_key = ('BTC/USDT:USDT', 'binance', 'bybit')
        assert spread.key == expected_key

    @pytest.mark.asyncio
    async def test_work_method(self, strategy):
        """Test Strategy work method can be called."""
        # Simply test that the method exists and runs briefly
        work_task = None
        try:
            work_task = asyncio.create_task(strategy.work())
            await asyncio.sleep(0.01)  # Let it start briefly
        finally:
            if work_task:
                work_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await work_task

        # Just verify the method exists and is callable
        assert hasattr(strategy, 'work')
        assert callable(strategy.work)
