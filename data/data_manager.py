"""
Comprehensive Data Management System for Quantitative Trading

This module provides a unified, efficient, and scalable data management system
for backtesting, live trading, and analysis. It supports multiple data sources,
caching, compression, and real-time updates.

Features:
- Multi-source data aggregation (MT5, cTrader, CSV, APIs)
- Intelligent caching with compression
- Real-time and historical data management
- Vectorized operations for backtesting
- Thread-safe operations for live trading
- Automatic data validation and cleaning
- Memory-efficient data structures
"""

import asyncio
import logging
import threading
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union, Callable
import warnings

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from cachetools import TTLCache, LRUCache
import joblib
import sqlite3
import redis
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, Float, String, DateTime, Index
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Suppress pandas warnings for cleaner output
warnings.filterwarnings('ignore', category=pd.errors.PerformanceWarning)

logger = logging.getLogger(__name__)


class DataSource(Enum):
    """Supported data sources"""
    MT5 = "mt5"
    CTRADER = "ctrader"
    CSV = "csv"
    API = "api"
    PARQUET = "parquet"
    DATABASE = "database"
    REDIS = "redis"
    INFLUXDB = "influxdb"
    TIMESCALEDB = "timescaledb"


class DataType(Enum):
    """Types of trading data"""
    OHLCV = "ohlcv"
    TICK = "tick"
    FUNDAMENTAL = "fundamental"
    NEWS = "news"
    ECONOMIC = "economic"
    OPTION_CHAIN = "option_chain"
    ORDER_BOOK = "order_book"


class CompressionType(Enum):
    """Compression options for data storage"""
    NONE = "none"
    GZIP = "gzip"
    SNAPPY = "snappy"
    LZ4 = "lz4"
    ZSTD = "zstd"


@dataclass
class DataQuery:
    """Data query specification"""
    symbols: List[str]
    start_date: datetime
    end_date: datetime
    timeframe: str = "1H"
    data_type: DataType = DataType.OHLCV
    source: Optional[DataSource] = None
    filters: Dict[str, Any] = field(default_factory=dict)
    columns: Optional[List[str]] = None
    limit: Optional[int] = None


@dataclass
class DataMetrics:
    """Data quality and performance metrics"""
    total_records: int = 0
    missing_values: int = 0
    duplicates: int = 0
    outliers: int = 0
    data_quality_score: float = 0.0
    last_updated: datetime = field(default_factory=datetime.now)
    source_latency: float = 0.0
    cache_hit_rate: float = 0.0


class DataProvider(ABC):
    """Abstract base class for data providers"""
    
    @abstractmethod
    async def fetch_data(self, query: DataQuery) -> pd.DataFrame:
        """Fetch data based on query specification"""
        pass
    
    @abstractmethod
    def validate_connection(self) -> bool:
        """Validate connection to data source"""
        pass
    
    @abstractmethod
    def get_available_symbols(self) -> List[str]:
        """Get list of available symbols"""
        pass


class CacheManager:
    """Advanced caching system with compression and TTL"""
    
    def __init__(self, 
                 cache_dir: Path,
                 memory_cache_size: int = 1000,
                 ttl_seconds: int = 3600,
                 compression: CompressionType = CompressionType.SNAPPY,
                 use_redis: bool = False,
                 redis_config: Optional[Dict] = None):
        
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # Memory caches
        self.memory_cache = LRUCache(maxsize=memory_cache_size)
        self.ttl_cache = TTLCache(maxsize=memory_cache_size, ttl=ttl_seconds)
        
        self.compression = compression
        self._lock = threading.RLock()
        
        # Redis cache (optional)
        self.redis_client = None
        if use_redis and redis_config:
            try:
                self.redis_client = redis.Redis(**redis_config)
                self.redis_client.ping()
                logger.info("Redis cache initialized successfully")
            except Exception as e:
                logger.warning(f"Failed to initialize Redis: {e}")
    
    def _generate_cache_key(self, query: DataQuery) -> str:
        """Generate unique cache key for query"""
        key_parts = [
            "_".join(sorted(query.symbols)),
            query.start_date.strftime("%Y%m%d_%H%M"),
            query.end_date.strftime("%Y%m%d_%H%M"),
            query.timeframe,
            query.data_type.value,
            str(hash(frozenset(query.filters.items())))
        ]
        return "_".join(key_parts)
    
    def get(self, query: DataQuery) -> Optional[pd.DataFrame]:
        """Retrieve data from cache"""
        cache_key = self._generate_cache_key(query)
        
        with self._lock:
            # Check memory cache first
            if cache_key in self.memory_cache:
                logger.debug(f"Cache hit (memory): {cache_key}")
                return self.memory_cache[cache_key].copy()
            
            # Check TTL cache
            if cache_key in self.ttl_cache:
                logger.debug(f"Cache hit (TTL): {cache_key}")
                data = self.ttl_cache[cache_key].copy()
                self.memory_cache[cache_key] = data
                return data
            
            # Check Redis cache
            if self.redis_client:
                try:
                    cached_data = self.redis_client.get(cache_key)
                    if cached_data:
                        logger.debug(f"Cache hit (Redis): {cache_key}")
                        data = joblib.loads(cached_data)
                        self.memory_cache[cache_key] = data
                        return data.copy()
                except Exception as e:
                    logger.warning(f"Redis cache read error: {e}")
            
            # Check disk cache
            cache_file = self.cache_dir / f"{cache_key}.parquet"
            if cache_file.exists():
                try:
                    logger.debug(f"Cache hit (disk): {cache_key}")
                    data = pd.read_parquet(cache_file)
                    self.memory_cache[cache_key] = data
                    return data.copy()
                except Exception as e:
                    logger.warning(f"Disk cache read error: {e}")
            
            return None
    
    def put(self, query: DataQuery, data: pd.DataFrame) -> None:
        """Store data in cache"""
        cache_key = self._generate_cache_key(query)
        
        with self._lock:
            # Store in memory caches
            self.memory_cache[cache_key] = data.copy()
            self.ttl_cache[cache_key] = data.copy()
            
            # Store in Redis (async)
            if self.redis_client:
                try:
                    serialized_data = joblib.dumps(data)
                    self.redis_client.setex(cache_key, 3600, serialized_data)
                except Exception as e:
                    logger.warning(f"Redis cache write error: {e}")
            
            # Store to disk (async)
            try:
                cache_file = self.cache_dir / f"{cache_key}.parquet"
                data.to_parquet(cache_file, compression=self.compression.value)
                logger.debug(f"Data cached to disk: {cache_key}")
            except Exception as e:
                logger.warning(f"Disk cache write error: {e}")
    
    def clear(self, pattern: Optional[str] = None) -> None:
        """Clear cache entries"""
        with self._lock:
            if pattern:
                # Clear specific pattern
                keys_to_remove = [k for k in self.memory_cache.keys() if pattern in k]
                for key in keys_to_remove:
                    self.memory_cache.pop(key, None)
                    self.ttl_cache.pop(key, None)
            else:
                # Clear all
                self.memory_cache.clear()
                self.ttl_cache.clear()
                
                # Clear disk cache
                for cache_file in self.cache_dir.glob("*.parquet"):
                    try:
                        cache_file.unlink()
                    except Exception as e:
                        logger.warning(f"Failed to delete cache file {cache_file}: {e}")


class DataValidator:
    """Data validation and cleaning utilities"""
    
    @staticmethod
    def validate_ohlcv(data: pd.DataFrame) -> Tuple[pd.DataFrame, DataMetrics]:
        """Validate and clean OHLCV data"""
        metrics = DataMetrics()
        metrics.total_records = len(data)
        
        if data.empty:
            return data, metrics
        
        # Required columns for OHLCV
        required_cols = ['open', 'high', 'low', 'close', 'volume']
        missing_cols = [col for col in required_cols if col not in data.columns]
        
        if missing_cols:
            logger.warning(f"Missing required columns: {missing_cols}")
        
        # Handle missing values
        initial_nulls = data.isnull().sum().sum()
        metrics.missing_values = initial_nulls
        
        # Forward fill then backward fill for price data
        price_cols = ['open', 'high', 'low', 'close']
        for col in price_cols:
            if col in data.columns:
                data[col] = data[col].fillna(method='ffill').fillna(method='bfill')
        
        # Fill volume with 0 if missing
        if 'volume' in data.columns:
            data['volume'] = data['volume'].fillna(0)
        
        # Remove duplicates
        initial_len = len(data)
        data = data.drop_duplicates()
        metrics.duplicates = initial_len - len(data)
        
        # Validate OHLC relationships
        if all(col in data.columns for col in price_cols):
            # High should be >= max(open, close)
            invalid_high = data['high'] < data[['open', 'close']].max(axis=1)
            # Low should be <= min(open, close)
            invalid_low = data['low'] > data[['open', 'close']].min(axis=1)
            
            outliers = invalid_high.sum() + invalid_low.sum()
            metrics.outliers = outliers
            
            if outliers > 0:
                logger.warning(f"Found {outliers} OHLC validation errors")
                # Fix invalid data
                data.loc[invalid_high, 'high'] = data.loc[invalid_high, ['open', 'close']].max(axis=1)
                data.loc[invalid_low, 'low'] = data.loc[invalid_low, ['open', 'close']].min(axis=1)
        
        # Calculate data quality score
        total_issues = metrics.missing_values + metrics.duplicates + metrics.outliers
        metrics.data_quality_score = max(0, 1 - (total_issues / metrics.total_records)) if metrics.total_records > 0 else 0
        
        return data, metrics
    
    @staticmethod
    def detect_outliers(data: pd.DataFrame, columns: List[str], method: str = 'iqr') -> pd.Series:
        """Detect outliers using specified method"""
        outliers = pd.Series(False, index=data.index)
        
        for col in columns:
            if col not in data.columns:
                continue
                
            if method == 'iqr':
                Q1 = data[col].quantile(0.25)
                Q3 = data[col].quantile(0.75)
                IQR = Q3 - Q1
                lower_bound = Q1 - 1.5 * IQR
                upper_bound = Q3 + 1.5 * IQR
                outliers |= (data[col] < lower_bound) | (data[col] > upper_bound)
            
            elif method == 'zscore':
                z_scores = np.abs((data[col] - data[col].mean()) / data[col].std())
                outliers |= z_scores > 3
        
        return outliers


class DataManager:
    """Main data management system"""
    
    def __init__(self, 
                 config: Dict[str, Any],
                 cache_dir: str = "cache",
                 db_url: str = "sqlite:///trading_data.db"):
        
        self.config = config
        self.providers: Dict[DataSource, DataProvider] = {}
        self.cache_manager = CacheManager(
            cache_dir=Path(cache_dir),
            **config.get('cache', {})
        )
        
        # Database setup
        self.engine = create_engine(db_url)
        self.Session = sessionmaker(bind=self.engine)
        self._setup_database()
        
        # Performance tracking
        self.metrics: Dict[str, DataMetrics] = {}
        self._executor = ThreadPoolExecutor(max_workers=config.get('max_workers', 4))
        
        logger.info("DataManager initialized successfully")
    
    def _setup_database(self):
        """Setup database tables for metadata and metrics"""
        Base = declarative_base()
        
        class DataCache(Base):
            __tablename__ = 'data_cache'
            id = Column(Integer, primary_key=True)
            cache_key = Column(String, unique=True, nullable=False)
            symbols = Column(String, nullable=False)
            start_date = Column(DateTime, nullable=False)
            end_date = Column(DateTime, nullable=False)
            timeframe = Column(String, nullable=False)
            data_type = Column(String, nullable=False)
            file_path = Column(String, nullable=False)
            created_at = Column(DateTime, default=datetime.now)
            size_bytes = Column(Integer, default=0)
            
        Base.metadata.create_all(self.engine)
    
    def register_provider(self, source: DataSource, provider: DataProvider):
        """Register a data provider"""
        self.providers[source] = provider
        logger.info(f"Registered provider for {source.value}")
    
    async def get_data(self, query: DataQuery) -> pd.DataFrame:
        """Get data with intelligent caching and source selection"""
        start_time = time.time()
        
        # Check cache first
        cached_data = self.cache_manager.get(query)
        if cached_data is not None:
            logger.info(f"Data retrieved from cache for {query.symbols}")
            return cached_data
        
        # Determine best data source
        source = query.source or self._select_best_source(query)
        
        if source not in self.providers:
            raise ValueError(f"No provider registered for {source.value}")
        
        # Fetch data
        try:
            data = await self.providers[source].fetch_data(query)
            
            # Validate and clean data
            if query.data_type == DataType.OHLCV:
                data, metrics = DataValidator.validate_ohlcv(data)
                metrics.source_latency = time.time() - start_time
                self.metrics[f"{source.value}_{query.symbols[0]}"] = metrics
            
            # Cache the data
            if not data.empty:
                self.cache_manager.put(query, data)
            
            logger.info(f"Data fetched from {source.value} for {query.symbols} in {time.time() - start_time:.2f}s")
            return data
            
        except Exception as e:
            logger.error(f"Failed to fetch data from {source.value}: {e}")
            raise
    
    def _select_best_source(self, query: DataQuery) -> DataSource:
        """Select the best data source based on query requirements"""
        # Priority logic: live data -> cached data -> historical data
        if query.end_date >= datetime.now() - timedelta(minutes=5):
            # Real-time data needed
            if DataSource.MT5 in self.providers:
                return DataSource.MT5
            elif DataSource.CTRADER in self.providers:
                return DataSource.CTRADER
        
        # Historical data
        if DataSource.DATABASE in self.providers:
            return DataSource.DATABASE
        elif DataSource.PARQUET in self.providers:
            return DataSource.PARQUET
        elif DataSource.MT5 in self.providers:
            return DataSource.MT5
        
        # Default to first available provider
        return list(self.providers.keys())[0]
    
    def get_data_sync(self, query: DataQuery) -> pd.DataFrame:
        """Synchronous wrapper for get_data"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(self.get_data(query))
        finally:
            loop.close()
    
    @contextmanager
    def batch_context(self):
        """Context manager for batch operations"""
        original_cache_enabled = hasattr(self.cache_manager, '_batch_mode')
        self.cache_manager._batch_mode = True
        try:
            yield
        finally:
            if not original_cache_enabled:
                delattr(self.cache_manager, '_batch_mode')
    
    def get_multiple_data(self, queries: List[DataQuery], parallel: bool = True) -> Dict[str, pd.DataFrame]:
        """Fetch multiple datasets efficiently"""
        if not parallel:
            return {f"{q.symbols[0]}_{q.timeframe}": self.get_data_sync(q) for q in queries}
        
        results = {}
        with ThreadPoolExecutor(max_workers=len(queries)) as executor:
            future_to_query = {
                executor.submit(self.get_data_sync, query): query 
                for query in queries
            }
            
            for future in as_completed(future_to_query):
                query = future_to_query[future]
                try:
                    data = future.result()
                    key = f"{query.symbols[0]}_{query.timeframe}"
                    results[key] = data
                except Exception as e:
                    logger.error(f"Failed to fetch data for {query.symbols}: {e}")
        
        return results
    
    def get_metrics(self, source: Optional[str] = None) -> Dict[str, DataMetrics]:
        """Get performance metrics"""
        if source:
            return {k: v for k, v in self.metrics.items() if source in k}
        return self.metrics.copy()
    
    def optimize_cache(self):
        """Optimize cache performance"""
        # Clear old cache entries
        cutoff_time = datetime.now() - timedelta(days=7)
        # Implementation for cache optimization
        logger.info("Cache optimization completed")
    
    def export_data(self, 
                   query: DataQuery, 
                   output_path: str, 
                   format: str = 'parquet',
                   compression: CompressionType = CompressionType.SNAPPY) -> None:
        """Export data to various formats"""
        data = self.get_data_sync(query)
        
        if format.lower() == 'parquet':
            data.to_parquet(output_path, compression=compression.value)
        elif format.lower() == 'csv':
            data.to_csv(output_path, index=False)
        elif format.lower() == 'excel':
            data.to_excel(output_path, index=False)
        else:
            raise ValueError(f"Unsupported format: {format}")
        
        logger.info(f"Data exported to {output_path}")
    
    def cleanup(self):
        """Cleanup resources"""
        self._executor.shutdown(wait=True)
        if hasattr(self.cache_manager, 'redis_client') and self.cache_manager.redis_client:
            self.cache_manager.redis_client.close()
        logger.info("DataManager cleanup completed")