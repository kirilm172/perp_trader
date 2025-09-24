import asyncio

from ccxt.async_support.base.exchange import Exchange
from ccxt.base.errors import InvalidOrder
from settings import console


class ArbitragePosition:
    bought_amount = None
    sold_amount = None
    opened_at = None
    # For trailing stop orders
    buy_trailing_stop_order_info = None
    sell_trailing_stop_order_info = None

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
        trailing_stop_mode: bool = False,  # New parameter
    ):
        self.buy_exchange = buy_exchange
        self.buy_price = buy_price
        self.sell_exchange = sell_exchange
        self.sell_price = sell_price
        self.market = market
        self.usd_amount = usd_amount
        self.leverage = leverage
        self.order_type = order_type
        self.trailing_stop_mode = trailing_stop_mode
        self.buy_trailing_stop_order_info = None
        self.sell_trailing_stop_order_info = None

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

    def _calculate_dynamic_callback_rate(
        self, exchange: Exchange, position_side: str
    ) -> float:
        """
        Calculate a dynamic callback rate for trailing stop orders.
        Currently uses a fixed rate, but designed for future expansion.

        Args:
            exchange: The exchange for this position leg
            position_side: 'buy' or 'sell' - the main arbitrage position leg

        Returns:
            float: The callback rate (e.g., 0.005 = 0.5%)

        TODO: Implement dynamic calculation based on:
        - Market volatility (ATR, standard deviation)
        - Order book depth and spread
        - Recent price movement patterns
        - Time of day / market hours
        - Asset-specific characteristics
        """
        # Base callback rate - could be made configurable
        base_rate = 0.005  # 0.5%

        # Future enhancements could include:
        # - Volatility adjustment: higher volatility = wider callback
        # - Liquidity adjustment: thinner books = wider callback
        # - Momentum adjustment: strong trends = tighter callback

        console.log(
            f'[grey]Calculating callback rate for {exchange.id} {self.market} {position_side}: {base_rate * 100:.2f}%[/grey]'
        )

        return base_rate

    async def create_trailing_stop_order(
        self,
        exchange: Exchange,
        amount: float,
        stop_order_side: str,  # 'buy' or 'sell' for the stop order itself
        callback_rate: float,
    ):
        """
        Creates a TRAILING_STOP_MARKET order with reduceOnly.

        Args:
            exchange: Exchange to place the order on
            amount: Order amount
            stop_order_side: 'buy' or 'sell' for the stop order
            callback_rate: Trailing callback rate (e.g., 0.005 = 0.5%)
        """
        if amount <= 0:
            raise ValueError(f'Invalid amount for trailing stop: {amount}')

        if not (0.001 <= callback_rate <= 0.1):  # 0.1% to 10% range
            console.log(
                f'[yellow]Warning: Unusual callback rate {callback_rate * 100:.2f}%[/yellow]'
            )

        # Exchange-specific parameters and order types
        if exchange.id == 'binance':
            # Binance expects callbackRate as a string percentage (e.g., "0.5" for 0.5%)
            params = {
                'reduceOnly': True,
                'callbackRate': f'{callback_rate * 100:.1f}',  # Convert to string percentage
            }
            order_type = (
                'trailing_stop_market'  # Use unified naming convention
            )
            func = exchange.create_order_ws(
                self.market,
                order_type,
                stop_order_side,
                amount,
                params=params,
            )
        elif exchange.id == 'bybit':
            # Bybit supports trailing orders using trailingAmount (absolute amount) instead of trailingPercent
            try:
                ticker = await exchange.fetch_ticker(self.market)
                current_price = ticker['last']

                # Calculate trailing amount based on callback rate and current price
                # trailingAmount is the absolute dollar amount away from market price
                trailing_amount = current_price * callback_rate

                params = {
                    'reduceOnly': True,
                    'trailingAmount': str(
                        trailing_amount
                    ),  # Bybit expects string format
                }
                order_type = 'market'  # Use unified naming convention

                console.log(
                    f'[cyan]Bybit: Using trailing order with trailingAmount={trailing_amount:.4f} '
                    f'(current price: {current_price}, rate: {callback_rate * 100:.2f}%)[/cyan]'
                )
                func = exchange.create_order(
                    self.market,
                    order_type,
                    stop_order_side,
                    amount,
                    params=params,
                )
            except Exception as e:
                console.log(
                    f'[red]Failed to get current price for Bybit trailing order: {e}[/red]'
                )
                return {
                    'id': f'failed_{exchange.id}_{stop_order_side}',
                    'status': 'failed',
                    'error': f'price_fetch_failed: {e}',
                }
        else:
            # Default parameters for other exchanges
            params = {
                'reduceOnly': True,
                'callbackRate': callback_rate,
            }
            order_type = (
                'trailing_stop_market'  # Use unified naming convention
            )
            func = exchange.create_order_ws(
                self.market,
                order_type,
                stop_order_side,
                amount,
                params=params,
            )

        console.log(
            f'[cyan]Creating {order_type}: {self.market} on {exchange.id}, '
            f'side={stop_order_side}, amount={amount}, rate={callback_rate * 100:.2f}%[/cyan]'
        )
        try:
            return await func
        except Exception as e:
            console.log(
                f'[red]Failed to create trailing stop on {exchange.id}: {e}[/red]'
            )
            # Return a mock response instead of raising to prevent breaking the main flow
            return {
                'id': f'failed_{exchange.id}_{stop_order_side}',
                'status': 'failed',
                'error': str(e),
            }

    async def open(self):
        console.log(
            f'[green]Opening arbitrage position for {self.market} (Trailing Stop Mode: {self.trailing_stop_mode})[/green]'
        )
        try:
            amount = self.get_order_amount()
        except InvalidOrder as e:
            console.log(f'[red]Invalid order for {self.market}: {e}[/red]')
            return False

        console.log(
            f'[yellow]Placing primary orders for {self.market}: amount={amount}[/yellow]'
        )

        primary_order_tasks = []
        if self.order_type == 'limit':
            primary_order_tasks.append(
                self.create_limit_order_by_usd(
                    self.buy_exchange, amount, 'buy', self.buy_price, True
                )
            )
            primary_order_tasks.append(
                self.create_limit_order_by_usd(
                    self.sell_exchange, amount, 'sell', self.sell_price, True
                )
            )
        else:  # market
            primary_order_tasks.append(
                self.create_market_order_by_usd(
                    self.buy_exchange, amount, 'buy'
                )
            )
            primary_order_tasks.append(
                self.create_market_order_by_usd(
                    self.sell_exchange, amount, 'sell'
                )
            )

        order_results = await asyncio.gather(
            *primary_order_tasks, return_exceptions=True
        )

        buy_order = order_results[0]
        sell_order = order_results[1]

        primary_orders_ok = True
        if isinstance(buy_order, Exception):
            console.log(f'[red]❌ Error creating buy order: {buy_order}[/red]')
            buy_order = None
            primary_orders_ok = False
        if isinstance(sell_order, Exception):
            console.log(
                f'[red]❌ Error creating sell order: {sell_order}[/red]'
            )
            sell_order = None
            primary_orders_ok = False

        if not primary_orders_ok:
            console.log(
                f'[red]❌ Failed to create one or more primary orders for {self.market}. Aborting open.[/red]'
            )
            # TODO: Consider cancelling any successful order if one part failed.
            return False

        console.log(
            f'[green]✓ Primary orders placed successfully for {self.market}.'
            f' Buy ID: {buy_order.get("id", "N/A") if buy_order else "Error"},'
            f' Sell ID: {sell_order.get("id", "N/A") if sell_order else "Error"}[/green]'
        )

        buy_pos_stop_callback_rate = 0.0  # Initialize for logging
        sell_pos_stop_callback_rate = 0.0  # Initialize for logging

        if self.trailing_stop_mode:
            console.log(
                f'[yellow]Placing trailing stop orders for {self.market}...[/yellow]'
            )
            buy_pos_stop_callback_rate = self._calculate_dynamic_callback_rate(
                self.buy_exchange, 'buy'
            )
            sell_pos_stop_callback_rate = (
                self._calculate_dynamic_callback_rate(
                    self.sell_exchange, 'sell'
                )
            )

            trailing_stop_tasks = []
            # Trailing stop for the long (buy) position is a SELL stop order
            trailing_stop_tasks.append(
                self.create_trailing_stop_order(
                    self.buy_exchange,
                    amount,
                    'sell',
                    buy_pos_stop_callback_rate,
                )
            )
            if self.buy_exchange.id == 'bybit':
                trailing_stop_tasks.append(
                    self.create_trailing_stop_order(
                        self.buy_exchange,
                        amount,
                        'buy',
                        buy_pos_stop_callback_rate,
                    )
                )
            # Trailing stop for the short (sell) position is a BUY stop order
            trailing_stop_tasks.append(
                self.create_trailing_stop_order(
                    self.sell_exchange,
                    amount,
                    'buy',
                    sell_pos_stop_callback_rate,
                )
            )
            if self.sell_exchange.id == 'bybit':
                trailing_stop_tasks.append(
                    self.create_trailing_stop_order(
                        self.sell_exchange,
                        amount,
                        'sell',
                        buy_pos_stop_callback_rate,
                    )
                )

            trailing_results = await asyncio.gather(
                *trailing_stop_tasks, return_exceptions=True
            )

            self.buy_trailing_stop_order_info = trailing_results[0]
            self.sell_trailing_stop_order_info = trailing_results[1]

            # Check if trailing stops were created successfully (not exceptions or mock responses)
            if isinstance(self.buy_trailing_stop_order_info, Exception):
                console.log(
                    f'[red]❌ Error creating buy-position trailing stop (sell order): {self.buy_trailing_stop_order_info}[/red]'
                )
                self.buy_trailing_stop_order_info = None
            elif isinstance(
                self.buy_trailing_stop_order_info, dict
            ) and self.buy_trailing_stop_order_info.get('status') in [
                'skipped',
                'failed',
            ]:
                console.log(
                    f'[yellow]⚠️  Buy-position trailing stop: {self.buy_trailing_stop_order_info.get("reason", "failed")}[/yellow]'
                )
                self.buy_trailing_stop_order_info = None

            if isinstance(self.sell_trailing_stop_order_info, Exception):
                console.log(
                    f'[red]❌ Error creating sell-position trailing stop (buy order): {self.sell_trailing_stop_order_info}[/red]'
                )
                self.sell_trailing_stop_order_info = None
            elif isinstance(
                self.sell_trailing_stop_order_info, dict
            ) and self.sell_trailing_stop_order_info.get('status') in [
                'skipped',
                'failed',
            ]:
                console.log(
                    f'[yellow]⚠️  Sell-position trailing stop: {self.sell_trailing_stop_order_info.get("reason", "failed")}[/yellow]'
                )
                self.sell_trailing_stop_order_info = None

            console.log(
                f'[magenta]Trailing stop orders attempt completed.'
                f' Buy Pos Stop (Sell Order) ID: {self.buy_trailing_stop_order_info.get("id", "N/A") if self.buy_trailing_stop_order_info else "Failed/None"}'
                f' (Rate: {buy_pos_stop_callback_rate * 100:.2f}%)'
                f' Sell Pos Stop (Buy Order) ID: {self.sell_trailing_stop_order_info.get("id", "N/A") if self.sell_trailing_stop_order_info else "Failed/None"}'
                f' (Rate: {sell_pos_stop_callback_rate * 100:.2f}%)[/magenta]'
            )

        self.bought_amount, self.sold_amount = await asyncio.gather(
            self.get_contracts_amount(self.buy_exchange),
            self.get_contracts_amount(self.sell_exchange),
        )
        self.opened_at = asyncio.get_running_loop().time()

        log_message_parts = [
            f'[bold green]✓ Opened arbitrage position: {self.market}',
            f' at {self.buy_price} on {self.buy_exchange.id}',
            f' and {self.sell_price} on {self.sell_exchange.id}.',
            f'\nPrimary Buy order ID: {buy_order.get("id", "N/A") if buy_order else "Error"}',
            f'\nPrimary Sell order ID: {sell_order.get("id", "N/A") if sell_order else "Error"}',
        ]
        if self.trailing_stop_mode:
            buy_stop_id_str = 'Failed/None'
            if self.buy_trailing_stop_order_info and not isinstance(
                self.buy_trailing_stop_order_info, Exception
            ):
                buy_stop_id_str = self.buy_trailing_stop_order_info.get(
                    'id', 'N/A'
                )

            sell_stop_id_str = 'Failed/None'
            if self.sell_trailing_stop_order_info and not isinstance(
                self.sell_trailing_stop_order_info, Exception
            ):
                sell_stop_id_str = self.sell_trailing_stop_order_info.get(
                    'id', 'N/A'
                )

            log_message_parts.append(
                f'\nBuy Pos Trailing Stop (Sell Order): ID {buy_stop_id_str} (Rate: {buy_pos_stop_callback_rate * 100:.2f}%)'
            )
            log_message_parts.append(
                f'\nSell Pos Trailing Stop (Buy Order): ID {sell_stop_id_str} (Rate: {sell_pos_stop_callback_rate * 100:.2f}%)'
            )

        console.log(''.join(log_message_parts) + '[/bold green]')
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

        # Check trailing stop status if enabled
        trailing_stops_status = {}
        if self.trailing_stop_mode:
            trailing_stops_status = await self._check_trailing_stops_status()

        console.log(
            f'[green]💰 Position {self.market} PnL: {total_pnl:.4f} '
            f'(Buy: {buy_position["unrealizedPnl"]:.4f}, Sell: {sell_position["unrealizedPnl"]:.4f})[/green]'
        )

        if trailing_stops_status:
            console.log(
                f'[cyan]🛡️  Trailing Stops: {trailing_stops_status}[/cyan]'
            )

        return {
            'profit': total_pnl,
            'trailing_stops': trailing_stops_status,
        }

    async def _check_trailing_stops_status(self):
        """Check the status of trailing stop orders."""
        status = {}

        try:
            # Check buy position trailing stop (sell order)
            if (
                self.buy_trailing_stop_order_info
                and not isinstance(
                    self.buy_trailing_stop_order_info, Exception
                )
                and self.buy_trailing_stop_order_info.get('id')
            ):
                try:
                    buy_stop_order = await self.buy_exchange.fetch_order(
                        self.buy_trailing_stop_order_info['id'], self.market
                    )
                    status['buy_position_stop'] = {
                        'id': buy_stop_order['id'],
                        'status': buy_stop_order['status'],
                        'side': buy_stop_order['side'],
                    }
                except Exception as e:
                    status['buy_position_stop'] = {'error': str(e)}

            # Check sell position trailing stop (buy order)
            if (
                self.sell_trailing_stop_order_info
                and not isinstance(
                    self.sell_trailing_stop_order_info, Exception
                )
                and self.sell_trailing_stop_order_info.get('id')
            ):
                try:
                    sell_stop_order = await self.sell_exchange.fetch_order(
                        self.sell_trailing_stop_order_info['id'], self.market
                    )
                    status['sell_position_stop'] = {
                        'id': sell_stop_order['id'],
                        'status': sell_stop_order['status'],
                        'side': sell_stop_order['side'],
                    }
                except Exception as e:
                    status['sell_position_stop'] = {'error': str(e)}

        except Exception as e:
            console.log(
                f'[yellow]Warning checking trailing stops status: {e}[/yellow]'
            )

        return status

    async def close_position(self, exchange: Exchange, side: str):
        if side == 'buy':
            amount = self.bought_amount
            side_to_close = 'sell'
        else:
            amount = self.sold_amount
            side_to_close = 'buy'
        if amount and amount > 0:
            return await exchange.create_market_order_ws(
                self.market,
                side_to_close,
                amount,
                params={'reduceOnly': True},
            )

    async def close(self):
        console.log(f'[red]Closing arbitrage position for {self.market}[/red]')

        close_tasks = []

        # Attempt to cancel trailing stop orders first if they were created and have IDs
        if self.trailing_stop_mode:
            if (
                self.buy_trailing_stop_order_info
                and not isinstance(
                    self.buy_trailing_stop_order_info, Exception
                )
                and self.buy_trailing_stop_order_info.get('id')
                and not self.buy_trailing_stop_order_info.get(
                    'id', ''
                ).startswith(('skipped_', 'failed_'))
            ):
                buy_stop_id = self.buy_trailing_stop_order_info['id']
                console.log(
                    f'[yellow]Attempting to cancel buy-position trailing stop order {buy_stop_id} for {self.market} on {self.buy_exchange.id}[/yellow]'
                )
                close_tasks.append(
                    self.buy_exchange.cancel_order(buy_stop_id, self.market)
                )

            if (
                self.sell_trailing_stop_order_info
                and not isinstance(
                    self.sell_trailing_stop_order_info, Exception
                )
                and self.sell_trailing_stop_order_info.get('id')
                and not self.sell_trailing_stop_order_info.get(
                    'id', ''
                ).startswith(('skipped_', 'failed_'))
            ):
                sell_stop_id = self.sell_trailing_stop_order_info['id']
                console.log(
                    f'[yellow]Attempting to cancel sell-position trailing stop order {sell_stop_id} for {self.market} on {self.sell_exchange.id}[/yellow]'
                )
                close_tasks.append(
                    self.sell_exchange.cancel_order(sell_stop_id, self.market)
                )

        # Add tasks to close main positions by market orders
        close_tasks.append(self.close_position(self.buy_exchange, 'buy'))
        close_tasks.append(self.close_position(self.sell_exchange, 'sell'))

        # Add tasks to cancel any other remaining orders for the market (e.g., limit orders not filled)
        # This is a general cleanup and might be redundant if specific cancellations are comprehensive
        close_tasks.append(self.buy_exchange.cancel_all_orders(self.market))
        close_tasks.append(self.sell_exchange.cancel_all_orders(self.market))

        results = await asyncio.gather(*close_tasks, return_exceptions=True)

        # Log any errors from close operations
        for i, res in enumerate(results):
            if isinstance(res, Exception):
                # Avoid logging errors for cancellations if order was already filled/cancelled (common exceptions)
                if not (
                    'OrderNotFound' in str(res)
                    or 'order not found' in str(res).lower()
                    or 'already closed' in str(res).lower()
                ):
                    console.log(
                        f'[yellow]Warning during close operation task {i}: {res}[/yellow]'
                    )

        console.log(
            f'[bold red]✗ Closed arbitrage position: {self.market}'
            f' on {self.buy_exchange.id} and {self.sell_exchange.id}[/bold red]'
        )
        # Reset trailing stop info after attempting to close/cancel
        self.buy_trailing_stop_order_info = None
        self.sell_trailing_stop_order_info = None

    async def update_trailing_stops(
        self, new_callback_rate: float | None = None
    ):
        """
        Update trailing stop orders with new parameters.
        This cancels existing stops and creates new ones.
        """
        if not self.trailing_stop_mode:
            console.log(
                '[yellow]Trailing stop mode not enabled for this position[/yellow]'
            )
            return False

        console.log(
            f'[yellow]Updating trailing stops for {self.market}[/yellow]'
        )

        # Cancel existing stops first
        cancel_tasks = []
        if (
            self.buy_trailing_stop_order_info
            and not isinstance(self.buy_trailing_stop_order_info, Exception)
            and self.buy_trailing_stop_order_info.get('id')
            and not self.buy_trailing_stop_order_info.get('id', '').startswith(
                ('skipped_', 'failed_')
            )
        ):
            cancel_tasks.append(
                self.buy_exchange.cancel_order(
                    self.buy_trailing_stop_order_info['id'], self.market
                )
            )

        if (
            self.sell_trailing_stop_order_info
            and not isinstance(self.sell_trailing_stop_order_info, Exception)
            and self.sell_trailing_stop_order_info.get('id')
            and not self.sell_trailing_stop_order_info.get(
                'id', ''
            ).startswith(('skipped_', 'failed_'))
        ):
            cancel_tasks.append(
                self.sell_exchange.cancel_order(
                    self.sell_trailing_stop_order_info['id'], self.market
                )
            )

        if cancel_tasks:
            await asyncio.gather(*cancel_tasks, return_exceptions=True)

        # Create new trailing stops
        amount = min(abs(self.bought_amount or 0), abs(self.sold_amount or 0))
        if amount <= 0:
            console.log(
                '[red]No position found to create trailing stops for[/red]'
            )
            return False

        buy_callback_rate = (
            new_callback_rate
            or self._calculate_dynamic_callback_rate(self.buy_exchange, 'buy')
        )
        sell_callback_rate = (
            new_callback_rate
            or self._calculate_dynamic_callback_rate(
                self.sell_exchange, 'sell'
            )
        )

        try:
            new_stops = await asyncio.gather(
                self.create_trailing_stop_order(
                    self.buy_exchange, amount, 'sell', buy_callback_rate
                ),
                self.create_trailing_stop_order(
                    self.sell_exchange, amount, 'buy', sell_callback_rate
                ),
                return_exceptions=True,
            )

            self.buy_trailing_stop_order_info = (
                new_stops[0]
                if not isinstance(new_stops[0], Exception)
                and new_stops[0].get('status') not in ['skipped', 'failed']
                else None
            )
            self.sell_trailing_stop_order_info = (
                new_stops[1]
                if not isinstance(new_stops[1], Exception)
                and new_stops[1].get('status') not in ['skipped', 'failed']
                else None
            )

            console.log(
                f'[green]✓ Updated trailing stops for {self.market}[/green]'
            )
            return True

        except Exception as e:
            console.log(f'[red]Failed to update trailing stops: {e}[/red]')
            return False
