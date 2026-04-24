from dataclasses import dataclass, field

from dotenv import load_dotenv
from rich.console import Console

load_dotenv()

console = Console()


@dataclass
class PositionConfig:
    usd_amount: float = 5.5
    leverage: int = 1
    size_buffer_factor: float = 1.05
    trailing_stop_mode: bool = False  # Enable trailing stops for positions


@dataclass
class BotConfig:
    # Strategy thresholds
    open_position_net_spread_threshold: float = 0.1
    close_position_raw_spread_threshold: float = 0.02
    close_position_after_seconds: int = 3 * 60 * 60  # 3 hours
    adaptive_thresholds: bool = False
    volatility_window: int = 100
    initial_open_position_net_spread_threshold: float | None = None
    initial_close_position_raw_spread_threshold: float | None = None

    position: PositionConfig = field(default_factory=PositionConfig)

    # General bot settings
    base_currency: str = 'USDT'
    debug: bool = False
    use_ui: bool = False
    use_profiler: bool = False
    top_n_markets: int = 200  # Number of markets to trade
    order_type: str = 'market'

    # Timing and retry settings
    data_feed_retry_seconds: int = 30
    balance_fetch_interval_seconds: int = 60
    status_report_interval_seconds: int = 60

    max_slippage_pct: float = 1.0

    # Data age thresholds (milliseconds)
    analyze_arbitrage_max_data_age_ms: int = 400
    open_position_max_data_age_ms: int = 200
    close_position_max_data_age_ms: int = 200

    # Exchange settings
    orderbook_depths: dict = field(
        default_factory=lambda: {
            'binance': 50,
            'bybit': 50,
        }
    )  # Depth for orderbook watch
    exchange_default_type: str = 'future'
    adjust_for_time_difference: bool = True
    enable_rate_limit: bool = True

    # UI settings
    ui_refresh_interval_seconds: float = 1.0
    live_auto_refresh_seconds: int = 2

    def __post_init__(self):
        if self.initial_open_position_net_spread_threshold is None:
            self.initial_open_position_net_spread_threshold = (
                self.open_position_net_spread_threshold
            )
        if self.initial_close_position_raw_spread_threshold is None:
            self.initial_close_position_raw_spread_threshold = (
                self.close_position_raw_spread_threshold
            )
