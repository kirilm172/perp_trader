import asyncio
import time
from itertools import product

from ccxt.async_support.base.exchange import Exchange
from settings import BotConfig, console

from .arbitrage_position import ArbitragePosition
from .base import BaseModule
from .data_feed import SpreadData


class Strategy(BaseModule):
    name = 'Strategy'

    def __init__(
        self,
        feed_queue: asyncio.Queue,
        render_queue: asyncio.Queue,
        exchange_by_id: dict[str, Exchange],
        config: BotConfig,
    ):
        self.feed_queue = feed_queue
        self.render_queue = render_queue
        self.exchange_by_id = exchange_by_id
        self.config = config

        self.positions: dict[str, ArbitragePosition] = {}
        self.exchange_balances: dict[str, float] = {}
        self.funding_rates: dict[str, dict[str, float]] = {}

        # Cache for performance optimization
        self._commission_cache: dict[tuple[str, str, str], float] = {}

    def get_cached_commission(
        self, buy_exchange_id: str, sell_exchange_id: str, market: str
    ) -> float:
        """Get cached commission or calculate and cache it"""
        cache_key = (buy_exchange_id, sell_exchange_id, market)
        if cache_key not in self._commission_cache:
            exchange1 = self.exchange_by_id[buy_exchange_id]
            exchange2 = self.exchange_by_id[sell_exchange_id]
            commission = (
                # open
                exchange1.markets[market]['taker']
                + exchange2.markets[market]['taker']
                # close
                + exchange1.markets[market]['taker']
                + exchange2.markets[market]['taker']
            ) * 100
            self._commission_cache[cache_key] = commission
        return self._commission_cache[cache_key]

    def get_tasks(self):
        return [
            self.process_feed(),
            self.periodic_status_report(),
            self.fetch_balance_data(),
            self.fetch_funding_rates(),
        ]

    def analyze_arbitrage(
        self, exchange_data: dict[str, dict[str, float]]
    ) -> dict[tuple[str, str, str], SpreadData]:
        new_spreads = {}
        current_timestamp = time.time() * 1000  # convert to milliseconds

        for buy_exchange_id, sell_exchange_id in product(
            exchange_data.keys(), repeat=2
        ):
            if buy_exchange_id == sell_exchange_id:
                continue

            buy_data = exchange_data[buy_exchange_id]
            sell_data = exchange_data[sell_exchange_id]

            common_markets = set(buy_data.keys()) & set(sell_data.keys())

            for market in common_markets:
                buy_price = buy_data[market]['ask']
                sell_price = sell_data[market]['bid']
                min_timestamp = min(
                    buy_data[market]['timestamp'],
                    sell_data[market]['timestamp'],
                )  # in milliseconds
                if (
                    current_timestamp - min_timestamp
                    > self.config.analyze_arbitrage_max_data_age_ms
                ):
                    # Skip markets with high time difference between exchanges
                    continue
                price_diff = sell_price - buy_price
                mid_price = (sell_price + buy_price) / 2
                raw_spread = price_diff / mid_price * 100

                # Use cached commission for faster calculation
                commission = self.get_cached_commission(
                    buy_exchange_id, sell_exchange_id, market
                )
                net_spread = raw_spread - commission

                spread = SpreadData(
                    market=market,
                    buy_exchange_id=buy_exchange_id,
                    buy_price=buy_price,
                    sell_exchange_id=sell_exchange_id,
                    sell_price=sell_price,
                    raw_spread=raw_spread,
                    commission=commission,
                    net_spread=net_spread,
                    min_timestamp=min_timestamp,
                )
                new_spreads[spread.key] = spread
        return new_spreads

    async def process_feed(self):
        spreads = {}
        while True:
            change_feed_data = await self.feed_queue.get()
            try:
                if len(change_feed_data) < 2:
                    continue

                new_spreads = self.analyze_arbitrage(change_feed_data)
                if not new_spreads:
                    continue

                spreads.update(new_spreads)

                # Process positions immediately for minimum latency
                await self.process_positions(new_spreads)
                if self.config.use_ui:
                    await self.render_queue.put(
                        {
                            'spreads': spreads,
                            'positions': list(self.positions.values()),
                        }
                    )
                    shallow_spreads = {
                        key: SpreadData(
                            market=spread.market,
                            buy_exchange_id=spread.buy_exchange_id,
                            buy_price=spread.buy_price,
                            sell_exchange_id=spread.sell_exchange_id,
                            sell_price=spread.sell_price,
                            raw_spread=spread.raw_spread,
                            commission=spread.commission,
                            net_spread=spread.net_spread,
                            min_timestamp=spread.min_timestamp,
                        )
                        for key, spread in spreads.items()
                    }
                    shallow_positions = [
                        ArbitragePosition(
                            buy_exchange=position.buy_exchange,
                            sell_exchange=position.sell_exchange,
                            buy_price=position.buy_price,
                            sell_price=position.sell_price,
                            market=position.market,
                            usd_amount=position.usd_amount,
                            leverage=position.leverage,
                        )
                        for position in self.positions.values()
                    ]
                    await self.render_queue.put(
                        {
                            'spreads': shallow_spreads,
                            'positions': shallow_positions,
                        }
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                console.log(f'[red]Error in Strategy process_feed: {e}[/red]')
            finally:
                self.feed_queue.task_done()

    def have_to_open_position(self, spread_data: SpreadData) -> bool:
        current_timestamp = time.time() * 1000  # convert to milliseconds
        funding_adj = 0.0
        if self.config.consider_funding:
            buy_rate = self.funding_rates.get(spread_data.buy_exchange_id, {}).get(
                spread_data.market, 0
            )
            sell_rate = self.funding_rates.get(spread_data.sell_exchange_id, {}).get(
                spread_data.market, 0
            )
            funding_adj = (buy_rate - sell_rate) * 100

        effective_net_spread = spread_data.net_spread - funding_adj
        net_spread_ok = (
            effective_net_spread
            >= self.config.open_position_net_spread_threshold
        )
        nes_spread_ok_half = (
            effective_net_spread
            >= self.config.open_position_net_spread_threshold / 2
        )
        age = current_timestamp - spread_data.min_timestamp
        age_ok = age < self.config.open_position_max_data_age_ms
        age_ok_half = age < (self.config.open_position_max_data_age_ms * 2)

        should_open = net_spread_ok and age_ok
        should_print = nes_spread_ok_half and age_ok_half

        if should_print:
            should_open_color = 'green' if should_open else 'red'
            console.log(
                f'[dim cyan]🔍 Checking opportunity {spread_data.market}:[/dim cyan]\n'
                f'   Net spread: [cyan]{spread_data.net_spread:.3f}%[/cyan] (threshold: {self.config.open_position_net_spread_threshold}%)\n'
                f'   Funding diff: [cyan]{funding_adj:.3f}%[/cyan]\n'
                f'   Effective spread: [cyan]{effective_net_spread:.3f}%[/cyan]\n'
                f'   Data age: [cyan]{age:.0f}ms[/cyan] (max: {self.config.open_position_max_data_age_ms}ms)\n'
                f'   Sell price: [cyan]{spread_data.sell_price:.7f}[/cyan]\n'
                f'   Buy price: [cyan]{spread_data.buy_price:.7f}[/cyan]\n'
                f'   Should open: [{should_open_color}]{should_open}[/{should_open_color}]'
            )

        return should_open

    def have_to_close_position(
        self, position: ArbitragePosition, spread_data: SpreadData
    ) -> bool:
        current_time = asyncio.get_running_loop().time()
        current_timestamp = time.time() * 1000  # convert to milliseconds
        close_after = (
            position.opened_at + self.config.close_position_after_seconds
        )
        time_based_close = close_after < current_time
        funding_adj = 0.0
        if self.config.consider_funding:
            buy_rate = self.funding_rates.get(spread_data.buy_exchange_id, {}).get(
                spread_data.market, 0
            )
            sell_rate = self.funding_rates.get(spread_data.sell_exchange_id, {}).get(
                spread_data.market, 0
            )
            funding_adj = (buy_rate - sell_rate) * 100

        effective_raw_spread = spread_data.raw_spread - funding_adj
        spread_based_close = (
            effective_raw_spread
            <= self.config.close_position_raw_spread_threshold
        )
        age = current_timestamp - spread_data.min_timestamp
        age_ok = age < self.config.close_position_max_data_age_ms

        should_close = (time_based_close or spread_based_close) and age_ok

        console.log(
            f'[dim yellow]🔍 Checking position {position.market}:[/dim yellow]\n'
            f'   Raw spread: [cyan]{spread_data.raw_spread:.3f}%[/cyan] (threshold: {self.config.close_position_raw_spread_threshold}%)\n'
            f'   Funding diff: [cyan]{funding_adj:.3f}%[/cyan]\n'
            f'   Effective spread: [cyan]{effective_raw_spread:.3f}%[/cyan]\n'
            f'   Time remaining: [cyan]{(close_after - current_time):.1f}s[/cyan]\n'
            f'   Data age: [cyan]{age:.0f}ms[/cyan] (max: {self.config.close_position_max_data_age_ms}ms)\n'
            f'   Should close: [{"green" if should_close else "red"}]{should_close}[/{"green" if should_close else "red"}]'
        )

        return should_close

    async def process_positions(
        self, spreads: dict[tuple[str, str, str], SpreadData]
    ):
        positions_to_close = []
        for position in self.positions.values():
            if position.key not in spreads:
                continue
            if self.have_to_close_position(position, spreads[position.key]):
                positions_to_close.append(position)

        closed_positions_markets = set()
        if positions_to_close:
            console.log(
                f'[red]🔄 Closing {len(positions_to_close)} positions[/red]'
            )
            await asyncio.gather(
                *(position.close() for position in positions_to_close),
                return_exceptions=True,
            )
            for position in positions_to_close:
                closed_positions_markets.add(position.market)
                del self.positions[position.market]

        positions_to_open = []
        for spread_data in spreads.values():
            if spread_data.market in closed_positions_markets:
                continue
            if self.have_to_open_position(spread_data):
                positions_to_open.append(
                    ArbitragePosition(
                        buy_exchange=(
                            self.exchange_by_id[spread_data.buy_exchange_id]
                        ),
                        sell_exchange=(
                            self.exchange_by_id[spread_data.sell_exchange_id]
                        ),
                        buy_price=spread_data.buy_price,
                        sell_price=spread_data.sell_price,
                        market=spread_data.market,
                        usd_amount=self.config.position.usd_amount,
                        leverage=self.config.position.leverage,
                    )
                )

        if not positions_to_open:
            return

        console.log(
            f'[green]💰 Attempting to open {len(positions_to_open)} new positions[/green]'
        )

        # Log current balances
        console.log('[blue]💳 Current exchange balances:[/blue]')
        for exchange_id, balance in self.exchange_balances.items():
            console.log(
                f'   {exchange_id}: [green]{balance:.2f}[/green] {self.config.base_currency}'
            )

        for position in positions_to_open:
            market = position.market
            if market in self.positions:
                continue
            position_size = (
                position.usd_amount * self.config.position.size_buffer_factor
            )
            buy_exchange_id = position.buy_exchange.id
            sell_exchange_id = position.sell_exchange.id
            buy_exchange_usd_amount = self.exchange_balances.get(
                buy_exchange_id, 0
            )
            sell_exchange_usd_amount = self.exchange_balances.get(
                sell_exchange_id, 0
            )
            if (
                buy_exchange_usd_amount < position_size
                or sell_exchange_usd_amount < position_size
            ):
                console.log(
                    f'[yellow]⚠️  Not enough USD to open position for {market}.[/yellow]\n'
                    f'Available: [green]{buy_exchange_usd_amount:.2f}[/green] on [cyan]{buy_exchange_id}[/cyan] '
                    f'and [green]{sell_exchange_usd_amount:.2f}[/green] on [cyan]{sell_exchange_id}[/cyan]\n'
                    f'Required: [red]{position_size:.2f}[/red] each'
                )
                continue
            if await position.open():
                self.positions[market] = position
                self.exchange_balances[buy_exchange_id] -= position_size
                self.exchange_balances[sell_exchange_id] -= position_size
                console.log(
                    f'[bold green]✅ Successfully opened position for {market}[/bold green]'
                )
            else:
                console.log(
                    f'[red]❌ Failed to open position for {market}[/red]'
                )

    async def fetch_balance_data(self):
        console.log('[blue]Starting balance fetching task...[/blue]')
        while True:
            try:
                available_usd_data = await asyncio.gather(
                    *(
                        exchange.fetch_balance()
                        for exchange in self.exchange_by_id.values()
                    )
                )
                self.exchange_balances = {
                    exchange.id: balance.get('free', {}).get(
                        self.config.base_currency, 0
                    )
                    for exchange, balance in zip(
                        self.exchange_by_id.values(), available_usd_data
                    )
                }
                console.log(
                    '[green]✅ Exchange balances updated successfully.[/green]'
                )
                await asyncio.sleep(self.config.balance_fetch_interval_seconds)
            except asyncio.CancelledError:  # noqa: PERF203
                break
            except Exception as e:
                console.log(f'[red]Error fetching balances: {e}[/red]')
        console.log('[blue]Balance fetching finished.[/blue]')

    async def _fetch_exchange_funding_rates(self, exchange: Exchange):
        if hasattr(exchange, 'fetch_funding_rates'):
            try:
                rates = await exchange.fetch_funding_rates()
            except Exception:
                rates = {}
        elif hasattr(exchange, 'fetch_funding_rate'):
            rates = {}
            for market in exchange.markets:
                try:
                    rate = await exchange.fetch_funding_rate(market)
                    if rate is not None:
                        rates[market] = rate
                except Exception:
                    continue
        else:
            rates = {}
        return {
            market: data.get('fundingRate', 0)
            for market, data in rates.items()
        }

    async def fetch_funding_rates(self):
        console.log('[blue]Starting funding rate fetching task...[/blue]')
        while True:
            try:
                funding_data = await asyncio.gather(
                    *(
                        self._fetch_exchange_funding_rates(exchange)
                        for exchange in self.exchange_by_id.values()
                    )
                )
                self.funding_rates = {
                    exchange.id: rates
                    for exchange, rates in zip(
                        self.exchange_by_id.values(), funding_data
                    )
                }
                console.log(
                    '[green]✅ Funding rates updated successfully.[/green]'
                )
                await asyncio.sleep(
                    self.config.funding_rate_fetch_interval_seconds
                )
            except asyncio.CancelledError:  # noqa: PERF203
                break
            except Exception as e:
                console.log(f'[red]Error fetching funding rates: {e}[/red]')
        console.log('[blue]Funding rate fetching finished.[/blue]')

    async def periodic_status_report(self):
        """Periodically log the status of the bot"""
        console.log('[blue]Starting periodic status report...[/blue]')
        while True:
            await asyncio.sleep(self.config.status_report_interval_seconds)
            try:
                num_positions = len(self.positions)
                if num_positions > 0:
                    console.log(
                        f'[bold blue]📊 Status Report: {num_positions} active positions[/bold blue]'
                    )

                    # Get stats for all positions
                    position_stats = await asyncio.gather(
                        *(
                            position.stats()
                            for position in self.positions.values()
                        ),
                        return_exceptions=True,
                    )

                    total_pnl = sum(
                        stats.get('profit', 0)
                        for stats in position_stats
                        if isinstance(stats, dict)
                    )

                    pnl_color = 'green' if total_pnl >= 0 else 'red'
                    console.log(
                        f'[bold {pnl_color}]💰 Total PnL: {total_pnl:.4f} {self.config.base_currency}[/bold {pnl_color}]'
                    )

                    # List all active positions
                    now = asyncio.get_running_loop().time()
                    for position in self.positions.values():
                        runtime = (
                            now - position.opened_at
                            if position.opened_at
                            else 0
                        )
                        console.log(
                            f'   • {position.market}: '
                            f'[green]{position.buy_exchange.id}[/green] → [red]{position.sell_exchange.id}[/red] '
                            f'(Runtime: {runtime / 60:.1f}min)'
                        )
                else:
                    console.log('[blue]📊 Status: No active positions[/blue]')

            except asyncio.CancelledError:
                break
            except Exception as e:
                console.log(f'[red]Error in status report: {e}[/red]')
        console.log('[blue]Periodic status report finished.[/blue]')
