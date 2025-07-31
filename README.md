# Enhanced Quantitative Trading System

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/Docker-Enabled-2496ED)](https://www.docker.com/)

## 🛠️ Built With

<div align="center">

![InfluxDB](https://img.shields.io/badge/InfluxDB-22ADF6?style=for-the-badge&logo=InfluxDB&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/postgresql-4169e1?style=for-the-badge&logo=postgresql&logoColor=white)
![Flask](https://img.shields.io/badge/flask-%23000.svg?style=for-the-badge&logo=flask&logoColor=white)
![Redis](https://img.shields.io/badge/redis-%23DD0031.svg?style=for-the-badge&logo=redis&logoColor=white)
![Docker](https://img.shields.io/badge/docker-%230db7ed.svg?style=for-the-badge&logo=docker&logoColor=white)
![MetaTrader 5](https://img.shields.io/badge/MetaTrader%205-1C1C1C?style=for-the-badge)
![cTrader](https://img.shields.io/badge/cTrader-00A651?style=for-the-badge)

</div>

A modular quantitative trading system that provides complete flexibility in selecting data providers and execution brokers independently. Build sophisticated pairs trading strategies with enterprise-grade reliability and performance.

## 🎯 Key Features

### ✅ Independent Provider Selection
- **Data Provider**: Choose between `ctrader` or `mt5` for historical and real-time market data
- **Execution Broker**: Choose between `ctrader` or `mt5` for trade execution  
- **Mix & Match**: Use any combination (e.g., cTrader data with MT5 execution)

### ✅ Real-Time Trading Capabilities
- **MT5 Real-Time Trading**: Complete implementation with MetaTrader5 API
- **cTrader Real-Time Trading**: Full implementation with cTrader Open API
- **Advanced Risk Management**: Portfolio-level and pair-level drawdown protection
- **Position Management**: Automated position sizing and balanced exposure

### ✅ Intelligent Data Management
- **Smart Caching**: Automatic gap detection and intelligent pre-fetching
- **InfluxDB Integration**: Store and retrieve historical data with enterprise-grade time-series database
- **Data Quality Validation**: Comprehensive data quality checks and reporting
- **Multi-Provider Support**: Seamless switching between data providers

### ✅ Enterprise-Grade Architecture
- **Containerized Deployment**: Docker & Docker Compose support
- **State Management**: Robust state persistence with automatic recovery
- **Comprehensive Monitoring**: Real-time dashboard with WebSocket updates
- **Production Ready**: CI/CD pipeline with AWS deployment automation

## � Quick Start

### Option 1: Automated Setup (Recommended)

1. **Clone and Setup**:
   ```bash
   git clone https://github.com/ramin-fazli/quant.git
   cd pair_trading_system
   python setup.py
   ```

2. **Configure Environment**:
   ```bash
   cp .env.development .env
   # Edit .env with your API credentials
   ```

3. **Run Your First Backtest**:
   ```bash
   python scripts/pair_trading/main.py --data-provider ctrader --broker ctrader --mode backtest
   ```

### Option 2: Docker Deployment

1. **Docker Compose (Development)**:
   ```bash
   export ENV_SUFFIX=.development
   docker-compose up --build
   ```

2. **Production Deployment**:
   ```bash
   export ENV_SUFFIX=.production
   docker-compose up --build -d
   ```

## 📋 Prerequisites

- **Python 3.8+**
- **InfluxDB 2.0+** (or use Docker)
- **MetaTrader 5** (optional - for MT5 provider)
- **cTrader Account** (optional - for cTrader provider)
- **Docker & Docker Compose** (for containerized deployment)

## ⚙️ Configuration

### Environment Variables

Create a `.env` file based on `.env.development`:

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

# Trading Configuration
TRADING_MODE=backtest
MAX_POSITION_SIZE=10000
MAX_OPEN_POSITIONS=10
```

### Trading Parameters

Configure strategy parameters in `config/trading_config.json`:

```json
{
  "data_provider": "ctrader",
  "broker": "ctrader", 
  "pairs": ["EURUSD-GBPUSD", "AAPL.US-MSFT.US"],
  "interval": "M15",
  "z_entry": 2.0,
  "z_exit": 0.5,
  "risk_management": {
    "max_position_size": 0.02,
    "max_portfolio_risk": 0.10,
    "stop_loss_pct": 0.05,
    "take_profit_pct": 0.10
  }
}
```

## � Usage

### Command Line Interface

```bash
# Basic usage - CTrader data and execution
python scripts/pair_trading/main.py

# Specify providers explicitly
python scripts/pair_trading/main.py --data-provider ctrader --broker ctrader --mode backtest

# Mix providers - CTrader data with MT5 execution
python scripts/pair_trading/main.py --data-provider ctrader --broker mt5 --mode backtest

# Live trading with CTrader
python scripts/pair_trading/main.py --data-provider ctrader --broker ctrader --mode live

# Force refresh all data (ignore cache)
python scripts/pair_trading/main.py --force-refresh
```

### Supported Provider Combinations

| Data Provider | Execution Broker | Use Case |
|---------------|------------------|----------|
| `ctrader` | `ctrader` | Pure cTrader environment |
| `mt5` | `mt5` | Pure MT5 environment |
| `ctrader` | `mt5` | cTrader data with MT5 execution |
| `mt5` | `ctrader` | MT5 data with cTrader execution |

### Dashboard Access

After starting the system, access the web dashboard at:
```
http://localhost:5000
```

**Available Pages:**
- **Overview** (`/`): Main dashboard with key metrics
- **Live Trading** (`/live`): Real-time trading monitoring
- **Portfolio** (`/portfolio`): Portfolio overview and positions
- **Backtest Results** (`/backtest`): Detailed backtest analysis
- **Reports** (`/reports`): Download Excel reports

## 🏗️ Architecture

### System Overview

```
Enhanced Quantitative Trading System
├── Data Sources
│   ├── cTrader API (Historical & Real-time)
│   └── MetaTrader 5 (Historical & Real-time)
├── Data Storage
│   ├── InfluxDB (Time-series Database)
│   └── Redis (Caching & State Management)
├── Processing Engine
│   ├── Strategy Engine (Configurable Strategies)
│   ├── Risk Management (Portfolio & Pair Level)
│   └── Order Management (Multi-Broker Support)
├── Backtesting
│   └── VectorBT Integration (High-Performance)
├── Web Dashboard
│   ├── Flask Server with WebSocket Streaming
│   └── Interactive Charts & Real-time Updates
└── Deployment
    ├── Docker Containers
    └── CI/CD Pipeline (GitHub Actions → AWS)
```

### Core Components

- **EnhancedTradingSystem**: Main orchestrator managing all components
- **Data Managers**: Provider-specific data handling (cTrader/MT5)
- **Real-Time Traders**: Live trading implementations for each broker
- **State Management**: Robust state persistence with automatic recovery
- **Dashboard Integration**: Real-time visualization and monitoring

## 📁 Project Structure

```
pair_trading_system/
├── scripts/pair_trading/
│   └── main.py                    # Main system entry point
├── setup.py                    # Automated setup script
├── docker-compose.yml             # Container orchestration
├── .env.development               # Development environment template
├── .env.production                # Production environment template
├── config/                        # Configuration files
│   ├── trading_config.json
│   └── pairs.json
├── dashboard/                     # Web dashboard
│   ├── dashboard_manager.py
│   ├── templates/                 # HTML templates
│   └── static/                    # CSS, JS, assets
├── data/                          # Data handling modules
│   ├── ctrader_data_manager.py
│   └── mt5_data_manager.py
├── brokers/                       # Broker integrations
│   ├── ctrader_trader.py
│   └── mt5_trader.py
├── strategies/                    # Trading strategies
│   └── optimized_pairs_strategy.py
├── backtesting/                   # Backtesting modules
│   └── vectorbt_backtester.py
├── utils/                         # Utility modules
│   ├── state_manager.py          # State persistence
│   ├── unified_state_manager.py   # Advanced state management
│   └── influxdb_manager.py        # Database operations
├── reporting/                     # Report generation
│   └── report_generator.py
├── backtest_reports/              # Generated reports
├── logs/                          # System logs
└── docs/                          # Documentation
```

## � Risk Management

### Built-in Risk Controls

- **Position Sizing**: Automatic position size calculation based on account balance
- **Portfolio Risk**: Maximum portfolio exposure limits with automatic suspension
- **Drawdown Protection**: Portfolio-level and pair-level drawdown monitoring
- **Correlation Monitoring**: Real-time correlation tracking between pairs
- **Stop Loss/Take Profit**: Configurable percentage-based risk controls

### Configuration

Risk parameters in `config/trading_config.json`:

```json
{
    "risk_management": {
        "max_position_size": 0.02,           // 2% max position size
        "max_portfolio_risk": 0.10,          // 10% max portfolio risk
        "stop_loss_pct": 0.05,               // 5% stop loss
        "take_profit_pct": 0.10,             // 10% take profit
        "max_drawdown": 0.15,                // 15% max drawdown
        "position_timeout_hours": 24         // Auto-close after 24h
    }
}
```

## � Advanced Features

### Intelligent Data Management

- **Smart Caching**: Automatic gap detection and intelligent pre-fetching
- **Data Quality Validation**: Comprehensive quality checks with detailed reporting
- **Gap Detection**: Identifies and fills missing data periods automatically
- **Force Refresh**: Override cache when needed for data integrity

### State Management

- **Persistent State**: Robust state persistence with automatic recovery
- **State Versioning**: Complete audit trail of all state changes
- **Cross-Session Recovery**: Seamlessly resume operations after restarts
- **Backup & Restore**: Automated backup creation and restoration

### Performance Optimization

- **Vectorized Calculations**: High-performance NumPy/Pandas operations
- **Efficient Data Structures**: Optimized storage with InfluxDB
- **Async Operations**: Non-blocking API calls where possible
- **Memory Management**: Intelligent buffering and cleanup

## � Production Deployment

### CI/CD Pipeline

The system includes automated deployment to AWS EC2:

1. **Setup GitHub Secrets**:
   ```
   EC2_HOST=your-ec2-ip
   EC2_USER=ubuntu
   EC2_PRIVATE_KEY=your-private-key
   DOCKER_USERNAME=your-docker-username
   DOCKER_PASSWORD=your-docker-password
   ```

2. **Automatic Deployment**:
   - Push to `main` branch triggers deployment
   - ~3-5 minute deployment time
   - Automatic health checks and rollback

3. **Manual Deployment**:
   ```bash
   # SSH to EC2 instance
   ssh -i your-key.pem ubuntu@your-ec2-ip
   
   # Run setup script
   curl -sSL https://raw.githubusercontent.com/ramin-fazli/quant/main/scripts/setup-ec2.sh | bash
   ```

### Docker Production Setup

```bash
# Production deployment with persistent volumes
export ENV_SUFFIX=.production
docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Scale services
docker-compose up --scale trading-system=2 -d

# Monitor logs
docker-compose logs -f trading-system
```

## 🐛 Troubleshooting

### Common Issues

**InfluxDB Connection Error**:
```bash
# Check InfluxDB status
docker-compose logs influxdb

# Verify credentials
echo $INFLUXDB_TOKEN
```

**MT5 Connection Failed**:
```bash
# Ensure MT5 terminal is running
# Check credentials in .env file
# Verify "Allow algorithmic trading" is enabled
```

**cTrader API Error**:
```bash
# Verify API credentials
echo $CTRADER_CLIENT_ID
echo $CTRADER_ACCESS_TOKEN

# Check API permissions at cTrader Developer Portal
```

**Data Quality Issues**:
```bash
# Run data coverage analysis
python data_management_demo.py

# Force refresh to bypass cache
python scripts/pair_trading/main.py --force-refresh
```

### Debug Mode

Enable comprehensive debugging:
```env
LOG_LEVEL=DEBUG
DASHBOARD_DEBUG=True
```

### Log Files

- `logs/enhanced_pairs_trading.log` - Main system log
- `logs/pairs_trading.log` - Strategy-specific log
- `logs/mt5.log` - MT5 operations
- `logs/ctrader.log` - cTrader operations

## � Performance Metrics

### Backtesting Results

The system provides comprehensive performance analysis:

- **Return Metrics**: Total return, annualized return, Sharpe ratio
- **Risk Metrics**: Maximum drawdown, volatility, VaR
- **Trade Metrics**: Win rate, profit factor, average trade duration
- **Portfolio Metrics**: Correlation, beta, alpha

### Real-time Monitoring

Live dashboard features:
- **Portfolio Status**: Real-time P&L, positions, exposure
- **System Health**: Connection status for all providers
- **Trade History**: Recent trades with provider attribution
- **Risk Monitoring**: Live drawdown and exposure tracking

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Make your changes and add tests
4. Commit: `git commit -m 'Add amazing feature'`
5. Push: `git push origin feature/amazing-feature`
6. Submit a pull request

### Development Setup

```bash
# Clone for development
git clone https://github.com/ramin-fazli/quant.git
cd pair_trading_system

# Install development dependencies
pip install -r requirements-dev.txt

# Run tests
python -m pytest tests/

# Run with development environment
cp .env.development .env
python scripts/pair_trading/main.py --mode backtest
```

## 📄 License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for details.

## 🔄 Version History

### v3.0.0 (Current)
- ✅ Independent data provider and broker selection
- ✅ cTrader real-time trading implementation  
- ✅ Enhanced state management with versioning
- ✅ Intelligent data management with gap detection
- ✅ Production-ready Docker deployment
- ✅ CI/CD pipeline with AWS integration

### v2.0.0
- Multi-provider data integration
- Advanced backtesting with VectorBT
- Real-time dashboard with WebSocket updates
- InfluxDB integration for time-series data

### v1.0.0
- Basic pairs trading strategy
- MT5 integration
- Simple backtesting framework

## 📞 Support

- **Documentation**: Check the `docs/` directory for detailed guides
- **Issues**: Open an issue on GitHub for bugs or feature requests
- **Discussions**: Use GitHub Discussions for questions and community support

## ⚠️ Disclaimer

**This software is for educational and research purposes only. Trading involves significant financial risk and this system should only be used with virtual/demo accounts initially. Always thoroughly test strategies before deploying real capital. The developers assume no responsibility for any financial losses incurred through the use of this software.**

---

**Built with ❤️ for the quantitative trading community**

*Enhanced Quantitative Trading System - Production Ready • Modular • Scalable*
