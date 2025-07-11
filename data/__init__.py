"""
Comprehensive Data Management System for Quantitative Trading

This module provides a unified, efficient, and scalable data management system
for backtesting, live trading, and analysis.

Main Components:
- DataManager: Core data management with caching and multiple sources
- DataFactory: Easy setup and configuration
- Streaming: Real-time data streaming and processing
- Utils: Optimized utilities and technical indicators
- Providers: Multiple data source implementations

Quick Start:
```python
from data import quick_data_setup, get_data_fast

# Quick setup
factory = quick_data_setup(["EURUSD", "GBPUSD"])

# Get data
data = get_data_fast(["EURUSD"], "2024-01-01", "2024-12-31")
```
"""

# Core components
from .data_manager import (
    DataManager,
    DataQuery,
    DataType,
    DataSource,
    DataMetrics,
    CompressionType,
    DataProvider,
    CacheManager,
    DataValidator
)

# Data providers
from .providers import (
    MT5DataProvider,
    ParquetDataProvider,
    CSVDataProvider,
    DatabaseDataProvider,
    AlphaVantageProvider,
    InfluxDBDataProvider,
    TimescaleDBDataProvider
)

# Streaming components
from .streaming import (
    LiveDataStream,
    MultiStreamManager,
    StreamConfig,
    Tick,
    StreamState,
    DataBuffer,
    StreamProcessor,
    create_forex_stream,
    create_stock_stream
)

# Utilities
from .utils import (
    DataUtils,
    TechnicalIndicators,
    DataCompression,
    DataProfiler,
    ParallelProcessor,
    fast_sma,
    fast_ema,
    fast_rsi
)

# Factory and convenience functions
from .factory import (
    DataFactory,
    quick_data_setup,
    get_data_fast,
    create_live_stream,
    example_usage
)

# Version info
__version__ = "1.0.0"
__author__ = "Trading System"

# Main exports for easy access
__all__ = [
    # Core classes
    "DataManager",
    "DataFactory",
    "DataQuery",
    "DataType",
    "DataSource",
    "DataMetrics",
    
    # Providers
    "MT5DataProvider",
    "ParquetDataProvider", 
    "CSVDataProvider",
    "DatabaseDataProvider",
    "AlphaVantageProvider",
    "InfluxDBDataProvider",
    "TimescaleDBDataProvider",
    
    # Streaming
    "LiveDataStream",
    "MultiStreamManager",
    "StreamConfig",
    "Tick",
    "create_forex_stream",
    "create_stock_stream",
    
    # Utilities
    "DataUtils",
    "TechnicalIndicators",
    "DataCompression",
    "DataProfiler",
    
    # Quick access functions
    "quick_data_setup",
    "get_data_fast",
    "create_live_stream",
    "example_usage"
]