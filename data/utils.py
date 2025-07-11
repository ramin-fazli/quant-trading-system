"""
Data utilities and helper functions for efficient data operations
"""

import logging
import warnings
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union
import concurrent.futures
import multiprocessing as mp

import numpy as np
import pandas as pd
from scipy import stats
import joblib
from numba import jit, njit
import pyarrow as pa
import pyarrow.parquet as pq

from .data_manager import DataQuery, DataType

logger = logging.getLogger(__name__)

# Suppress warnings for cleaner output
warnings.filterwarnings('ignore', category=pd.errors.PerformanceWarning)


class DataUtils:
    """Utility functions for data processing"""
    
    @staticmethod
    def resample_data(data: pd.DataFrame, 
                     timeframe: str, 
                     agg_method: str = 'ohlc') -> pd.DataFrame:
        """
        Resample data to different timeframes
        
        Args:
            data: Input DataFrame with datetime index
            timeframe: Target timeframe ('1T', '5T', '15T', '1H', '1D', etc.)
            agg_method: Aggregation method ('ohlc', 'mean', 'last', etc.)
        """
        if data.empty:
            return data
        
        try:
            if agg_method == 'ohlc':
                # Standard OHLC aggregation
                price_col = 'close' if 'close' in data.columns else data.select_dtypes(include=[np.number]).columns[0]
                
                resampled = data[price_col].resample(timeframe).agg([
                    ('open', 'first'),
                    ('high', 'max'),
                    ('low', 'min'),
                    ('close', 'last')
                ])
                
                # Add volume if present
                if 'volume' in data.columns:
                    resampled['volume'] = data['volume'].resample(timeframe).sum()
                
                # Add other numeric columns with last value
                numeric_cols = data.select_dtypes(include=[np.number]).columns
                for col in numeric_cols:
                    if col not in ['open', 'high', 'low', 'close', 'volume']:
                        resampled[col] = data[col].resample(timeframe).last()
                        
            else:
                # Other aggregation methods
                resampled = data.resample(timeframe).agg(agg_method)
            
            return resampled.dropna()
            
        except Exception as e:
            logger.error(f"Resampling failed: {e}")
            return pd.DataFrame()
    
    @staticmethod
    def align_data(data_dict: Dict[str, pd.DataFrame], 
                   method: str = 'outer',
                   fill_method: str = 'ffill') -> Dict[str, pd.DataFrame]:
        """
        Align multiple DataFrames to common time index
        
        Args:
            data_dict: Dictionary of symbol -> DataFrame
            method: Join method ('inner', 'outer', 'left', 'right')
            fill_method: Fill method for missing values ('ffill', 'bfill', 'interpolate')
        """
        if not data_dict:
            return {}
        
        # Get common time index
        all_indices = [df.index for df in data_dict.values() if not df.empty]
        if not all_indices:
            return data_dict
        
        if method == 'inner':
            common_index = all_indices[0]
            for idx in all_indices[1:]:
                common_index = common_index.intersection(idx)
        else:  # outer, left, right
            common_index = all_indices[0]
            for idx in all_indices[1:]:
                common_index = common_index.union(idx)
        
        # Align all DataFrames
        aligned_data = {}
        for symbol, df in data_dict.items():
            if df.empty:
                aligned_data[symbol] = df
                continue
                
            # Reindex to common index
            aligned_df = df.reindex(common_index)
            
            # Fill missing values
            if fill_method == 'ffill':
                aligned_df = aligned_df.fillna(method='ffill')
            elif fill_method == 'bfill':
                aligned_df = aligned_df.fillna(method='bfill')
            elif fill_method == 'interpolate':
                aligned_df = aligned_df.interpolate()
            
            aligned_data[symbol] = aligned_df
        
        return aligned_data
    
    @staticmethod
    def calculate_returns(data: pd.DataFrame, 
                         price_col: str = 'close',
                         return_type: str = 'simple',
                         periods: int = 1) -> pd.Series:
        """
        Calculate returns for price data
        
        Args:
            data: DataFrame with price data
            price_col: Column name for prices
            return_type: 'simple' or 'log'
            periods: Number of periods for return calculation
        """
        if price_col not in data.columns:
            raise ValueError(f"Column {price_col} not found in data")
        
        prices = data[price_col]
        
        if return_type == 'simple':
            returns = prices.pct_change(periods=periods)
        elif return_type == 'log':
            returns = np.log(prices / prices.shift(periods))
        else:
            raise ValueError("return_type must be 'simple' or 'log'")
        
        return returns.dropna()
    
    @staticmethod
    def calculate_volatility(returns: pd.Series, 
                            window: int = 20,
                            annualize: bool = True,
                            trading_periods: int = 252) -> pd.Series:
        """Calculate rolling volatility"""
        vol = returns.rolling(window=window).std()
        
        if annualize:
            vol = vol * np.sqrt(trading_periods)
        
        return vol
    
    @staticmethod
    def detect_gaps(data: pd.DataFrame, 
                   threshold: float = 0.05,
                   price_col: str = 'close') -> pd.DataFrame:
        """Detect price gaps in data"""
        if price_col not in data.columns:
            return pd.DataFrame()
        
        prices = data[price_col]
        returns = prices.pct_change().abs()
        
        gaps = returns > threshold
        gap_data = data[gaps].copy()
        gap_data['gap_size'] = returns[gaps]
        
        return gap_data
    
    @staticmethod
    def remove_outliers(data: pd.DataFrame, 
                       columns: List[str],
                       method: str = 'iqr',
                       factor: float = 1.5) -> pd.DataFrame:
        """Remove outliers from data"""
        cleaned_data = data.copy()
        
        for col in columns:
            if col not in data.columns:
                continue
            
            if method == 'iqr':
                Q1 = data[col].quantile(0.25)
                Q3 = data[col].quantile(0.75)
                IQR = Q3 - Q1
                lower_bound = Q1 - factor * IQR
                upper_bound = Q3 + factor * IQR
                
                mask = (data[col] >= lower_bound) & (data[col] <= upper_bound)
                cleaned_data = cleaned_data[mask]
            
            elif method == 'zscore':
                z_scores = np.abs(stats.zscore(data[col]))
                mask = z_scores < factor
                cleaned_data = cleaned_data[mask]
        
        logger.info(f"Removed {len(data) - len(cleaned_data)} outliers")
        return cleaned_data
    
    @staticmethod
    def fill_missing_data(data: pd.DataFrame, 
                         method: str = 'interpolate',
                         limit: Optional[int] = None) -> pd.DataFrame:
        """Fill missing data using various methods"""
        if method == 'interpolate':
            return data.interpolate(limit=limit)
        elif method == 'ffill':
            return data.fillna(method='ffill', limit=limit)
        elif method == 'bfill':
            return data.fillna(method='bfill', limit=limit)
        elif method == 'mean':
            return data.fillna(data.mean())
        elif method == 'median':
            return data.fillna(data.median())
        else:
            raise ValueError(f"Unknown fill method: {method}")


@njit
def fast_sma(prices: np.ndarray, window: int) -> np.ndarray:
    """Fast simple moving average using Numba"""
    n = len(prices)
    sma = np.full(n, np.nan)
    
    if window > n:
        return sma
    
    # Calculate first SMA
    sma[window-1] = np.mean(prices[:window])
    
    # Rolling calculation
    for i in range(window, n):
        sma[i] = sma[i-1] + (prices[i] - prices[i-window]) / window
    
    return sma


@njit
def fast_ema(prices: np.ndarray, window: int) -> np.ndarray:
    """Fast exponential moving average using Numba"""
    n = len(prices)
    ema = np.full(n, np.nan)
    
    if n == 0:
        return ema
    
    alpha = 2.0 / (window + 1.0)
    ema[0] = prices[0]
    
    for i in range(1, n):
        ema[i] = alpha * prices[i] + (1 - alpha) * ema[i-1]
    
    return ema


@njit
def fast_rsi(prices: np.ndarray, window: int = 14) -> np.ndarray:
    """Fast RSI calculation using Numba"""
    n = len(prices)
    rsi = np.full(n, np.nan)
    
    if n < window + 1:
        return rsi
    
    # Calculate price changes
    changes = np.diff(prices)
    gains = np.where(changes > 0, changes, 0.0)
    losses = np.where(changes < 0, -changes, 0.0)
    
    # Initial averages
    avg_gain = np.mean(gains[:window])
    avg_loss = np.mean(losses[:window])
    
    if avg_loss == 0:
        rsi[window] = 100.0
    else:
        rs = avg_gain / avg_loss
        rsi[window] = 100.0 - (100.0 / (1.0 + rs))
    
    # Rolling calculation
    alpha = 1.0 / window
    for i in range(window + 1, n):
        avg_gain = (1 - alpha) * avg_gain + alpha * gains[i-1]
        avg_loss = (1 - alpha) * avg_loss + alpha * losses[i-1]
        
        if avg_loss == 0:
            rsi[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi[i] = 100.0 - (100.0 / (1.0 + rs))
    
    return rsi


class TechnicalIndicators:
    """Fast technical indicators using vectorized operations"""
    
    @staticmethod
    def sma(data: pd.Series, window: int) -> pd.Series:
        """Simple Moving Average"""
        if len(data) < window:
            return pd.Series(index=data.index, dtype=float)
        
        result = fast_sma(data.values, window)
        return pd.Series(result, index=data.index)
    
    @staticmethod
    def ema(data: pd.Series, window: int) -> pd.Series:
        """Exponential Moving Average"""
        if len(data) == 0:
            return pd.Series(index=data.index, dtype=float)
        
        result = fast_ema(data.values, window)
        return pd.Series(result, index=data.index)
    
    @staticmethod
    def rsi(data: pd.Series, window: int = 14) -> pd.Series:
        """Relative Strength Index"""
        if len(data) < window + 1:
            return pd.Series(index=data.index, dtype=float)
        
        result = fast_rsi(data.values, window)
        return pd.Series(result, index=data.index)
    
    @staticmethod
    def bollinger_bands(data: pd.Series, window: int = 20, std_dev: float = 2.0) -> Dict[str, pd.Series]:
        """Bollinger Bands"""
        sma = data.rolling(window=window).mean()
        std = data.rolling(window=window).std()
        
        upper = sma + (std * std_dev)
        lower = sma - (std * std_dev)
        
        return {
            'middle': sma,
            'upper': upper,
            'lower': lower,
            'width': upper - lower,
            'position': (data - lower) / (upper - lower)
        }
    
    @staticmethod
    def macd(data: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> Dict[str, pd.Series]:
        """MACD indicator"""
        ema_fast = TechnicalIndicators.ema(data, fast)
        ema_slow = TechnicalIndicators.ema(data, slow)
        
        macd_line = ema_fast - ema_slow
        signal_line = TechnicalIndicators.ema(macd_line, signal)
        histogram = macd_line - signal_line
        
        return {
            'macd': macd_line,
            'signal': signal_line,
            'histogram': histogram
        }


class DataCompression:
    """Data compression utilities for efficient storage"""
    
    @staticmethod
    def compress_dataframe(df: pd.DataFrame) -> pd.DataFrame:
        """Optimize DataFrame memory usage"""
        df_optimized = df.copy()
        
        for col in df_optimized.columns:
            col_type = df_optimized[col].dtype
            
            if col_type != 'object':
                c_min = df_optimized[col].min()
                c_max = df_optimized[col].max()
                
                if str(col_type)[:3] == 'int':
                    if c_min > np.iinfo(np.int8).min and c_max < np.iinfo(np.int8).max:
                        df_optimized[col] = df_optimized[col].astype(np.int8)
                    elif c_min > np.iinfo(np.int16).min and c_max < np.iinfo(np.int16).max:
                        df_optimized[col] = df_optimized[col].astype(np.int16)
                    elif c_min > np.iinfo(np.int32).min and c_max < np.iinfo(np.int32).max:
                        df_optimized[col] = df_optimized[col].astype(np.int32)
                    elif c_min > np.iinfo(np.int64).min and c_max < np.iinfo(np.int64).max:
                        df_optimized[col] = df_optimized[col].astype(np.int64)
                        
                elif str(col_type)[:5] == 'float':
                    if c_min > np.finfo(np.float32).min and c_max < np.finfo(np.float32).max:
                        df_optimized[col] = df_optimized[col].astype(np.float32)
                    else:
                        df_optimized[col] = df_optimized[col].astype(np.float64)
        
        logger.info(f"Memory usage reduced from {df.memory_usage(deep=True).sum():,} to {df_optimized.memory_usage(deep=True).sum():,} bytes")
        return df_optimized
    
    @staticmethod
    def save_compressed(df: pd.DataFrame, filepath: str, compression: str = 'snappy') -> None:
        """Save DataFrame with compression"""
        df.to_parquet(filepath, compression=compression, engine='pyarrow')
    
    @staticmethod
    def load_compressed(filepath: str) -> pd.DataFrame:
        """Load compressed DataFrame"""
        return pd.read_parquet(filepath, engine='pyarrow')


class ParallelProcessor:
    """Parallel processing utilities for large datasets"""
    
    @staticmethod
    def process_parallel(data_chunks: List[pd.DataFrame], 
                        func: callable,
                        n_jobs: int = -1,
                        **kwargs) -> List[Any]:
        """Process data chunks in parallel"""
        if n_jobs == -1:
            n_jobs = mp.cpu_count()
        
        with concurrent.futures.ProcessPoolExecutor(max_workers=n_jobs) as executor:
            futures = [executor.submit(func, chunk, **kwargs) for chunk in data_chunks]
            results = [future.result() for future in concurrent.futures.as_completed(futures)]
        
        return results
    
    @staticmethod
    def chunk_dataframe(df: pd.DataFrame, chunk_size: int) -> List[pd.DataFrame]:
        """Split DataFrame into chunks"""
        chunks = []
        for i in range(0, len(df), chunk_size):
            chunks.append(df.iloc[i:i + chunk_size])
        return chunks
    
    @staticmethod
    def parallel_apply(df: pd.DataFrame, 
                      func: callable,
                      chunk_size: int = 10000,
                      n_jobs: int = -1) -> pd.DataFrame:
        """Apply function to DataFrame in parallel"""
        chunks = ParallelProcessor.chunk_dataframe(df, chunk_size)
        results = ParallelProcessor.process_parallel(chunks, func, n_jobs)
        return pd.concat(results, ignore_index=True)


class DataProfiler:
    """Data profiling and quality assessment"""
    
    @staticmethod
    def profile_dataframe(df: pd.DataFrame) -> Dict[str, Any]:
        """Generate comprehensive data profile"""
        profile = {
            'shape': df.shape,
            'memory_usage': df.memory_usage(deep=True).sum(),
            'dtypes': df.dtypes.to_dict(),
            'missing_values': df.isnull().sum().to_dict(),
            'duplicate_rows': df.duplicated().sum(),
            'numeric_stats': {},
            'categorical_stats': {},
            'date_range': {}
        }
        
        # Numeric columns statistics
        numeric_cols = df.select_dtypes(include=[np.number]).columns
        for col in numeric_cols:
            profile['numeric_stats'][col] = {
                'mean': df[col].mean(),
                'std': df[col].std(),
                'min': df[col].min(),
                'max': df[col].max(),
                'median': df[col].median(),
                'skewness': df[col].skew(),
                'kurtosis': df[col].kurtosis(),
                'outliers': len(df[col][np.abs(stats.zscore(df[col].dropna())) > 3])
            }
        
        # Categorical columns statistics
        categorical_cols = df.select_dtypes(include=['object', 'category']).columns
        for col in categorical_cols:
            profile['categorical_stats'][col] = {
                'unique_values': df[col].nunique(),
                'most_frequent': df[col].mode().iloc[0] if len(df[col].mode()) > 0 else None,
                'frequency': df[col].value_counts().head().to_dict()
            }
        
        # Date range for datetime index
        if isinstance(df.index, pd.DatetimeIndex):
            profile['date_range'] = {
                'start': df.index.min(),
                'end': df.index.max(),
                'frequency': pd.infer_freq(df.index),
                'business_days': len(pd.bdate_range(df.index.min(), df.index.max())),
                'gaps': DataProfiler._detect_missing_dates(df.index)
            }
        
        return profile
    
    @staticmethod
    def _detect_missing_dates(date_index: pd.DatetimeIndex) -> List[Tuple[datetime, datetime]]:
        """Detect missing date ranges in datetime index"""
        if len(date_index) < 2:
            return []
        
        # Infer frequency
        freq = pd.infer_freq(date_index)
        if not freq:
            return []
        
        # Generate expected date range
        expected_range = pd.date_range(date_index.min(), date_index.max(), freq=freq)
        missing_dates = expected_range.difference(date_index)
        
        # Group consecutive missing dates
        gaps = []
        if len(missing_dates) > 0:
            current_start = missing_dates[0]
            current_end = missing_dates[0]
            
            for date in missing_dates[1:]:
                if date == current_end + pd.Timedelta(freq):
                    current_end = date
                else:
                    gaps.append((current_start, current_end))
                    current_start = date
                    current_end = date
            
            gaps.append((current_start, current_end))
        
        return gaps
    
    @staticmethod
    def assess_data_quality(df: pd.DataFrame) -> float:
        """Calculate overall data quality score (0-1)"""
        if df.empty:
            return 0.0
        
        total_cells = df.shape[0] * df.shape[1]
        missing_cells = df.isnull().sum().sum()
        duplicate_rows = df.duplicated().sum()
        
        # Calculate quality score
        completeness = 1 - (missing_cells / total_cells)
        uniqueness = 1 - (duplicate_rows / df.shape[0])
        
        # Weighted average
        quality_score = 0.7 * completeness + 0.3 * uniqueness
        
        return max(0.0, min(1.0, quality_score))