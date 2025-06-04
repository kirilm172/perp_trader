import asyncio

from ccxt.async_support.base.exchange import Exchange
from ccxt.base.errors import InvalidOrder
from settings import console


class ArbitragePosition:
    bought_amount = None
    sold_amount = None
    opened_at = None

    def __init__(
        self,
        buy_exchange: Exchange,
        buy_price: float,
        sell_exchange: Exchange,
        sell_price: float,
        market: str,
        usd_amount: float,
        leverage: int,
        order_type: str = 'market',
    ):
        self.buy_exchange = buy_exchange
        self.buy_price = buy_price
        self.sell_exchange = sell_exchange
        self.sell_price = sell_price
        self.market = market
        self.usd_amount = usd_amount
        self.leverage = leverage
        self.order_type = order_type

    @property
    def key(self):
        return (
            self.market,
            self.buy_exchange.id,
            self.sell_exchange.id,
        )

    def get_order_amount(self):
        min_notional = max(
            self.buy_exchange.markets[self.market]
            .get('limits', {})
            .get('cost', {})
            .get('min')
            or 5,
            self.sell_exchange.markets[self.market]
            .get('limits', {})
            .get('cost', {})
            .get('min')
            or 5,
        )

        notional = self.usd_amount
        if notional < min_notional:
            console.log(
                f'[red]❌ Notional value {notional} must be >= {min_notional} USD for {self.market}[/red]'
            )
            raise InvalidOrder(f'Notional value must be >= {min_notional} USD')
        position_notional = notional * self.leverage
        mid_price = (self.buy_price + self.sell_price) / 2
        amount_raw = position_notional / mid_price
        buy_amount = float(
            self.buy_exchange.amount_to_precision(self.market, amount_raw)
        )
        sell_amount = float(
            self.sell_exchange.amount_to_precision(self.market, amount_raw)
        )
        if buy_amount != sell_amount:
            max_amount = max(buy_amount, sell_amount)
            buy_amount = float(
                self.buy_exchange.amount_to_precision(self.market, max_amount)
            )
            sell_amount = float(
                self.sell_exchange.amount_to_precision(self.market, max_amount)
            )
            if buy_amount != sell_amount:
                console.log(
                    f'[red]❌ Amounts do not match: buy={buy_amount}, sell={sell_amount} for {self.market}[/red]'
                )
                # raise InvalidOrder(
                #     f'Buy and sell amounts do not match: {buy_amount} != {sell_amount}'
                # )
        amount = min(buy_amount, sell_amount)
        console.log(
            f'[blue]📊 Calculated order amount for {self.market}: {amount} (notional: {position_notional})[/blue]'
        )
        return amount

    async def create_market_order_by_usd(
        self, exchange: Exchange, amount: float, side: str
    ):
        return await exchange.create_order_ws(
            self.market, 'market', side, amount
        )

    async def create_limit_order_by_usd(
        self,
        exchange: Exchange,
        amount: float,
        side: str,
        price: float,
        post_only: bool,
    ):
        params = {'postOnly': post_only} if post_only else {}
        return await exchange.create_order_ws(
            self.market,
            'limit',
            side,
            amount,
            price,
            params=params,
        )

    async def get_contracts_amount(self, exchange: Exchange):
        """Get the amount of contracts for the given exchange and market."""
        positions = await exchange.fetch_positions([self.market])
        if not positions:
            return 0
        position = positions[0]
        return position['contracts']

    async def open(self):
        console.log(
            f'[green]Opening arbitrage position for {self.market}[/green]'
        )
        try:
            amount = self.get_order_amount()
        except InvalidOrder as e:
            console.log(f'[red]Invalid order for {self.market}: {e}[/red]')
            return False

        console.log(
            f'[yellow]Placing orders for {self.market}: buy_amount={amount}, sell_amount={amount}[/yellow]'
        )
        if self.order_type == 'limit':
            buy_order, sell_order = await asyncio.gather(
                self.create_limit_order_by_usd(
                    self.buy_exchange,
                    amount,
                    'buy',
                    self.buy_price,
                    True,
                ),
                self.create_limit_order_by_usd(
                    self.sell_exchange,
                    amount,
                    'sell',
                    self.sell_price,
                    True,
                ),
            )
        else:
            buy_order, sell_order = await asyncio.gather(
                self.create_market_order_by_usd(
                    self.buy_exchange,
                    amount,
                    'buy',
                ),
                self.create_market_order_by_usd(
                    self.sell_exchange,
                    amount,
                    'sell',
                ),
            )
        self.bought_amount, self.sold_amount = await asyncio.gather(
            self.get_contracts_amount(self.buy_exchange),
            self.get_contracts_amount(self.sell_exchange),
        )
        self.opened_at = asyncio.get_running_loop().time()
        console.log(
            f'[bold green]✓ Opened arbitrage position: {self.market}'
            f' at {self.buy_price}'
            f' on {self.buy_exchange.id}'
            f' and {self.sell_price} on'
            f' {self.sell_exchange.id}.'
            f'\nBuy order: {buy_order},'
            f'\nSell order: {sell_order}[/bold green]'
        )
        return True

    async def stats(self):
        console.log(
            f'[blue]📊 Fetching stats for position {self.market}[/blue]'
        )
        buy_positions, sell_positions = await asyncio.gather(
            self.buy_exchange.fetch_positions([self.market]),
            self.sell_exchange.fetch_positions([self.market]),
        )
        if not buy_positions or not sell_positions:
            console.log(
                f'[yellow]⚠️  No positions found for {self.market}[/yellow]'
            )
            return {'profit': 0.0}
        buy_position = buy_positions[0]
        sell_position = sell_positions[0]
        total_pnl = (
            buy_position['unrealizedPnl'] + sell_position['unrealizedPnl']
        )
        console.log(
            f'[green]💰 Position {self.market} PnL: {total_pnl:.4f} '
            f'(Buy: {buy_position["unrealizedPnl"]:.4f}, Sell: {sell_position["unrealizedPnl"]:.4f})[/green]'
        )
        return {
            'profit': total_pnl,
        }

    async def close_position(self, exchange: Exchange, side: str):
        if side == 'buy':
            amount = self.bought_amount
            side_to_close = 'sell'
        else:
            amount = self.sold_amount
            side_to_close = 'buy'
        if amount:
            return await exchange.create_market_order_ws(
                self.market,
                side_to_close,
                amount,
                params={'reduceOnly': True},
            )

    async def close(self):
        console.log(f'[red]Closing arbitrage position for {self.market}[/red]')
        await asyncio.gather(
            self.close_position(self.buy_exchange, 'buy'),
            self.close_position(self.sell_exchange, 'sell'),
            self.buy_exchange.cancel_all_orders(self.market),
            self.sell_exchange.cancel_all_orders(self.market),
            return_exceptions=True,
        )
        console.log(
            f'[bold red]✗ Closed arbitrage position: {self.market}'
            f' on {self.buy_exchange.id} and {self.sell_exchange.id}[/bold red]'
        )
