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
from utils.portfolio_manager import PortfolioCalculator
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
        required_fields = ['z_period', 'corr_period', 'adf_period']
        periods = []
        
        for field in required_fields:
            if not hasattr(self.config, field):
                logger.error(f"Missing required config field: {field}")
                return 0  # Return 0 to indicate configuration error
            periods.append(getattr(self.config, field))
        
        return max(periods)

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
                    logger.error(f"DataFrame creation failed completely: {e2}")
                    return {}
            
            # Check minimum data requirements with validation
            min_required = self.get_minimum_data_points()
            if min_required == 0:
                logger.error("Configuration validation failed - cannot proceed with indicators")
                return {}
            
            if len(df) < min_required:
                logger.warning(f"Insufficient data: {len(df)} points available, {min_required} required")
                return {}
            
            df.columns = ['price1', 'price2']
            
            # Check for zero or negative prices (would cause issues with ratio calculation)
            if (df['price1'] <= 0).any() or (df['price2'] <= 0).any():
                return {}
            
            # Vectorized ratio calculation
            ratio = df['price1'] / df['price2']
            
            # Check for invalid ratios
            if ratio.isnull().all() or not ratio.std() > 0:
                logger.error("Invalid price ratios - cannot calculate indicators")
                return {}
            
            # Rolling statistics using pandas optimized functions with validation
            if not hasattr(self.config, 'z_period'):
                logger.error("Missing z_period in configuration")
                return {}
                
            if not hasattr(self.config, 'corr_period'):
                logger.error("Missing corr_period in configuration")
                return {}
                
            z_period = self.config.z_period
            if z_period <= 0:
                logger.error(f"Invalid z_period: {z_period}. Must be positive.")
                return {}
                
            if self.config.corr_period <= 0:
                logger.error(f"Invalid corr_period: {self.config.corr_period}. Must be positive.")
                return {}
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
            if distance_from_mean.empty:
                logger.error("Distance from mean calculation resulted in empty series")
                return {}
            distance_from_mean_perc = distance_from_mean.iloc[-1]
            
            # Correlation using rolling window
            correlation = df['price1'].rolling(self.config.corr_period).corr(df['price2'])
            
            # Volatility measures
            vol1 = df['price1'].rolling(self.config.z_period).std() / df['price1'].rolling(self.config.z_period).mean()
            vol2 = df['price2'].rolling(self.config.z_period).std() / df['price2'].rolling(self.config.z_period).mean()
            vol_ratio = np.maximum(vol1, vol2) / np.minimum(vol1, vol2)
            
            # ADF test - validate configuration and skip if disabled
            if not hasattr(self.config, 'enable_adf'):
                logger.error("Missing enable_adf in configuration")
                return {}
                
            if self.config.enable_adf:
                if not hasattr(self.config, 'adf_period'):
                    logger.error("Missing adf_period in configuration when enable_adf is True")
                    return {}
                    
                if self.config.adf_period <= 0:
                    logger.error(f"Invalid adf_period: {self.config.adf_period}. Must be positive.")
                    return {}
                    
                adf_pvals = self._rolling_adf_vectorized(ratio, self.config.adf_period)
            else:
                # When ADF is disabled, create array that will pass suitability checks
                adf_pvals = np.zeros(len(ratio))  # p-value = 0 means stationary
            
            # Johansen test - validate configuration and skip if disabled
            if not hasattr(self.config, 'enable_johansen'):
                logger.error("Missing enable_johansen in configuration")
                return {}
                
            if self.config.enable_johansen:
                if not hasattr(self.config, 'adf_period'):
                    logger.error("Missing adf_period in configuration when enable_johansen is True")
                    return {}
                    
                if self.config.adf_period <= 0:
                    logger.error(f"Invalid adf_period for Johansen test: {self.config.adf_period}. Must be positive.")
                    return {}
                    
                johansen_stats, johansen_crits = self._rolling_johansen_vectorized(
                    df['price1'], df['price2'], self.config.adf_period
                )
            else:
                # When Johansen is disabled, create arrays that will pass suitability checks
                johansen_stats = np.ones(len(ratio))  # stat > crit means cointegrated
                johansen_crits = np.zeros(len(ratio))  # crit = 0 makes stat > crit always true
        
            # Dynamic thresholds if enabled
            if self.config.dynamic_z:
                # Validate dynamic_z related configuration
                if not hasattr(self.config, 'min_volatility'):
                    logger.error("Missing min_volatility in configuration for dynamic_z")
                    return {}
                    
                if not hasattr(self.config, 'z_entry'):
                    logger.error("Missing z_entry in configuration for dynamic_z")
                    return {}
                    
                if not hasattr(self.config, 'z_exit'):
                    logger.error("Missing z_exit in configuration for dynamic_z")
                    return {}
                    
                if self.config.min_volatility <= 0:
                    logger.error(f"Invalid min_volatility: {self.config.min_volatility}. Must be positive for dynamic_z.")
                    return {}
                    
                if self.config.z_entry <= 0:
                    logger.error(f"Invalid z_entry: {self.config.z_entry}. Must be positive.")
                    return {}
                    
                if self.config.z_exit < 0:
                    logger.error(f"Invalid z_exit: {self.config.z_exit}. Must be non-negative.")
                    return {}
                    
                min_vol = self.config.min_volatility
                dynamic_entry = np.maximum(self.config.z_entry, self.config.z_entry * ratio_std / min_vol)
                dynamic_exit = np.maximum(self.config.z_exit, self.config.z_exit * ratio_std / min_vol)
            else:
                # Validate static threshold configuration
                if not hasattr(self.config, 'z_entry'):
                    logger.error("Missing z_entry in configuration")
                    return {}
                    
                if not hasattr(self.config, 'z_exit'):
                    logger.error("Missing z_exit in configuration")
                    return {}
                    
                if self.config.z_entry <= 0:
                    logger.error(f"Invalid z_entry: {self.config.z_entry}. Must be positive.")
                    return {}
                    
                if self.config.z_exit < 0:
                    logger.error(f"Invalid z_exit: {self.config.z_exit}. Must be non-negative.")
                    return {}
                    
                dynamic_entry = np.full(len(zscore), self.config.z_entry)
                dynamic_exit = np.full(len(zscore), self.config.z_exit)
        
            # Calculate cost-based filter for pair trading
            mode = os.getenv('TRADING_MODE', 'backtest').lower()
            if mode == 'backtest':
                cost_filter = pd.Series(True, index=df.index)  # Always pass cost filter in backtest mode
            else:
                cost_filter = self._calculate_cost_filter(price1.name, price2.name, df['price1'], df['price2'])
        
            # Validate configuration for suitability filter conditions
            required_suitability_fields = [
                'min_volatility', 'min_distance', 'enable_correlation', 'min_corr',
                'max_adf_pval', 'johansen_crit_level', 'enable_vol_ratio', 'vol_ratio_max'
            ]
            for field in required_suitability_fields:
                if not hasattr(self.config, field):
                    logger.error(f"Missing required suitability configuration field: {field}")
                    return {}
                    
            # Validate numeric ranges for suitability parameters
            if self.config.min_volatility < 0:
                logger.error(f"Invalid min_volatility: {self.config.min_volatility}. Must be non-negative.")
                return {}
                
            if self.config.min_distance < 0:
                logger.error(f"Invalid min_distance: {self.config.min_distance}. Must be non-negative.")
                return {}
                
            if not (-1 <= self.config.min_corr <= 1):
                logger.error(f"Invalid min_corr: {self.config.min_corr}. Must be between -1 and 1.")
                return {}
                
            if not (0 <= self.config.max_adf_pval <= 1):
                logger.error(f"Invalid max_adf_pval: {self.config.max_adf_pval}. Must be between 0 and 1.")
                return {}
                
            if self.config.johansen_crit_level not in [90, 95, 99]:
                logger.error(f"Invalid johansen_crit_level: {self.config.johansen_crit_level}. Must be 90, 95, or 99.")
                return {}
                
            if self.config.vol_ratio_max <= 0:
                logger.error(f"Invalid vol_ratio_max: {self.config.vol_ratio_max}. Must be positive.")
                return {}
        
            # Suitability filter - build conditionally based on enabled tests and parameters
            suitable_conditions = []

            # Core conditions that are always checked regardless of enable flags
            if self.config.min_volatility > 0:
                suitable_conditions.append(ratio_std > self.config.min_volatility)
                
            if self.config.min_distance > 0:
                suitable_conditions.append(distance_from_mean > self.config.min_distance)
                
            # Always check cost filter as it's critical for profitability
            suitable_conditions.append(cost_filter)

            # --- Market session filter for MT5---
            # if mode == 'realtime':
            #     session_filter = self._market_session_filter(price1.name, price2.name)
            #     suitable_conditions.append(session_filter)

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
        
        # Check trading mode
        mode = os.getenv('TRADING_MODE', 'backtest').lower()
        if mode == 'backtest':
            return pd.Series(True, index=price1.index)  # Always pass cost filter in backtest mode
        
        # For live trading, validate cost parameters
        if not self.data_manager:
            logger.error("No data manager available for cost calculation in live mode")
            return pd.Series(False, index=price1.index)
        
        # Validate required configuration for cost calculation
        required_cost_fields = ['commission_fixed', 'max_commission_perc']
        for field in required_cost_fields:
            if not hasattr(self.config, field):
                logger.error(f"Missing required cost configuration field: {field}")
                return pd.Series(False, index=price1.index)
        
        if self.config.commission_fixed < 0:
            logger.error(f"Invalid commission_fixed: {self.config.commission_fixed}. Must be non-negative.")
            return pd.Series(False, index=price1.index)
            
        if not (0 <= self.config.max_commission_perc <= 100):
            logger.error(f"Invalid max_commission_perc: {self.config.max_commission_perc}. Must be between 0 and 100.")
            return pd.Series(False, index=price1.index)
        
        # Check for different data manager types
        symbol_info_available = False
        info1 = None
        info2 = None
        
        # MT5 data manager uses symbol_info_cache
        if hasattr(self.data_manager, 'symbol_info_cache'):
            if symbol1 in self.data_manager.symbol_info_cache and symbol2 in self.data_manager.symbol_info_cache:
                info1 = self.data_manager.symbol_info_cache[symbol1]
                info2 = self.data_manager.symbol_info_cache[symbol2]
                symbol_info_available = True
                logger.debug(f"Using MT5 symbol info cache for {symbol1}-{symbol2}")
        
        # cTrader data manager uses symbol_details  
        elif hasattr(self.data_manager, 'symbol_details'):
            # Check if both symbols exist in cTrader
            if symbol1 not in self.data_manager.symbol_details or symbol2 not in self.data_manager.symbol_details:
                logger.info(f"One or both symbols {symbol1}, {symbol2} not available in cTrader - skipping cost validation")
                # For symbols not available in cTrader (like US stocks), skip cost validation 
                # This allows the strategy to continue without cost filtering for unavailable symbols
                return pd.Series(True, index=price1.index)
            
            info1 = self.data_manager.symbol_details[symbol1]
            info2 = self.data_manager.symbol_details[symbol2]
            
            # Check if spread information is available (updated by cTrader broker)
            if 'spread' in info1 and 'point' in info1 and 'spread' in info2 and 'point' in info2:
                symbol_info_available = True
                logger.debug(f"Using cTrader symbol details with spread info for {symbol1}-{symbol2}")
            else:
                logger.debug(f"cTrader symbol details available but spread info not yet calculated for {symbol1}-{symbol2}")
                # Return True temporarily until spread info is available (allow trading to continue)
                return pd.Series(True, index=price1.index)
        
        # If no symbol info available in live mode, cannot validate costs
        if not symbol_info_available or info1 is None or info2 is None:
            logger.debug(f"Symbol info not available for {symbol1} or {symbol2} in live mode - using fallback cost validation")
            # For cases where symbol info is not available, we can't do cost validation
            # but we should allow trading to continue (this handles cases like symbols not available in the broker)
            return pd.Series(True, index=price1.index)
              
        # Process symbol info for cost calculation - spread calculation is now unified
        # Calculate spreads as percentage of price (both MT5 and cTrader use same calculation)
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
        
        # Calculate commission using the new portfolio manager method with fallback
        def calculate_commission_percentage(symbol_info, symbol, price_series):
            """Calculate commission percentage for a symbol using portfolio manager or fallback"""
            try:
                # Try to use the new _calculate_trade_commission function
                if hasattr(symbol_info, 'get') and all(key in symbol_info for key in ['commissionType', 'preciseTradingCommissionRate', 'preciseMinCommission']):
                    # Create a temporary portfolio calculator to use the commission calculation
                    temp_calculator = PortfolioCalculator(0, "USD")  # Initial value and currency don't matter for commission calc
                    
                    # Calculate commission for a sample trade (1 lot) and convert to percentage
                    sample_volume = 1.0
                    commission_results = []
                    
                    for price in price_series:
                        if price > 0:
                            commission_dollar = temp_calculator._calculate_trade_commission(symbol_info, sample_volume, price)
                            # Convert to percentage of trade value
                            lot_size = symbol_info.get('lot_size', 100000)  # Default for forex
                            trade_value = sample_volume * price * lot_size
                            if trade_value > 0:
                                commission_perc = (commission_dollar / trade_value) * 100
                                commission_results.append(commission_perc)
                            else:
                                commission_results.append(0.0)
                        else:
                            commission_results.append(0.0)
                    
                    if commission_results:
                        logger.debug(f"Using portfolio manager commission calculation for {symbol}: "
                                   f"avg={np.mean(commission_results):.4f}%")
                        return pd.Series(commission_results, index=price_series.index)
                    else:
                        raise ValueError("No valid commission results calculated")
                        
                else:
                    raise ValueError("Missing commission fields in symbol_info")
                    
            except Exception as e:
                logger.debug(f"Portfolio manager commission calculation failed for {symbol}: {e}, using fallback")
                
                # Fallback to the original fixed commission method for stocks/ETFs
                if is_stock_or_etf(symbol):
                    return (self.config.commission_fixed / price_series) * 100
                else:
                    return pd.Series(0.0, index=price_series.index)  # No fixed commission for non-stocks
        
        # Calculate commission for both symbols
        commission1_perc = calculate_commission_percentage(info1, symbol1, price1)
        commission2_perc = calculate_commission_percentage(info2, symbol2, price2)
        
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
            
            # Determine commission calculation method used
            commission1_method = "Portfolio Manager" if (hasattr(info1, 'get') and 
                                                       all(key in info1 for key in ['commissionType', 'preciseTradingCommissionRate', 'preciseMinCommission'])) else "Fixed Fallback"
            commission2_method = "Portfolio Manager" if (hasattr(info2, 'get') and 
                                                       all(key in info2 for key in ['commissionType', 'preciseTradingCommissionRate', 'preciseMinCommission'])) else "Fixed Fallback"
            
            logger.info(f"Cost filter for {symbol1}-{symbol2}: avg_cost={avg_cost:.4f}%, threshold={self.config.max_commission_perc:.4f}%, "
                       f"acceptable={cost_acceptable.mean()*100:.1f}% of time")
            logger.info(f"  Cost for {symbol1}: commission={commission1_perc.mean():.4f}%, spread={spread1_perc.mean():.4f}%")
            logger.info(f"  Cost for {symbol2}: commission={commission2_perc.mean():.4f}%, spread={spread2_perc.mean():.4f}%")

        return cost_acceptable

    def _rolling_adf_vectorized(self, series: pd.Series, window: int) -> np.ndarray:
        """Optimized rolling ADF test with proper error handling"""
        pvals = np.full(len(series), np.nan)
        
        # Use numpy arrays for faster computation
        values = series.values
        
        for i in range(window, len(values)):
            try:
                window_data = values[i-window+1:i+1]
                if len(np.unique(window_data)) > 1:  # Avoid constant series
                    pvals[i] = adfuller(window_data, autolag='AIC')[1]
                else:
                    # Skip calculation for constant series, leave as NaN
                    logger.debug(f"Skipping ADF test at index {i} due to constant series")
            except Exception as e:
                # Log specific error and skip this window
                logger.debug(f"ADF test failed at index {i}: {e}")
                # Leave as NaN - will be handled by suitability filter
                
        return pvals
    
    def _rolling_johansen_vectorized(self, series1: pd.Series, series2: pd.Series, 
                                   window: int) -> Tuple[np.ndarray, np.ndarray]:
        """Optimized rolling Johansen test with proper error handling"""
        if not hasattr(self.config, 'johansen_crit_level'):
            logger.error("Missing johansen_crit_level in configuration")
            return np.array([]), np.array([])
            
        if self.config.johansen_crit_level not in [90, 95, 99]:
            logger.error(f"Invalid johansen_crit_level: {self.config.johansen_crit_level}. Must be 90, 95, or 99.")
            return np.array([]), np.array([])
            
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
                else:
                    # Skip calculation for windows with no variance, leave as NaN
                    logger.debug(f"Skipping Johansen test at index {i} due to zero variance")
            except Exception as e:
                # Log specific error and skip this window
                logger.debug(f"Johansen test failed at index {i}: {e}")
                # Leave as NaN - will be handled by suitability filter
                
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
        if mode == 'backtest' and symbol1 and symbol2:
            session_active = True  # Always allow exits in backtest mode
        else:
            session_active = True
            # session_active = self._market_session_filter(symbol1, symbol2)      # for MT5 only: check if both symbols are in an open and tradable market session      
        
        # SIMPLIFIED signal generation - trigger when Z-score exceeds threshold
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
        For backtesting mode, returns True. For live trading, validates market sessions.
        """
        # Check trading mode from environment
        mode = os.getenv('TRADING_MODE', 'backtest').lower()
        if mode == 'backtest':
            return True  # Always allow trading in backtest mode
        
        # For live trading, validate market sessions
        if not self.data_manager:
            logger.error("No data manager available for market session validation in live mode")
            return False
            
        if not hasattr(self.data_manager, 'symbol_info_cache'):
            logger.error("No symbol_info_cache available for market session validation")
            return False

        cache = self.data_manager.symbol_info_cache
        info1 = cache.get(symbol1)
        info2 = cache.get(symbol2)
        
        if not info1:
            logger.warning(f"No symbol info available for {symbol1} - market session unknown")
            return False
            
        if not info2:
            logger.warning(f"No symbol info available for {symbol2} - market session unknown")
            return False

        def is_tradable(info):
            # MT5: trade_mode==4 means full trading allowed
            if 'trade_mode' in info:
                if info['trade_mode'] != 4:
                    logger.debug(f"Symbol not tradable: trade_mode={info['trade_mode']}")
                    return False
            else:
                logger.warning("No trade_mode information available")
                return False
            return True

        def has_recent_tick(symbol):
            """ For MT5: Check if the last tick for the symbol is recent (within 10 minutes)"""
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
                
                # Only accept if tick is within 10 minutes (600 seconds)
                if tick_time_diff > 600:
                    last_tick_time = datetime.datetime.fromtimestamp(last_tick.time, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                    logger.debug(f"{symbol}: Last tick is too old at {last_tick_time}). Market might be inactive.")
                    return False
                return True
            except Exception as e:
                logger.error(f"Error checking tick data for {symbol}: {e}")
                return False

        return (
            is_tradable(info1) and is_tradable(info2)
            and has_recent_tick(symbol1) and has_recent_tick(symbol2)
        )

