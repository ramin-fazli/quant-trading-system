# Comprehensive Data Management System

A highly efficient, scalable, and optimal data management system for quantitative trading projects. This system provides unified access to multiple data sources, intelligent caching, real-time streaming, and advanced analytics capabilities.

## Features

### 🚀 **Core Capabilities**
- **Multi-Source Data Integration**: MT5, cTrader, CSV, Parquet, Database, APIs
- **Intelligent Caching**: Memory, disk, and Redis caching with compression
- **Real-Time Streaming**: Live data feeds with buffering and aggregation
- **Data Validation**: Automatic cleaning, outlier detection, and quality scoring
- **Performance Optimization**: Vectorized operations, parallel processing, Numba acceleration

### 📊 **Data Sources Supported**
- **MetaTrader 5** (MT5) - Live and historical forex/CFD data
- **cTrader** - Alternative trading platform integration
- **CSV Files** - Flexible CSV data import with auto-detection
- **Parquet Files** - High-performance columnar storage
- **Databases** - SQLite, PostgreSQL, MySQL support
- **InfluxDB** - Time series database optimized for financial data
- **TimescaleDB** - PostgreSQL extension for time series data
- **APIs** - Alpha Vantage, custom REST APIs

### ⚡ **Performance Features**
- **Numba-accelerated** technical indicators
- **Parallel processing** for large datasets
- **Memory optimization** with data compression
- **Vectorized operations** for maximum speed
- **Intelligent caching** to minimize data fetching

## Quick Start

### 1. Basic Usage

```python
from data import quick_data_setup, get_data_fast

# Quick setup for forex symbols
symbols = ["EURUSD", "GBPUSD", "USDJPY"]
factory = quick_data_setup(symbols, data_dir="my_data")

# Get historical data
data = get_data_fast(
    symbols=["EURUSD"], 
    start_date="2024-01-01", 
    end_date="2024-12-31",
    timeframe="1H"
)

print(f"Retrieved {len(data['data'])} data points")
print(f"Data quality score: {data['quality_score']:.2f}")
```

### 2. Advanced Configuration

```python
from data import DataFactory, DataQuery, DataType
from datetime import datetime, timedelta

# Create factory with custom config
factory = DataFactory(config_path="config.json")
data_manager = factory.create_data_manager()

# Custom query
query = DataQuery(
    symbols=["EURUSD", "GBPUSD"],
    start_date=datetime.now() - timedelta(days=30),
    end_date=datetime.now(),
    timeframe="4H",
    data_type=DataType.OHLCV
)

# Get data
data = data_manager.get_data_sync(query)
```

### 3. Real-Time Streaming

```python
from data import create_live_stream

# Create live data stream
symbols = ["EURUSD", "GBPUSD"]
stream_manager = create_live_stream(symbols, stream_type="forex")

# Get stream and start
main_stream = stream_manager.get_stream("main")

# Subscribe to events
def on_tick(tick):
    print(f"New tick: {tick.symbol} @ {tick.last:.5f}")

main_stream.subscribe("tick", on_tick)
main_stream.start()

# Get latest data
latest_data = main_stream.get_latest_data("EURUSD", "1T")
```

### 4. Technical Analysis

```python
from data import TechnicalIndicators, DataUtils

# Calculate indicators
sma_20 = TechnicalIndicators.sma(prices, 20)
ema_12 = TechnicalIndicators.ema(prices, 12)
rsi = TechnicalIndicators.rsi(prices, 14)
bollinger = TechnicalIndicators.bollinger_bands(prices)
macd = TechnicalIndicators.macd(prices)

# Calculate returns and volatility
returns = DataUtils.calculate_returns(data, 'close')
volatility = DataUtils.calculate_volatility(returns)
```

### 5. InfluxDB Integration

```python
from data import DataFactory, DataQuery, DataSource

# Configure InfluxDB
config = {
    "data_sources": {
        "influxdb": {
            "enabled": True,
            "url": "http://localhost:8086",
            "token": "your-token",
            "org": "trading",
            "bucket": "market_data"
        }
    }
}

factory = DataFactory()
factory.config = config
data_manager = factory.create_data_manager()

# Query from InfluxDB
query = DataQuery(
    symbols=["EURUSD"],
    start_date=start,
    end_date=end,
    timeframe="1H",
    source=DataSource.INFLUXDB
)

data = data_manager.get_data_sync(query)
```

### 6. TimescaleDB Integration

```python
from data import DataFactory, DataSource

# Configure TimescaleDB
config = {
    "data_sources": {
        "timescaledb": {
            "enabled": True,
            "connection_string": "postgresql://user:pass@localhost/trading_db",
            "table_name": "ohlcv_data"
        }
    }
}

# Use TimescaleDB for time series optimization
query = DataQuery(
    symbols=["EURUSD"],
    start_date=start,
    end_date=end,
    source=DataSource.TIMESCALEDB
)

data = data_manager.get_data_sync(query)
```

## Configuration

### Configuration File Structure

The system uses a JSON configuration file for setup. Copy `config_template.json` and customize:

```json
{
  "data_sources": {
    "mt5": {
      "enabled": true,
      "login": null,
      "password": null,
      "server": null
    },
    "parquet": {
      "enabled": true,
      "data_dir": "data/parquet"
    }
  },
  "cache": {
    "memory_cache_size": 1000,
    "ttl_seconds": 3600,
    "compression": "snappy"
  }
}
```

### Environment Variables

You can also use environment variables:

```bash
export MT5_LOGIN=your_login
export MT5_PASSWORD=your_password
export MT5_SERVER=your_server
export ALPHA_VANTAGE_API_KEY=your_api_key
```

## Architecture

### Core Components

1. **DataManager**: Central orchestrator for all data operations
2. **DataProviders**: Source-specific data fetchers (MT5, CSV, etc.)
3. **CacheManager**: Multi-level caching with compression
4. **StreamManager**: Real-time data streaming and buffering
5. **DataUtils**: Optimized utilities and technical indicators

### Data Flow

```
Data Sources → Providers → DataManager → Cache → Application
                    ↓
            Validation & Cleaning
                    ↓
               Performance Metrics
```

## Advanced Features

### 1. Data Validation and Quality

```python
from data import DataValidator, DataProfiler

# Validate OHLCV data
clean_data, metrics = DataValidator.validate_ohlcv(raw_data)

# Generate data profile
profile = DataProfiler.profile_dataframe(data)
quality_score = DataProfiler.assess_data_quality(data)
```

### 2. Parallel Processing

```python
from data import ParallelProcessor

# Process large datasets in parallel
chunks = ParallelProcessor.chunk_dataframe(large_df, chunk_size=10000)
results = ParallelProcessor.process_parallel(chunks, my_function)
```

### 3. Data Compression

```python
from data import DataCompression

# Optimize memory usage
optimized_df = DataCompression.compress_dataframe(df)

# Save with compression
DataCompression.save_compressed(df, "data.parquet", compression="snappy")
```

### 4. Batch Operations

```python
# Efficient batch processing
with data_manager.batch_context():
    queries = [create_query(symbol) for symbol in symbols]
    results = data_manager.get_multiple_data(queries, parallel=True)
```

## Performance Benchmarks

### Technical Indicators (10,000 data points)
- **SMA(20)**: ~0.001 seconds (Numba-accelerated)
- **EMA(20)**: ~0.001 seconds
- **RSI(14)**: ~0.002 seconds
- **MACD**: ~0.003 seconds

### Data Operations
- **Data retrieval**: Sub-second for cached data
- **Compression**: 70-90% size reduction
- **Parallel processing**: 3-4x speedup on multi-core systems

## Integration Examples

### With Backtesting Systems

```python
# Integration with backtesting
def backtest_strategy(symbols, start_date, end_date):
    data_manager = DataFactory().create_data_manager()
    
    for symbol in symbols:
        query = DataQuery(symbols=[symbol], start_date=start_date, end_date=end_date)
        data = data_manager.get_data_sync(query)
        
        # Run backtest with data
        results = run_backtest(data)
    
    return results
```

### With Live Trading

```python
# Integration with live trading
def live_trading_setup(symbols):
    factory = DataFactory()
    
    # Historical data for strategy initialization
    data_manager = factory.create_data_manager()
    historical_data = get_historical_data(symbols)
    
    # Live data stream
    stream_manager = factory.create_stream_manager()
    live_stream = create_forex_stream(symbols)
    
    # Connect strategy
    live_stream.subscribe("tick", strategy.on_tick)
    live_stream.start()
```

## Error Handling

The system includes comprehensive error handling:

```python
try:
    data = data_manager.get_data_sync(query)
except ConnectionError:
    # Handle connection issues
    logger.error("Data source connection failed")
except ValidationError:
    # Handle data validation issues
    logger.error("Data validation failed")
except CacheError:
    # Handle cache issues
    logger.error("Cache operation failed")
```

## Monitoring and Metrics

### Performance Monitoring

```python
# Get system metrics
metrics = data_manager.get_metrics()
status = factory.get_system_status()

# Stream statistics
stream_stats = live_stream.get_statistics()
print(f"Ticks processed: {stream_stats['ticks_processed']}")
print(f"Cache hit rate: {stream_stats['cache_hit_rate']}")
```

### Logging Configuration

```python
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('data_manager.log'),
        logging.StreamHandler()
    ]
)
```

## Troubleshooting

### Common Issues

1. **MT5 Connection Failed**
   - Ensure MT5 terminal is running
   - Check credentials and server settings
   - Verify firewall settings

2. **High Memory Usage**
   - Reduce cache sizes in configuration
   - Use data compression
   - Implement data chunking for large datasets

3. **Slow Performance**
   - Enable parallel processing
   - Optimize cache settings
   - Use Numba-accelerated functions

4. **Data Quality Issues**
   - Enable data validation
   - Configure outlier detection
   - Check data source reliability

### Debug Mode

```python
# Enable debug mode
import logging
logging.getLogger('data').setLevel(logging.DEBUG)

# Detailed cache information
cache_manager.enable_debug_logging()
```

## Best Practices

### 1. Configuration Management
- Use configuration files for different environments
- Store sensitive data in environment variables
- Version control your configuration templates

### 2. Data Storage
- Use Parquet format for historical data
- Implement proper data partitioning
- Regular data cleanup and archiving

### 3. Performance Optimization
- Enable caching for frequently accessed data
- Use appropriate chunk sizes for parallel processing
- Monitor memory usage and optimize data types

### 4. Error Handling
- Implement retry logic for network operations
- Use circuit breakers for unreliable data sources
- Log all errors with sufficient context

## Contributing

To extend the system:

1. **Add New Data Provider**:
   ```python
   class MyDataProvider(DataProvider):
       async def fetch_data(self, query: DataQuery) -> pd.DataFrame:
           # Implementation
   ```

2. **Add Technical Indicator**:
   ```python
   @njit
   def my_indicator(prices: np.ndarray, window: int) -> np.ndarray:
       # Numba-accelerated implementation
   ```

3. **Extend Validation**:
   ```python
   class MyValidator(DataValidator):
       @staticmethod
       def validate_my_data(data: pd.DataFrame) -> Tuple[pd.DataFrame, DataMetrics]:
           # Custom validation logic
   ```

## License

This data management system is part of the quantitative trading project and follows the project's licensing terms.

## Support

For issues and questions:
1. Check the example files in `data/example_usage.py`
2. Review the configuration template
3. Enable debug logging for detailed diagnostics
4. Check system status with `factory.get_system_status()`