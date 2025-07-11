"""
InfluxDB Integration Example

This script demonstrates how to use InfluxDB with the data management system.
It shows how to:
- Configure InfluxDB connection
- Write trading data to InfluxDB
- Query data from InfluxDB
- Use InfluxDB as a data source for trading strategies
"""

import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Import the data management system
try:
    from data import (
        DataFactory,
        DataQuery,
        DataSource,
        DataType,
        InfluxDBDataProvider
    )
except ImportError as e:
    logger.error(f"Failed to import data management system: {e}")
    exit(1)


def create_sample_data():
    """Create sample OHLCV data for demonstration"""
    logger.info("Creating sample OHLCV data...")
    
    # Generate sample data for multiple symbols
    symbols = ["EURUSD", "GBPUSD", "USDJPY"]
    timeframes = ["1H", "4H", "1D"]
    
    all_data = []
    
    for symbol in symbols:
        for timeframe in timeframes:
            # Generate realistic price data
            dates = pd.date_range(
                start=datetime.now() - timedelta(days=30),
                end=datetime.now(),
                freq="1H" if timeframe == "1H" else ("4H" if timeframe == "4H" else "1D")
            )
            
            # Starting price based on symbol
            if "EUR" in symbol:
                base_price = 1.1000
            elif "GBP" in symbol:
                base_price = 1.2500
            else:
                base_price = 110.00
            
            # Generate price movements
            price_changes = np.random.normal(0, 0.001, len(dates))
            prices = base_price + np.cumsum(price_changes)
            
            # Create OHLCV data
            data = pd.DataFrame({
                'symbol': symbol,
                'timeframe': timeframe,
                'open': prices + np.random.normal(0, 0.0005, len(prices)),
                'high': prices + np.abs(np.random.normal(0, 0.0010, len(prices))),
                'low': prices - np.abs(np.random.normal(0, 0.0010, len(prices))),
                'close': prices,
                'volume': np.random.randint(1000, 10000, len(prices))
            }, index=dates)
            
            # Ensure OHLC relationships are valid
            data['high'] = data[['open', 'close', 'high']].max(axis=1)
            data['low'] = data[['open', 'close', 'low']].min(axis=1)
            
            all_data.append(data)
    
    combined_data = pd.concat(all_data, ignore_index=False)
    logger.info(f"Created {len(combined_data)} sample data points")
    
    return combined_data


def setup_influxdb_config():
    """Create InfluxDB configuration"""
    config = {
        "data_sources": {
            "influxdb": {
                "enabled": True,
                "url": "http://localhost:8086",
                "token": "your-influxdb-token",  # Replace with your token
                "org": "trading",
                "bucket": "market_data",
                "timeout": 30000
            }
        },
        "cache": {
            "memory_cache_size": 500,
            "ttl_seconds": 1800
        }
    }
    
    return config


def test_influxdb_connection():
    """Test InfluxDB connection"""
    logger.info("Testing InfluxDB connection...")
    
    try:
        config = setup_influxdb_config()
        influxdb_config = config["data_sources"]["influxdb"]
        
        # Create provider directly for testing
        provider = InfluxDBDataProvider(influxdb_config)
        
        if provider.validate_connection():
            logger.info("✓ InfluxDB connection successful")
            
            # Test getting available symbols
            symbols = provider.get_available_symbols()
            logger.info(f"Available symbols: {symbols[:10]}...")  # Show first 10
            
            # Test getting available timeframes
            timeframes = provider.get_available_timeframes()
            logger.info(f"Available timeframes: {timeframes}")
            
            return True
        else:
            logger.error("✗ InfluxDB connection failed")
            return False
            
    except Exception as e:
        logger.error(f"InfluxDB connection test failed: {e}")
        return False


def write_sample_data_to_influxdb():
    """Write sample data to InfluxDB"""
    logger.info("Writing sample data to InfluxDB...")
    
    try:
        # Create sample data
        sample_data = create_sample_data()
        
        # Setup InfluxDB provider
        config = setup_influxdb_config()
        provider = InfluxDBDataProvider(config["data_sources"]["influxdb"])
        
        # Write data in chunks to avoid overwhelming InfluxDB
        chunk_size = 1000
        total_written = 0
        
        for i in range(0, len(sample_data), chunk_size):
            chunk = sample_data.iloc[i:i + chunk_size]
            
            if provider.write_data(chunk, measurement="ohlcv"):
                total_written += len(chunk)
                logger.info(f"Written {total_written}/{len(sample_data)} records...")
            else:
                logger.error(f"Failed to write chunk {i//chunk_size + 1}")
                break
        
        logger.info(f"Successfully wrote {total_written} records to InfluxDB")
        return True
        
    except Exception as e:
        logger.error(f"Failed to write sample data: {e}")
        return False


def query_data_from_influxdb():
    """Query data from InfluxDB using the data management system"""
    logger.info("Querying data from InfluxDB...")
    
    try:
        # Setup data factory with InfluxDB
        config = setup_influxdb_config()
        factory = DataFactory()
        factory.config = config
        
        # Create data manager
        data_manager = factory.create_data_manager()
        
        # Manually register InfluxDB provider for this example
        influxdb_provider = InfluxDBDataProvider(config["data_sources"]["influxdb"])
        data_manager.register_provider(DataSource.INFLUXDB, influxdb_provider)
        
        # Create query
        query = DataQuery(
            symbols=["EURUSD", "GBPUSD"],
            start_date=datetime.now() - timedelta(days=7),
            end_date=datetime.now(),
            timeframe="1H",
            data_type=DataType.OHLCV,
            source=DataSource.INFLUXDB  # Explicitly use InfluxDB
        )
        
        # Get data
        data = data_manager.get_data_sync(query)
        
        if not data.empty:
            logger.info(f"Retrieved {len(data)} records from InfluxDB")
            logger.info(f"Data shape: {data.shape}")
            logger.info(f"Columns: {data.columns.tolist()}")
            logger.info(f"Date range: {data.index.min()} to {data.index.max()}")
            
            # Show sample data
            logger.info("Sample data:")
            print(data.head())
            
            return data
        else:
            logger.warning("No data retrieved from InfluxDB")
            return pd.DataFrame()
            
    except Exception as e:
        logger.error(f"Failed to query data from InfluxDB: {e}")
        return pd.DataFrame()


def performance_comparison():
    """Compare performance between different data sources"""
    logger.info("Running performance comparison...")
    
    import time
    
    try:
        # Setup
        config = setup_influxdb_config()
        factory = DataFactory()
        factory.config = config
        data_manager = factory.create_data_manager()
        
        # Register InfluxDB provider
        influxdb_provider = InfluxDBDataProvider(config["data_sources"]["influxdb"])
        data_manager.register_provider(DataSource.INFLUXDB, influxdb_provider)
        
        # Test query
        query = DataQuery(
            symbols=["EURUSD"],
            start_date=datetime.now() - timedelta(days=7),
            end_date=datetime.now(),
            timeframe="1H",
            data_type=DataType.OHLCV
        )
        
        # Test InfluxDB performance
        start_time = time.time()
        query.source = DataSource.INFLUXDB
        influx_data = data_manager.get_data_sync(query)
        influx_time = time.time() - start_time
        
        # Test cache performance (second query)
        start_time = time.time()
        cached_data = data_manager.get_data_sync(query)
        cache_time = time.time() - start_time
        
        logger.info("Performance Results:")
        logger.info(f"InfluxDB query time: {influx_time:.4f} seconds ({len(influx_data)} records)")
        logger.info(f"Cached query time: {cache_time:.4f} seconds ({len(cached_data)} records)")
        logger.info(f"Cache speedup: {influx_time / cache_time:.1f}x faster")
        
        return {
            "influx_time": influx_time,
            "cache_time": cache_time,
            "speedup": influx_time / cache_time
        }
        
    except Exception as e:
        logger.error(f"Performance comparison failed: {e}")
        return None


def advanced_influxdb_features():
    """Demonstrate advanced InfluxDB features"""
    logger.info("Demonstrating advanced InfluxDB features...")
    
    try:
        config = setup_influxdb_config()
        provider = InfluxDBDataProvider(config["data_sources"]["influxdb"])
        
        # Test different data types
        logger.info("Testing different query types...")
        
        # OHLCV query
        ohlcv_query = DataQuery(
            symbols=["EURUSD"],
            start_date=datetime.now() - timedelta(days=3),
            end_date=datetime.now(),
            timeframe="4H",
            data_type=DataType.OHLCV
        )
        
        ohlcv_data = provider.fetch_data(ohlcv_query)
        logger.info(f"OHLCV data: {len(ohlcv_data)} records")
        
        # Limited query
        limited_query = DataQuery(
            symbols=["GBPUSD"],
            start_date=datetime.now() - timedelta(days=7),
            end_date=datetime.now(),
            timeframe="1H",
            limit=100
        )
        
        limited_data = provider.fetch_data(limited_query)
        logger.info(f"Limited data: {len(limited_data)} records (max 100)")
        
        # Multi-symbol query
        multi_query = DataQuery(
            symbols=["EURUSD", "GBPUSD", "USDJPY"],
            start_date=datetime.now() - timedelta(days=1),
            end_date=datetime.now(),
            timeframe="1H"
        )
        
        multi_data = provider.fetch_data(multi_query)
        logger.info(f"Multi-symbol data: {len(multi_data)} records")
        if not multi_data.empty and 'symbol' in multi_data.columns:
            symbol_counts = multi_data['symbol'].value_counts()
            logger.info(f"Records per symbol: {symbol_counts.to_dict()}")
        
        return True
        
    except Exception as e:
        logger.error(f"Advanced features test failed: {e}")
        return False


def main():
    """Main example function"""
    logger.info("Starting InfluxDB Integration Examples")
    logger.info("=" * 50)
    
    # Check if InfluxDB is available
    logger.info("Note: Make sure InfluxDB is running and properly configured")
    logger.info("Update the token and connection details in setup_influxdb_config()")
    
    try:
        results = {}
        
        # Test connection
        if test_influxdb_connection():
            results['connection'] = True
            
            # Write sample data
            if write_sample_data_to_influxdb():
                results['write'] = True
                
                # Query data
                data = query_data_from_influxdb()
                results['query'] = not data.empty
                
                # Performance test
                perf_results = performance_comparison()
                results['performance'] = perf_results
                
                # Advanced features
                results['advanced'] = advanced_influxdb_features()
            else:
                logger.warning("Skipping read tests due to write failure")
        else:
            logger.warning("Skipping all tests due to connection failure")
            logger.info("Please check:")
            logger.info("1. InfluxDB is running on localhost:8086")
            logger.info("2. Token is valid and has write/read permissions")
            logger.info("3. Organization and bucket exist")
        
        # Summary
        logger.info("=" * 50)
        logger.info("InfluxDB Integration Summary:")
        for test, result in results.items():
            status = "✓" if result else "✗"
            logger.info(f"{status} {test.capitalize()}")
        
        return results
        
    except Exception as e:
        logger.error(f"Main execution failed: {e}")
        return None


if __name__ == "__main__":
    # Run the InfluxDB integration examples
    results = main()
    
    if results and any(results.values()):
        print("\n🎉 InfluxDB integration is working!")
        print("\nNext steps:")
        print("1. Configure your InfluxDB connection details")
        print("2. Start using InfluxDB as a data source in your trading system")
        print("3. Take advantage of InfluxDB's time series optimizations")
    else:
        print("\n⚠️  InfluxDB integration needs configuration")
        print("\nSetup steps:")
        print("1. Install InfluxDB (docker run -p 8086:8086 influxdb:latest)")
        print("2. Create organization and bucket")
        print("3. Generate API token with read/write permissions")
        print("4. Update configuration in setup_influxdb_config()")