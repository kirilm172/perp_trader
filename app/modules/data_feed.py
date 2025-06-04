import asyncio
from collections import defaultdict
from dataclasses import dataclass
from itertools import product
from typing import Sequence

from ccxt.async_support.base.exchange import Exchange
from ccxt.base.errors import NetworkError
from settings import BotConfig, console

from .base import BaseModule


@dataclass(frozen=True)
class SpreadData:
    market: str
    buy_exchange_id: str
    buy_price: float
    sell_exchange_id: str
    sell_price: float
    raw_spread: float
    commission: float
    net_spread: float
    min_timestamp: float  # in milliseconds

    @property
    def key(self):
        return (
            self.market,
            self.buy_exchange_id,
            self.sell_exchange_id,
        )


class DataFeed(BaseModule):
    name = 'DataFeed'

    def __init__(
        self,
        exchanges: Sequence[Exchange],
        markets: Sequence[str],
        feed_queue: asyncio.Queue,
        config: BotConfig,
    ):
        self.exchanges = exchanges
        self.markets = markets
        self.feed_queue = feed_queue
        self.collect_queue = asyncio.Queue()
        self.ws_last_received: dict[tuple[str, str], float] = {}
        self.config = config

    def get_tasks(self):
        return [
            self.collect_feed(),
            *(
                self.watch_orderbook(exchange, market)
                for exchange, market in product(self.exchanges, self.markets)
            ),
        ]

    async def collect_feed(self):
        feed = defaultdict(dict)

        target_amount = (
            self.config.position.usd_amount * self.config.position.leverage
        )

        def get_price(asks_or_bids):
            amounts_sum = 0
            volumes_sum = 0
            for price, volume in asks_or_bids:
                amount = price * volume
                if amount + amounts_sum >= target_amount:
                    _amount = target_amount - amounts_sum
                    _volume = _amount / price
                    amounts_sum += _amount
                    volumes_sum += _volume
                    break
                else:
                    amounts_sum += amount
                    volumes_sum += volume
            else:
                raise ValueError('Not enough orderbook depth!')
            return amounts_sum / volumes_sum

        while True:
            _orderbook = await self.collect_queue.get()
            try:
                changed_markets = set()
                for orderbook in (_orderbook,):
                    exchange_id = orderbook['exchange']
                    market = orderbook['market']
                    timestamp = orderbook['timestamp']
                    if not orderbook['bids'] or not orderbook['asks']:
                        continue
                    bid = get_price(orderbook['bids'])
                    ask = get_price(orderbook['asks'])
                    if (
                        market not in feed[exchange_id]
                        or feed[exchange_id][market]['bid'] != bid
                        or feed[exchange_id][market]['ask'] != ask
                    ):
                        feed[exchange_id][market] = {
                            'bid': bid,
                            'ask': ask,
                            'timestamp': timestamp,
                        }
                        changed_markets.add(market)

                if changed_markets:
                    changed_feed = {
                        exchange_id: {
                            market: {
                                'bid': market_data['bid'],
                                'ask': market_data['ask'],
                                'timestamp': market_data['timestamp'],
                            }
                            for market, market_data in markets.items()
                            if market in changed_markets
                        }
                        for exchange_id, markets in feed.items()
                    }
                    await self.feed_queue.put(changed_feed)
            except asyncio.CancelledError:
                break
            except Exception as e:
                console.log(f'[red]Error in DataFeed collect_feed: {e}[/red]')
            finally:
                self.collect_queue.task_done()

    async def watch_orderbook(self, exchange: Exchange, market: str):
        console.log(
            f'[blue]📊 Starting orderbook watch for {exchange.id} {market}[/blue]'
        )

        # Optimize order book depth based on exchange
        depths = self.config.orderbook_depths

        key = (exchange.id, market)
        loop = asyncio.get_running_loop()
        self.ws_last_received[key] = loop.time()

        while True:
            try:
                orderbook = await exchange.watch_order_book(
                    market,
                    depths.get(
                        exchange.id, 20
                    ),  # Default to 20 if not specified
                )

                # Skip if orderbook is empty or invalid
                if not orderbook.get('bids') or not orderbook.get('asks'):
                    continue

                now = loop.time()
                delay = now - self.ws_last_received.get(key, now)
                self.ws_last_received[key] = now
                if delay > self.config.ws_latency_threshold:
                    console.log(
                        f'[yellow]⚠️  WS latency {delay:.2f}s for {exchange.id} {market} exceeds {self.config.ws_latency_threshold}s. Restarting...[/yellow]'
                    )
                    await exchange.close()
                    continue

                await self.collect_queue.put(
                    {
                        'exchange': exchange.id,
                        'market': orderbook['symbol'],
                        'bids': orderbook['bids'],
                        'asks': orderbook['asks'],
                        'timestamp': orderbook['timestamp'],
                    }
                )
            except asyncio.CancelledError:
                console.log(
                    f'[blue]📊 Orderbook watch cancelled for {exchange.id} {market}[/blue]'
                )
                break
            except NetworkError:
                console.log(
                    f'[yellow]⚠️  Network error for {exchange.id} {market}. Retrying in {self.config.data_feed_retry_seconds}s...[/yellow]'
                )
                await asyncio.sleep(self.config.data_feed_retry_seconds)
            except Exception as e:
                console.log(
                    f'[red]❌ Error watching {exchange.id} {market}: {e}. Retrying in {self.config.data_feed_retry_seconds}s...[/red]'
                )
                await asyncio.sleep(self.config.data_feed_retry_seconds)
