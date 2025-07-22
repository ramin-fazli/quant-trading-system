#!/bin/bash
# Script Management System for Trading Platform
# Usage: ./run_script.sh <script_name> [args]

set -e

SCRIPT_NAME=$1
SCRIPT_ARGS="${@:2}"

# Colors for better output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_header() {
    echo -e "${BLUE}================================${NC}"
    echo -e "${BLUE} Trading System Script Manager${NC}"
    echo -e "${BLUE}================================${NC}"
}

# Check if script name is provided
if [ -z "$SCRIPT_NAME" ]; then
    print_header
    echo ""
    print_error "Script name is required!"
    echo ""
    echo "Available scripts:"
    echo "  🚀 main              - Run main trading system"
    echo "  📈 backtest          - Run backtesting engine"
    echo "  📊 dashboard         - Start web dashboard"
    echo "  💾 data-collection   - Collect market data"
    echo "  📋 portfolio-analysis - Analyze portfolio performance"
    echo "  🔧 health-check      - System health diagnostics"
    echo ""
    echo "Usage Examples:"
    echo "  ./run_script.sh main --mode live --data-provider ctrader --broker ctrader"
    echo "  ./run_script.sh backtest --symbol EURUSD --period 30"
    echo "  ./run_script.sh dashboard"
    echo "  ./run_script.sh data-collection --provider ctrader --symbols EURUSD,GBPUSD"
    echo ""
    exit 1
fi

print_header
print_status "Starting script: $SCRIPT_NAME"
print_status "Arguments: $SCRIPT_ARGS"
print_status "Timestamp: $(date)"
echo ""

# Script execution logic
case $SCRIPT_NAME in
    "main")
        print_status "🚀 Launching Main Trading System..."
        cd /app
        python -m scripts.pair_trading.main $SCRIPT_ARGS
        ;;
    "backtest")
        print_status "📈 Starting Backtest Engine..."
        cd /app
        if [ -f "scripts/backtesting/run_backtest.py" ]; then
            python scripts/backtesting/run_backtest.py $SCRIPT_ARGS
        elif [ -f "backtesting/vectorbt.py" ]; then
            python backtesting/vectorbt.py $SCRIPT_ARGS
        else
            python -m scripts.pair_trading.main --mode backtest $SCRIPT_ARGS
        fi
        ;;
    "dashboard")
        print_status "📊 Starting Web Dashboard..."
        cd /app
        if [ -f "dashboard/app.py" ]; then
            python dashboard/app.py $SCRIPT_ARGS
        elif [ -f "dashboard/dashboard_manager.py" ]; then
            python -m dashboard.dashboard_manager $SCRIPT_ARGS
        else
            print_error "Dashboard module not found!"
            exit 1
        fi
        ;;
    "data-collection")
        print_status "💾 Starting Data Collection..."
        cd /app
        if [ -f "scripts/data/collect_data.py" ]; then
            python scripts/data/collect_data.py $SCRIPT_ARGS
        else
            python -m scripts.pair_trading.main --mode data-collection $SCRIPT_ARGS
        fi
        ;;
    "portfolio-analysis")
        print_status "📋 Running Portfolio Analysis..."
        cd /app
        if [ -f "scripts/analysis/portfolio_analysis.py" ]; then
            python scripts/analysis/portfolio_analysis.py $SCRIPT_ARGS
        else
            print_warning "Portfolio analysis module not found. Running basic analysis..."
            python -c "
import sys
sys.path.append('/app')
print('📊 Portfolio Analysis Summary')
print('=' * 40)
try:
    from scripts.pair_trading.main import load_config
    config = load_config()
    print(f'Environment: {config.get(\"environment\", \"unknown\")}')
    print(f'Data Provider: {config.get(\"data_provider\", \"unknown\")}')
    print(f'Broker: {config.get(\"broker\", \"unknown\")}')
    print('✅ Configuration loaded successfully')
except Exception as e:
    print(f'❌ Error loading configuration: {e}')
"
        fi
        ;;
    "health-check")
        print_status "🔧 Running System Health Check..."
        cd /app
        python -c "
import sys
import os
import requests
from datetime import datetime

print('🏥 Trading System Health Check')
print('=' * 40)
print(f'Timestamp: {datetime.now()}')
print(f'Python Version: {sys.version}')
print(f'Working Directory: {os.getcwd()}')
print()

# Check environment variables
required_vars = ['TRADING_MODE', 'DATA_PROVIDER', 'BROKER', 'INFLUXDB_URL']
print('📋 Environment Variables:')
for var in required_vars:
    value = os.getenv(var, 'NOT SET')
    status = '✅' if value != 'NOT SET' else '❌'
    print(f'  {status} {var}: {value}')
print()

# Check InfluxDB connectivity
print('🔗 Service Connectivity:')
try:
    influxdb_url = os.getenv('INFLUXDB_URL', 'http://influxdb:8086')
    response = requests.get(f'{influxdb_url}/health', timeout=5)
    if response.status_code == 200:
        print('  ✅ InfluxDB: Connected')
    else:
        print(f'  ❌ InfluxDB: Error {response.status_code}')
except Exception as e:
    print(f'  ❌ InfluxDB: Connection failed - {e}')

# Check file system
print()
print('📁 File System:')
for path in ['/app/logs', '/app/data', '/app/scripts']:
    if os.path.exists(path):
        print(f'  ✅ {path}: Exists')
    else:
        print(f'  ❌ {path}: Missing')

print()
print('🎯 Health Check Complete!')
"
        ;;
    *)
        print_error "Unknown script: $SCRIPT_NAME"
        echo ""
        echo "Available scripts:"
        echo "  🚀 main              - Run main trading system"
        echo "  📈 backtest          - Run backtesting engine"
        echo "  📊 dashboard         - Start web dashboard"
        echo "  💾 data-collection   - Collect market data"
        echo "  📋 portfolio-analysis - Analyze portfolio performance"
        echo "  🔧 health-check      - System health diagnostics"
        echo ""
        exit 1
        ;;
esac

echo ""
print_status "Script execution completed: $SCRIPT_NAME"
print_status "Finished at: $(date)"
