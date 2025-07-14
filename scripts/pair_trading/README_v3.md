# Enhanced Pairs Trading System V3

## Overview

Enhanced Pairs Trading System V3 is a highly optimized, modular trading system that provides complete flexibility in selecting data providers and execution brokers independently. This version maintains all functionality from V2 while adding cTrader real-time trading capabilities.

## Key Features

### ✅ Independent Provider Selection
- **Data Provider**: Choose between `ctrader` or `mt5` for historical and real-time market data
- **Execution Broker**: Choose between `ctrader` or `mt5` for trade execution
- **Mix & Match**: Use any combination (e.g., cTrader data with MT5 execution)

### ✅ Real-Time Trading Capabilities
- **MT5 Real-Time Trading**: Complete implementation with MetaTrader5 API
- **cTrader Real-Time Trading**: Full implementation with cTrader Open API
- **Advanced Risk Management**: Portfolio-level and pair-level drawdown protection
- **Position Management**: Automated position sizing and balanced exposure

### ✅ Data Management
- **InfluxDB Integration**: Store and retrieve historical data, trade records, and backtest results
- **Intelligent Data Caching**: Automatic detection of missing data and gap-filling
- **Multi-Provider Support**: Seamless switching between data providers
- **Real-Time Data Streaming**: Live price feeds for strategy calculations
- **Smart Pre-fetching**: Optimized data collection before backtesting
- **Data Quality Validation**: Comprehensive data quality checks and reporting

### ✅ Backtesting & Analysis
- **VectorBT Integration**: High-performance vectorized backtesting
- **Enhanced Reporting**: Comprehensive Excel reports with charts and analytics
- **Performance Metrics**: Sharpe ratio, drawdown, win rate, and more

### ✅ Dashboard Integration
- **Real-Time Visualization**: Live trading dashboard with WebSocket updates
- **Portfolio Monitoring**: Real-time P&L, position tracking, and risk metrics
- **Multi-Provider Display**: Shows both data provider and execution broker status

## Installation

### Prerequisites
```bash
# Core dependencies
pip install pandas numpy scipy statsmodels vectorbt openpyxl xlsxwriter

# MT5 support
pip install MetaTrader5

# cTrader support  
pip install ctrader-open-api twisted

# InfluxDB support
pip install influxdb-client

# Dashboard support
pip install dash plotly websockets

# Environment management
pip install python-dotenv
```

### Environment Configuration
Create a `.env` file in the project root:

```env
# MT5 Configuration
MT5_LOGIN=your_mt5_login
MT5_PASSWORD=your_mt5_password
MT5_SERVER=your_mt5_server

# cTrader Configuration
CTRADER_CLIENT_ID=your_client_id
CTRADER_CLIENT_SECRET=your_client_secret
CTRADER_ACCESS_TOKEN=your_access_token
CTRADER_ACCOUNT_ID=your_account_id

# InfluxDB Configuration
INFLUXDB_URL=http://localhost:8086
INFLUXDB_TOKEN=your_influxdb_token
INFLUXDB_ORG=trading-org
INFLUXDB_BUCKET=trading-data
```

## Usage

### Command Line Interface

```bash
# Basic usage - CTrader data and execution
python enhanced_main_v3.py

# Specify providers explicitly
python enhanced_main_v3.py --data-provider ctrader --broker ctrader --mode backtest

# Mix providers - CTrader data with MT5 execution
python enhanced_main_v3.py --data-provider ctrader --broker mt5 --mode backtest

# MT5 data with CTrader execution
python enhanced_main_v3.py --data-provider mt5 --broker ctrader --mode backtest

# Live trading with CTrader
python enhanced_main_v3.py --data-provider ctrader --broker ctrader --mode live

# Live trading with MT5
python enhanced_main_v3.py --data-provider mt5 --broker mt5 --mode live
```

### Arguments

| Argument | Options | Default | Description |
|----------|---------|---------|-------------|
| `--data-provider` | `ctrader`, `mt5` | `ctrader` | Provider for historical and real-time data |
| `--broker` | `ctrader`, `mt5` | `ctrader` | Broker for trade execution |
| `--mode` | `backtest`, `live` | `backtest` | Execution mode |
| `--force-refresh` | flag | `false` | Force refresh all data ignoring cache |

## Intelligent Data Management

### Overview

V3 introduces intelligent data management that automatically:
1. **Checks existing data** in InfluxDB before fetching
2. **Detects data gaps** and fetches only missing portions
3. **Caches all data** locally for faster backtesting
4. **Validates data quality** and provides detailed reports

### Features

#### Smart Caching
```bash
# First run - fetches all data from provider
python enhanced_main_v3.py --data-provider ctrader --mode backtest

# Subsequent runs - uses cached data, fetches only gaps
python enhanced_main_v3.py --data-provider ctrader --mode backtest

# Force complete refresh
python enhanced_main_v3.py --data-provider ctrader --mode backtest --force-refresh
```

#### Data Coverage Analysis
```python
# Get data coverage report
coverage_report = system.intelligent_data_manager.get_data_coverage_report(
    symbols=['EURUSD', 'GBPUSD'],
    interval='M15',
    start_date=datetime.now() - timedelta(days=30),
    end_date=datetime.now()
)

for symbol, report in coverage_report.items():
    print(f"{symbol}: {report['coverage_percentage']:.1f}% coverage, {report['gaps_count']} gaps")
```

#### Gap Detection and Filling
- Automatically detects missing time periods in data
- Fetches only the missing portions from data provider
- Seamlessly combines cached and new data

#### Data Quality Validation
- Checks for NaN values, invalid prices
- Validates date range coverage
- Provides quality status: GOOD, WARNING, POOR, EMPTY

### Data Management Demo
```bash
# Run comprehensive data management demonstration
python data_management_demo.py
```

The demo script provides interactive tests for:
- Data coverage analysis
- Gap detection functionality
- Force refresh testing
- Sample backtest with intelligent caching

## Provider Combinations

### Supported Combinations

| Data Provider | Execution Broker | Use Case |
|---------------|------------------|----------|
| `ctrader` | `ctrader` | Pure cTrader environment |
| `mt5` | `mt5` | Pure MT5 environment |
| `ctrader` | `mt5` | cTrader data with MT5 execution |
| `mt5` | `ctrader` | MT5 data with cTrader execution |

### When to Use Each Combination

**cTrader Data + cTrader Execution**
- Best for cTrader-only environments
- Optimal latency and data consistency
- Full feature support

**MT5 Data + MT5 Execution**  
- Best for MT5-only environments
- Established and reliable
- Extensive broker support

**cTrader Data + MT5 Execution**
- Leverage cTrader's data quality with MT5's execution reliability
- Useful when MT5 broker has better execution but cTrader has better data

**MT5 Data + cTrader Execution**
- Use MT5's data infrastructure with cTrader's execution features
- Good for testing cTrader execution with familiar MT5 data

## Architecture

### Core Components

#### EnhancedTradingSystemV3
Main system orchestrator that manages all components and provider selection.

#### Data Managers
- **CTraderDataManager**: Historical and real-time data from cTrader
- **MT5DataManager**: Historical and real-time data from MetaTrader5
- **InfluxDBDataManager**: Historical data storage and retrieval

#### Real-Time Traders
- **CTraderRealTimeTrader**: Live trading with cTrader Open API
- **MT5RealTimeTrader**: Live trading with MetaTrader5 API
- **EnhancedRealTimeTrader**: Wrapper with InfluxDB and dashboard integration

#### Additional Components
- **InfluxDBManager**: Data storage and retrieval
- **VectorBTBacktester**: High-performance backtesting
- **Dashboard Integration**: Real-time visualization and monitoring

### Data Flow

```
[Data Provider] → [Primary Data Manager] → [Strategy Engine] → [Signals]
                                                                    ↓
[InfluxDB] ← [Enhanced Trader] ← [Execution Broker] ← [Execution Data Manager]
     ↓
[Dashboard] ← [WebSocket Updates] ← [Live Data Collection]
```

## Configuration

### Trading Parameters
Configure trading parameters in `config/trading_config.json`:

```json
{
  "data_provider": "ctrader",
  "broker": "ctrader", 
  "pairs": ["EURUSD-GBPUSD", "AAPL.US-MSFT.US"],
  "interval": "M15",
  "z_entry": 2.0,
  "z_exit": 0.5,
  "max_position_size": 10000,
  "max_open_positions": 10
}
```

### Risk Management
- **Portfolio Drawdown**: Automatic suspension when max portfolio drawdown exceeded
- **Pair Drawdown**: Individual pair suspension based on performance
- **Position Sizing**: Balanced monetary exposure across pair legs
- **Stop Loss/Take Profit**: Configurable percentage-based levels

## Monitoring & Logging

### Log Files
- `logs/enhanced_pairs_trading_v3.log`: Main system log
- `logs/pairs_trading.log`: Strategy-specific log
- `logs/mt5.log`: MT5-specific operations
- `logs/ctrader.log`: cTrader-specific operations

### Dashboard Monitoring
- **Portfolio Status**: Real-time P&L, positions, exposure
- **System Health**: Connection status for all providers
- **Trade History**: Recent trades with provider attribution
- **Performance Metrics**: Live calculation of key statistics

## Error Handling

### Robust Error Management
- **Connection Resilience**: Automatic reconnection for both providers
- **Graceful Degradation**: System continues with available providers
- **Error Isolation**: Provider-specific errors don't affect overall system
- **Comprehensive Logging**: Detailed error tracking and debugging

### Common Issues & Solutions

**cTrader Connection Issues**
```bash
# Check credentials
echo $CTRADER_CLIENT_ID
echo $CTRADER_ACCESS_TOKEN

# Verify API access
# Ensure your cTrader app has proper permissions
```

**MT5 Connection Issues**
```bash
# Check MT5 terminal is running
# Verify credentials in .env file
# Ensure MT5 allows automated trading
```

**InfluxDB Issues**
```bash
# Check InfluxDB is running
docker run -d -p 8086:8086 influxdb:latest

# Verify connection
curl http://localhost:8086/health
```

**Data Management Issues**

*No data fetched*
```bash
# Check data provider connection
# Verify symbol names are correct
# Check date ranges are valid

# Force refresh to bypass cache
python enhanced_main_v3.py --force-refresh
```

*Incomplete data*
```bash
# Run data coverage analysis
python data_management_demo.py
# Choose option 2 for gap detection

# Check InfluxDB storage
# Verify data provider limits
```

*Slow backtest performance*
```bash
# Ensure data is pre-cached
# Check InfluxDB connection
# Use force-refresh only when necessary

# Monitor cache hit rates in logs
```

*Data quality issues*
```python
# Check data quality report
quality_report = system.backtest_data_manager.validate_data_quality(data_cache)
for symbol, report in quality_report.items():
    if report['status'] != 'GOOD':
        print(f"{symbol}: {report['issues']}")
```

## Performance Optimization

### High-Performance Features
- **Vectorized Calculations**: All strategy calculations use NumPy/Pandas vectorization
- **Intelligent Data Caching**: Automatic gap detection and smart pre-fetching
- **Efficient Data Structures**: Optimized data storage and retrieval with InfluxDB
- **Async Operations**: Non-blocking API calls where possible
- **Memory Management**: Efficient buffering and cleanup

### Scalability
- **Multi-Pair Support**: Handle hundreds of pairs simultaneously
- **Provider Independence**: Scale data and execution independently
- **Resource Management**: Configurable worker threads and memory limits

## Development & Customization

### Adding New Providers
1. Implement data manager interface
2. Implement real-time trader interface  
3. Add provider to system initialization
4. Update configuration options

### Custom Strategies
1. Extend `OptimizedPairsStrategy` class
2. Implement vectorized calculations
3. Add signal generation logic
4. Integrate with enhanced trader

### Testing
```bash
# Run backtests with different providers
python enhanced_main_v3.py --data-provider ctrader --mode backtest
python enhanced_main_v3.py --data-provider mt5 --mode backtest

# Compare results between providers
# Verify data consistency and execution performance
```

## Support

### Documentation
- Code is extensively documented with docstrings
- Configuration files include inline comments
- Log messages provide detailed operational information

### Troubleshooting
1. Check log files for detailed error messages
2. Verify all credentials and connections
3. Test individual components separately
4. Use dashboard monitoring for real-time diagnostics

---

## Version History

### V3.0 Features
- ✅ Independent data provider and broker selection
- ✅ cTrader real-time trading implementation
- ✅ Enhanced error handling and resilience
- ✅ Improved performance and scalability
- ✅ Comprehensive monitoring and logging

### Upgrade from V2
- No breaking changes to configuration
- All V2 functionality preserved
- Additional broker selection parameter
- Enhanced real-time capabilities

---

**Author**: Trading System v3.0  
**Date**: July 2025  
**License**: MIT
