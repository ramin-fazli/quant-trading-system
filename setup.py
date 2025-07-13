#!/usr/bin/env python
"""
Setup and Installation Script for Enhanced Pairs Trading System

This script sets up the complete trading system with all dependencies and configurations.
"""

import os
import sys
import subprocess
import json
from pathlib import Path


def print_banner():
    """Print installation banner"""
    banner = """
    ╔══════════════════════════════════════════════════════════════╗
    ║              Enhanced Pairs Trading System                   ║
    ║                    Setup & Installation                      ║
    ╚══════════════════════════════════════════════════════════════╝
    
    This script will:
    • Install Python dependencies
    • Configure InfluxDB settings
    • Set up the trading environment
    • Validate all components
    
    """
    print(banner)


def check_python_version():
    """Check if Python version is compatible"""
    print("📋 Checking Python version...")
    
    if sys.version_info < (3, 8):
        print("❌ Error: Python 3.8 or higher is required")
        print(f"   Current version: {sys.version}")
        sys.exit(1)
    
    print(f"✅ Python {sys.version.split()[0]} is compatible")


def install_dependencies():
    """Install required Python packages"""
    print("\n📦 Installing Python dependencies...")
    
    # Core dependencies
    packages = [
        "pandas>=1.5.0",
        "numpy>=1.20.0",
        "MetaTrader5>=5.0.0",
        "vectorbt>=0.25.0",
        "flask>=2.0.0",
        "flask-cors>=3.0.0",
        "flask-socketio>=5.0.0",
        "plotly>=5.0.0",
        "influxdb-client>=1.30.0",
        "requests>=2.25.0",
        "openpyxl>=3.0.0",
        "xlsxwriter>=3.0.0",
        "python-dotenv>=0.19.0",
        "schedule>=1.1.0",
        "python-socketio[client]>=5.0.0",
        "aiohttp>=3.8.0",
        "asyncio-mqtt>=0.11.0",
        "psutil>=5.8.0"
    ]
    
    for package in packages:
        try:
            print(f"Installing {package}...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", package], 
                                stdout=subprocess.DEVNULL, 
                                stderr=subprocess.DEVNULL)
            print(f"✅ {package} installed")
        except subprocess.CalledProcessError:
            print(f"❌ Failed to install {package}")
            return False
    
    print("✅ All dependencies installed successfully")
    return True


def setup_directories():
    """Create necessary directories"""
    print("\n📁 Setting up directories...")
    
    directories = [
        "logs",
        "backtest_reports",
        "data/cache",
        "config/backup",
        "dashboard/static/css",
        "dashboard/static/js",
        "dashboard/static/images"
    ]
    
    for directory in directories:
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        print(f"✅ Created directory: {directory}")


def create_config_files():
    """Create default configuration files"""
    print("\n⚙️  Creating configuration files...")
    
    # Trading configuration
    trading_config = {
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
                "enabled": True,
                "symbols": ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD"]
            },
            "ctrader": {
                "enabled": True,
                "api_key": "your_ctrader_api_key",
                "account_id": "your_account_id"
            }
        },
        "influxdb": {
            "url": "http://localhost:8086",
            "token": "your_influxdb_token",
            "org": "trading_org",
            "bucket": "trading_data"
        }
    }
    
    with open("config/trading_config.json", "w") as f:
        json.dump(trading_config, f, indent=4)
    print("✅ Created trading_config.json")
    
    # Pairs configuration
    pairs_config = {
        "pairs": [
            {
                "symbol1": "EURUSD",
                "symbol2": "GBPUSD",
                "lookback_period": 60,
                "entry_threshold": 2.0,
                "exit_threshold": 0.5,
                "enabled": True
            },
            {
                "symbol1": "AUDUSD",
                "symbol2": "NZDUSD",
                "lookback_period": 60,
                "entry_threshold": 2.0,
                "exit_threshold": 0.5,
                "enabled": True
            }
        ],
        "default_parameters": {
            "lookback_period": 60,
            "entry_threshold": 2.0,
            "exit_threshold": 0.5,
            "correlation_threshold": 0.7
        }
    }
    
    with open("config/pairs.json", "w") as f:
        json.dump(pairs_config, f, indent=4)
    print("✅ Created pairs.json")
    
    # Environment file
    env_content = """# Enhanced Pairs Trading System Configuration

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

# Trading Mode (backtest, realtime, hybrid)
TRADING_MODE=backtest

# Logging Level
LOG_LEVEL=INFO
"""
    
    with open(".env.enhanced", "w") as f:
        f.write(env_content)
    print("✅ Created .env.enhanced")


def validate_installation():
    """Validate that all components are properly installed"""
    print("\n🔍 Validating installation...")
    
    try:
        # Test imports
        import pandas as pd
        import numpy as np
        import vectorbt as vbt
        import flask
        import plotly
        import influxdb_client
        
        print("✅ All Python packages imported successfully")
        
        # Check configuration files
        config_files = [
            "config/trading_config.json",
            "config/pairs.json",
            ".env.enhanced"
        ]
        
        for config_file in config_files:
            if Path(config_file).exists():
                print(f"✅ Configuration file exists: {config_file}")
            else:
                print(f"❌ Missing configuration file: {config_file}")
                return False
        
        # Check main script files
        main_files = [
            "enhanced_main.py",
            "dashboard/web_server.py",
            "dashboard/dashboard_manager.py"
        ]
        
        for main_file in main_files:
            if Path(main_file).exists():
                print(f"✅ Main script exists: {main_file}")
            else:
                print(f"❌ Missing main script: {main_file}")
                return False
        
        print("✅ Installation validation completed successfully")
        return True
        
    except ImportError as e:
        print(f"❌ Import error: {e}")
        return False


def print_next_steps():
    """Print next steps for the user"""
    steps = """
    ╔══════════════════════════════════════════════════════════════╗
    ║                        Next Steps                            ║
    ╚══════════════════════════════════════════════════════════════╝
    
    🎉 Installation completed successfully!
    
    📝 Configuration Required:
    
    1. Edit .env.enhanced with your credentials:
       • InfluxDB connection details
       • CTrader API credentials
       • MT5 login information
    
    2. Configure trading pairs in config/pairs.json
    
    3. Adjust risk settings in config/trading_config.json
    
    🚀 Running the System:
    
    Backtest Mode:
    set TRADING_MODE=backtest && python enhanced_main.py
    
    Real-time Mode:
    set TRADING_MODE=realtime && python enhanced_main.py
    
    Hybrid Mode:
    set TRADING_MODE=hybrid && python enhanced_main.py
    
    📊 Dashboard Access:
    http://localhost:5000
    
    📋 Available Features:
    • Real-time market data from CTrader
    • Automated trading via MT5
    • Comprehensive backtesting with vectorbt
    • InfluxDB time-series data storage
    • Interactive web dashboard
    • Excel report downloads
    
    ⚠️  Important Notes:
    • Ensure InfluxDB is running before starting
    • Configure MetaTrader 5 for API access
    • Test with demo accounts first
    • Monitor logs in the logs/ directory
    
    📖 For detailed documentation, check the README.md file.
    
    """
    print(steps)


def main():
    """Main installation function"""
    print_banner()
    
    try:
        # Step 1: Check Python version
        check_python_version()
        
        # Step 2: Install dependencies
        if not install_dependencies():
            print("❌ Failed to install dependencies")
            sys.exit(1)
        
        # Step 3: Setup directories
        setup_directories()
        
        # Step 4: Create configuration files
        create_config_files()
        
        # Step 5: Validate installation
        if not validate_installation():
            print("❌ Installation validation failed")
            sys.exit(1)
        
        # Step 6: Print next steps
        print_next_steps()
        
    except KeyboardInterrupt:
        print("\n\n❌ Installation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Installation failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
