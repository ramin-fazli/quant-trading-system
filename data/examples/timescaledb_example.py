"""
TimescaleDB Integration Example

This script demonstrates how to use TimescaleDB with the data management system.
TimescaleDB is a PostgreSQL extension optimized for time series data.
"""

import logging
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

try:
    from data import (
        DataFactory,
        DataQuery,
        DataSource,
        DataType,
        TimescaleDBDataProvider
    )
except ImportError as e:
    logger.error(f"Failed to import data management system: {e}")
    exit(1)


def setup_timescaledb_config():
    """Create TimescaleDB configuration"""
    config = {
        "data_sources": {
            "timescaledb": {
                "enabled": True,
                "connection_string": "postgresql://trading_user:password@localhost:5432/trading_db",
                "table_name": "ohlcv_data",
                "time_column": "timestamp"
            }
        }
    }
    return config


def create_timescaledb_schema():
    """Create TimescaleDB schema and hypertable"""
    logger.info("Creating TimescaleDB schema...")
    
    try:
        import psycopg2
        from sqlalchemy import create_engine, text
        
        config = setup_timescaledb_config()
        connection_string = config["data_sources"]["timescaledb"]["connection_string"]
        
        engine = create_engine(connection_string)
        
        with engine.connect() as conn:
            # Create table
            create_table_sql = """
            CREATE TABLE IF NOT EXISTS ohlcv_data (
                timestamp TIMESTAMPTZ NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                open DOUBLE PRECISION,
                high DOUBLE PRECISION,
                low DOUBLE PRECISION,
                close DOUBLE PRECISION,
                volume BIGINT
            );
            """
            
            conn.execute(text(create_table_sql))
            
            # Create hypertable (TimescaleDB specific)
            hypertable_sql = """
            SELECT create_hypertable('ohlcv_data', 'timestamp', 
                                   if_not_exists => TRUE,
                                   chunk_time_interval => INTERVAL '1 day');
            """
            
            try:
                conn.execute(text(hypertable_sql))
                logger.info("✓ Hypertable created successfully")
            except Exception as e:
                if "already a hypertable" in str(e):
                    logger.info("✓ Hypertable already exists")
                else:
                    logger.warning(f"Hypertable creation warning: {e}")
            
            # Create indexes for better performance
            indexes = [
                "CREATE INDEX IF NOT EXISTS idx_symbol_time ON ohlcv_data (symbol, timestamp DESC);",
                "CREATE INDEX IF NOT EXISTS idx_timeframe ON ohlcv_data (timeframe);",
                "CREATE INDEX IF NOT EXISTS idx_symbol_timeframe_time ON ohlcv_data (symbol, timeframe, timestamp DESC);"
            ]
            
            for index_sql in indexes:
                conn.execute(text(index_sql))
            
            conn.commit()
            logger.info("✓ Schema and indexes created")
            
        return True
        
    except Exception as e:
        logger.error(f"Failed to create schema: {e}")
        return False


def test_timescaledb_connection():
    """Test TimescaleDB connection"""
    logger.info("Testing TimescaleDB connection...")
    
    try:
        config = setup_timescaledb_config()
        provider = TimescaleDBDataProvider(config["data_sources"]["timescaledb"])
        
        if provider.validate_connection():
            logger.info("✓ TimescaleDB connection successful")
            
            symbols = provider.get_available_symbols()
            logger.info(f"Available symbols: {symbols[:10]}...")
            
            return True
        else:
            logger.error("✗ TimescaleDB connection failed")
            return False
            
    except Exception as e:
        logger.error(f"TimescaleDB connection test failed: {e}")
        return False


def insert_sample_data():
    """Insert sample data into TimescaleDB"""
    logger.info("Inserting sample data into TimescaleDB...")
    
    try:
        from sqlalchemy import create_engine
        
        config = setup_timescaledb_config()
        connection_string = config["data_sources"]["timescaledb"]["connection_string"]
        engine = create_engine(connection_string)
        
        # Generate sample data
        symbols = ["EURUSD", "GBPUSD", "USDJPY"]
        timeframes = ["1H", "4H", "1D"]
        
        all_data = []
        
        for symbol in symbols:
            for timeframe in timeframes:
                dates = pd.date_range(
                    start=datetime.now() - timedelta(days=30),
                    end=datetime.now(),
                    freq="1H" if timeframe == "1H" else ("4H" if timeframe == "4H" else "1D")
                )
                
                base_price = 1.1000 if "EUR" in symbol else (1.2500 if "GBP" in symbol else 110.00)
                price_changes = np.random.normal(0, 0.001, len(dates))
                prices = base_price + np.cumsum(price_changes)
                
                data = pd.DataFrame({
                    'timestamp': dates,
                    'symbol': symbol,
                    'timeframe': timeframe,
                    'open': prices + np.random.normal(0, 0.0005, len(prices)),
                    'high': prices + np.abs(np.random.normal(0, 0.0010, len(prices))),
                    'low': prices - np.abs(np.random.normal(0, 0.0010, len(prices))),
                    'close': prices,
                    'volume': np.random.randint(1000, 10000, len(prices))
                })
                
                # Ensure OHLC relationships
                data['high'] = data[['open', 'close', 'high']].max(axis=1)
                data['low'] = data[['open', 'close', 'low']].min(axis=1)
                
                all_data.append(data)
        
        combined_data = pd.concat(all_data, ignore_index=True)
        
        # Insert data using pandas to_sql for efficiency
        combined_data.to_sql(
            'ohlcv_data',
            engine,
            if_exists='append',
            index=False,
            method='multi',
            chunksize=1000
        )
        
        logger.info(f"✓ Inserted {len(combined_data)} records into TimescaleDB")
        return True
        
    except Exception as e:
        logger.error(f"Failed to insert sample data: {e}")
        return False


def query_timescaledb_data():
    """Query data from TimescaleDB"""
    logger.info("Querying data from TimescaleDB...")
    
    try:
        config = setup_timescaledb_config()
        factory = DataFactory()
        factory.config = config
        
        data_manager = factory.create_data_manager()
        
        # Register TimescaleDB provider
        provider = TimescaleDBDataProvider(config["data_sources"]["timescaledb"])
        data_manager.register_provider(DataSource.TIMESCALEDB, provider)
        
        # Query data
        query = DataQuery(
            symbols=["EURUSD", "GBPUSD"],
            start_date=datetime.now() - timedelta(days=7),
            end_date=datetime.now(),
            timeframe="1H",
            source=DataSource.TIMESCALEDB
        )
        
        data = data_manager.get_data_sync(query)
        
        if not data.empty:
            logger.info(f"✓ Retrieved {len(data)} records from TimescaleDB")
            logger.info(f"Date range: {data.index.min()} to {data.index.max()}")
            print(data.head())
            return data
        else:
            logger.warning("No data retrieved")
            return pd.DataFrame()
            
    except Exception as e:
        logger.error(f"Failed to query TimescaleDB: {e}")
        return pd.DataFrame()


def timescaledb_performance_features():
    """Demonstrate TimescaleDB performance features"""
    logger.info("Testing TimescaleDB performance features...")
    
    try:
        from sqlalchemy import create_engine, text
        import time
        
        config = setup_timescaledb_config()
        connection_string = config["data_sources"]["timescaledb"]["connection_string"]
        engine = create_engine(connection_string)
        
        with engine.connect() as conn:
            # Time-bucketed aggregation (TimescaleDB specific)
            aggregation_sql = """
            SELECT 
                time_bucket('1 hour', timestamp) AS hour,
                symbol,
                avg(close) as avg_price,
                max(high) as max_price,
                min(low) as min_price,
                sum(volume) as total_volume
            FROM ohlcv_data 
            WHERE timestamp >= NOW() - INTERVAL '7 days'
            AND symbol = 'EURUSD'
            AND timeframe = '1H'
            GROUP BY hour, symbol
            ORDER BY hour DESC
            LIMIT 24;
            """
            
            start_time = time.time()
            result = conn.execute(text(aggregation_sql))
            rows = result.fetchall()
            query_time = time.time() - start_time
            
            logger.info(f"✓ Time-bucketed aggregation: {len(rows)} results in {query_time:.4f}s")
            
            # Continuous aggregates (advanced TimescaleDB feature)
            create_cagg_sql = """
            CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_hourly
            WITH (timescaledb.continuous) AS
            SELECT 
                time_bucket('1 hour', timestamp) AS hour,
                symbol,
                first(open, timestamp) as open,
                max(high) as high,
                min(low) as low,
                last(close, timestamp) as close,
                sum(volume) as volume
            FROM ohlcv_data
            GROUP BY hour, symbol
            WITH NO DATA;
            """
            
            try:
                conn.execute(text(create_cagg_sql))
                logger.info("✓ Continuous aggregate created")
            except Exception as e:
                if "already exists" in str(e):
                    logger.info("✓ Continuous aggregate already exists")
                else:
                    logger.warning(f"Continuous aggregate warning: {e}")
            
            # Compression policy (TimescaleDB 2.0+)
            compression_sql = """
            SELECT add_compression_policy('ohlcv_data', INTERVAL '7 days');
            """
            
            try:
                conn.execute(text(compression_sql))
                logger.info("✓ Compression policy added")
            except Exception as e:
                if "already exists" in str(e) or "policy already exists" in str(e):
                    logger.info("✓ Compression policy already exists")
                else:
                    logger.warning(f"Compression policy warning: {e}")
            
            conn.commit()
            
        return True
        
    except Exception as e:
        logger.error(f"Performance features test failed: {e}")
        return False


def main():
    """Main TimescaleDB example"""
    logger.info("Starting TimescaleDB Integration Examples")
    logger.info("=" * 50)
    
    logger.info("Note: Make sure PostgreSQL with TimescaleDB extension is running")
    logger.info("Update connection details in setup_timescaledb_config()")
    
    try:
        results = {}
        
        # Create schema
        if create_timescaledb_schema():
            results['schema'] = True
            
            # Test connection
            if test_timescaledb_connection():
                results['connection'] = True
                
                # Insert sample data
                if insert_sample_data():
                    results['insert'] = True
                    
                    # Query data
                    data = query_timescaledb_data()
                    results['query'] = not data.empty
                    
                    # Performance features
                    results['performance'] = timescaledb_performance_features()
        
        # Summary
        logger.info("=" * 50)
        logger.info("TimescaleDB Integration Summary:")
        for test, result in results.items():
            status = "✓" if result else "✗"
            logger.info(f"{status} {test.capitalize()}")
        
        return results
        
    except Exception as e:
        logger.error(f"Main execution failed: {e}")
        return None


if __name__ == "__main__":
    results = main()
    
    if results and any(results.values()):
        print("\n🎉 TimescaleDB integration is working!")
        print("\nBenefits of TimescaleDB:")
        print("- Automatic partitioning by time")
        print("- Built-in compression")
        print("- Continuous aggregates")
        print("- SQL compatibility")
    else:
        print("\n⚠️  TimescaleDB needs setup")
        print("\nSetup steps:")
        print("1. Install PostgreSQL")
        print("2. Install TimescaleDB extension")
        print("3. Create database and user")
        print("4. Update connection string")