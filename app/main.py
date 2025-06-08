import asyncio
import logging
from collections import defaultdict
from os import environ
from typing import Sequence

import pyinstrument
from ccxt.async_support.base.exchange import Exchange, NotSupported
from ccxt.pro import binance, bybit  # okx
from modules.base import BaseModule
from modules.data_feed import DataFeed, SpreadData
from modules.strategy import Strategy
from modules.ui_renderer import UIRenderer
from rich.live import Live
from rich.logging import RichHandler
from settings import BotConfig, PositionConfig, console

__all__ = [
    'ArbitrageBot',
    'SpreadData',
    'Strategy',
]


class ArbitrageBot(BaseModule):
    name = 'ArbitrageBot'

    def __init__(
        self,
        exchanges: Sequence[Exchange],
        config: BotConfig,
    ):
        self.exchange_by_id = {exchange.id: exchange for exchange in exchanges}
        self.config = config
        self.markets = []
        self.live = Live(
            screen=True, auto_refresh=config.live_auto_refresh_seconds
        )

    def get_tasks(self):
        feed_queue = asyncio.Queue()
        render_queue = asyncio.Queue()
        data_feed = DataFeed(
            exchanges=self.exchange_by_id.values(),
            markets=self.markets,
            feed_queue=feed_queue,
            config=self.config,
        )
        strategy = Strategy(
            feed_queue=feed_queue,
            render_queue=render_queue,
            exchange_by_id=self.exchange_by_id,
            config=self.config,
        )
        tasks = [
            data_feed.work(),
            strategy.work(),
        ]
        if self.config.use_ui:
            ui_renderer = UIRenderer(
                render_queue=render_queue,
                live=self.live,
                config=self.config,
            )
            tasks.append(ui_renderer.work())
        return tasks

    async def open_exchanges(self):
        console.log('[blue]🔌 Opening exchanges...[/blue]')
        await asyncio.gather(
            *(
                exchange.load_time_difference()
                for exchange in self.exchange_by_id.values()
            )
        )
        await asyncio.gather(
            *(
                exchange.load_markets()
                for exchange in self.exchange_by_id.values()
            )
        )

        def get_markets(exchange):
            return [
                symbol
                for symbol in exchange.markets
                if symbol.endswith(f':{self.config.base_currency}')
            ]

        markets = None
        for exchange in self.exchange_by_id.values():
            if markets is None:
                markets = set(get_markets(exchange))
            else:
                markets &= set(get_markets(exchange))

        console.log(
            f'[green]📊 Found {len(markets)} common markets across exchanges.[/green]'
        )
        console.log(
            '[blue]📈 Fetching tickers for common markets to sort them by volume...[/blue]'
        )
        tickers_data_by_exchange = await asyncio.gather(
            *(
                exchange.fetch_tickers(list(markets))
                for exchange in self.exchange_by_id.values()
            )
        )
        volumes_by_market = defaultdict(float)
        for tickers_data in tickers_data_by_exchange:
            for market, ticker in tickers_data.items():
                if ticker['quoteVolume']:
                    volumes_by_market[market] += ticker['quoteVolume']

        self.markets = sorted(
            markets,
            key=lambda market: volumes_by_market.get(market, 0),
            reverse=True,
        )[: self.config.top_n_markets]
        console.log(
            f'[green]✅ Selected top {self.config.top_n_markets} markets by volume. Exchanges opened.[/green]'
        )

        console.log(
            '[blue]🔧 Configuring exchanges with selected markets...[/blue]'
        )

        async def configure_exchange(exchange: bybit):
            try:
                leverages = await exchange.fetch_leverages(self.markets)
            except NotSupported:
                leverages = {}
            await asyncio.gather(
                *(
                    exchange.set_margin_mode('ISOLATED', market)
                    for market in self.markets
                    if (
                        leverages.get(market, {}).get('marginMode')
                        != 'ISOLATED'
                    )
                ),
                *(
                    exchange.set_leverage(
                        self.config.position.leverage, market
                    )
                    for market in self.markets
                    if (
                        leverages.get(market, {}).get('longLeverage')
                        != self.config.position.leverage
                        or leverages.get(market, {}).get('shortLeverage')
                        != self.config.position.leverage
                    )
                ),
                return_exceptions=True,
            )

        await asyncio.gather(
            *(
                configure_exchange(exchange)
                for exchange in self.exchange_by_id.values()
            )
        )

    async def close_exchanges(self):
        console.log('[blue]🔌 Closing exchanges...[/blue]')
        await asyncio.gather(
            *(exchange.close() for exchange in self.exchange_by_id.values()),
            return_exceptions=True,
        )
        console.log('[green]✅ Exchanges closed.[/green]')

    async def run(self):
        console.log('[bold green]🚀 Starting ArbitrageBot...[/bold green]')
        try:
            await self.open_exchanges()
            if self.config.use_ui:
                self.live.start()
            if self.config.use_profiler:
                profiler = pyinstrument.Profiler()
                profiler.start()
            await self.work()
        except asyncio.CancelledError:
            console.log('[blue]ArbitrageBot run cancelled.[/blue]')
        finally:
            if self.config.use_ui:
                self.live.stop()
            if self.config.use_profiler:
                profiler.stop()
                profiler.print()
            await self.close_exchanges()


async def main():
    logging.basicConfig(
        level='INFO',
        format='%(message)s',
        datefmt='[%X]',
        handlers=[
            RichHandler(rich_tracebacks=True),
        ],
    )

    console.log('[bold blue]🏗️  Initializing ArbitrageBot...[/bold blue]')

    # Create BotConfig instance
    bot_config = BotConfig(
        open_position_net_spread_threshold=0.4,
        close_position_raw_spread_threshold=-0.1,
        close_position_after_seconds=3 * 60 * 60,  # 3 hours
        position=PositionConfig(
            usd_amount=15,
            leverage=1,
            size_buffer_factor=1.05,
        ),
        base_currency='USDT',
        use_ui=False,  # Set to True to enable UI
        # use_profiler=True, # Set to True to enable profiler
        top_n_markets=200,
        analyze_arbitrage_max_data_age_ms=200,
    )

    same_settings = {
        'enableRateLimit': bot_config.enable_rate_limit,
        'options': {
            'adjustForTimeDifference': bot_config.adjust_for_time_difference,
            'defaultType': bot_config.exchange_default_type,
        },
    }

    console.log('[blue]🔧 Setting up exchanges...[/blue]')
    binance_exchange = binance(
        {
            'apiKey': environ.get('BINANCE_API_KEY'),
            'secret': environ.get('BINANCE_SECRET'),
            **same_settings,
        }
    )
    bybit_exchange = bybit(
        {
            'apiKey': environ.get('BYBIT_API_KEY'),
            'secret': environ.get('BYBIT_SECRET'),
            **same_settings,
        }
    )
    # okx_exchange = okx(
    #     {
    #         'apiKey': environ.get('OKX_API_KEY'),
    #         'secret': environ.get('OKX_SECRET'),
    #         'password': environ.get('OKX_PASSPHRASE'),
    #         **same_settings,
    #     }
    # )

    exchanges = [
        binance_exchange,
        bybit_exchange,
        # okx_exchange,  # Commented out but defined for future use
    ]

    console.log('[green]✅ Exchanges configured[/green]')
    console.log('[blue]⚙️  Creating bot with parameters from config:[/blue]')
    console.log(
        f'   • Open threshold: [cyan]{bot_config.open_position_net_spread_threshold:.2f}%[/cyan]'
    )
    console.log(
        f'   • Close threshold: [cyan]{bot_config.close_position_raw_spread_threshold:.2f}%[/cyan]'
    )
    console.log(
        f'   • Position timeout: [cyan]{bot_config.close_position_after_seconds} s[/cyan]'
    )
    console.log(
        f'   • Position size: [cyan]${bot_config.position.usd_amount}[/cyan]'
    )
    console.log(f'   • Leverage: [cyan]{bot_config.position.leverage}x[/cyan]')
    console.log(f'   • Base Currency: [cyan]{bot_config.base_currency}[/cyan]')
    console.log(f'   • Use UI: [cyan]{bot_config.use_ui}[/cyan]')
    console.log(f'   • Top N Markets: [cyan]{bot_config.top_n_markets}[/cyan]')

    bot = ArbitrageBot(
        exchanges,
        config=bot_config,
    )
    await bot.run()


if __name__ == '__main__':
    asyncio.run(main())
