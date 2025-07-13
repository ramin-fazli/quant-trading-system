# Enhanced Pairs Trading System

A comprehensive quantitative trading system that integrates real-time data from CTrader, executes trades through MetaTrader 5, performs advanced backtesting with vectorbt, stores data in InfluxDB, and provides a beautiful web dashboard for monitoring and analysis.

## 🚀 Features

### Core Trading System
- **Multi-Source Data Integration**: Real-time and historical data from CTrader
- **Automated Execution**: Trade execution through MetaTrader 5
- **Advanced Backtesting**: Powered by vectorbt for comprehensive strategy testing
- **Time-Series Database**: InfluxDB integration for efficient data storage and retrieval
- **Real-Time Dashboard**: Interactive web interface with live updates

### Dashboard Features
- **Live Market Data**: Real-time price feeds and market updates
- **Interactive Charts**: Advanced Plotly.js visualizations
- **Portfolio Monitoring**: Track positions, PnL, and performance metrics
- **Backtest Analysis**: Detailed backtest results with downloadable Excel reports
- **Pairs Analysis**: Correlation analysis and pair selection tools
- **Risk Management**: Real-time risk monitoring and alerts

### Technical Capabilities
- **WebSocket Streaming**: Real-time data updates via Socket.IO
- **Multi-Mode Operation**: Backtest, real-time, and hybrid modes
- **Excel Reporting**: Automated generation of detailed Excel reports
- **Scalable Architecture**: Modular design for easy extension
- **Comprehensive Logging**: Detailed logging for debugging and monitoring

## 📋 Prerequisites

- **Python 3.8+**
- **InfluxDB 2.0+**
- **MetaTrader 5** (with API access enabled)
- **CTrader API** credentials

## 🛠️ Installation

### Quick Setup

1. **Clone and Navigate**:
   ```bash
   cd pair_trading_system
   ```

2. **Run Setup Script**:
   ```bash
   python setup.py
   ```

The setup script will automatically:
- Install all Python dependencies
- Create necessary directories
- Generate configuration files
- Validate the installation

### Manual Installation

If you prefer manual installation:

1. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Install Additional Packages**:
   ```bash
   pip install pandas numpy MetaTrader5 vectorbt flask flask-cors flask-socketio plotly influxdb-client requests openpyxl xlsxwriter python-dotenv schedule python-socketio aiohttp asyncio-mqtt psutil
   ```

## ⚙️ Configuration

### 1. Environment Variables

Edit `.env.enhanced` with your credentials:

```env
# InfluxDB Settings
INFLUXDB_URL=http://localhost:8086
INFLUXDB_TOKEN=your_influxdb_token_here
INFLUXDB_ORG=trading_org
INFLUXDB_BUCKET=trading_data

# CTrader Settings
CTRADER_API_KEY=your_ctrader_api_key_here
CTRADER_ACCOUNT_ID=your_ctrader_account_id_here
CTRADER_BASE_URL=https://api.ctrader.com

# MT5 Settings
MT5_LOGIN=your_mt5_login
MT5_PASSWORD=your_mt5_password
MT5_SERVER=your_mt5_server

# Dashboard Settings
DASHBOARD_HOST=localhost
DASHBOARD_PORT=5000
DASHBOARD_DEBUG=False

# Trading Mode
TRADING_MODE=backtest
```

### 2. Trading Configuration

Edit `config/trading_config.json`:

```json
{
    "risk_management": {
        "max_position_size": 0.02,
        "max_portfolio_risk": 0.10,
        "stop_loss_pct": 0.05,
        "take_profit_pct": 0.10
    },
    "trading_hours": {
        "start_hour": 9,
        "end_hour": 17,
        "timezone": "UTC"
    },
    "data_sources": {
        "mt5": {
            "enabled": true,
            "symbols": ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]
        },
        "ctrader": {
            "enabled": true,
            "api_key": "your_ctrader_api_key",
            "account_id": "your_account_id"
        }
    }
}
```

### 3. Pairs Configuration

Edit `config/pairs.json`:

```json
{
    "pairs": [
        {
            "symbol1": "EURUSD",
            "symbol2": "GBPUSD",
            "lookback_period": 60,
            "entry_threshold": 2.0,
            "exit_threshold": 0.5,
            "enabled": true
        }
    ]
}
```

## 🚀 Usage

### Running the System

#### Backtest Mode
```bash
set TRADING_MODE=backtest && python enhanced_main.py
```

#### Real-time Mode
```bash
set TRADING_MODE=realtime && python enhanced_main.py
```

#### Hybrid Mode
```bash
set TRADING_MODE=hybrid && python enhanced_main.py
```

### Accessing the Dashboard

Open your browser and navigate to:
```
http://localhost:5000
```

### Dashboard Pages

- **Overview** (`/`): Main dashboard with key metrics
- **Backtest Results** (`/backtest`): Detailed backtest analysis
- **Live Trading** (`/live`): Real-time trading monitoring
- **Portfolio** (`/portfolio`): Portfolio overview and positions
- **Pairs Analysis** (`/pairs`): Correlation analysis and pair selection
- **Reports** (`/reports`): Download Excel reports
- **Settings** (`/settings`): System configuration

## 📊 System Architecture

```
Enhanced Pairs Trading System
├── Data Sources
│   ├── CTrader API (Historical & Real-time)
│   └── MetaTrader 5 (Execution & Market Data)
├── Data Storage
│   └── InfluxDB (Time-series Database)
├── Processing Engine
│   ├── Strategy Engine
│   ├── Risk Management
│   └── Order Management
├── Backtesting
│   └── vectorbt Integration
├── Web Dashboard
│   ├── Flask Server
│   ├── WebSocket Streaming
│   └── Interactive Charts
└── Reporting
    └── Excel Report Generation
```

## 📁 Project Structure

```
pair_trading_system/
├── enhanced_main.py              # Main system orchestrator
├── setup.py                      # Installation script
├── requirements.txt              # Python dependencies
├── .env.enhanced                 # Environment configuration
├── config/                       # Configuration files
│   ├── trading_config.json
│   └── pairs.json
├── dashboard/                    # Web dashboard
│   ├── dashboard_manager.py
│   ├── web_server.py
│   ├── websocket_handler.py
│   ├── chart_generator.py
│   ├── data_adapter.py
│   ├── templates/               # HTML templates
│   └── static/                  # CSS, JS, images
├── brokers/                      # Broker integrations
│   └── mt5.py
├── data/                         # Data handling
│   ├── ctrader.py
│   └── mt5.py
├── strategies/                   # Trading strategies
├── backtesting/                  # Backtesting modules
│   └── vectorbt.py
├── reporting/                    # Report generation
│   └── report_generator.py
├── backtest_reports/            # Generated Excel reports
└── logs/                        # System logs
```

## 🔧 Advanced Configuration

### InfluxDB Setup

1. **Install InfluxDB**:
   ```bash
   # Windows
   winget install InfluxData.InfluxDB

   # Linux/Mac
   curl -sL https://repos.influxdata.com/influxdb.key | sudo apt-key add -
   ```

2. **Create Organization and Bucket**:
   ```bash
   influx setup
   influx org create -n trading_org
   influx bucket create -n trading_data -o trading_org
   ```

### MetaTrader 5 Setup

1. **Enable API Trading**:
   - Open MetaTrader 5
   - Go to Tools → Options → Expert Advisors
   - Check "Allow algorithmic trading"
   - Check "Allow DLL imports"

2. **Install Python Package**:
   ```bash
   pip install MetaTrader5
   ```

### CTrader API Setup

1. **Get API Credentials**:
   - Register at [CTrader Developer Portal](https://connect.ctrader.com/)
   - Create new application
   - Note down API key and account ID

## 📈 Usage Examples

### Basic Backtesting

```python
from enhanced_main import EnhancedTradingSystem

# Initialize system
system = EnhancedTradingSystem(mode='backtest')

# Run backtest
results = system.run_backtest(
    start_date='2023-01-01',
    end_date='2023-12-31',
    pairs=['EURUSD-GBPUSD', 'AUDUSD-NZDUSD']
)

# Generate report
system.generate_excel_report(results)
```

### Real-time Trading

```python
from enhanced_main import EnhancedTradingSystem

# Initialize system
system = EnhancedTradingSystem(mode='realtime')

# Start real-time trading
system.start_realtime_trading()
```

### Dashboard Integration

```python
from dashboard.dashboard_manager import DashboardManager

# Start dashboard
dashboard = DashboardManager()
dashboard.start()
```

## 📋 API Endpoints

### REST API

- `GET /api/status` - System status
- `GET /api/backtest/summary` - Backtest summary
- `GET /api/backtest/pairs` - Backtest pairs data
- `GET /api/live/data` - Live market data
- `GET /api/portfolio` - Portfolio data
- `GET /api/reports` - Available reports
- `GET /api/download/report/<filename>` - Download report
- `DELETE /api/reports/<filename>` - Delete report

### WebSocket Events

- `connect` - Client connection
- `disconnect` - Client disconnection
- `market_data` - Real-time market data
- `trade_update` - Trade execution updates
- `portfolio_update` - Portfolio changes

## 🚨 Risk Management

### Built-in Risk Controls

- **Position Sizing**: Automatic position size calculation
- **Portfolio Risk**: Maximum portfolio exposure limits
- **Stop Loss**: Automatic stop loss orders
- **Take Profit**: Profit-taking mechanisms
- **Correlation Monitoring**: Real-time correlation tracking

### Configuration

Risk parameters are configured in `config/trading_config.json`:

```json
{
    "risk_management": {
        "max_position_size": 0.02,      // 2% max position size
        "max_portfolio_risk": 0.10,     // 10% max portfolio risk
        "stop_loss_pct": 0.05,          // 5% stop loss
        "take_profit_pct": 0.10,        // 10% take profit
        "max_correlation": 0.8,         // Max pair correlation
        "min_liquidity": 1000000        // Min daily volume
    }
}
```

## 📊 Performance Monitoring

### Key Metrics

- **Return Metrics**: Total return, annualized return, Sharpe ratio
- **Risk Metrics**: Maximum drawdown, volatility, VaR
- **Trade Metrics**: Win rate, profit factor, average trade
- **Portfolio Metrics**: Correlation, beta, alpha

### Real-time Monitoring

The dashboard provides real-time monitoring of:
- Open positions and PnL
- Risk exposure by pair
- Strategy performance
- Market data quality
- System health metrics

## 🐛 Troubleshooting

### Common Issues

1. **InfluxDB Connection Error**:
   ```
   Error: Failed to connect to InfluxDB
   Solution: Check INFLUXDB_URL and INFLUXDB_TOKEN in .env.enhanced
   ```

2. **MT5 Connection Failed**:
   ```
   Error: MT5 initialization failed
   Solution: Ensure MT5 is running and API trading is enabled
   ```

3. **CTrader API Error**:
   ```
   Error: Unauthorized CTrader API access
   Solution: Verify CTRADER_API_KEY and CTRADER_ACCOUNT_ID
   ```

### Debug Mode

Enable debug mode by setting:
```env
DASHBOARD_DEBUG=True
LOG_LEVEL=DEBUG
```

### Log Files

Check log files in the `logs/` directory:
- `trading_system.log` - Main system logs
- `dashboard.log` - Dashboard-specific logs
- `error.log` - Error logs

## 📝 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## 📞 Support

For support and questions:
- Check the troubleshooting section
- Review log files for errors
- Open an issue on GitHub

## 🔄 Updates

To update the system:
1. Pull latest changes
2. Run `python setup.py` to update dependencies
3. Review configuration changes
4. Restart the system

---

**⚠️ Important Disclaimer**: This system is for educational and research purposes. Always test with demo accounts before using real money. Trading involves significant risk of loss.
