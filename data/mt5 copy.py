import os
import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller
from statsmodels.tsa.vector_ar.vecm import coint_johansen

import warnings
import datetime
import time
import logging
import threading
from collections import deque, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Union
import json
import MetaTrader5 as mt5
import matplotlib.pyplot as plt
import seaborn as sns
import xlsxwriter
import vectorbt as vbt
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiprocessing
from reporting.report_generator import generate_enhanced_report
from config import TradingConfig


# --- ENVIRONMENT & CONFIG LOADING (import-safe) ---
def load_mt5_env(env_path=None):
    """Load .env file for MT5 credentials. Only loads once per process."""
    from dotenv import load_dotenv
    if env_path is None:
        env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    load_dotenv(env_path, override=False)

def get_mt5_config():
    """Get TradingConfig from config module, ensuring .env is loaded first."""
    load_mt5_env()
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
logger = logging.getLogger("MT5")
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

# === OPTIMIZED STRATEGY ENGINE ===
class OptimizedPairsStrategy:
    """Vectorized and optimized pairs trading strategy"""
    
    def __init__(self, config: TradingConfig, data_manager: MT5DataManager = None):
        self.config = config
        self.data_manager = data_manager
        
    def calculate_indicators_vectorized(self, price1: pd.Series, price2: pd.Series) -> Dict:
        """Calculate all indicators using vectorized operations for maximum speed"""
        
        # Align series and handle missing data
        df = pd.concat([price1, price2], axis=1).fillna(method='ffill').dropna()
        if len(df) < max(self.config.z_period, self.config.corr_period, self.config.adf_period):
            return {}
        
        df.columns = ['price1', 'price2']
        
        # Vectorized ratio calculation
        ratio = df['price1'] / df['price2']
        
        # Rolling statistics using pandas optimized functions
        ratio_ma = ratio.rolling(self.config.z_period, min_periods=self.config.z_period//2).mean()
        ratio_std = ratio.rolling(self.config.z_period, min_periods=self.config.z_period//2).std()
        
        # Z-score calculation
        zscore = (ratio - ratio_ma) / ratio_std
        zscore_perc = zscore[-1]  # Latest value only
        # logger.info(f"[{price1.name}-{price2.name}] z-score: {zscore_perc:.2f}")      
        
        # Calculate percentage distance from mean
        distance_from_mean = abs((ratio - ratio_ma) / ratio_ma) * 100  # Convert to percentage
        distance_from_mean_perc = distance_from_mean[-1]  # Latest value only
        # logger.info(f"[{price1.name}-{price2.name}] Distance from mean: {distance_from_mean_perc:.2f}%")
        
        # Correlation using rolling window
        correlation = df['price1'].rolling(self.config.corr_period).corr(df['price2'])
        
        # Volatility measures
        vol1 = df['price1'].rolling(self.config.z_period).std() / df['price1'].rolling(self.config.z_period).mean()
        vol2 = df['price2'].rolling(self.config.z_period).std() / df['price2'].rolling(self.config.z_period).mean()
        vol_ratio = np.maximum(vol1, vol2) / np.minimum(vol1, vol2)
        
        # ADF test - skip if disabled
        if self.config.enable_adf:
            adf_pvals = self._rolling_adf_vectorized(ratio, self.config.adf_period)
        else:
            # Set to pass all tests when disabled
            adf_pvals = np.zeros(len(ratio))  # Always pass (p-value = 0)
        
        # Johansen test - skip if disabled
        if self.config.enable_johansen:
            johansen_stats, johansen_crits = self._rolling_johansen_vectorized(
                df['price1'], df['price2'], self.config.adf_period
            )
        else:
            # Set to pass all tests when disabled
            johansen_stats = np.ones(len(ratio))  # Always pass
            johansen_crits = np.zeros(len(ratio))  # Always pass
        
        # Dynamic thresholds if enabled
        if self.config.dynamic_z:
            # Prevent division by zero if min_volatility is zero or very small
            min_vol = self.config.min_volatility if self.config.min_volatility > 0 else 1e-8
            dynamic_entry = np.maximum(self.config.z_entry, self.config.z_entry * ratio_std / min_vol)
            dynamic_exit = np.maximum(self.config.z_exit, self.config.z_exit * ratio_std / min_vol)
        else:
            dynamic_entry = np.full(len(zscore), self.config.z_entry)
            dynamic_exit = np.full(len(zscore), self.config.z_exit)
        
        # Calculate cost-based filter for pair trading
        mode = os.getenv('TRADING_MODE', 'backtest').lower()
        if mode == 'backtest':
            cost_filter = pd.Series(True, index=df.index)  # Always pass cost filter in backtest mode
        else:
            cost_filter = self._calculate_cost_filter(price1.name, price2.name, df['price1'], df['price2'])
        
        # Suitability filter - build conditionally based on enabled tests and parameters
        suitable_conditions = []
        
        # Core conditions that are always checked regardless of enable flags
        if self.config.min_volatility > 0:
            suitable_conditions.append(ratio_std > self.config.min_volatility)
            
        if self.config.min_distance > 0:
            suitable_conditions.append(distance_from_mean > self.config.min_distance)
            
        # Always check cost filter as it's critical for profitability
        suitable_conditions.append(cost_filter)
        
        # Optional conditions based on enable flags and their respective parameters
        if self.config.enable_correlation and self.config.min_corr > 0:
            suitable_conditions.append(correlation > self.config.min_corr)
            
        if self.config.enable_adf and self.config.max_adf_pval < 1:
            suitable_conditions.append(adf_pvals < self.config.max_adf_pval)
            
        if self.config.enable_johansen and self.config.johansen_crit_level > 0:
            suitable_conditions.append(johansen_stats > johansen_crits)
            
        if self.config.enable_vol_ratio and self.config.vol_ratio_max < float('inf'):
            suitable_conditions.append(vol_ratio <= self.config.vol_ratio_max)
        
        # Initialize suitable as True if no conditions, otherwise combine all conditions
        if not suitable_conditions:
            suitable = pd.Series(True, index=ratio.index)
        else:
            suitable = suitable_conditions[0]
            for condition in suitable_conditions[1:]:
                suitable = suitable & condition
        
        return {
            'df': df,
            'ratio': ratio,
            'ratio_ma': ratio_ma,
            'ratio_std': ratio_std,
            'zscore': zscore,
            'correlation': correlation,
            'vol_ratio': vol_ratio,
            'adf_pvals': adf_pvals,
            'johansen_stats': johansen_stats,
            'johansen_crits': johansen_crits,
            'dynamic_entry': dynamic_entry,
            'dynamic_exit': dynamic_exit,
            'suitable': suitable,
            'cost_filter': cost_filter
        }
    
    def _calculate_cost_filter(self, symbol1: str, symbol2: str, price1: pd.Series, price2: pd.Series) -> pd.Series:
        """Calculate cost-based filter for pair trading suitability"""
        
        # If no data manager available (backtesting), assume costs are acceptable
        if not self.data_manager or not hasattr(self.data_manager, 'symbol_info_cache'):
            logger.warning("No data manager available for cost calculation - assuming costs are acceptable")
            return pd.Series(True, index=price1.index)
        
        # Get symbol information from cache
        if symbol1 not in self.data_manager.symbol_info_cache or symbol2 not in self.data_manager.symbol_info_cache:
            logger.warning(f"Symbol info not available for {symbol1} or {symbol2} - assuming costs are acceptable")
            return pd.Series(True, index=price1.index)
        
        info1 = self.data_manager.symbol_info_cache[symbol1]
        info2 = self.data_manager.symbol_info_cache[symbol2]
        
        # Calculate spreads as percentage of price
        spread1_perc = (info1['spread'] * info1['point']) / price1 * 100
        spread2_perc = (info2['spread'] * info2['point']) / price2 * 100
        
        # Determine if symbols are stocks/ETFs based on naming convention
        def is_stock_or_etf(symbol: str) -> bool:
            """Check if symbol is a stock or ETF based on naming patterns"""
            # Common patterns for stocks and ETFs
            stock_patterns = [
                '.US',      # US stocks (AAPL.US, MSFT.US)
            ]
            
            # Check for stock exchange suffixes
            for pattern in stock_patterns:
                if pattern in symbol:
                    return True
            
            return False
        
        # Calculate commission based on instrument type
        if is_stock_or_etf(symbol1):
            commission1_perc = (self.config.commission_fixed / price1) * 100
        else:
            commission1_perc = pd.Series(0.0, index=price1.index)  # No fixed commission for non-stocks
        
        if is_stock_or_etf(symbol2):
            commission2_perc = (self.config.commission_fixed / price2) * 100
        else:
            commission2_perc = pd.Series(0.0, index=price2.index)  # No fixed commission for non-stocks
        
        # Total cost per trade (open + close) for both legs
        # Each leg: commission (open) + commission (close) + spread (paid once per round-trip)
        # Calculate cost per leg, then take weighted average based on position sizes
        leg1_total_cost_perc = (2 * commission1_perc + spread1_perc)
        leg2_total_cost_perc = (2 * commission2_perc + spread2_perc)
        
        # Weight the costs by the monetary value of each leg (equal weighting for balanced pairs)
        total_cost_perc = (leg1_total_cost_perc + leg2_total_cost_perc) / 2
        
        # Create boolean filter where cost is acceptable
        cost_acceptable = total_cost_perc <= self.config.max_commission_perc
        
        # Log cost information for monitoring with instrument type details
        if len(cost_acceptable) > 0:
            avg_cost = total_cost_perc.mean()
            symbol1_type = "Stock/ETF" if is_stock_or_etf(symbol1) else "Other"
            symbol2_type = "Stock/ETF" if is_stock_or_etf(symbol2) else "Other"
            
            # logger.info(f"Cost filter for {symbol1}-{symbol2}: avg_cost={avg_cost:.4f}%, threshold={self.config.max_commission_perc:.4f}%, "
            #            f"acceptable={cost_acceptable.mean()*100:.1f}% of time")
            # logger.info(f"  {symbol1} ({symbol1_type}): commission={commission1_perc.mean():.4f}%, spread={spread1_perc.mean():.4f}%")
            # logger.info(f"  {symbol2} ({symbol2_type}): commission={commission2_perc.mean():.4f}%, spread={spread2_perc.mean():.4f}%")
        
        return cost_acceptable

    def _rolling_adf_vectorized(self, series: pd.Series, window: int) -> np.ndarray:
        """Optimized rolling ADF test"""
        pvals = np.full(len(series), np.nan)
        
        # Use numpy arrays for faster computation
        values = series.values
        
        for i in range(window, len(values)):
            try:
                window_data = values[i-window+1:i+1]
                if len(np.unique(window_data)) > 1:  # Avoid constant series
                    pvals[i] = adfuller(window_data, autolag='AIC')[1]
            except:
                pvals[i] = 1.0  # Conservative assumption
                
        return pvals
    
    def _rolling_johansen_vectorized(self, series1: pd.Series, series2: pd.Series, 
                                   window: int) -> Tuple[np.ndarray, np.ndarray]:
        """Optimized rolling Johansen test"""
        stats = np.full(len(series1), np.nan)
        crits = np.full(len(series1), np.nan)
        
        crit_idx = {90: 0, 95: 1, 99: 2}[self.config.johansen_crit_level]
        
        # Convert to numpy for speed
        vals1 = series1.values
        vals2 = series2.values
        
        for i in range(window, len(vals1)):
            try:
                data = np.column_stack([vals1[i-window+1:i+1], vals2[i-window+1:i+1]])
                if np.std(data[:, 0]) > 0 and np.std(data[:, 1]) > 0:
                    result = coint_johansen(data, det_order=0, k_ar_diff=1)
                    stats[i] = result.lr1[0]
                    crits[i] = result.cvt[0, crit_idx]
            except:
                stats[i] = 0.0
                crits[i] = 999.0  # High threshold for failed tests
                
        return stats, crits
    
    def generate_signals_vectorized(self, indicators: Dict) -> pd.DataFrame:
        """Generate trading signals using vectorized operations"""
        
        if not indicators:
            return pd.DataFrame()
        
        zscore = indicators['zscore']
        suitable = indicators['suitable']
        dynamic_entry = indicators['dynamic_entry']
        dynamic_exit = indicators['dynamic_exit']
        
        signals = pd.DataFrame(index=indicators['df'].index)
        signals['zscore'] = zscore
        signals['suitable'] = suitable
        
        # SIMPLIFIED signal generation - trigger when Z-score exceeds threshold
        # Remove the transition requirement for more aggressive entries
        signals['long_entry'] = (
            (zscore < -dynamic_entry) & 
            suitable
        )
        
        signals['short_entry'] = (
            (zscore > dynamic_entry) & 
            suitable
        )
        
        signals['long_exit'] = (
            (zscore > -dynamic_exit)
        )
        
        signals['short_exit'] = (
            (zscore < dynamic_exit)
        )
        
        return signals


# === REAL-TIME TRADING ENGINE ===
class MT5RealTimeTrader:
    """High-performance real-time trading engine for MetaTrader5"""
    
    def __init__(self, config: TradingConfig, data_manager: MT5DataManager):
        self.config = config
        self.data_manager = data_manager
        self.strategy = OptimizedPairsStrategy(config, data_manager)
        
        # Trading state
        self.active_positions = {}
        self.pair_states = {}
        self.is_trading = False
        self.last_update = {}
        
        # Performance optimization
        self._price_buffer = defaultdict(lambda: deque(maxlen=1000))
        self._update_lock = threading.Lock()
        
        # Add drawdown tracking
        self.portfolio_peak_value = config.initial_portfolio_value
        self.pair_peak_values = {}
        self.suspended_pairs = set()
        self.portfolio_trading_suspended = False
    
    def _calculate_portfolio_current_value(self) -> float:
        """Calculate current portfolio value including unrealized P&L"""
        current_value = self.config.initial_portfolio_value
        
        for pair_str, position in self.active_positions.items():
            try:
                bid_ask_prices = self.data_manager.get_multiple_bid_ask([position['symbol1'], position['symbol2']])
                if len(bid_ask_prices) != 2:
                    continue
                
                bid1, ask1 = bid_ask_prices[position['symbol1']]
                bid2, ask2 = bid_ask_prices[position['symbol2']]
                
                # Calculate P&L based on position direction and order types
                if position['order1_type'] == 'buy':
                    current_market_price1 = bid1
                else:
                    current_market_price1 = ask1
                
                if position['order2_type'] == 'buy':
                    current_market_price2 = bid2
                else:
                    current_market_price2 = ask2
                
                current_value1 = position['volume1'] * current_market_price1 * self.data_manager.symbol_info_cache[position['symbol1']]['trade_contract_size']
                current_value2 = position['volume2'] * current_market_price2 * self.data_manager.symbol_info_cache[position['symbol2']]['trade_contract_size']
                
                entry_value1 = position['volume1'] * position['entry_exec_price1'] * self.data_manager.symbol_info_cache[position['symbol1']]['trade_contract_size']
                entry_value2 = position['volume2'] * position['entry_exec_price2'] * self.data_manager.symbol_info_cache[position['symbol2']]['trade_contract_size']
                
                if position['direction'] == 'LONG':
                    pnl = (current_value1 - entry_value1) + (entry_value2 - current_value2)
                else:
                    pnl = (entry_value1 - current_value1) + (current_value2 - entry_value2)
                
                current_value += pnl
                
            except Exception as e:
                logger.error(f"Error calculating P&L for {pair_str}: {e}")
        
        return current_value

    def _calculate_position_pnl(self, pair_str: str) -> float:
        """Calculate the current P&L for a given pair position in dollar terms"""
        if pair_str not in self.active_positions:
            return 0.0
        
        position = self.active_positions[pair_str]
        s1 = position['symbol1']
        s2 = position['symbol2']
        
        try:
            # Get current bid/ask prices for accurate P&L calculation
            bid_ask_prices = self.data_manager.get_multiple_bid_ask([s1, s2])
            if s1 not in bid_ask_prices or s2 not in bid_ask_prices:
                return 0.0
            
            bid1, ask1 = bid_ask_prices[s1]
            bid2, ask2 = bid_ask_prices[s2]
            
            # Determine close prices based on order types
            if position['order1_type'] == 'buy':
                close_price1 = bid1
            else:
                close_price1 = ask1
            
            if position['order2_type'] == 'buy':
                close_price2 = bid2
            else:
                close_price2 = ask2
            
            # Calculate values
            contract_size1 = self.data_manager.symbol_info_cache[s1]['trade_contract_size']
            contract_size2 = self.data_manager.symbol_info_cache[s2]['trade_contract_size']
            
            entry_value1 = position['volume1'] * position['entry_exec_price1'] * contract_size1
            entry_value2 = position['volume2'] * position['entry_exec_price2'] * contract_size2
            close_value1 = position['volume1'] * close_price1 * contract_size1
            close_value2 = position['volume2'] * close_price2 * contract_size2
            
            if position['direction'] == 'LONG':
                pnl_dollar = (close_value1 - entry_value1) + (entry_value2 - close_value2)
            else:
                pnl_dollar = (entry_value1 - close_value1) + (close_value2 - entry_value2)
            
            return pnl_dollar
            
        except Exception as e:
            logger.error(f"Error calculating P&L for {pair_str}: {e}")
            return 0.0

    def _check_drawdown_limits(self, pair_str: str = None) -> bool:
        """Check if trading should be allowed based on drawdown limits"""
        
        # Check portfolio-level drawdown
        current_value = self._calculate_portfolio_current_value()
        if current_value > self.portfolio_peak_value:
            self.portfolio_peak_value = current_value
        
        portfolio_drawdown = ((self.portfolio_peak_value - current_value) / self.portfolio_peak_value) * 100
        
        # Update portfolio suspension status
        if portfolio_drawdown > self.config.max_portfolio_drawdown_perc:
            if not self.portfolio_trading_suspended:
                logger.warning(f"Portfolio drawdown limit exceeded: {portfolio_drawdown:.2f}% > {self.config.max_portfolio_drawdown_perc:.2f}%")
                logger.warning("All new trading suspended until drawdown improves")
                self.portfolio_trading_suspended = True
            return False
        elif self.portfolio_trading_suspended and portfolio_drawdown <= self.config.max_portfolio_drawdown_perc * 0.8:  # 80% recovery rule
            logger.info(f"Portfolio drawdown improved to {portfolio_drawdown:.2f}%. Resuming trading")
            self.portfolio_trading_suspended = False
        
        # If checking specific pair
        if pair_str and pair_str in self.active_positions:
            position = self.active_positions[pair_str]
            if pair_str not in self.pair_peak_values:
                self.pair_peak_values[pair_str] = 0
            
            # Calculate pair P&L - use fallback calculation if monetary values are missing
            total_initial_value = (position.get('monetary_value1', 0) + position.get('monetary_value2', 0)) / 2
            if total_initial_value == 0:
                # Fallback calculation if monetary values are missing
                contract_size1 = self.data_manager.symbol_info_cache[position['symbol1']]['trade_contract_size']
                contract_size2 = self.data_manager.symbol_info_cache[position['symbol2']]['trade_contract_size']
                entry_value1 = position['volume1'] * position['entry_exec_price1'] * contract_size1
                entry_value2 = position['volume2'] * position['entry_exec_price2'] * contract_size2
                total_initial_value = (abs(entry_value1) + abs(entry_value2)) / 2
            
            current_pnl = self._calculate_position_pnl(pair_str)
            
            # Update pair peak value
            if current_pnl > self.pair_peak_values[pair_str]:
                self.pair_peak_values[pair_str] = current_pnl
            
            # Calculate pair drawdown
            pair_drawdown = ((self.pair_peak_values[pair_str] - current_pnl) / total_initial_value) * 100
            
            # Check pair-level drawdown
            if pair_drawdown > self.config.max_pair_drawdown_perc:
                if pair_str not in self.suspended_pairs:
                    logger.warning(f"Pair {pair_str} drawdown limit exceeded: {pair_drawdown:.2f}% > {self.config.max_pair_drawdown_perc:.2f}%")
                    logger.warning(f"Suspending trading for {pair_str} until drawdown improves")
                    self.suspended_pairs.add(pair_str)
                return False
            elif pair_str in self.suspended_pairs and pair_drawdown <= self.config.max_pair_drawdown_perc * 0.8:  # 80% recovery rule
                logger.info(f"Pair {pair_str} drawdown improved to {pair_drawdown:.2f}%. Resuming trading")
                self.suspended_pairs.remove(pair_str)
        
        return True

    def _can_open_new_position(self, estimated_position_size: float = None) -> Tuple[bool, str]:
        """Check if we can open a new position based on portfolio limits"""
        
        # Check if trading is suspended due to drawdown
        if self.portfolio_trading_suspended:
            return False, "Trading suspended due to portfolio drawdown limit"
        
        # Check position count limit
        current_positions = len(self.active_positions)
        if current_positions >= self.config.max_open_positions:
            return False, f"Position count limit reached: {current_positions}/{self.config.max_open_positions}"
        
        # Check monetary exposure limit
        current_exposure = self._calculate_total_exposure()
        if estimated_position_size:
            projected_exposure = current_exposure + estimated_position_size
            if projected_exposure > self.config.max_monetary_exposure:
                return False, f"Monetary exposure limit would be exceeded: ${projected_exposure:.2f} > ${self.config.max_monetary_exposure:.2f}"
        
        return True, f"OK - Positions: {current_positions}/{self.config.max_open_positions}, Exposure: ${current_exposure:.2f}/${self.config.max_monetary_exposure:.2f}"

    def initialize(self) -> bool:
        """Initialize real-time trading system"""
        if not self.data_manager.is_connected:
            logger.error("MT5 not connected")
            return False
        
        # Attempt to load previous state
        if not self._load_state():
            # Initialize pair states with recent historical data if no state was loaded
            logger.info("Initializing pair states with historical data...")
            
            lookback_bars = max(self.config.z_period, self.config.corr_period, self.config.adf_period) * 2
            
            for pair_str in self.config.pairs:
                s1, s2 = pair_str.split('-')
                
                # Get recent data for both symbols
                data1 = self.data_manager.get_historical_data(s1, self.config.interval, 
                                                             self.config.start_date, count=lookback_bars)
                data2 = self.data_manager.get_historical_data(s2, self.config.interval,
                                                             self.config.start_date, count=lookback_bars)
                
                if not data1.empty and not data2.empty:
                    self.pair_states[pair_str] = {
                        'symbol1': s1,
                        'symbol2': s2,
                        'price1': data1,
                        'price2': data2,
                        'position': None,
                        'last_signal': None,
                        'cooldown': 0,
                        'last_update': datetime.datetime.now(),
                        'last_candle_time': data1.index[-1] if len(data1) > 0 else None  # Track last candle timestamp
                    }
                    logger.info(f"Initialized {pair_str} with {len(data1)} bars")
                else:
                    logger.warning(f"Failed to initialize {pair_str}")
        
        self.is_trading = True
        logger.info(f"Real-time trading initialized for {len(self.pair_states)} pairs")
        return True
    
    def start_trading(self):
        """Start the real-time trading loop with auto-restart."""
        if not self.is_trading:
            logger.error("Trading not initialized")
            return

        logger.info("Starting real-time trading loop...")

        while self.is_trading:
            try:
                # Start price monitoring thread
                price_thread = threading.Thread(target=self._price_monitoring_loop, daemon=True)
                price_thread.start()
                logger.info("Price monitoring thread started")
                
                # Start signal processing thread
                signal_thread = threading.Thread(target=self._signal_processing_loop, daemon=True)
                signal_thread.start()
                logger.info("Signal processing thread started")
                
                # Log system status every 5 minutes
                status_counter = 0
                save_counter = 0
                
                # Main monitoring loop
                while self.is_trading:
                    if not price_thread.is_alive() or not signal_thread.is_alive():
                        logger.error("A critical trading thread has died. Attempting to restart...")
                        raise RuntimeError("Critical thread failure")

                    self._monitor_positions()
                    
                    # Status update every 5 minutes (300 seconds / 1 second = 300 loops)
                    status_counter += 1
                    if status_counter % 300 == 0:
                        logger.info("=== TRADING SYSTEM STATUS ===")
                        logger.info(f"Active pairs: {len(self.pair_states)}")
                        logger.info(f"Open positions: {len(self.active_positions)}")
                        logger.info(f"Threads running: Price monitoring and signal processing")
                        if self.data_manager._ensure_connection():
                            account_info = mt5.account_info()
                            logger.info(f"Account balance: {account_info.balance if account_info else 'N/A'}")
                        else:
                            logger.warning("Could not retrieve account balance due to connection issue.")
                        logger.info("==============================")
                    
                    # Save state every 15 minutes
                    # save_counter += 1
                    # if save_counter % 900 == 0:
                    self._save_state()

                    time.sleep(10)  # Check positions every second
                    
            except KeyboardInterrupt:
                logger.info("Trading stopped by user")
                break  # Exit the while loop
            except Exception as e:
                logger.error(f"Main trading loop crashed: {e}. Restarting threads in 15 seconds...")
                time.sleep(10)
    
    def _price_monitoring_loop(self):
        """Continuous price monitoring loop"""
        loop_count = 0
        while self.is_trading:
            try:
                loop_count += 1
                
                # Get all symbols from pairs
                all_symbols = set()
                for pair_str in self.pair_states.keys():
                    s1, s2 = pair_str.split('-')
                    all_symbols.update([s1, s2])
                
                # Get current prices
                current_prices = self.data_manager.get_multiple_prices(list(all_symbols))
                
                # Log price updates every 60 seconds (60 loops)
                # if loop_count % 60 == 0:
                #     logger.info(f"Price monitoring active - Retrieved {len(current_prices)} prices")
                #     for symbol, price in list(current_prices.items())[:3]:  # Show first 3 prices
                #         logger.info(f"  {symbol}: {price}")
                
                # Update price buffers and pair states
                with self._update_lock:
                    current_time = datetime.datetime.now()
                    
                    for symbol, price in current_prices.items():
                        self._price_buffer[symbol].append((current_time, price))
                    
                    # Update pair states when new candle completes
                    self._check_for_new_candles(current_time)
                
                time.sleep(1)  # Update prices every second
                
            except Exception as e:
                logger.error(f"Error in price monitoring: {e}")
                time.sleep(5)
    
    def _check_for_new_candles(self, current_time: datetime.datetime):
        """Check if new candles are available and update pair states"""
        
        # Convert interval to minutes
        interval_minutes = {'M1': 1, 'M5': 5, 'M15': 15, 'M30': 30, 'H1': 60, 'H4': 240, 'D1': 1440}
        minutes = interval_minutes.get(self.config.interval, 15)
        
        for pair_str, state in self.pair_states.items():
            s1, s2 = state['symbol1'], state['symbol2']
            
            # Check if enough time has passed for a new candle
            if (current_time - state['last_update']).total_seconds() >= minutes * 60:
                
                # Get latest candle for both symbols
                latest1 = self.data_manager.get_historical_data(s1, self.config.interval, 
                                                               self.config.start_date, count=1)
                latest2 = self.data_manager.get_historical_data(s2, self.config.interval,
                                                               self.config.start_date, count=1)
                
                if not latest1.empty and not latest2.empty:
                    # Check if we have a new candle (timestamp is different from last)
                    new_candle_time = latest1.index[-1]
                    
                    if state['last_candle_time'] is None or new_candle_time > state['last_candle_time']:
                        # New candle detected - decrement cooldown for this pair
                        if state['cooldown'] > 0:
                            state['cooldown'] -= 1
                            logger.info(f"New candle for {pair_str} - Cooldown reduced to {state['cooldown']} bars")
                        
                        # Add new data points
                        state['price1'] = pd.concat([state['price1'], latest1]).drop_duplicates().tail(1000)
                        state['price2'] = pd.concat([state['price2'], latest2]).drop_duplicates().tail(1000)
                        state['last_update'] = current_time
                        state['last_candle_time'] = new_candle_time
                        
                        logger.debug(f"Updated {pair_str} with new candle data at {new_candle_time}")

    def _signal_processing_loop(self):
        """Process trading signals for all pairs"""
        loop_count = 0
        while self.is_trading:
            try:
                loop_count += 1
                
                # Log signal processing status every 60 seconds (12 loops * 5 seconds)
                if loop_count % 1 == 0:
                    try:
                        current_exposure = self._calculate_total_exposure() or 0.0
                        current_portfolio_value = self._calculate_portfolio_current_value() or 0.0
                        portfolio_drawdown = 0.0
                        if self.portfolio_peak_value > 0:
                            portfolio_drawdown = ((self.portfolio_peak_value - current_portfolio_value) / self.portfolio_peak_value) * 100
                        
                        # Calculate P&L metrics
                        total_open_pnl = 0.0
                        pairs_info = []
                        
                        for pair_str, position in self.active_positions.items():
                            try:
                                bid_ask_prices = self.data_manager.get_multiple_bid_ask([position['symbol1'], position['symbol2']])
                                if len(bid_ask_prices) != 2:
                                    continue
                                
                                # Calculate current P&L for pair
                                bid1, ask1 = bid_ask_prices[position['symbol1']]
                                bid2, ask2 = bid_ask_prices[position['symbol2']]
                                current_price1 = bid1 if position['order1_type'] == 'buy' else ask1
                                current_price2 = bid2 if position['order2_type'] == 'buy' else ask2
                                
                                value1 = position['volume1'] * current_price1 * self.data_manager.symbol_info_cache[position['symbol1']]['trade_contract_size']
                                value2 = position['volume2'] * current_price2 * self.data_manager.symbol_info_cache[position['symbol2']]['trade_contract_size']
                                total_value = (value1 + value2) / 2
                                
                                entry_value1 = position['volume1'] * position['entry_exec_price1'] * self.data_manager.symbol_info_cache[position['symbol1']]['trade_contract_size']
                                entry_value2 = position['volume2'] * position['entry_exec_price2'] * self.data_manager.symbol_info_cache[position['symbol2']]['trade_contract_size']
                                
                                # Calculate current values for both legs
                                current_value1 = position['volume1'] * current_price1 * self.data_manager.symbol_info_cache[position['symbol1']]['trade_contract_size']
                                current_value2 = position['volume2'] * current_price2 * self.data_manager.symbol_info_cache[position['symbol2']]['trade_contract_size']

                                if position['direction'] == 'LONG':
                                    pair_pnl = (current_value1 - entry_value1) + (entry_value2 - current_value2)
                                else:
                                    pair_pnl = (entry_value1 - current_value1) + (current_value2 - entry_value2)
                                
                                total_open_pnl += pair_pnl
                                pairs_info.append((pair_str, pair_pnl, total_value))
                            except Exception as e:
                                logger.error(f"Error calculating P&L for {pair_str}: {e}")
                        
                        # Portfolio Status Table
                        logger.info("")
                        logger.info("=" * 80)
                        logger.info("PORTFOLIO STATUS")
                        logger.info("-" * 80)
                        logger.info(f"Active Pairs     : {len(self.pair_states)}")
                        logger.info(f"Open Positions   : {len(self.active_positions)}/{self.config.max_open_positions}")
                        logger.info(f"Current Value    : ${current_portfolio_value:,.2f}")
                        # Safe exposure percentage calculation
                        exposure_pct = 0.0
                        if self.config.max_monetary_exposure > 0:
                            exposure_pct = (current_exposure/self.config.max_monetary_exposure) * 100
                        
                        logger.info(f"Exposure         : ${current_exposure:,.2f}/{self.config.max_monetary_exposure:,.2f} ({exposure_pct:.1f}%)")
                        logger.info(f"Drawdown         : {portfolio_drawdown:.2f}%")
                        logger.info(f"Trading Status   : {'SUSPENDED' if self.portfolio_trading_suspended else 'ACTIVE'}")
                        logger.info(f"Suspended Pairs  : {len(self.suspended_pairs)}")
                           
                        # Calculate percentage P&Ls
                        open_pnl_pct = (total_open_pnl / current_portfolio_value * 100) if current_portfolio_value > 0 else 0
                        realized_pnl = current_portfolio_value - self.config.initial_portfolio_value - total_open_pnl
                        realized_pnl_pct = (realized_pnl / self.config.initial_portfolio_value * 100)
                        
                        logger.info(f"Open P&L        : ${total_open_pnl:,.2f} ({open_pnl_pct:.2f}%)")
                        logger.info(f"Realized P&L    : ${realized_pnl:,.2f} ({realized_pnl_pct:.2f}%)")
                        
                        # Active Pairs P&L Table
                        if pairs_info:
                            logger.info("-" * 80)
                            logger.info("ACTIVE PAIRS P&L")
                            logger.info("-" * 80)
                            logger.info("PAIR            P&L($)      P&L(%)   VALUE($)")
                            logger.info("-" * 80)
                            for pair_str, pnl, value in sorted(pairs_info, key=lambda x: abs(x[1]), reverse=True):
                                pnl_pct = (pnl / value * 100) if value > 0 else 0
                                logger.info(f"{pair_str:<15} {pnl:>8,.2f}  {pnl_pct:>8.2f}  {value:>9,.0f}")
                        
                        logger.info("=" * 80)
                        logger.info("")
                        
                        # Continue with signal analysis...
                    except Exception as e:
                        logger.error(f"Error generating status report: {str(e)}")
                
                with self._update_lock:
                    for pair_str in list(self.pair_states.keys()):
                        self._process_pair_signals(pair_str)
                
                time.sleep(10)  # Process signals every 5 seconds
                
            except Exception as e:
                logger.error(f"Error in signal processing: {e}")
                time.sleep(10)
    
    def _process_pair_signals(self, pair_str: str):
        """Process trading signals for a specific pair"""
        state = self.pair_states[pair_str]

        # Skip if not enough data
        if len(state['price1']) < self.config.z_period or len(state['price2']) < self.config.z_period:
            logger.debug(f"Not enough data for {pair_str}: {len(state['price1'])} vs {self.config.z_period} required")
            return
        
        # Calculate indicators
        indicators = self.strategy.calculate_indicators_vectorized(state['price1'], state['price2'])
        if not indicators:
            logger.debug(f"No indicators calculated for {pair_str}")
            return
        
        # Generate signals
        signals = self.strategy.generate_signals_vectorized(indicators)
        if signals.empty:
            logger.debug(f"No signals generated for {pair_str}")
            return
        
        # Get latest signal
        latest_signal = signals.iloc[-1]
        current_position = state['position']
        has_active_position = pair_str in self.active_positions

        # Entry logic with enhanced portfolio-level checks
        if current_position is None and not has_active_position:
            if state['cooldown'] == 0:
                # Check if we can open a new position (portfolio level limits)
                can_open, limit_reason = self._can_open_new_position()
                if not can_open:
                    logger.info(f"Portfolio limit prevents entry for {pair_str}: {limit_reason}")
                    return
                # Check drawdown limits (portfolio and pair)
                if not self._check_drawdown_limits(pair_str):
                    logger.info(f"Drawdown limit prevents entry for {pair_str}")
                    return
                # Use all entry filters from config
                if latest_signal['long_entry'] and latest_signal['suitable']:
                    logger.info(f"[LONG ENTRY] Signal trigger for {pair_str} - {limit_reason}")
                    if self._execute_pair_trade(pair_str, 'LONG'):
                        state['position'] = 'LONG'
                        # No cooldown on entry - only after losing trades
                    else:
                        logger.error(f"[ERROR] Failed to execute LONG trade for {pair_str}")
                        
                elif latest_signal['short_entry'] and latest_signal['suitable']:
                    logger.info(f"[SHORT ENTRY] Signal trigger for {pair_str} - {limit_reason}")
                    if self._execute_pair_trade(pair_str, 'SHORT'):
                        state['position'] = 'SHORT'
                        # No cooldown on entry - only after losing trades
                    else:
                        logger.error(f"[ERROR] Failed to execute SHORT trade for {pair_str}")

            else:
                logger.info(f"Cooldown active for {pair_str}: {state['cooldown']} bars remaining")
        else:
            if current_position is not None or has_active_position:
                logger.debug(f"Skipping entry for {pair_str}: already has position")

        # Exit logic
        if current_position is not None or has_active_position:
            should_exit = False
            exit_reason = ""
            
            if current_position == 'LONG' and latest_signal['long_exit']:
                should_exit = True
                exit_reason = "LONG EXIT signal"
                
            elif current_position == 'SHORT' and latest_signal['short_exit']:
                should_exit = True
                exit_reason = "SHORT EXIT signal"
            
            if should_exit:
                logger.info(f"[EXIT] {exit_reason} for {pair_str}")
                result = self._close_pair_position(pair_str)
                if isinstance(result, tuple) and result[0]:
                    pnl = result[1]
                    logger.info(f"[SUCCESS] Signal-based position closed for {pair_str} with P&L: {pnl:.2f}%")
                else:
                    logger.error(f"[ERROR] Failed to close position for {pair_str}")

    def _execute_pair_trade(self, pair_str: str, direction: str) -> bool:
        """Execute a pairs trade with proper risk management and equal monetary exposure"""
        s1, s2 = pair_str.split('-')
        
        # Get current bid/ask prices for accurate P&L calculation
        bid_ask_prices = self.data_manager.get_multiple_bid_ask([s1, s2])
        if s1 not in bid_ask_prices or s2 not in bid_ask_prices:
            logger.error(f"Cannot get bid/ask prices for {pair_str}")
            return False
        
        bid1, ask1 = bid_ask_prices[s1]
        bid2, ask2 = bid_ask_prices[s2]
        
        # Use mid-prices for volume calculation (consistent with indicators)
        mid_price1 = (bid1 + ask1) / 2
        mid_price2 = (bid2 + ask2) / 2
        
        # Final portfolio limit check before execution with estimated position size
        estimated_position_size = self.config.max_position_size
        can_open, limit_reason = self._can_open_new_position(estimated_position_size)
        
        if not can_open:
            logger.error(f"Portfolio limit check failed before execution for {pair_str}: {limit_reason}")
            return False
        
        # Calculate balanced volumes for equal monetary exposure using mid-prices
        volumes = self._calculate_balanced_volumes(s1, s2, mid_price1, mid_price2)
        if volumes is None:
            logger.error(f"Cannot calculate balanced volumes for {pair_str}")
            return False
        volume1, volume2, monetary_value1, monetary_value2 = volumes
        # Validate monetary values are within tolerance
        value_diff_pct = abs(monetary_value1 - monetary_value2) / max(monetary_value1, monetary_value2)
        if value_diff_pct > self.config.monetary_value_tolerance:
            return False
        # Check max position size per pair
        if (monetary_value1 + monetary_value2) > self.config.max_position_size:
            logger.error(f"Position size for {pair_str} exceeds max_position_size")
            return False
        # Determine execution prices based on order direction
        if direction == 'LONG':
            # Long ratio: Buy s1 (use ask), Sell s2 (use bid)
            exec_price1 = ask1
            exec_price2 = bid2
            order1_type = 'buy'
            order2_type = 'sell'
        else:
            # Short ratio: Sell s1 (use bid), Buy s2 (use ask)
            exec_price1 = bid1
            exec_price2 = ask2
            order1_type = 'sell'
            order2_type = 'buy'
        
        # logger.info(f"Executing {direction} trade for {pair_str}:")
        # logger.info(f"  {s1}: {order1_type} volume={volume1:.4f} at {exec_price1:.5f} (bid={bid1:.5f}, ask={ask1:.5f})")
        # logger.info(f"  {s2}: {order2_type} volume={volume2:.4f} at {exec_price2:.5f} (bid={bid2:.5f}, ask={ask2:.5f})")
        # logger.info(f"  Value difference: {value_diff_pct:.4f} (within tolerance)")
        # logger.info(f"  Portfolio will have: {len(self.active_positions)+1}/{self.config.max_open_positions} positions")
        
        # Execute trades
        order1 = self._send_order(s1, order1_type, volume1)
        order2 = self._send_order(s2, order2_type, volume2)
        
        if order1 and order2:
            # Store position info with actual execution prices and current bid/ask
            self.active_positions[pair_str] = {
                'direction': direction,
                'symbol1': s1,
                'symbol2': s2,
                'ticket1': order1,
                'ticket2': order2,
                'entry_time': datetime.datetime.now(),
                'entry_exec_price1': exec_price1,  # Actual execution price
                'entry_exec_price2': exec_price2,  # Actual execution price
                'entry_mid_price1': mid_price1,    # Mid-price for ratio calculation
                'entry_mid_price2': mid_price2,    # Mid-price for ratio calculation
                'volume1': volume1,
                'volume2': volume2,
                'monetary_value1': monetary_value1,
                'monetary_value2': monetary_value2,
                'max_favorable_pnl': 0.0,
                'trailing_stop_level': 0.0,
                'order1_type': order1_type,  # Store order types for P&L calculation
                'order2_type': order2_type
            }
            
            # Log updated portfolio status
            current_exposure = self._calculate_total_exposure()
            logger.info(f"Successfully executed {direction} trade for {pair_str}")
            # logger.info(f"Portfolio now: {len(self.active_positions)}/{self.config.max_open_positions} positions, ${current_exposure:.2f}/${self.config.max_monetary_exposure:.2f} exposure")
            return True
        else:
            logger.error(f"Failed to execute one or both orders for {pair_str}")
            # If one order succeeded but the other failed, we should close the successful one
            if order1 and not order2:
                logger.warning(f"Rolling back {s1} order due to {s2} failure")
                mt5.Close(symbol=s1, ticket=order1)
            elif order2 and not order1:
                logger.warning(f"Rolling back {s2} order due to {s1} failure")
                mt5.Close(symbol=s2, ticket=order2)
        
        return False
    
    def _get_filling_mode(self, symbol: str) -> int:
        """Get appropriate filling mode for symbol based on supported modes"""
        if symbol not in self.data_manager.symbol_info_cache:
            return mt5.ORDER_FILLING_IOC  # Default to IOC if info not available

        filling_mode = self.data_manager.symbol_info_cache[symbol]['filling_mode']

        # Use integer value directly to return MT5 constants
        if filling_mode == 1:
            return mt5.ORDER_FILLING_FOK
        elif filling_mode == 2:
            return mt5.ORDER_FILLING_IOC
        else:
            return mt5.ORDER_FILLING_RETURN  # Fallback to RETURN mode
        

    def _send_order(self, symbol: str, action: str, volume: float) -> Optional[int]:
        """Send a buy or sell order to MT5 using appropriate bid/ask prices"""
        if not self.data_manager._ensure_connection():
            logger.error("MT5 not connected, cannot send order.")
            return None

        # Validate symbol exists and is enabled
        symbol_info = mt5.symbol_info(symbol)
        if not symbol_info:
            logger.error(f"Symbol {symbol} not found")
            return None
            
        if not symbol_info.visible:
            # Try to enable the symbol
            if not mt5.symbol_select(symbol, True):
                logger.error(f"Failed to enable symbol {symbol}")
                return None
            logger.info(f"Enabled symbol {symbol} for trading")

        # Map action to order type
        if action.lower() == 'buy':
            order_type = mt5.ORDER_TYPE_BUY
        elif action.lower() == 'sell':
            order_type = mt5.ORDER_TYPE_SELL
        else:
            logger.error(f"Unknown order action: {action}")
            return None

        # Get current bid/ask prices for accurate execution
        bid_ask = self.data_manager.get_current_bid_ask(symbol)
        if bid_ask is None:
            logger.error(f"Cannot get bid/ask prices for {symbol}")
            return None
        
        bid, ask = bid_ask
        
        # Use appropriate price based on order type
        if order_type == mt5.ORDER_TYPE_BUY:
            price = ask  # Buy at ask price
        else:
            price = bid  # Sell at bid price

        # Get symbol-specific filling mode
        filling_mode = self._get_filling_mode(symbol)
        
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": order_type,
            "price": price,
            "deviation": self.config.slippage_points,
            "magic": self.config.magic_number,
            "comment": "PairsTradingMT5",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_mode,
        }
        result = mt5.order_send(request)
        if result is not None and hasattr(result, "retcode") and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"Order sent: {action.upper()} {symbol} {volume} lots at {price} (bid={bid:.5f}, ask={ask:.5f}, ticket={result.order})")
            return result.order
        else:
            logger.error(f"Order send failed for {symbol}: retcode={getattr(result, 'retcode', 'N/A')}")
            return None

    def _calculate_balanced_volumes(self, symbol1: str, symbol2: str, 
                                  price1: float, price2: float) -> Optional[Tuple[float, float, float, float]]:
        """Calculate volumes for equal monetary exposure between two symbols"""
        
        # Get symbol information
        if symbol1 not in self.data_manager.symbol_info_cache or symbol2 not in self.data_manager.symbol_info_cache:
            logger.error(f"Symbol info not available for {symbol1} or {symbol2}")
            return None
        
        info1 = self.data_manager.symbol_info_cache[symbol1]
        info2 = self.data_manager.symbol_info_cache[symbol2]
        
        # Enhanced logging for debugging
        # logger.info(f"Volume calculation for {symbol1}-{symbol2}:")
        # logger.info(f"  {symbol1}: price={price1:.5f}, contract_size={info1['trade_contract_size']}, "
        #            f"vol_min={info1['volume_min']}, vol_step={info1['volume_step']}")
        # logger.info(f"  {symbol2}: price={price2:.5f}, contract_size={info2['trade_contract_size']}, "
        #            f"vol_min={info2['volume_min']}, vol_step={info2['volume_step']}")
        
        # Get contract sizes
        contract_size1 = info1['trade_contract_size']
        contract_size2 = info2['trade_contract_size']
        
        # Calculate target monetary value (half of max position size for each leg)
        target_monetary_value = self.config.max_position_size / 2
        # logger.info(f"  Target monetary value per leg: ${target_monetary_value:.2f}")
        
        # Calculate required volumes for target monetary value
        volume1_raw = target_monetary_value / (price1 * contract_size1)
        volume2_raw = target_monetary_value / (price2 * contract_size2)
        
               
        # logger.info(f"     Raw volumes: {symbol1}={volume1_raw:.6f}, {symbol2}={volume2_raw:.6f}")
        
        # Apply volume constraints
        volume1 = self._normalize_volume(symbol1, volume1_raw, info1)
        volume2 = self._normalize_volume(symbol2, volume2_raw, info2)
        
        if volume1 is None or volume2 is None:
            logger.error(f"Volume normalization failed for {symbol1}-{symbol2}")
            return None
        
        # logger.info(f"  Normalized volumes: {symbol1}={volume1:.6f}, {symbol2}={volume2:.6f}")
        
        # Calculate actual monetary values with normalized volumes
        monetary_value1 = volume1 * price1 * contract_size1
        monetary_value2 = volume2 * price2 * contract_size2
        
        # logger.info(f"  Initial monetary values: {symbol1}=${monetary_value1:.2f}, {symbol2}=${monetary_value2:.2f}")
        
        # Try iterative adjustment for better balance
        best_volume1, best_volume2 = volume1, volume2
        best_monetary1, best_monetary2 = monetary_value1, monetary_value2
        best_diff = abs(monetary_value1 - monetary_value2) / max(monetary_value1, monetary_value2)
        
        # Try small adjustments to improve balance
        for multiplier in [0.95, 0.98, 1.02, 1.05]:
            try:
                # Adjust the larger volume down or smaller volume up
                if monetary_value1 > monetary_value2:
                    test_volume1 = self._normalize_volume(symbol1, volume1 * multiplier, info1)
                    test_volume2 = volume2
                else:
                    test_volume1 = volume1

                    test_volume2 = self._normalize_volume(symbol2, volume2 * multiplier, info2)
                
                if test_volume1 and test_volume2:
                    test_monetary1 = test_volume1 * price1 * contract_size1
                   
                    test_monetary2 = test_volume2 * price2 * contract_size2
                    test_diff = abs(test_monetary1 - test_monetary2) / max(test_monetary1, test_monetary2)
                    
                    if test_diff < best_diff:
                        best_volume1, best_volume2 = test_volume1, test_volume2
                        best_monetary1, best_monetary2 = test_monetary1, test_monetary2
                        best_diff = test_diff
                        # logger.info(f"  Improved balance with multiplier {multiplier}: diff={test_diff:.4f}")
            except:
                continue
        
        volume1, volume2 = best_volume1, best_volume2
        monetary_value1, monetary_value2 = best_monetary1, best_monetary2
        
        # Final validation
        final_diff_pct = abs(monetary_value1 - monetary_value2) / max(monetary_value1, monetary_value2)
        
        # logger.info(f"  Final monetary values: {symbol1}=${monetary_value1:.2f}, {symbol2}=${monetary_value2:.2f}")
        # logger.info(f"  Final difference: {final_diff_pct:.4f} vs tolerance: {self.config.monetary_value_tolerance:.4f}")
        
        if final_diff_pct > self.config.monetary_value_tolerance:
            # logger.error(f"Cannot achieve monetary balance for {symbol1}-{symbol2}: "
            #             f"final difference {final_diff_pct:.4f} > tolerance {self.config.monetary_value_tolerance:.4f}")
            # logger.error(f"  Consider increasing monetary_value_tolerance or adjusting max_position_size")
            return None
        
        # logger.info(f"  Successfully calculated balanced volumes with {final_diff_pct:.4f} difference")
        return volume1, volume2, monetary_value1, monetary_value2
    
    def _normalize_volume(self, symbol: str, volume_raw: float, symbol_info: Dict) -> Optional[float]:
        """Normalize volume to valid increments and constraints"""
        
        min_vol = symbol_info['volume_min']
        max_vol = symbol_info['volume_max']
        step = symbol_info['volume_step']
        
        # logger.debug(f"Normalizing volume for {symbol}: raw={volume_raw:.6f}, min={min_vol}, max={max_vol}, step={step}")
        
        # Round to valid step increments
        volume = round(volume_raw / step) * step
        
        # Apply min/max constraints
        volume = max(min_vol, min(max_vol, volume))
        
        # Validate minimum volume requirement
        if volume < min_vol:
            logger.error(f"Calculated volume {volume} below minimum {min_vol} for {symbol}")
            return None
        
        logger.debug(f"Normalized volume for {symbol}: {volume:.6f}")
        return volume
    
    def _calculate_volume(self, symbol: str, price: float) -> Optional[float]:
        """Legacy method - kept for backward compatibility but now uses balanced calculation"""
        logger.warning("Using legacy _calculate_volume method - consider using _calculate_balanced_volumes instead")

        
        if symbol not in self.data_manager.symbol_info_cache:
            return None
        
        info = self.data_manager.symbol_info_cache[symbol]
        
        # Calculate volume based on fixed position size
        position_value = self.config.max_position_size / 2  # Half for each leg
        contract_size = info['trade_contract_size']
        
        volume_raw = position_value / (price * contract_size)
        
        return self._normalize_volume(symbol, volume_raw, info)

    def _monitor_positions(self):
        """Monitor open positions for risk management with trailing stop using accurate bid/ask prices"""
        
        # Log position monitoring status with portfolio summary
        if len(self.active_positions) > 0:
            current_exposure = self._calculate_total_exposure()
            # logger.info(f"Monitoring {len(self.active_positions)} active positions (${current_exposure:.2f}/${self.config.max_monetary_exposure:.2f} exposure)")
        
        for pair_str, position in list(self.active_positions.items()):
            try:
                # Get current bid/ask prices for accurate P&L calculation
                bid_ask_prices = self.data_manager.get_multiple_bid_ask([position['symbol1'], position['symbol2']])
                if len(bid_ask_prices) != 2:
                    continue
                
                bid1, ask1 = bid_ask_prices[position['symbol1']]
                bid2, ask2 = bid_ask_prices[position['symbol2']]
                
                # Calculate P&L based on position direction and order types
                if position['order1_type'] == 'buy':
                    current_market_price1 = bid1
                else:
                    current_market_price1 = ask1
                
                if position['order2_type'] == 'buy':
                    current_market_price2 = bid2
                else:
                    current_market_price2 = ask2
                
                # Calculate P&L based on actual execution and current market prices
                current_value1 = position['volume1'] * current_market_price1 * self.data_manager.symbol_info_cache[position['symbol1']]['trade_contract_size']
                current_value2 = position['volume2'] * current_market_price2 * self.data_manager.symbol_info_cache[position['symbol2']]['trade_contract_size']
                
                entry_value1 = position['volume1'] * position['entry_exec_price1'] * self.data_manager.symbol_info_cache[position['symbol1']]['trade_contract_size']
                entry_value2 = position['volume2'] * position['entry_exec_price2'] * self.data_manager.symbol_info_cache[position['symbol2']]['trade_contract_size']
                
                # Calculate P&L based on position direction and order types
                if position['direction'] == 'LONG':
                    # Long: bought s1, sold s2
                    # P&L = (current_value1 - entry_value1) + (entry_value2 - current_value2)
                    pnl_leg1 = current_value1 - entry_value1  # Profit from s1 appreciation
                    pnl_leg2 = entry_value2 - current_value2  # Profit from s2 depreciation
                else:
                    # Short: sold s1, bought s2
                    # P&L = (entry_value1 - current_value1) + (current_value2 - entry_value2)
                    pnl_leg1 = entry_value1 - current_value1  # Profit from s1 depreciation
                    pnl_leg2 = current_value2 - entry_value2  # Profit from s2 appreciation
                
                pnl_dollar = pnl_leg1 + pnl_leg2
                
                # Calculate PnL percentage based on total position size
                total_position_value = (abs(entry_value1) + abs(entry_value2)) / 2
                pnl_pct = (pnl_dollar / total_position_value) * 100
                
                # Update trailing stop tracking
                if pnl_pct > position['max_favorable_pnl']:
                    position['max_favorable_pnl'] = pnl_pct
                    # Update trailing stop level
                    position['trailing_stop_level'] = position['max_favorable_pnl'] - self.config.trailing_stop_perc
                logger.debug(f"{['symbol1']}-{['symbol2']} trailing_stop_level: {position['trailing_stop_level']}")
                
                # Log position status with detailed bid/ask and P&L breakdown
                duration = (datetime.datetime.now() - position['entry_time']).total_seconds() / 60
                # logger.info(f"Position {pair_str} [{position['direction']}]: "
                #            f"PnL=${pnl_dollar:.2f} ({pnl_pct:.2f}%), Max={position['max_favorable_pnl']:.2f}%, "
                #            f"TrailStop={position['trailing_stop_level']:.2f}%, Duration={duration:.1f}min")
                # logger.info(f"  {position['symbol1']}: entry={position['entry_exec_price1']:.5f}, market={current_market_price1:.5f} (bid={bid1:.5f}/ask={ask1:.5f}), P&L=${pnl_leg1:.2f}")
                # logger.info(f"  {position['symbol2']}: entry={position['entry_exec_price2']:.5f}, market={current_market_price2:.5f} (bid={bid2:.5f}/ask={ask2:.5f}), P&L=${pnl_leg2:.2f}")
                
                # Check exit conditions
                should_close = False
                close_reason = ""
                
                # Stop loss check
                if pnl_pct <= -self.config.stop_loss_perc:
                    should_close = True
                    close_reason = f"Stop loss triggered: {pnl_pct:.2f}%"
                
                # Take profit check
                elif pnl_pct >= self.config.take_profit_perc:
                    should_close = True
                    close_reason = f"Take profit triggered: {pnl_pct:.2f}%"
                
                # Trailing stop check (only if we have favorable movement)
                elif position['max_favorable_pnl'] > 0 and pnl_pct <= position['trailing_stop_level']:
                    should_close = True
                    close_reason = f"Trailing stop triggered: PnL={pnl_pct:.2f}% fell below trail level={position['trailing_stop_level']:.2f}%"
                
                # Max pair drawdown check
                if not self._check_drawdown_limits(pair_str):
                    should_close = True
                    close_reason = f"Pair drawdown limit triggered"
                
                if should_close:
                    logger.warning(f"{close_reason} for {pair_str}")
                    self._close_pair_position(pair_str)
                
            except Exception as e:
                logger.error(f"Error monitoring position {pair_str}: {e}")
    
    def stop_trading(self):
        """Stop real-time trading and close all positions"""
        self.is_trading = False
        
        logger.info("Attempting to save final state before exiting...")
        self._save_state()

        # Close all open positions

        for pair_str in list(self.active_positions.keys()):
            self._close_pair_position(pair_str)
        
        logger.info("Real-time trading stopped")

    def _save_state(self):
        """Save the current state of the trader to a file."""
        with self._update_lock:
            try:
                # logger.info(f"Saving trading state to {self.config.state_file}...")
                
                # Create a copy of active positions to avoid modifying the original
                positions_to_save = {}
                for pair, pos_data in self.active_positions.items():
                    # Create a new dict with serializable values
                    pos_copy = pos_data.copy()
                    if 'entry_time' in pos_copy and isinstance(pos_copy['entry_time'], datetime.datetime):
                        pos_copy['entry_time'] = pos_copy['entry_time'].isoformat()
                    positions_to_save[pair] = pos_copy

                # Prepare pair states
                pair_states_to_save = {}
                for pair, data in self.pair_states.items():
                    # Convert price series to serializable format
                    pair_states_to_save[pair] = {
                        'symbol1': data['symbol1'],
                        'symbol2': data['symbol2'],
                        'price1': {
                            'index': [t.isoformat() for t in data['price1'].index],
                            'values': data['price1'].values.tolist()
                        },
                        'price2': {
                            'index': [t.isoformat() for t in data['price2'].index],
                            'values': data['price2'].values.tolist()
                        },
                        'position': data['position'],
                        'cooldown': data['cooldown'],
                        'last_update': data['last_update'].isoformat(),
                        'last_candle_time': data['last_candle_time'].isoformat() if data['last_candle_time'] else None
                    }

                state = {
                    'active_positions': positions_to_save,
                    'pair_states': pair_states_to_save,
                    'portfolio_peak_value': self.portfolio_peak_value,
                    'pair_peak_values': self.pair_peak_values,
                    'suspended_pairs': list(self.suspended_pairs),
                    'portfolio_trading_suspended': self.portfolio_trading_suspended,
                    'last_save_time': datetime.datetime.now().isoformat()
                }

                # Save with proper encoding and formatting
                with open(self.config.state_file, 'w', encoding='utf-8') as f:
                    json.dump(state, f, indent=4, ensure_ascii=False)
                
                # logger.info("Trading state saved successfully")
                
                # Verify the save was successful by trying to read it back
                with open(self.config.state_file, 'r', encoding='utf-8') as f:
                    _ = json.load(f)

            except Exception as e:
                logger.error(f"Failed to save state: {str(e)}")
                # If saving fails, try to create a backup of the current state file
                try:
                    if os.path.exists(self.config.state_file):
                        backup_file = f"{self.config.state_file}.backup"
                        os.replace(self.config.state_file, backup_file)
                        logger.info(f"Created backup of state file: {backup_file}")
                except Exception as backup_error:
                    logger.error(f"Failed to create backup: {str(backup_error)}")

    def _load_state(self) -> bool:
        """Load the trader's state from a file."""
        if not os.path.exists(self.config.state_file):
            logger.info("No state file found. Starting fresh.")
            return False
        
        try:
            logger.info(f"Loading trading state from {self.config.state_file}...")
            with open(self.config.state_file, 'r') as f:
                state = json.load(f)

            # First, initialize pair_states with current config pairs
            self.pair_states = {}
            current_pairs = set(self.config.pairs)
            
            # Load historical data for all current pairs
            lookback_bars = max(self.config.z_period, self.config.corr_period, self.config.adf_period) * 2
            
            # Initialize all current pairs with fresh data
            for pair_str in current_pairs:
                s1, s2 = pair_str.split('-')
                data1 = self.data_manager.get_historical_data(s1, self.config.interval, 
                                                            self.config.start_date, count=lookback_bars)
                data2 = self.data_manager.get_historical_data(s2, self.config.interval,
                                                            self.config.start_date, count=lookback_bars)
                
                if not data1.empty and not data2.empty:
                    self.pair_states[pair_str] = {
                        'symbol1': s1,
                        'symbol2': s2,
                        'price1': data1,
                        'price2': data2,
                        'position': None,
                        'last_signal': None,
                        'cooldown': 0,
                        'last_update': datetime.datetime.now(),
                        'last_candle_time': data1.index[-1] if len(data1) > 0 else None  # Track last candle timestamp
                    }
                    logger.info(f"Initialized new pair {pair_str} with {len(data1)} bars")

            # Now overlay saved state for existing pairs
            if 'pair_states' in state:
                for pair, data in state['pair_states'].items():
                    if pair in current_pairs:  # Only load state for pairs that are in current config
                        # Update existing pair state with saved position and cooldown
                        if pair in self.pair_states:
                            self.pair_states[pair]['position'] = data['position']
                            self.pair_states[pair]['cooldown'] = data['cooldown']
                            logger.info(f"Restored state for existing pair {pair}")

            # Handle active positions
            self.active_positions = {}
            if 'active_positions' in state:
                for pair, pos_data in state['active_positions'].items():
                    if pair in current_pairs:  # Only load positions for current pairs
                        if 'entry_time' in pos_data and isinstance(pos_data['entry_time'], str):
                            pos_data['entry_time'] = datetime.datetime.fromisoformat(pos_data['entry_time'])
                        self.active_positions[pair] = pos_data
                        logger.info(f"Restored active position for {pair}")
                    else:
                        logger.warning(f"Skipping position for removed pair {pair}")

            logger.info("State loaded. Reconciling with MT5 server...")
            self._reconcile_positions()
            
            logger.info(f"State loaded and reconciled successfully. "
                       f"Managing {len(self.pair_states)} pairs with {len(self.active_positions)} active positions")
            return True

        except Exception as e:
            logger.error(f"Failed to load state: {e}. Starting fresh.")
            # Clean up potentially corrupted state
            self.active_positions = {}
            self.pair_states = {}
            return False

    def _reconcile_positions(self):
        """Verify that loaded positions still exist on the MT5 server and update their states."""
        if not self.data_manager._ensure_connection():
            logger.error("Cannot reconcile positions, no MT5 connection.")
            return

        # Get all positions from MT5 server with our magic number
        server_positions = mt5.positions_get(magic=self.config.magic_number)
        if server_positions is None:
            logger.warning("Could not retrieve positions from MT5 server.")
            server_positions = []
        
        # Create lookup dictionaries
        server_positions_by_symbol = {}
        for pos in server_positions:
            server_positions_by_symbol[pos.symbol] = pos

        # Check if loaded positions are still active and update their states
        reconciled_positions = {}
        for pair_str, pos_data in self.active_positions.items():
            s1, s2 = pos_data['symbol1'], pos_data['symbol2']
            
            # Check if both legs of the pair exist on server
            pos1 = server_positions_by_symbol.get(s1)
            pos2 = server_positions_by_symbol.get(s2)
            
            if pos1 and pos2:
                # Calculate monetary values if missing
                contract_size1 = self.data_manager.symbol_info_cache[s1]['trade_contract_size']
                contract_size2 = self.data_manager.symbol_info_cache[s2]['trade_contract_size']
                monetary_value1 = pos1.volume * pos1.price_open * contract_size1
                monetary_value2 = pos2.volume * pos2.price_open * contract_size2
                
                # Update position data with current server information
                pos_data.update({
                    'ticket1': pos1.ticket,
                    'ticket2': pos2.ticket,
                    'volume1': pos1.volume,
                    'volume2': pos2.volume,
                    'entry_exec_price1': pos1.price_open,
                    'entry_exec_price2': pos2.price_open,
                    'monetary_value1': monetary_value1,  # Add missing monetary values
                    'monetary_value2': monetary_value2,  # Add missing monetary values
                    # Keep the original entry time if it exists, otherwise use server time
                    'entry_time': pos_data.get('entry_time') or datetime.datetime.fromtimestamp(min(pos1.time, pos2.time)),
                    # Preserve the direction based on position types
                    'direction': ('LONG' if pos1.type == mt5.POSITION_TYPE_BUY else 'SHORT'),
                    'order1_type': 'buy' if pos1.type == mt5.POSITION_TYPE_BUY else 'sell',
                    'order2_type': 'buy' if pos2.type == mt5.POSITION_TYPE_BUY else 'sell'
                })
                
                # Update or initialize tracking values
                if 'max_favorable_pnl' not in pos_data:
                    pos_data['max_favorable_pnl'] = 0.0
                if 'trailing_stop_level' not in pos_data:
                    pos_data['trailing_stop_level'] = 0.0
                
                reconciled_positions[pair_str] = pos_data
                logger.info(f"Position for {pair_str} reconciled with server state:")
                logger.info(f"  Tickets: {pos1.ticket}, {pos2.ticket}")
                logger.info(f"  Volumes: {pos1.volume}, {pos2.volume}")
                logger.info(f"  Direction: {pos_data['direction']}")
            else:
                logger.warning(f"Position for {pair_str} not fully found on server "
                             f"({s1}: {'found' if pos1 else 'missing'}, "
                             f"{s2}: {'found' if pos2 else 'missing'})")
                # Reset pair state if position is gone
                if pair_str in self.pair_states:
                    self.pair_states[pair_str]['position'] = None
                    self.pair_states[pair_str]['cooldown'] = 0

        # Look for any untracked positions that might belong to pairs but weren't in our state
        known_symbols = set()
        for pos_data in self.active_positions.values():
            known_symbols.add(pos_data['symbol1'])
            known_symbols.add(pos_data['symbol2'])

        # Check for any untracked positions with our magic number
        untracked_positions = {}
        for pos in server_positions:
            if pos.symbol not in known_symbols:
                untracked_positions[pos.symbol] = pos
                
        # Try to match untracked positions into pairs
        for pair_str in self.config.pairs:
            s1, s2 = pair_str.split('-')
            if s1 in untracked_positions and s2 in untracked_positions:
                pos1 = untracked_positions[s1]
                pos2 = untracked_positions[s2]
                
                # Calculate monetary values
                contract_size1 = self.data_manager.symbol_info_cache[s1]['trade_contract_size']
                contract_size2 = self.data_manager.symbol_info_cache[s2]['trade_contract_size']
                monetary_value1 = pos1.volume * pos1.price_open * contract_size1
                monetary_value2 = pos2.volume * pos2.price_open * contract_size2
                
                # Create new position entry
                reconciled_positions[pair_str] = {
                    'symbol1': s1,
                    'symbol2': s2,
                    'ticket1': pos1.ticket,
                    'ticket2': pos2.ticket,
                    'volume1': pos1.volume,
                    'volume2': pos2.volume,
                    'entry_exec_price1': pos1.price_open,
                    'entry_exec_price2': pos2.price_open,
                    'monetary_value1': monetary_value1,  # Add monetary values
                    'monetary_value2': monetary_value2,  # Add monetary values
                    'entry_time': datetime.datetime.fromtimestamp(min(pos1.time, pos2.time)),
                    'direction': 'LONG' if pos1.type == mt5.POSITION_TYPE_BUY else 'SHORT',
                    'order1_type': 'buy' if pos1.type == mt5.POSITION_TYPE_BUY else 'sell',
                    'order2_type': 'buy' if pos2.type == mt5.POSITION_TYPE_BUY else 'sell',
                    'max_favorable_pnl': 0.0,
                    'trailing_stop_level': 0.0
                }
                logger.info(f"Recovered untracked position pair {pair_str}")
                
                # Update pair state
                if pair_str in self.pair_states:
                    self.pair_states[pair_str]['position'] = reconciled_positions[pair_str]['direction']
                    
                # Remove from untracked
                del untracked_positions[s1]
                del untracked_positions[s2]

        # Log any remaining untracked positions
        for symbol, pos in untracked_positions.items():
            logger.warning(f"Unmatched position found: {symbol} (ticket: {pos.ticket})")

        self.active_positions = reconciled_positions
        logger.info(f"Reconciliation complete. Managing {len(self.active_positions)} active positions.")

    def _close_position_by_symbol(self, symbol: str, volume: float) -> bool:
        """Close a position by symbol using MT5's position close functionality"""
        if not self.data_manager._ensure_connection():
            logger.error("MT5 not connected, cannot close position.")
            return False

        # Get all positions for this symbol with our magic number
        positions = mt5.positions_get(symbol=symbol)
        if not positions:
            logger.warning(f"No positions found for symbol {symbol}")
            return False

        # Filter positions by magic number
        our_positions = [pos for pos in positions if pos.magic == self.config.magic_number]
        if not our_positions:
            logger.warning(f"No positions with our magic number {self.config.magic_number} found for {symbol}")
            return False

        # Find position with matching volume (or closest)
        target_position = None
        for pos in our_positions:
            if abs(pos.volume - volume) < 0.001:  # Allow small tolerance for volume matching
                target_position = pos
                break
        
        if not target_position:
            # If exact volume not found, use the first position
            target_position = our_positions[0]
            logger.warning(f"Exact volume {volume} not found for {symbol}, closing position with volume {target_position.volume}")

        # Determine close price based on position type
        tick = mt5.symbol_info_tick(symbol)
        if not tick:
            logger.error(f"Cannot get tick data for {symbol}")
            return False

        if target_position.type == mt5.POSITION_TYPE_BUY:
            close_price = tick.bid
        else:
            close_price = tick.ask

        filling_mode = self._get_filling_mode(symbol)

        # Create close request
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": target_position.volume,
            "type": mt5.ORDER_TYPE_SELL if target_position.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY,
            "position": target_position.ticket,
            "price": close_price,
            "deviation": self.config.slippage_points,
            "magic": self.config.magic_number,
            "comment": "PairsTradingMT5_Close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling_mode,
        }

        # Send close request
        result = mt5.order_send(request)

        if result is not None and hasattr(result, "retcode") and result.retcode == mt5.TRADE_RETCODE_DONE:
            logger.info(f"Position closed: {symbol} ticket={target_position.ticket} volume={target_position.volume} at {close_price}")
            return True
        else:
            logger.error(f"Failed to close position for {symbol}: retcode={getattr(result, 'retcode', 'N/A')}")
            return False


    def _close_pair_position(self, pair_str: str) -> Tuple[bool, float]:
        """Close both legs of an open pair position and return (success, pnl_pct)"""
        if pair_str not in self.active_positions:
            logger.warning(f"No active position to close for {pair_str}")
            return (False, 0.0)

        position = self.active_positions[pair_str]
        
        # Calculate final P&L before closing
        try:
            pnl_pct = self._calculate_position_pnl_pct(pair_str)
        except Exception as e:
            logger.error(f"Error calculating final P&L for {pair_str}: {e}")
            pnl_pct = 0.0
        s1 = position['symbol1']
        s2 = position['symbol2']
        volume1 = position['volume1']
        volume2 = position['volume2']
        direction = position['direction']

        logger.info(f"Closing pair position {pair_str} [{direction}]")
        logger.info(f"  Closing {s1}: volume={volume1:.4f}")
        logger.info(f"  Closing {s2}: volume={volume2:.4f}")

        # Close both positions using the dedicated close function
        close1_success = self._close_position_by_symbol(s1, volume1)
        close2_success = self._close_position_by_symbol(s2, volume2)

        if close1_success and close2_success:
            logger.info(f"Successfully closed both legs for {pair_str}")
            
            # Update active_positions and pair_states
            del self.active_positions[pair_str]
            if pair_str in self.pair_states:
                self.pair_states[pair_str]['position'] = None
                
                # Apply cooldown if it was a losing trade
                if pnl_pct < 0:
                    self.pair_states[pair_str]['cooldown'] = self.config.cooldown_bars
                    logger.info(f"Applied cooldown of {self.config.cooldown_bars} bars to {pair_str} due to losing trade: {pnl_pct:.2f}%")
                else:
                    logger.info(f"No cooldown applied to {pair_str} - profitable trade: {pnl_pct:.2f}%")
                    
            return (True, pnl_pct)
        
        logger.error(f"Failed to close pair position for {pair_str}")
        logger.error(f"  {s1} close: {'SUCCESS' if close1_success else 'FAILED'}")
        logger.error(f"  {s2} close: {'SUCCESS' if close2_success else 'FAILED'}")
        
        # If only one leg failed, we have a problem - log it but don't remove from active positions
        if close1_success != close2_success:
            logger.critical(f"PARTIAL CLOSE DETECTED for {pair_str} - manual intervention may be required!")
        
        return False
    
    def _calculate_position_pnl_pct(self, pair_str: str) -> float:
        """Calculate the PnL percentage for a given pair position."""
        if pair_str not in self.active_positions:
            return 0.0
        
        position = self.active_positions[pair_str]
        s1 = position['symbol1']
        s2 = position['symbol2']
        
        # Get current bid/ask prices for accurate P&L calculation
        bid_ask_prices = self.data_manager.get_multiple_bid_ask([s1, s2])
        if s1 not in bid_ask_prices or s2 not in bid_ask_prices:
            return 0.0
        
        bid1, ask1 = bid_ask_prices[s1]
        bid2, ask2 = bid_ask_prices[s2]
        
        # Determine close prices based on order types
        if position['order1_type'] == 'buy':
            close_price1 = bid1
        else:
            close_price1 = ask1
        
        if position['order2_type'] == 'buy':
            close_price2 = bid2
        else:
            close_price2 = ask2
        
        # Calculate values
        contract_size1 = self.data_manager.symbol_info_cache[s1]['trade_contract_size']
        contract_size2 = self.data_manager.symbol_info_cache[s2]['trade_contract_size']
        
        entry_value1 = position['volume1'] * position['entry_exec_price1'] * contract_size1
        entry_value2 = position['volume2'] * position['entry_exec_price2'] * contract_size2
        close_value1 = position['volume1'] * close_price1 * contract_size1
        close_value2 = position['volume2'] * close_price2 * contract_size2
        
        if position['direction'] == 'LONG':
            pnl_dollar = (close_value1 - entry_value1) + (entry_value2 - close_value2)
        else:
            pnl_dollar = (entry_value1 - close_value1) + (close_value2 - entry_value2)
        
        total_position_value = (abs(entry_value1) + abs(entry_value2)) / 2
        if total_position_value == 0:
            return 0.0
        
        pnl_pct = (pnl_dollar / total_position_value) * 100
        return pnl_pct

    def _calculate_total_exposure(self) -> float:
        """Calculate total monetary exposure across all active positions"""
        total_exposure = 0.0
        
        try:
            for pair_str, position in self.active_positions.items():
                # Add both legs of the position to total exposure
                leg1_exposure = position.get('monetary_value1', 0)
                leg2_exposure = position.get('monetary_value2', 0)
                # Use the average of both legs to avoid double counting
                position_exposure = (leg1_exposure + leg2_exposure) / 2
                total_exposure += position_exposure
                
        except Exception as e:
            logger.error(f"Error calculating total exposure: {e}")
            
        return total_exposure

# === VECTORBT BACKTESTING ENGINE ===
class VectorBTBacktester:
    """High-performance vectorbt-based backtesting engine"""
    
    def __init__(self, config: TradingConfig, data_manager: MT5DataManager):
        self.config = config
        self.data_manager = data_manager
        self.strategy = OptimizedPairsStrategy(config, data_manager)
        
        # Performance optimization - use config values
        self.use_multiprocessing = self.config.use_multiprocessing
        self.max_workers = min(self.config.max_workers, multiprocessing.cpu_count())
        
        # Results storage
        self.pair_results = []
        self.portfolio_results = {}
        
    def run_backtest(self) -> Dict:
        """Run comprehensive backtesting with portfolio optimization"""
        logger.info("Starting VectorBT backtesting engine...")
        start_time = time.time()
        
        # Run individual pair backtests
        pair_results = self._run_pair_backtests()
        
        # Run portfolio-level analysis
        portfolio_results = self._run_portfolio_backtest(pair_results)
        
        # Calculate ranking and composite scores
        ranked_results = self._calculate_pair_rankings(pair_results)
        
        # Generate comprehensive report
        backtest_results = {
            'pair_results': ranked_results,
            'portfolio_metrics': portfolio_results,
            'portfolio_equity': portfolio_results.get('equity_curve', []),
            'portfolio_dates': portfolio_results.get('dates', []),
            'config': self.config
        }

        elapsed_time = time.time() - start_time
        logger.info(f"Backtest completed in {elapsed_time:.2f} seconds")
        
        return backtest_results
    
    def _run_pair_backtests(self) -> List[Dict]:
        """Run backtests for all pairs using parallel processing"""
        logger.info(f"Running backtests for {len(self.config.pairs)} pairs...")
        
        if self.use_multiprocessing:
            return self._run_pairs_parallel()
        else:
            return self._run_pairs_sequential()
    
    def _run_pairs_parallel(self) -> List[Dict]:
        """Run pair backtests in parallel for maximum performance"""
        results = []
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all pair backtests
            future_to_pair = {
                executor.submit(self._backtest_single_pair, pair_str): pair_str 
                for pair_str in self.config.pairs
            }
            
            # Collect results as they complete
            for future in as_completed(future_to_pair):
                pair_str = future_to_pair[future]
                try:
                    result = future.result()
                    if result:
                        results.append(result)
                        logger.info(f"Completed backtest for {pair_str}")
                except Exception as e:
                    logger.error(f"Error backtesting {pair_str}: {e}")
        
        logger.info(f"Completed {len(results)} pair backtests")
        return results
    
    def _run_pairs_sequential(self) -> List[Dict]:
        """Run pair backtests sequentially (for debugging)"""
        results = []
        
        for i, pair_str in enumerate(self.config.pairs):
            try:
                logger.info(f"Backtesting {pair_str} ({i+1}/{len(self.config.pairs)})")
                result = self._backtest_single_pair(pair_str)
                if result:
                    results.append(result)
            except Exception as e:
                logger.error(f"Error backtesting {pair_str}: {e}")
        
        return results
    
    def _backtest_single_pair(self, pair_str: str) -> Optional[Dict]:
        """Backtest a single pair using vectorbt"""
        try:
            s1, s2 = pair_str.split('-')
            
            # Get historical data
            data1 = self.data_manager.get_historical_data(s1, self.config.interval, 
                                                         self.config.start_date, self.config.end_date)
            data2 = self.data_manager.get_historical_data(s2, self.config.interval,
                                                         self.config.start_date, self.config.end_date)
            
            if data1.empty or data2.empty:
                logger.warning(f"No data for {pair_str}")
                return None
            
            # Align data
            aligned_data = pd.concat([data1, data2], axis=1).fillna(method='ffill').dropna()
            if len(aligned_data) < self.config.z_period:
                logger.warning(f"Insufficient data for {pair_str}")
                return None
            
            aligned_data.columns = ['price1', 'price2']
            
            # Calculate indicators
            indicators = self.strategy.calculate_indicators_vectorized(
                aligned_data['price1'], aligned_data['price2']
            )
            
            if not indicators:
                logger.warning(f"No indicators calculated for {pair_str}")
                return None
            
            # Generate signals
            signals = self.strategy.generate_signals_vectorized(indicators)
            if signals.empty:
                logger.warning(f"No signals generated for {pair_str}")
                return None
            
            # Run vectorbt simulation
            trades, equity_curve = self._simulate_pair_trades(pair_str, aligned_data, signals)
            
            # Calculate metrics
            metrics = self._calculate_pair_metrics(pair_str, trades, equity_curve, aligned_data)
            
            return {
                'pair': pair_str,
                'trades': trades,
                'equity_curve': equity_curve,
                'metrics': metrics,
                'data': aligned_data
            }
            
        except Exception as e:
            logger.error(f"Error in single pair backtest for {pair_str}: {e}")
            return None
    
    def _simulate_pair_trades(self, pair_str: str, data: pd.DataFrame, signals: pd.DataFrame) -> Tuple[List[Dict], pd.Series]:
        """Simulate trades using proper pairs trading logic with both legs tracked separately"""
        
        # Initialize trade tracking
        trades = []
        position = 0  # 0=neutral, 1=long, -1=short
        entry_price1 = None
        entry_price2 = None
        entry_time = None
        equity_curve = pd.Series(1.0, index=data.index)
        cooldown_remaining = 0  # Track cooldown period
        
        # Calculate costs for both legs
        s1, s2 = pair_str.split('-')
        costs_per_leg = self.config.max_commission_perc / 2  # Split costs between legs
        
        # Process signals
        for i, (timestamp, signal) in enumerate(signals.iterrows()):
            if i == 0:
                continue
                
            current_price1 = data.loc[timestamp, 'price1']
            current_price2 = data.loc[timestamp, 'price2']
            
            # Decrement cooldown
            if cooldown_remaining > 0:
                cooldown_remaining -= 1
            
            # Entry logic - only if no cooldown
            if position == 0 and cooldown_remaining == 0:
                if signal['long_entry'] and signal['suitable']:
                    # LONG pair trade: Long s1, Short s2
                    position = 1
                    entry_price1 = current_price1
                    entry_price2 = current_price2
                    entry_time = timestamp
                    
                elif signal['short_entry'] and signal['suitable']:
                    # SHORT pair trade: Short s1, Long s2
                    position = -1
                    entry_price1 = current_price1
                    entry_price2 = current_price2
                    entry_time = timestamp
            
            # Exit logic
            elif position != 0:
                should_exit = False
                exit_reason = ""
                
                if position == 1 and signal['long_exit']:
                    should_exit = True
                    exit_reason = "Signal Exit"
                elif position == -1 and signal['short_exit']:
                    should_exit = True
                    exit_reason = "Signal Exit"
                
                # Risk management exits - calculate combined P&L
                if entry_price1 is not None and entry_price2 is not None:
                    # Calculate P&L for both legs
                    if position == 1:
                        # Long s1, Short s2
                        pnl_leg1 = (current_price1 - entry_price1) / entry_price1 * 100  # Long leg P&L
                        pnl_leg2 = (entry_price2 - current_price2) / entry_price2 * 100  # Short leg P&L
                    else:
                        # Short s1, Long s2
                        pnl_leg1 = (entry_price1 - current_price1) / entry_price1 * 100  # Short leg P&L
                        pnl_leg2 = (current_price2 - entry_price2) / entry_price2 * 100  # Long leg P&L
                    
                    # Combined P&L (average of both legs for equal monetary exposure)
                    combined_pnl_pct = (pnl_leg1 + pnl_leg2) / 2
                    
                    if combined_pnl_pct <= -self.config.stop_loss_perc:
                        should_exit = True
                        exit_reason = "Stop Loss"
                    elif combined_pnl_pct >= self.config.take_profit_perc:
                        should_exit = True
                        exit_reason = "Take Profit"
                
                if should_exit:
                    # Calculate final trade metrics
                    exit_price1 = current_price1
                    exit_price2 = current_price2
                    bars_held = len(data.loc[entry_time:timestamp]) - 1
                    
                    # Calculate P&L for both legs
                    if position == 1:
                        # Long s1, Short s2
                        pnl_leg1 = (exit_price1 - entry_price1) / entry_price1 * 100
                        pnl_leg2 = (entry_price2 - exit_price2) / entry_price2 * 100
                    else:
                        # Short s1, Long s2
                        pnl_leg1 = (entry_price1 - exit_price1) / entry_price1 * 100
                        pnl_leg2 = (exit_price2 - entry_price2) / entry_price2 * 100
                    
                    # Combined gross P&L (average for equal monetary exposure)
                    gross_pnl_pct = (pnl_leg1 + pnl_leg2) / 2
                    
                    # Apply costs (costs for both legs)
                    total_trade_costs = costs_per_leg * 2  # Costs for both legs
                    net_pnl_pct = gross_pnl_pct - total_trade_costs
                    
                    # Update equity curve
                    equity_multiplier = 1 + (net_pnl_pct / 100)
                    equity_curve.loc[timestamp:] *= equity_multiplier
                    
                    # Apply cooldown if losing trade
                    if net_pnl_pct < 0:
                        cooldown_remaining = self.config.cooldown_bars
                    
                    # Record trade with detailed leg information
                    trades.append({
                        'entry_time': entry_time,
                        'exit_time': timestamp,
                        'direction': 'LONG' if position == 1 else 'SHORT',
                        'entry_price1': entry_price1,
                        'entry_price2': entry_price2,
                        'exit_price1': exit_price1,
                        'exit_price2': exit_price2,
                        'pnl_leg1': pnl_leg1,
                        'pnl_leg2': pnl_leg2,
                        'pnl_pct': gross_pnl_pct,
                        'net_pnl_pct': net_pnl_pct,
                        'costs_pct': total_trade_costs,
                        'exit_reason': exit_reason,
                        'bars_held': bars_held,
                        'symbol1': s1,
                        'symbol2': s2
                    })
                    
                    # Reset position
                    position = 0
                    entry_price1 = None
                    entry_price2 = None
                    entry_time = None
        
        return trades, equity_curve
    
    def _calculate_trading_costs(self, s1: str, s2: str, data: pd.DataFrame) -> pd.Series:
        """Calculate trading costs for each timestamp"""
        
        if not self.data_manager.symbol_info_cache:
            # Return minimal costs if no symbol info
            return pd.Series(0.1, index=data.index)  # 0.1% default cost
        
        # Get symbol info
        info1 = self.data_manager.symbol_info_cache.get(s1, {})
        info2 = self.data_manager.symbol_info_cache.get(s2, {})
        
        # Calculate spread costs
        spread1_pct = info1.get('spread', 2) * info1.get('point', 0.0001) / data['price1'] * 100
        spread2_pct = info2.get('spread', 2) * info2.get('point', 0.0001) / data['price2'] * 100
        
        # Calculate commission costs
        def is_stock_or_etf(symbol: str) -> bool:
            return '.US' in symbol
        
        commission1_pct = pd.Series(0.0, index=data.index)
        commission2_pct = pd.Series(0.0, index=data.index)
        
        if is_stock_or_etf(s1):
            commission1_pct = self.config.commission_fixed / data['price1'] * 100
        
        if is_stock_or_etf(s2):
            commission2_pct = self.config.commission_fixed / data['price2'] * 100
        
        # Total costs (round trip for both legs)
        total_costs = (2 * commission1_pct + 2 * commission2_pct + 
                      spread1_pct + spread2_pct)
        
        return total_costs.fillna(0.1)  # Default to 0.1% if calculation fails
    
    def _calculate_pair_metrics(self, pair_str: str, trades: List[Dict], 
                               equity_curve: pd.Series, data: pd.DataFrame) -> Dict:
        """Calculate comprehensive metrics for a pair"""
        
        if not trades:
            return {
                'pair': pair_str,
                'total_trades': 0,
                'total_return': 0.0,
                'sharpe_ratio': 0.0,
                'max_drawdown': 0.0,
                'win_rate': 0.0,
                'profit_factor': 0.0,
                'avg_trade_pnl': 0.0,
                'max_trade_pnl': 0.0,
                'min_trade_pnl': 0.0,
                'avg_bars_held': 0.0,
                'composite_score': 0.0,
                'rank': 0
            }
        
        # Basic metrics
        total_trades = len(trades)
        total_return = (equity_curve.iloc[-1] - 1) * 100
        
        # Trade-based metrics
        pnl_list = [trade['net_pnl_pct'] for trade in trades]
        winning_trades = [pnl for pnl in pnl_list if pnl > 0]
        losing_trades = [pnl for pnl in pnl_list if pnl < 0]
        
        win_rate = len(winning_trades) / total_trades if total_trades > 0 else 0
        avg_trade_pnl = np.mean(pnl_list) if pnl_list else 0
        max_trade_pnl = max(pnl_list) if pnl_list else 0
        min_trade_pnl = min(pnl_list) if pnl_list else 0
        
        # Profit factor
        gross_profit = sum(winning_trades) if winning_trades else 0
        gross_loss = abs(sum(losing_trades)) if losing_trades else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
        
        # Sharpe ratio (annualized)
        returns = equity_curve.pct_change().dropna()
        if len(returns) > 0 and returns.std() > 0:
            periods_per_year = self._get_periods_per_year()
            sharpe_ratio = (returns.mean() * periods_per_year) / (returns.std() * np.sqrt(periods_per_year))
        else:
            sharpe_ratio = 0
        
        # Maximum drawdown
        running_max = equity_curve.expanding().max()
        drawdown = (equity_curve - running_max) / running_max
        max_drawdown = drawdown.min()
        
        # Average bars held
        avg_bars_held = np.mean([trade['bars_held'] for trade in trades]) if trades else 0
        
        # Composite score (higher is better)
        composite_score = self._calculate_composite_score(
            total_return, sharpe_ratio, max_drawdown, win_rate, 
            profit_factor, total_trades, avg_trade_pnl
        )
        
        return {
            'pair': pair_str,
            'total_trades': total_trades,
            'total_return': total_return,
            'sharpe_ratio': sharpe_ratio,
            'max_drawdown': max_drawdown,
            'win_rate': win_rate,
            'profit_factor': profit_factor,
            'avg_trade_pnl': avg_trade_pnl,
            'max_trade_pnl': max_trade_pnl,
            'min_trade_pnl': min_trade_pnl,
            'avg_bars_held': avg_bars_held,
            'composite_score': composite_score,
            'rank': 0  # Will be set during ranking
        }
    
    def _get_periods_per_year(self) -> int:
        """Get number of periods per year for annualization"""
        interval_map = {
            'M1': 525600,   # 1 minute
            'M5': 105120,   # 5 minutes
            'M15': 35040,   # 15 minutes
            'M30': 17520,   # 30 minutes
            'H1': 8760,     # 1 hour
            'H4': 2190,     # 4 hours
            'D1': 365,      # 1 day
            'W1': 52,       # 1 week
            'MN1': 12       # 1 month
        }
        return interval_map.get(self.config.interval, 35040)
    
    def _calculate_composite_score(self, total_return: float, sharpe_ratio: float, 
                                  max_drawdown: float, win_rate: float, 
                                  profit_factor: float, total_trades: int, 
                                  avg_trade_pnl: float) -> float:
        """Calculate composite score for ranking pairs"""
        
        # Normalize metrics (0-1 scale)
        return_score = min(max(total_return / 100, -1), 1)  # Cap at +/-100%
        sharpe_score = min(max(sharpe_ratio / 3, -1), 1)    # Cap at +/-3
        drawdown_score = min(max(max_drawdown / -50, -1), 1)  # Cap at -50%
        winrate_score = win_rate
        
        # Profit factor score (logarithmic)
        pf_score = min(np.log(max(profit_factor, 0.1)) / np.log(5), 1)
        
        # Trade frequency score
        freq_score = min(total_trades / 100, 1)  # Cap at 100 trades
        
        # Average trade score
        avg_trade_score = min(max(avg_trade_pnl / 5, -1), 1)  # Cap at +/-5%
        
        # Weighted composite score
        weights = {
            'return': 0.25,
            'sharpe': 0.25,
            'drawdown': 0.15,
            'winrate': 0.15,
            'profit_factor': 0.10,
            'frequency': 0,
            'avg_trade': 0.10
        }
        
        composite_score = (
            weights['return'] * return_score +
            weights['sharpe'] * sharpe_score +
            weights['drawdown'] * drawdown_score +
            weights['winrate'] * winrate_score +
            weights['profit_factor'] * pf_score +
            weights['frequency'] * freq_score +
            weights['avg_trade'] * avg_trade_score
        )
        
        return composite_score * 100  # Scale to 0-100
    
    def _calculate_pair_rankings(self, pair_results: List[Dict]) -> List[Dict]:
        """Calculate rankings based on composite scores"""
        
        # Sort by composite score (descending)
        sorted_results = sorted(
            pair_results, 
            key=lambda x: x['metrics']['composite_score'], 
            reverse=True
        )
        
        # Add rankings
        for i, result in enumerate(sorted_results):
            result['metrics']['rank'] = i + 1
        
        # logger.info(f"Pair rankings calculated. Top 5 pairs:")
        # for i, result in enumerate(sorted_results[:5]):
        #     metrics = result['metrics']
        #     logger.info(f"  {i+1}. {metrics['pair']}: Score={metrics['composite_score']:.2f}, "
        #                f"Return={metrics['total_return']:.2f}%, Sharpe={metrics['sharpe_ratio']:.2f}")
        
        return sorted_results
    
    def _run_portfolio_backtest(self, pair_results: List[Dict]) -> Dict:
        """Run portfolio-level backtest with position sizing and risk management"""
        
        logger.info("Running portfolio-level backtest...")
        
        # Filter pairs with trades
        valid_pairs = [result for result in pair_results if result['trades']]
        
        if not valid_pairs:
            logger.warning("No valid pairs for portfolio backtest")
            return {}
        
        # Align all equity curves
        portfolio_equity, portfolio_dates = self._build_portfolio_equity_curve(valid_pairs)
        
        # Calculate portfolio metrics
        portfolio_metrics = self._calculate_portfolio_metrics(
            portfolio_equity, portfolio_dates, valid_pairs
        )
        
        return {
            'equity_curve': portfolio_equity,
            'dates': portfolio_dates,
            **portfolio_metrics
        }
    
    def _build_portfolio_equity_curve(self, pair_results: List[Dict]) -> Tuple[List[float], List[pd.Timestamp]]:
        """Build portfolio equity curve with position sizing"""
        
        # Get common date range
        all_dates = set()
        for result in pair_results:
            all_dates.update(result['equity_curve'].index)
        
        common_dates = sorted(all_dates)
        
        # Initialize portfolio equity
        portfolio_equity = []
        portfolio_value = self.config.initial_portfolio_value
        
        # Track active positions
        active_positions = {}
        position_counter = 0
        
        for date in common_dates:
            daily_pnl = 0.0
            
            # Check for new entries and exits
            for result in pair_results:
                pair_str = result['pair']
                trades = result['trades']
                
                for trade in trades:
                    # Check for entries
                    if trade['entry_time'] == date:
                        if len(active_positions) < self.config.max_open_positions:
                            position_id = f"{pair_str}_{position_counter}"
                            active_positions[position_id] = {
                                'pair': pair_str,
                                'entry_date': date,
                                'position_size': self.config.max_position_size,
                                'trade': trade
                            }
                            position_counter += 1
                    
                    # Check for exits
                    elif trade['exit_time'] == date and any(
                        pos['pair'] == pair_str and pos['entry_date'] == trade['entry_time']
                        for pos in active_positions.values()
                    ):
                        # Find matching position
                        for pos_id, pos in list(active_positions.items()):
                            if (pos['pair'] == pair_str and 
                                pos['entry_date'] == trade['entry_time']):
                                
                                # Calculate position P&L
                                position_pnl = pos['position_size'] * (trade['net_pnl_pct'] / 100)
                                daily_pnl += position_pnl
                                
                                # Remove position
                                del active_positions[pos_id]
                                break
            
            # Update portfolio value
            portfolio_value += daily_pnl
            portfolio_equity.append(portfolio_value)
        
        return portfolio_equity, common_dates
    
    def _calculate_portfolio_metrics(self, equity_curve: List[float], 
                                   dates: List[pd.Timestamp], 
                                   pair_results: List[Dict]) -> Dict:
        """Calculate portfolio-level metrics"""
        
        if not equity_curve:
            return {}
        
        # Convert to series for calculations
        equity_series = pd.Series(equity_curve, index=dates)
        
        # Basic metrics
        initial_value = self.config.initial_portfolio_value
        final_value = equity_series.iloc[-1]
        portfolio_return = (final_value - initial_value) / initial_value * 100
        
        # Sharpe ratio
        returns = equity_series.pct_change().dropna()
        if len(returns) > 0 and returns.std() > 0:
            periods_per_year = self._get_periods_per_year()
            portfolio_sharpe = (returns.mean() * periods_per_year) / (returns.std() * np.sqrt(periods_per_year))
        else:
            portfolio_sharpe = 0
        
        # Maximum drawdown
        running_max = equity_series.expanding().max()
        drawdown = (equity_series - running_max) / running_max * 100
        portfolio_max_drawdown = drawdown.min()
        
        # Trade statistics
        total_trades = sum(len(result['trades']) for result in pair_results)
        total_pairs = len(pair_results)
        
        # Position statistics
        max_concurrent_positions = self._calculate_max_concurrent_positions(pair_results)
        avg_concurrent_positions = self._calculate_avg_concurrent_positions(pair_results)
        
        return {
            'portfolio_return': portfolio_return,
            'portfolio_sharpe': portfolio_sharpe,
            'portfolio_max_drawdown': portfolio_max_drawdown,
            'total_trades': total_trades,
            'total_pairs': total_pairs,
            'max_concurrent_positions': max_concurrent_positions,
            'avg_concurrent_positions': avg_concurrent_positions,
            'final_value': final_value,
            'initial_value': initial_value
        }
    
    def _calculate_max_concurrent_positions(self, pair_results: List[Dict]) -> int:
        """Calculate maximum number of concurrent positions"""
        
        # Get all trade periods
        trade_periods = []
        for result in pair_results:
            for trade in result['trades']:
                trade_periods.append({
                    'start': trade['entry_time'],
                    'end': trade['exit_time'],
                    'pair': result['pair']
                })
        
        if not trade_periods:
            return 0
        
        # Find maximum overlap
        max_concurrent = 0
        all_dates = set()
        
        for period in trade_periods:
            all_dates.add(period['start'])
            all_dates.add(period['end'])
        
        for date in sorted(all_dates):
            concurrent = 0
            for period in trade_periods:
                if period['start'] <= date < period['end']:
                    concurrent += 1
            max_concurrent = max(max_concurrent, concurrent)
        
        return max_concurrent
    
    def _calculate_avg_concurrent_positions(self, pair_results: List[Dict]) -> float:
        """Calculate average number of concurrent positions"""
        
        # Get all trade periods
        trade_periods = []
        for result in pair_results:
            for trade in result['trades']:
                trade_periods.append({
                    'start': trade['entry_time'],
                    'end': trade['exit_time'],
                    'pair': result['pair']
                })
        
        if not trade_periods:
            return 0.0
        
        # Calculate average over time
        all_dates = set()
        for period in trade_periods:
            all_dates.add(period['start'])
            all_dates.add(period['end'])
        
        concurrent_counts = []
        for date in sorted(all_dates):
            concurrent = 0
            for period in trade_periods:
                if period['start'] <= date < period['end']:
                    concurrent += 1
            concurrent_counts.append(concurrent)
        
        return np.mean(concurrent_counts) if concurrent_counts else 0.0

# === MAIN EXECUTION ===
def main():
    """Main execution function"""
    
    # Initialize MT5 connection
    data_manager = MT5DataManager(CONFIG)
    if not data_manager.connect():
        logger.error("Failed to connect to MT5")
        return
    
    try:
        # Check mode
        mode = os.getenv('TRADING_MODE', 'backtest').lower()
        
        if mode == 'realtime':
            # Real-time trading mode
            logger.info("Starting real-time trading mode")
            
            trader = MT5RealTimeTrader(CONFIG, data_manager)
            
            if trader.initialize():
                trader.start_trading()
            else:
                logger.error("Failed to initialize real-time trader")
        
        else:
            # Backtesting mode
            logger.info("Starting backtesting mode")
            
            # Initialize vectorbt backtester
            backtester = VectorBTBacktester(CONFIG, data_manager)
            
            # Run comprehensive backtest
            backtest_results = backtester.run_backtest()
            
            # Generate enhanced report
            generate_enhanced_report(backtest_results, CONFIG)
            
            # Print summary
            portfolio_metrics = backtest_results.get('portfolio_metrics', {})
            logger.info("=== BACKTEST SUMMARY ===")
            logger.info(f"Total Pairs: {portfolio_metrics.get('total_pairs', 0)}")
            logger.info(f"Total Trades: {portfolio_metrics.get('total_trades', 0)}")
            logger.info(f"Portfolio Return: {portfolio_metrics.get('portfolio_return', 0):.2f}%")
            logger.info(f"Sharpe Ratio: {portfolio_metrics.get('portfolio_sharpe', 0):.2f}")
            logger.info(f"Max Drawdown: {portfolio_metrics.get('portfolio_max_drawdown', 0):.2f}%")
            logger.info(f"Max Concurrent Positions: {portfolio_metrics.get('max_concurrent_positions', 0)}")
            
            # Show top performing pairs
            pair_results = backtest_results.get('pair_results', [])
            if pair_results:
                logger.info("\n=== TOP 10 PERFORMING PAIRS ===")
                for i, result in enumerate(pair_results[:10]):
                    metrics = result['metrics']
                    logger.info(f"{i+1:2d}. {metrics['pair']:<20} | "
                               f"Return: {metrics['total_return']:.2f}% | "
                               f"Sharpe: {metrics['sharpe_ratio']:.2f} | "
                               f"Trades: {metrics['total_trades']:3d} | "
                               f"Score: {metrics['composite_score']:.2f}")
            
            logger.info("========================")

    finally:
        data_manager.disconnect()

if __name__ == "__main__":
    main()
