"""
Data factory for easy initialization and configuration of the data management system
"""

import logging
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Type
import os

from .data_manager import DataManager, DataSource, DataQuery
from .providers import (
    MT5DataProvider, 
    ParquetDataProvider, 
    CSVDataProvider, 
    DatabaseDataProvider,
    AlphaVantageProvider,
    InfluxDBDataProvider,
    TimescaleDBDataProvider
)
from .streaming import MultiStreamManager, StreamConfig, create_forex_stream, create_stock_stream
from .utils import DataUtils, TechnicalIndicators, DataCompression, DataProfiler

logger = logging.getLogger(__name__)


class DataFactory:
    """Factory class for creating and configuring data management components"""
    
    # Registry of available providers
    PROVIDER_REGISTRY = {
        DataSource.MT5: MT5DataProvider,
        DataSource.PARQUET: ParquetDataProvider,
        DataSource.CSV: CSVDataProvider,
        DataSource.DATABASE: DatabaseDataProvider,
        DataSource.API: AlphaVantageProvider,
        DataSource.INFLUXDB: InfluxDBDataProvider,
        DataSource.TIMESCALEDB: TimescaleDBDataProvider
    }
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize data factory
        
        Args:
            config_path: Path to configuration file
        """
        self.config = self._load_config(config_path)
        self.data_manager: Optional[DataManager] = None
        self.stream_manager: Optional[MultiStreamManager] = None
        
    def _load_config(self, config_path: Optional[str]) -> Dict[str, Any]:
        """Load configuration from file or use defaults"""
        default_config = {
            "data_sources": {
                "mt5": {
                    "enabled": True,
                    "login": None,
                    "password": None,
                    "server": None
                },
                "parquet": {
                    "enabled": True,
                    "data_dir": "data/parquet"
                },
                "csv": {
                    "enabled": True,
                    "data_dir": "data/csv",
                    "date_column": "timestamp",
                    "date_format": "%Y-%m-%d %H:%M:%S"
                },
                "database": {
                    "enabled": False,
                    "connection_string": "sqlite:///trading_data.db",
                    "table_name": "ohlcv_data"
                },
                "alpha_vantage": {
                    "enabled": False,
                    "api_key": None
                },
                "influxdb": {
                    "enabled": False,
                    "url": "http://localhost:8086",
                    "token": None,
                    "org": "trading",
                    "bucket": "market_data",
                    "timeout": 30000
                },
                "timescaledb": {
                    "enabled": False,
                    "connection_string": "postgresql://user:password@localhost:5432/trading_db",
                    "table_name": "ohlcv_data",
                    "time_column": "timestamp"
                }
            },
            "cache": {
                "memory_cache_size": 1000,
                "ttl_seconds": 3600,
                "compression": "snappy",
                "use_redis": False,
                "redis_config": {
                    "host": "localhost",
                    "port": 6379,
                    "db": 0
                }
            },
            "database": {
                "url": "sqlite:///trading_data.db"
            },
            "performance": {
                "max_workers": 4,
                "chunk_size": 10000,
                "parallel_processing": True
            },
            "streaming": {
                "buffer_size": 10000,
                "update_interval": 0.1,
                "auto_reconnect": True,
                "batch_size": 100
            }
        }
        
        if config_path and Path(config_path).exists():
            try:
                with open(config_path, 'r') as f:
                    file_config = json.load(f)
                # Merge configurations
                self._deep_merge(default_config, file_config)
                logger.info(f"Configuration loaded from {config_path}")
            except Exception as e:
                logger.warning(f"Failed to load config from {config_path}: {e}")
        
        return default_config
    
    def _deep_merge(self, base: Dict, override: Dict) -> None:
        """Deep merge two dictionaries"""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value
    
    def create_data_manager(self, 
                           cache_dir: str = "cache",
                           auto_register_providers: bool = True) -> DataManager:
        """
        Create and configure data manager
        
        Args:
            cache_dir: Cache directory path
            auto_register_providers: Automatically register available providers
        """
        if self.data_manager is None:
            db_url = self.config.get("database", {}).get("url", "sqlite:///trading_data.db")
            
            self.data_manager = DataManager(
                config=self.config,
                cache_dir=cache_dir,
                db_url=db_url
            )
            
            if auto_register_providers:
                self._register_providers()
            
            logger.info("Data manager created successfully")
        
        return self.data_manager
    
    def _register_providers(self) -> None:
        """Register available data providers"""
        if not self.data_manager:
            raise RuntimeError("Data manager not created yet")
        
        data_sources_config = self.config.get("data_sources", {})
        
        # MT5 Provider
        if data_sources_config.get("mt5", {}).get("enabled", False):
            try:
                mt5_provider = MT5DataProvider(data_sources_config["mt5"])
                if mt5_provider.validate_connection():
                    self.data_manager.register_provider(DataSource.MT5, mt5_provider)
                    logger.info("MT5 provider registered")
                else:
                    logger.warning("MT5 connection validation failed")
            except Exception as e:
                logger.error(f"Failed to register MT5 provider: {e}")
        
        # Parquet Provider
        if data_sources_config.get("parquet", {}).get("enabled", True):
            try:
                parquet_provider = ParquetDataProvider(data_sources_config["parquet"])
                self.data_manager.register_provider(DataSource.PARQUET, parquet_provider)
                logger.info("Parquet provider registered")
            except Exception as e:
                logger.error(f"Failed to register Parquet provider: {e}")
        
        # CSV Provider
        if data_sources_config.get("csv", {}).get("enabled", True):
            try:
                csv_provider = CSVDataProvider(data_sources_config["csv"])
                self.data_manager.register_provider(DataSource.CSV, csv_provider)
                logger.info("CSV provider registered")
            except Exception as e:
                logger.error(f"Failed to register CSV provider: {e}")
        
        # Database Provider
        if data_sources_config.get("database", {}).get("enabled", False):
            try:
                db_provider = DatabaseDataProvider(data_sources_config["database"])
                if db_provider.validate_connection():
                    self.data_manager.register_provider(DataSource.DATABASE, db_provider)
                    logger.info("Database provider registered")
                else:
                    logger.warning("Database connection validation failed")
            except Exception as e:
                logger.error(f"Failed to register Database provider: {e}")
        
        # Alpha Vantage Provider
        if data_sources_config.get("alpha_vantage", {}).get("enabled", False):
            api_key = data_sources_config["alpha_vantage"].get("api_key")
            if api_key:
                try:
                    av_provider = AlphaVantageProvider(data_sources_config["alpha_vantage"])
                    self.data_manager.register_provider(DataSource.API, av_provider)
                    logger.info("Alpha Vantage provider registered")
                except Exception as e:
                    logger.error(f"Failed to register Alpha Vantage provider: {e}")
            else:
                logger.warning("Alpha Vantage API key not provided")
        
        # InfluxDB Provider
        if data_sources_config.get("influxdb", {}).get("enabled", False):
            token = data_sources_config["influxdb"].get("token")
            if token:
                try:
                    influxdb_provider = InfluxDBDataProvider(data_sources_config["influxdb"])
                    if influxdb_provider.validate_connection():
                        self.data_manager.register_provider(DataSource.INFLUXDB, influxdb_provider)
                        logger.info("InfluxDB provider registered")
                    else:
                        logger.warning("InfluxDB connection validation failed")
                except Exception as e:
                    logger.error(f"Failed to register InfluxDB provider: {e}")
            else:
                logger.warning("InfluxDB token not provided")
        
        # TimescaleDB Provider
        if data_sources_config.get("timescaledb", {}).get("enabled", False):
            connection_string = data_sources_config["timescaledb"].get("connection_string")
            if connection_string:
                try:
                    timescaledb_provider = TimescaleDBDataProvider(data_sources_config["timescaledb"])
                    if timescaledb_provider.validate_connection():
                        self.data_manager.register_provider(DataSource.TIMESCALEDB, timescaledb_provider)
                        logger.info("TimescaleDB provider registered")
                    else:
                        logger.warning("TimescaleDB connection validation failed")
                except Exception as e:
                    logger.error(f"Failed to register TimescaleDB provider: {e}")
            else:
                logger.warning("TimescaleDB connection string not provided")
    
    def create_stream_manager(self) -> MultiStreamManager:
        """Create stream manager for live data"""
        if self.stream_manager is None:
            self.stream_manager = MultiStreamManager()
            logger.info("Stream manager created")
        
        return self.stream_manager
    
    def create_quick_setup(self, 
                          symbols: List[str],
                          data_dir: str = "data",
                          stream_type: str = "forex") -> Dict[str, Any]:
        """
        Quick setup for common use cases
        
        Args:
            symbols: List of symbols to track
            data_dir: Data directory
            stream_type: Type of stream ('forex' or 'stock')
        
        Returns:
            Dictionary with created components
        """
        # Create data directory if it doesn't exist
        Path(data_dir).mkdir(parents=True, exist_ok=True)
        
        # Update config with data directory
        self.config["data_sources"]["parquet"]["data_dir"] = data_dir
        self.config["data_sources"]["csv"]["data_dir"] = data_dir
        
        # Create data manager
        data_manager = self.create_data_manager()
        
        # Create stream manager
        stream_manager = self.create_stream_manager()
        
        # Create appropriate stream
        if stream_type.lower() == "forex":
            stream = create_forex_stream(symbols)
        else:
            stream = create_stock_stream(symbols)
        
        stream_manager.create_stream("main", stream.config)
        
        logger.info(f"Quick setup completed for {len(symbols)} symbols")
        
        return {
            "data_manager": data_manager,
            "stream_manager": stream_manager,
            "symbols": symbols,
            "data_dir": data_dir
        }
    
    def get_data_simple(self, 
                       symbols: List[str],
                       start_date: str,
                       end_date: str,
                       timeframe: str = "1H") -> Dict[str, Any]:
        """
        Simple interface for getting data
        
        Args:
            symbols: List of symbols
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)
            timeframe: Timeframe
        
        Returns:
            Dictionary with data and metadata
        """
        from datetime import datetime
        
        if not self.data_manager:
            self.create_data_manager()
        
        # Parse dates
        start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        
        # Create query
        query = DataQuery(
            symbols=symbols,
            start_date=start_dt,
            end_date=end_dt,
            timeframe=timeframe
        )
        
        # Get data
        data = self.data_manager.get_data_sync(query)
        
        # Calculate basic stats
        profile = DataProfiler.profile_dataframe(data) if not data.empty else {}
        
        return {
            "data": data,
            "symbols": symbols,
            "timeframe": timeframe,
            "date_range": (start_date, end_date),
            "profile": profile,
            "quality_score": DataProfiler.assess_data_quality(data)
        }
    
    def create_analysis_suite(self, data: Any) -> Dict[str, Any]:
        """
        Create analysis suite with technical indicators
        
        Args:
            data: Price data (DataFrame or Series)
        
        Returns:
            Dictionary with calculated indicators
        """
        import pandas as pd
        
        if isinstance(data, dict) and "data" in data:
            df = data["data"]
        elif isinstance(data, pd.DataFrame):
            df = data
        else:
            raise ValueError("Invalid data format")
        
        if df.empty:
            return {"error": "No data provided"}
        
        # Determine price column
        price_col = "close" if "close" in df.columns else df.select_dtypes(include=["number"]).columns[0]
        prices = df[price_col] if len(df.columns) > 1 else df.iloc[:, 0]
        
        # Calculate indicators
        indicators = {}
        
        try:
            # Moving averages
            indicators["sma_20"] = TechnicalIndicators.sma(prices, 20)
            indicators["sma_50"] = TechnicalIndicators.sma(prices, 50)
            indicators["ema_12"] = TechnicalIndicators.ema(prices, 12)
            indicators["ema_26"] = TechnicalIndicators.ema(prices, 26)
            
            # Momentum indicators
            indicators["rsi"] = TechnicalIndicators.rsi(prices, 14)
            indicators["macd"] = TechnicalIndicators.macd(prices)
            
            # Volatility indicators
            indicators["bollinger"] = TechnicalIndicators.bollinger_bands(prices, 20, 2.0)
            
            # Returns and volatility
            indicators["returns"] = DataUtils.calculate_returns(df, price_col)
            indicators["volatility"] = DataUtils.calculate_volatility(indicators["returns"])
            
            logger.info("Analysis suite created successfully")
            
        except Exception as e:
            logger.error(f"Failed to create analysis suite: {e}")
            indicators["error"] = str(e)
        
        return indicators
    
    def export_config(self, filepath: str) -> None:
        """Export current configuration to file"""
        try:
            with open(filepath, 'w') as f:
                json.dump(self.config, f, indent=2, default=str)
            logger.info(f"Configuration exported to {filepath}")
        except Exception as e:
            logger.error(f"Failed to export configuration: {e}")
    
    def get_system_status(self) -> Dict[str, Any]:
        """Get overall system status"""
        status = {
            "data_manager": None,
            "stream_manager": None,
            "providers": [],
            "streams": {},
            "config_loaded": bool(self.config)
        }
        
        if self.data_manager:
            status["data_manager"] = {
                "initialized": True,
                "providers": list(self.data_manager.providers.keys()),
                "metrics": self.data_manager.get_metrics()
            }
        
        if self.stream_manager:
            status["stream_manager"] = {
                "initialized": True,
                "streams": self.stream_manager.get_all_status()
            }
        
        return status


# Convenience functions for quick access
def quick_data_setup(symbols: List[str], 
                    data_dir: str = "data",
                    config_path: Optional[str] = None) -> DataFactory:
    """Quick setup for data management"""
    factory = DataFactory(config_path)
    factory.create_quick_setup(symbols, data_dir)
    return factory


def get_data_fast(symbols: List[str],
                 start_date: str,
                 end_date: str,
                 timeframe: str = "1H",
                 config_path: Optional[str] = None) -> Dict[str, Any]:
    """Fast data retrieval"""
    factory = DataFactory(config_path)
    return factory.get_data_simple(symbols, start_date, end_date, timeframe)


def create_live_stream(symbols: List[str],
                      stream_type: str = "forex",
                      config_path: Optional[str] = None) -> MultiStreamManager:
    """Create live data stream"""
    factory = DataFactory(config_path)
    setup = factory.create_quick_setup(symbols, stream_type=stream_type)
    return setup["stream_manager"]


# Example usage
def example_usage():
    """Example of how to use the data management system"""
    
    # Quick setup
    symbols = ["EURUSD", "GBPUSD", "USDJPY"]
    factory = quick_data_setup(symbols)
    
    # Get historical data
    data = get_data_fast(symbols, "2024-01-01", "2024-12-31", "1H")
    
    # Create analysis
    analysis = factory.create_analysis_suite(data)
    
    # Create live stream
    stream_manager = create_live_stream(symbols)
    
    return {
        "factory": factory,
        "data": data,
        "analysis": analysis,
        "stream_manager": stream_manager
    }