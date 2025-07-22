from __future__ import annotations
import os
import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller
from statsmodels.tsa.vector_ar.vecm import coint_johansen
import warnings
import logging
from typing import Dict, Tuple, Any
from strategies.base_strategy import PairsStrategyInterface
import datetime

# Import TradingConfig - this needs to be a runtime import to avoid type checking issues
try:
    from config.production_config import TradingConfig
except ImportError:
    # Fallback to general config import
    from config import TradingConfig

def get_config():
    """Get TradingConfig from config module, ensuring .env is loaded first."""
    from config import get_config, force_config_update
    force_config_update()
    return get_config()

# Only load config if run as script, not on import
if __name__ == "__main__":
    CONFIG = get_config()
    log_file_path = os.path.join(CONFIG.logs_dir, "pairs_trading.log")
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file_path, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )
else:
    CONFIG = None
    # Setup basic logging for imported module
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )

logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore")

# Linux-compatible MT5 import with graceful fallback
try:
    import MetaTrader5 as mt5
    MT5_AVAILABLE = True
    print("✅ MetaTrader5 module loaded successfully")
except ImportError:
    mt5 = None
    MT5_AVAILABLE = False
    import platform
    system_info = platform.system()
    print(f"⚠️  MetaTrader5 not available on {system_info} - continuing without it")

# === OPTIMIZED STRATEGY ENGINE ===
class OptimizedPairsStrategy(PairsStrategyInterface):
    """Vectorized and optimized pairs trading strategy"""
    
    def __init__(self, config: Any, data_manager = None):
        super().__init__(config, data_manager)

    def get_minimum_data_points(self) -> int:
        """Get the minimum number of data points required for strategy calculation."""
        return max(
            getattr(self.config, 'z_period', 50),
            getattr(self.config, 'corr_period', 50),
            getattr(self.config, 'adf_period', 50)
        )

    def validate_market_data(self, market_data: Dict[str, Any]) -> bool:
        """Validate that market data is sufficient for pairs strategy calculations."""
        if 'price1' not in market_data or 'price2' not in market_data:
            return False
        
        price1 = market_data['price1']
        price2 = market_data['price2']
        
        if not isinstance(price1, pd.Series) or not isinstance(price2, pd.Series):
            return False
        
        min_points = self.get_minimum_data_points()
        if len(price1) < min_points or len(price2) < min_points:
            return False
        
        # Check for sufficient non-null values
        if price1.isnull().sum() > len(price1) * 0.2 or price2.isnull().sum() > len(price2) * 0.2:
            return False
        
        return True

    def calculate_indicators_vectorized(self, price1: pd.Series, price2: pd.Series) -> Dict:
        """Calculate all indicators using vectorized operations for maximum speed"""
        
        try:
            # Enhanced input validation with duplicate handling
            if price1 is None or price2 is None:
                logger.error("None price series provided to calculate_indicators_vectorized")
                return {}
            
            if len(price1) == 0 or len(price2) == 0:
                logger.error("Empty price series provided to calculate_indicators_vectorized")
                return {}
            
            # Optimized duplicate handling with rate-limited logging
            original_len1, original_len2 = len(price1), len(price2)
            
            # Fast duplicate removal - check and remove in one step if needed
            if price1.index.has_duplicates:
                price1 = price1[~price1.index.duplicated(keep='last')]
                # Rate-limited logging - only log every 100 duplicates to reduce noise
                if not hasattr(self, '_duplicate_count1'):
                    self._duplicate_count1 = 0
                self._duplicate_count1 += 1
                if self._duplicate_count1 % 100 == 1:  # Log first, then every 100th
                    duplicates_removed = original_len1 - len(price1)
                    logger.warning(f"Price1: Removed {duplicates_removed} duplicate timestamps (total processed: {self._duplicate_count1})")
            
            if price2.index.has_duplicates:
                price2 = price2[~price2.index.duplicated(keep='last')]
                # Rate-limited logging - only log every 100 duplicates to reduce noise
                if not hasattr(self, '_duplicate_count2'):
                    self._duplicate_count2 = 0
                self._duplicate_count2 += 1
                if self._duplicate_count2 % 100 == 1:  # Log first, then every 100th
                    duplicates_removed = original_len2 - len(price2)
                    logger.warning(f"Price2: Removed {duplicates_removed} duplicate timestamps (total processed: {self._duplicate_count2})")
            
            # Quick validation after duplicate removal
            if len(price1) == 0 or len(price2) == 0:
                logger.error("All data removed during duplicate cleanup")
                return {}
            
            # Align series and handle missing data with explicit DataFrame creation
            try:
                # Create DataFrame with explicit handling of duplicates
                df = pd.DataFrame({'price1': price1, 'price2': price2})
                
                # Remove any remaining duplicate indices
                if df.index.has_duplicates:
                    logger.warning("DataFrame has duplicate indices after creation, keeping last values")
                    df = df[~df.index.duplicated(keep='last')]
                
                # Forward fill and drop NaN values
                df = df.fillna(method='ffill').dropna()
                
            except Exception as e:
                logger.error(f"Error creating DataFrame: {e}")
                # Fallback: try using pd.concat with duplicate handling
                try:
                    # Ensure indices are unique before concatenation
                    price1_clean = price1[~price1.index.duplicated(keep='last')]
                    price2_clean = price2[~price2.index.duplicated(keep='last')]
                    df = pd.concat([price1_clean, price2_clean], axis=1).fillna(method='ffill').dropna()
                except Exception as e2:
                    logger.error(f"Fallback DataFrame creation also failed: {e2}")
                    return {}
            
            # Check minimum data requirements
            min_required = max(
                getattr(self.config, 'z_period', 50),
                getattr(self.config, 'corr_period', 50), 
                getattr(self.config, 'adf_period', 50)
            )
            
            if len(df) < min_required:
                return {}
            
            df.columns = ['price1', 'price2']
            
            # Check for zero or negative prices (would cause issues with ratio calculation)
            if (df['price1'] <= 0).any() or (df['price2'] <= 0).any():
                return {}
            
            # Vectorized ratio calculation
            ratio = df['price1'] / df['price2']
            
            # Check for invalid ratios
            if ratio.isnull().all() or not ratio.std() > 0:
                return {}
            
            # Rolling statistics using pandas optimized functions
            z_period = getattr(self.config, 'z_period', 50)
            ratio_ma = ratio.rolling(z_period, min_periods=z_period).mean()
            ratio_std = ratio.rolling(z_period, min_periods=z_period).std()
            
            # Add safeguard against very small standard deviations that cause artificially high z-scores
            min_std_threshold = 1e-6  # Minimum standard deviation threshold
            ratio_std = np.maximum(ratio_std, min_std_threshold)
            
            # Z-score calculation with improved stability
            zscore = (ratio - ratio_ma) / ratio_std
            
            # Check for valid z-score
            if zscore.isnull().all():
                return {}
            
            zscore_perc = zscore.iloc[-1]  # Use iloc for safer access
            
            # Calculate percentage distance from mean
            distance_from_mean = abs((ratio - ratio_ma) / ratio_ma) * 100
            distance_from_mean_perc = distance_from_mean.iloc[-1] if not distance_from_mean.empty else 0
            
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

            # --- Market session filter ---
            if mode == 'realtime':
                # logger.info(f"Checking market session for {price1.name} and {price2.name}")
                session_filter = self._market_session_filter(price1.name, price2.name)
                suitable_conditions.append(session_filter)

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
        
        except Exception as e:
            logger.error(f"Comprehensive error in calculate_indicators_vectorized: {e}")
            logger.error(f"Price1 info: type={type(price1)}, length={len(price1) if hasattr(price1, '__len__') else 'N/A'}")
            logger.error(f"Price2 info: type={type(price2)}, length={len(price2) if hasattr(price2, '__len__') else 'N/A'}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return {}
    
    def _calculate_cost_filter(self, symbol1: str, symbol2: str, price1: pd.Series, price2: pd.Series) -> pd.Series:
        """Calculate cost-based filter for pair trading suitability"""
        
        # If no data manager available (backtesting), assume costs are acceptable
        if not self.data_manager:
            logger.warning("No data manager available for cost calculation - assuming costs are acceptable")
            return pd.Series(True, index=price1.index)
        
        # Check for different data manager types
        symbol_info_available = False
        
        # MT5 data manager uses symbol_info_cache
        if hasattr(self.data_manager, 'symbol_info_cache'):
            if symbol1 in self.data_manager.symbol_info_cache and symbol2 in self.data_manager.symbol_info_cache:
                info1 = self.data_manager.symbol_info_cache[symbol1]
                info2 = self.data_manager.symbol_info_cache[symbol2]
                symbol_info_available = True
                logger.debug(f"Using MT5 symbol info cache for {symbol1}-{symbol2}")
        
        # cTrader data manager uses symbol_details  
        elif hasattr(self.data_manager, 'symbol_details'):
            if symbol1 in self.data_manager.symbol_details and symbol2 in self.data_manager.symbol_details:
                # cTrader doesn't provide spread info in the same way, so assume costs are acceptable
                logger.debug(f"cTrader data manager detected for {symbol1}-{symbol2} - assuming costs are acceptable")
                return pd.Series(True, index=price1.index)
        
        # If no symbol info available, assume costs are acceptable
        if not symbol_info_available:
            logger.debug(f"Symbol info not available for {symbol1} or {symbol2} - assuming costs are acceptable")
            return pd.Series(True, index=price1.index)
        
        # Process MT5 symbol info for cost calculation
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
    
    def generate_signals_vectorized(self, indicators: Dict, symbol1: str = None, symbol2: str = None) -> pd.DataFrame:
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
        
        # Check session filter for exit signals in real-time mode
        mode = os.getenv('TRADING_MODE', 'backtest').lower()
        if mode == 'realtime' and symbol1 and symbol2:
            session_active = self._market_session_filter(symbol1, symbol2)
        else:
            session_active = True  # Always allow exits in backtest mode
        
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
        
        # Exit signals now include session filter check
        signals['long_exit'] = (
            (zscore > -dynamic_exit) & 
            session_active
        )
        
        signals['short_exit'] = (
            (zscore < dynamic_exit) & 
            session_active
        )
        
        return signals

    def _market_session_filter(self, symbol1: str, symbol2: str) -> bool:
        """
        Returns True if both symbols are currently in an open and tradable market session,
        and both have recent tick data (Moscow time check).
        If no data_manager or symbol_info_cache, assume True (for backtesting).
        """
        # If no data manager or no symbol info, assume tradable (for backtest)
        if not self.data_manager or not hasattr(self.data_manager, 'symbol_info_cache'):
            return True

        cache = self.data_manager.symbol_info_cache
        info1 = cache.get(symbol1)
        info2 = cache.get(symbol2)
        if not info1 or not info2:
            return True

        def is_tradable(info):
            # MT5: trade_mode==0 means disabled, >0 means enabled
            if 'trade_mode' in info and info['trade_mode'] != 4:
                return False
            # if 'session_deals' in info and info['session_deals'] == 0:
            #     return False
            # if 'session_trade' in info and info['session_trade'] == 0:
            #     return False
            return True

        def has_recent_tick(symbol):
            try:
                last_tick = mt5.symbol_info_tick(symbol)
                if last_tick is None:
                    logger.info(f"{symbol}: No tick data available. Market might be inactive or closed.")
                    return False
                # Moscow time (UTC+3)
                moscow_tz = datetime.timezone(datetime.timedelta(hours=3))
                current_utc_time = datetime.datetime.now(moscow_tz)
                # Adjust for Moscow offset (+10800 seconds)
                tick_time_diff = current_utc_time.timestamp() - last_tick.time + 10800
                # Only accept if tick is within 60 seconds
                if tick_time_diff > 600:
                    last_tick_time = datetime.datetime.fromtimestamp(last_tick.time, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                    # logger.info(f"{symbol}: Last tick is too old at {last_tick_time}). Market might be inactive.")
                    return False
                return True
            except Exception:
                return False

        return (
            is_tradable(info1) and is_tradable(info2)
            and has_recent_tick(symbol1) and has_recent_tick(symbol2)
        )

