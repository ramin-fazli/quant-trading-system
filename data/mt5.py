import os
import pandas as pd
import warnings
import time
import logging
import threading
from typing import Dict, List, Tuple, Optional, Union
import MetaTrader5 as mt5
from reporting.report_generator import generate_enhanced_report
from config import TradingConfig


# --- ENVIRONMENT & CONFIG LOADING (import-safe) ---
# def load_mt5_env(env_path=None):
#     """Load .env file for MT5 credentials. Only loads once per process."""
#     from dotenv import load_dotenv
#     if env_path is None:
#         env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
#     load_dotenv(env_path, override=False)

def get_mt5_config():
    """Get TradingConfig from config module, ensuring .env is loaded first."""
    # load_mt5_env()
    from config import get_config, force_config_update
    force_config_update()
    return get_config()

# Only load config if run as script, not on import
if __name__ == "__main__":
    CONFIG = get_mt5_config()

# Setup optimized logging with proper log file path
CONFIG = get_mt5_config()
log_file_path = os.path.join(CONFIG.logs_dir, "pairs_trading.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore")

# Update log level based on config
logger.setLevel(getattr(logging, CONFIG.log_level))

# === OPTIMIZED MT5 DATA MANAGER ===
class MT5DataManager:
    """High-performance MetaTrader5 data manager with caching and optimization"""
    
    def __init__(self, config: TradingConfig):
        self.config = config
        self.is_connected = False
        self.symbol_info_cache = {}
        self.data_cache = {}
        self.last_data_update = {}
        self._lock = threading.Lock()
        
    def connect(self) -> bool:
        """Establish connection to MT5 terminal"""
            
        # Initialize MT5 connection
        if not mt5.initialize():
            logger.error(f"MT5 initialization failed: {mt5.last_error()}")
            return False
        
        # Login if credentials provided
        if self.config.mt5_login and self.config.mt5_password:
            if not mt5.login(
                login=self.config.mt5_login,
                password=self.config.mt5_password,
                server=self.config.mt5_server
            ):
                logger.error(f"MT5 login failed: {mt5.last_error()}")
                return False
                
        self.is_connected = True
        account_info = mt5.account_info()
        if account_info:
            logger.info(f"Connected to MT5 - Account: {account_info.login}, "
                       f"Balance: {account_info.balance}, Server: {account_info.server}")
        
        # Cache symbol information for better performance
        self._cache_symbol_info()
        return True
    
    def _ensure_connection(self) -> bool:
        """Check connection and attempt to reconnect if lost."""
        # mt5.terminal_info() is a lightweight way to check the connection status.
        if not self.is_connected or not mt5.terminal_info():
            logger.warning("MT5 connection lost. Attempting to reconnect...")
            self.disconnect()  # Ensure clean state before reconnecting
            time.sleep(5)  # Wait before retrying
            if self.connect():
                logger.info("Successfully reconnected to MT5.")
                return True
            else:
                logger.error("Failed to reconnect to MT5.")
                return False
        return True

    def disconnect(self):
        """Disconnect from MT5"""
        if self.is_connected:
            mt5.shutdown()
            self.is_connected = False
            logger.info("Disconnected from MT5")
    
    def _cache_symbol_info(self):
        """Cache symbol information for faster access"""
        all_symbols = set()
        for pair in self.config.pairs:
            s1, s2 = pair.split('-')
            all_symbols.update([s1, s2])
        
        for symbol in all_symbols:
            info = mt5.symbol_info(symbol)
            if info:
                self.symbol_info_cache[symbol] = {
                    'digits': getattr(info, 'digits', 5),
                    'point': getattr(info, 'point', 0.00001),
                    'spread': getattr(info, 'spread', 0),
                    'trade_contract_size': getattr(info, 'trade_contract_size', 100000),
                    'volume_min': getattr(info, 'volume_min', 0.01),
                    'volume_max': getattr(info, 'volume_max', 100.0),
                    'volume_step': getattr(info, 'volume_step', 0.01),
                    'margin_initial': getattr(info, 'margin_initial', 0),
                    'profit_mode': getattr(info, 'profit_mode', 0),
                    'swap_long': getattr(info, 'swap_long', 0),
                    'swap_short': getattr(info, 'swap_short', 0),
                    'filling_mode': getattr(info, 'filling_mode', 0),  # Add filling mode
                    'trade_mode': getattr(info, 'trade_mode', 0),      # Add trade mode
                    'session_deals': getattr(info, 'session_deals', 0),  # Add session deals
                    'session_trade': getattr(info, 'session_trade', 0),  # Add session trade
                }
                

                # Enable symbol for trading if not already enabled
                if not info.visible:
                    mt5.symbol_select(symbol, True)
                    logger.info(f"Enabled symbol {symbol} for trading")
                    
            else:
                logger.warning(f"Symbol {symbol} not found in MT5")
    
    def get_historical_data(self, symbol: str, timeframe: str, 
                          start_date: str, end_date: Optional[str] = None,
                          count: Optional[int] = None) -> pd.Series:
        """Get optimized historical data with caching"""
        if not self._ensure_connection():
            return pd.Series(dtype=float)
        
        # Convert timeframe
        tf_map = {
            "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30, "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1, "W1": mt5.TIMEFRAME_W1, "MN1": mt5.TIMEFRAME_MN1
        }
        
        if timeframe not in tf_map:
            logger.error(f"Unsupported timeframe: {timeframe}")
            return pd.Series(dtype=float)
        
        mt5_timeframe = tf_map[timeframe]
        
        try:
            if count:
                # Get specified number of bars
                rates = mt5.copy_rates_from_pos(symbol, mt5_timeframe, 0, count)
            else:
                # Get data for date range
                start_dt = pd.to_datetime(start_date)
                end_dt = pd.to_datetime(end_date) if end_date else pd.Timestamp.now()
                
                rates = mt5.copy_rates_range(symbol, mt5_timeframe, start_dt, end_dt)
            
            if rates is None or len(rates) == 0:
                logger.warning(f"No data retrieved for {symbol}")
                return pd.Series(dtype=float)
            
            # Convert to pandas with timezone-aware timestamps
            df = pd.DataFrame(rates)
            df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)
            
            # Create price series using close prices
            price_series = pd.Series(df['close'].values, index=df['time'])
            price_series.name = symbol
            
            # logger.info(f"Retrieved {len(price_series)} bars for {symbol}")
            return price_series
            
        except Exception as e:
            logger.error(f"Error getting data for {symbol}: {e}")
            return pd.Series(dtype=float)
    
    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get current market price for symbol (mid-price for indicators only)"""
        if not self._ensure_connection():
            return None
            
        tick = mt5.symbol_info_tick(symbol)
        if tick:
            return (tick.bid + tick.ask) / 2
        return None
    
    def get_current_bid_ask(self, symbol: str) -> Optional[Tuple[float, float]]:
        """Get current bid and ask prices for symbol"""
        if not self._ensure_connection():
            return None
            
        tick = mt5.symbol_info_tick(symbol)
        if tick:
            return (tick.bid, tick.ask)
        return None
    
    def get_multiple_prices(self, symbols: List[str]) -> Dict[str, float]:
        """Get current prices for multiple symbols efficiently (mid-prices for indicators)"""
        if not self._ensure_connection():
            return {}
        prices = {}
        for symbol in symbols:
            price = self.get_current_price(symbol)
            if price:
                prices[symbol] = price
        return prices
    
    def get_multiple_bid_ask(self, symbols: List[str]) -> Dict[str, Tuple[float, float]]:
        """Get current bid/ask prices for multiple symbols efficiently"""
        if not self._ensure_connection():
            return {}
        prices = {}
        for symbol in symbols:
            bid_ask = self.get_current_bid_ask(symbol)
            if bid_ask:
                prices[symbol] = bid_ask
        return prices
