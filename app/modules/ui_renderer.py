import asyncio
import time
from typing import Sequence

from rich import box
from rich.layout import Layout
from rich.live import Live
from rich.table import Table
from settings import BotConfig, console

from .arbitrage_position import ArbitragePosition
from .base import BaseModule
from .data_feed import SpreadData


class UIRenderer(BaseModule):
    name = 'UIRenderer'

    def __init__(
        self,
        render_queue: asyncio.Queue,
        live: Live,
        config: BotConfig,
    ):
        self.render_queue = render_queue
        self.live = live
        self.config = config
        self.render_data_lock = asyncio.Lock()
        self.render_data = {}

    def get_tasks(self):
        return [
            self.collect_render_data(),
            self.render(),
        ]

    def get_positions_table(
        self,
        spreads: dict[tuple[str, str, str], SpreadData],
        positions: Sequence[ArbitragePosition],
    ):
        def get_position_key(position: ArbitragePosition):
            return (
                position.market,
                position.buy_exchange.id,
                position.sell_exchange.id,
            )

        table = Table(title='Open Positions', box=box.ROUNDED)
        table.add_column('Market', justify='left', style='cyan')
        table.add_column('Buy EX', justify='left', style='green')
        table.add_column('Sell EX', justify='left', style='red')
        table.add_column('Amount', justify='right')
        table.add_column('Leverage', justify='right')
        table.add_column('Spread', justify='right')
        table.add_column('Close in (s)', justify='right')

        cur_time = asyncio.get_running_loop().time()
        for position in sorted(
            positions,
            key=lambda x: spreads[key].raw_spread
            if (key := get_position_key(x)) in spreads
            else 0,
            reverse=True,
        ):
            spread = spreads.get(get_position_key(position))
            table.add_row(
                position.market,
                position.buy_exchange.id,
                position.sell_exchange.id,
                f'{position.usd_amount * 2 * position.leverage:.4f}',
                f'{position.leverage:.2f}',
                f'{spread.raw_spread:.2f}' if spread else 'N/A',
                (
                    f'{(position.opened_at + self.config.close_position_after_seconds - cur_time):.0f}'
                    if position.opened_at
                    else 'N/A'
                ),
            )
        return table

    def get_spreads_table(
        self, spreads: dict[tuple[str, str, str], SpreadData]
    ):
        table = Table(title='Arbitrage Opportunities', box=box.ROUNDED)
        table.add_column('Market', justify='left', style='cyan')
        table.add_column('Buy EX', justify='left', style='green')
        table.add_column('Buy price', justify='right', style='green')
        table.add_column('Sell EX', justify='left', style='red')
        table.add_column('Sell Price', justify='right', style='red')
        table.add_column('Raw spread', justify='right')
        table.add_column('Commission', justify='right')
        table.add_column('Net spread', justify='right')
        table.add_column('Time diff (s)', justify='right')
        current_timestamp = time.time() * 1000  # convert to ms
        for spread_data in sorted(
            list(spreads.values())[:30],
            key=lambda x: x.net_spread,
            reverse=True,
        ):
            age = current_timestamp - spread_data.min_timestamp
            age_color = 'green' if age < 0.5 else 'red'
            row_style = (
                'dim' if (spread_data.net_spread < 0 or age > 0.5) else None
            )
            table.add_row(
                spread_data.market,
                spread_data.buy_exchange_id,
                f'{spread_data.buy_price:.5f}',
                spread_data.sell_exchange_id,
                f'{spread_data.sell_price:.5f}',
                f'{spread_data.raw_spread:.2f}',
                f'{spread_data.commission:.2f}',
                f'{spread_data.net_spread:.2f}',
                f'[{age_color}]{age:.3f}',
                style=row_style,
            )
        return table

    async def collect_render_data(self):
        while True:
            render_data = await self.render_queue.get()
            try:
                async with self.render_data_lock:
                    self.render_data = render_data
            except asyncio.CancelledError:
                break
            except Exception as e:
                console.log(
                    f'[red]Error in UIRenderer collect_render_data: {e}[/red]'
                )
            finally:
                self.render_queue.task_done()

    async def render(self):
        layout = Layout(name='root')
        layout.split_row(
            Layout(name='spreads', ratio=3),
            Layout(name='positions', ratio=2),
        )
        while True:
            try:
                async with self.render_data_lock:
                    spreads = self.render_data.get('spreads', {})
                    positions = self.render_data.get('positions', [])
                    spreads_table = self.get_spreads_table(spreads)
                    positions_table = self.get_positions_table(
                        spreads, positions
                    )
                layout['spreads'].update(spreads_table)
                layout['positions'].update(positions_table)
                self.live.update(layout)
                await asyncio.sleep(self.config.ui_refresh_interval_seconds)
            except asyncio.CancelledError:  # noqa: PERF203
                break
            except Exception:
                console.log(
                    '[red]Error in UIRenderer render, retrying...[/red]'
                )
