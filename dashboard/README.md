# Trading Dashboard System

A comprehensive, scalable, and efficient dashboard system for visualizing backtesting data, live trading streams, and analysis for quantitative trading projects.

## Features

### 🚀 **Core Capabilities**
- **Real-time WebSocket updates** for live data streaming
- **Interactive charts** using Plotly.js with multiple visualization types
- **Responsive web interface** that works on desktop and mobile
- **Multi-data source support** - easily integrate with any trading system
- **Flexible architecture** - highly customizable and extensible

### 📊 **Visualization Types**
- Portfolio equity curves and drawdown analysis
- Performance distribution histograms and box plots
- Monthly returns heatmaps
- Risk metrics radar charts
- Pairs performance comparison tables
- Trade analysis and statistics
- Real-time market data displays

### 🔧 **Integration Options**
- **Backtest Results**: Visualize historical backtesting results
- **Live Trading**: Real-time monitoring of active trading systems
- **Portfolio Management**: Track positions, P&L, and risk metrics
- **Custom Data**: Add any custom visualizations and data streams

## Quick Start

### 1. Basic Installation

```bash
# Install required dependencies
pip install flask flask-socketio flask-cors plotly pandas numpy
```

### 2. Simple Usage

```python
from dashboard.dashboard_integration import quick_start_dashboard

# Start dashboard with minimal configuration
dashboard = quick_start_dashboard(port=8050, theme='dark')
# Dashboard will be available at http://127.0.0.1:8050
```

### 3. With Backtest Results

```python
from dashboard.dashboard_integration import start_dashboard_with_backtest

# Your backtest results
backtest_results = {
    'portfolio_metrics': {
        'portfolio_return': 15.5,
        'portfolio_sharpe': 1.25,
        'portfolio_max_drawdown': -8.2,
        'total_trades': 150,
        'portfolio_win_rate': 62.5
    },
    'pair_results': [
        # ... pair-specific results
    ]
}

# Start dashboard with backtest data
dashboard = start_dashboard_with_backtest(
    backtest_results=backtest_results,
    config=your_trading_config
)
```

### 4. With Live Trading Data

```python
from dashboard.dashboard_integration import start_dashboard_with_live_trading

# Connect your live data source (MT5, cTrader, etc.)
dashboard = start_dashboard_with_live_trading(
    data_source=your_data_manager,
    symbols=['AAPL.US', 'MSFT.US', 'NVDA.US']
)
```

## Integration with Existing Trading Systems

### With MT5 Data Manager

```python
from data.mt5 import MT5DataManager
from dashboard.dashboard_integration import start_comprehensive_dashboard

# Initialize your MT5 data manager
data_manager = MT5DataManager(config)
data_manager.connect()

# Start dashboard with both backtest and live data
dashboard = start_comprehensive_dashboard(
    backtest_results=your_backtest_results,
    live_data_source=data_manager,
    symbols=config.pairs,
    config=config
)
```

### With VectorBT Backtester

```python
from backtesting.vectorbt import VectorBTBacktester
from dashboard.dashboard_integration import start_dashboard_with_backtest

# Run backtest
backtester = VectorBTBacktester(config, data_manager)
results = backtester.run_backtest()

# Visualize results in dashboard
dashboard = start_dashboard_with_backtest(
    backtest_results=results,
    config=config
)
```

### Custom Data Integration

```python
from dashboard.dashboard_manager import DashboardManager

# Create dashboard
dashboard = DashboardManager()

# Add custom data
dashboard.add_custom_data(
    data_key='custom_indicator',
    data=your_custom_data,
    chart_config={
        'type': 'line',
        'title': 'Custom Indicator',
        'x_field': 'timestamp',
        'y_field': 'value'
    }
)

dashboard.start()
```

## Configuration Options

```python
dashboard_config = {
    'host': '127.0.0.1',           # Server host
    'port': 8050,                  # Server port
    'theme': 'dark',               # 'dark' or 'light'
    'auto_refresh': True,          # Auto-refresh data
    'websocket_enabled': True,     # Enable WebSocket updates
    'cors_enabled': True,          # Enable CORS for API
    'update_interval': 1.0,        # Update interval in seconds
    'chart_height': 400,           # Default chart height
    'max_data_points': 10000,      # Max data points to cache
    'cache_size': 1000             # Cache size for performance
}
```

## API Endpoints

The dashboard provides RESTful API endpoints for accessing data:

- `GET /api/status` - Dashboard status
- `GET /api/backtest/summary` - Backtest summary metrics
- `GET /api/backtest/pairs` - Pairs performance data
- `GET /api/backtest/pair/{pair_name}` - Detailed pair analysis
- `GET /api/backtest/charts` - Chart data for backtests
- `GET /api/live/data` - Live market data
- `GET /api/portfolio` - Portfolio information
- `GET /api/config` - Dashboard configuration
- `POST /api/config` - Update dashboard configuration

## WebSocket Events

Real-time updates are delivered via WebSocket:

- `backtest_update` - New backtest data
- `live_update` - Live market data updates
- `portfolio_update` - Portfolio status updates
- `trade_signal` - Trading signals
- `alert` - System alerts and notifications
- `system_status` - System status updates

## Dashboard Pages

### 1. Overview (`/`)
- Key performance metrics
- Portfolio equity curve
- Top performing pairs
- Risk metrics summary
- System status indicators

### 2. Backtest Results (`/backtest`)
- Detailed backtest analysis
- Portfolio performance charts
- Monthly returns heatmap
- Pairs performance table with detailed drill-down
- Trade analysis statistics

### 3. Live Trading (`/live`)
- Real-time market data
- Active positions monitoring
- Live P&L tracking
- Trading signals display

### 4. Portfolio (`/portfolio`)
- Portfolio composition
- Position management
- Risk analysis
- Performance tracking

### 5. Pairs Analysis (`/pairs`)
- Individual pair analysis
- Correlation matrices
- Statistical test results
- Pair-specific charts

## Architecture

```
Dashboard System Architecture
├── DashboardManager          # Central coordinator
├── WebServer                # Flask-based web server
├── DataAdapter              # Data processing and formatting
├── ChartGenerator           # Interactive chart creation
├── WebSocketHandler         # Real-time data streaming
└── Templates & Static       # Web interface files
```

### Components

- **DashboardManager**: Central coordinator managing all components
- **DataAdapter**: Processes and formats data from various sources
- **WebServer**: Flask-based server providing web interface and API
- **ChartGenerator**: Creates interactive charts using Plotly
- **WebSocketHandler**: Manages real-time data streaming
- **Templates**: Responsive HTML templates with Bootstrap
- **Static Assets**: CSS, JavaScript, and other static files

## Advanced Usage

### Custom Chart Types

```python
# Add custom chart to dashboard
dashboard.add_custom_data(
    data_key='volatility_surface',
    data=volatility_data,
    chart_config={
        'type': 'heatmap',
        'title': 'Volatility Surface',
        'x_field': 'strike',
        'y_field': 'expiry',
        'z_field': 'volatility'
    }
)
```

### Real-time Alerts

```python
# Send custom alert
dashboard.websocket_handler.broadcast_alert({
    'level': 'warning',
    'title': 'High Volatility Alert',
    'message': 'Market volatility exceeded threshold',
    'data': {'symbol': 'AAPL.US', 'volatility': 0.45}
})
```

### Trading Signals

```python
# Broadcast trading signal
dashboard.websocket_handler.broadcast_trade_signal({
    'pair': 'AAPL.US-MSFT.US',
    'action': 'buy',
    'signal_type': 'entry',
    'confidence': 0.85,
    'z_score': -2.1
})
```

## Performance Optimization

- **Efficient Data Caching**: Multi-level caching system
- **Compressed Data Transfer**: Automatic data compression
- **Lazy Loading**: Charts and data loaded on demand
- **WebSocket Optimization**: Efficient real-time updates
- **Responsive Design**: Optimized for all screen sizes

## Security Features

- **CORS Support**: Configurable cross-origin resource sharing
- **API Key Authentication**: Optional API key validation
- **Input Validation**: Comprehensive input sanitization
- **Error Handling**: Robust error handling and logging

## Troubleshooting

### Common Issues

1. **Import Errors**: Ensure all dependencies are installed
2. **Port Conflicts**: Change port if 8050 is already in use
3. **WebSocket Issues**: Check firewall settings
4. **Chart Loading**: Verify Plotly.js is accessible

### Debug Mode

```python
# Enable debug mode for troubleshooting
dashboard_config = {
    'debug': True,
    'log_level': 'DEBUG'
}
```

## Examples

See `dashboard_integration.py` for complete examples including:
- Mock data generation
- Integration patterns
- Error handling
- Performance optimization

## Dependencies

### Required
- `flask` - Web framework
- `flask-socketio` - WebSocket support
- `pandas` - Data manipulation
- `numpy` - Numerical computations

### Optional
- `plotly` - Interactive charts (recommended)
- `flask-cors` - CORS support
- `redis` - Advanced caching
- `psycopg2` - PostgreSQL support

## License

This dashboard system is designed to be highly flexible and can be easily integrated with any quantitative trading project. It provides a solid foundation for visualizing both historical and real-time trading data.

For support and additional features, refer to the source code and examples provided.
