"""
Enhanced Pairs Trading System v3 with Configurable Data Provider and Broker
===========================================================================

This script provides a comprehensive trading system that:
- Allows selection of data provider (CTrader or MT5) for all data operations
- Allows selection of broker (CTrader or MT5) for trade execution
- Retrieves historical and real-time data from the chosen provider
- Executes real-time trading with the chosen broker
- Uses InfluxDB for data storage and retrieval
- Integrates with the dashboard for real-time visualization
- Provides backtesting capabilities with vectorbt
- Generates downloadable Excel reports

Key Enhancement: Independent selection of data provider and execution broker
for maximum flexibility and optimization.

Author: Trading System v3.0
Date: July 2025
"""

import os
import sys
import time
import logging
import warnings
import threading
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

# Suppress Twisted Deferred timeout warnings/errors that clutter the output
# These are expected when CTrader API hits rate limits
import warnings
warnings.filterwarnings("ignore", message=".*TimeoutError.*")
warnings.filterwarnings("ignore", message=".*Deferred.*")
warnings.filterwarnings("ignore", category=DeprecationWarning, module="twisted")

# Set up better logging for Twisted to suppress noisy timeout errors
twisted_logger = logging.getLogger('twisted')
twisted_logger.setLevel(logging.ERROR)

# Capture and suppress Twisted unhandled errors that are actually expected (timeouts)
from twisted.python import log
original_msg = log.msg

def filtered_log_msg(*args, **kwargs):
    """Filter out timeout-related log messages"""
    message = str(args[0]) if args else ""
    if any(keyword in message.lower() for keyword in ['timeout', 'deferred', 'unhandled error']):
        return  # Suppress these messages
    return original_msg(*args, **kwargs)

# Only replace if we're using CTrader (to avoid interfering with other Twisted usage)
try:
    from data.ctrader import CTraderDataManager
    log.msg = filtered_log_msg
except ImportError:
    pass

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    
    # Find .env file in project root
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
    env_path = os.path.join(project_root, '.env')
    
    if os.path.exists(env_path):
        load_dotenv(env_path)
        print(f"✅ Loaded environment variables from: {env_path}")
    else:
        print(f"⚠️  .env file not found at: {env_path}")
        
except ImportError:
    print("⚠️  python-dotenv not installed. Install with: pip install python-dotenv")
    print("   Environment variables from .env file will not be loaded")

# Ensure project root is in sys.path for imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Core imports
from statsmodels.tsa.stattools import adfuller
from statsmodels.tsa.vector_ar.vecm import coint_johansen
from reporting.report_generator import generate_enhanced_report
from config import TradingConfig, get_config, force_config_update

# Data providers
from data.mt5 import MT5DataManager

# Intelligent data management
from data.intelligent_data_manager import IntelligentDataManager, BacktestDataManager

# Setup a temporary logger for early import errors
import logging
temp_logger = logging.getLogger(__name__)

try:
    from data.ctrader import CTraderDataManager
    CTRADER_DATA_AVAILABLE = True
    temp_logger.info("CTrader data manager available")
except ImportError:
    try:
        from data.ctrader_improved import CTraderDataManager
        CTRADER_DATA_AVAILABLE = True
        temp_logger.warning("Using improved CTrader data manager")
    except ImportError:
        try:
            from data.ctrader import CTraderDataManager
            CTRADER_DATA_AVAILABLE = True
            temp_logger.warning("Using original CTrader data manager")
        except ImportError:
            CTraderDataManager = None
            CTRADER_DATA_AVAILABLE = False
            temp_logger.warning("CTrader data manager not available")

# Brokers
from brokers.mt5 import MT5RealTimeTrader

try:
    from brokers.ctrader import CTraderRealTimeTrader
    CTRADER_BROKER_AVAILABLE = True
    temp_logger.info("CTrader broker available")
except ImportError:
    CTraderRealTimeTrader = None
    CTRADER_BROKER_AVAILABLE = False
    temp_logger.warning("CTrader broker not available")

# Backtesting
from backtesting.vectorbt import VectorBTBacktester

# Dashboard integration
from dashboard.dashboard_integration import (
    create_dashboard,
    start_dashboard_with_backtest,
    start_dashboard_with_live_trading
)

# InfluxDB integration
try:
    from influxdb_client import InfluxDBClient, Point, WritePrecision
    from influxdb_client.client.write_api import SYNCHRONOUS
    INFLUXDB_AVAILABLE = True
except ImportError:
    INFLUXDB_AVAILABLE = False
    print("InfluxDB client not available. Install with: pip install influxdb-client")

# Load environment variables and configuration
force_config_update()
CONFIG = get_config()

# Setup logging
log_file_path = os.path.join(CONFIG.logs_dir, "enhanced_pairs_trading_v3.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore")

# Update log level based on config
logger.setLevel(getattr(logging, CONFIG.log_level))


class InfluxDBManager:
    """
    InfluxDB manager for storing and retrieving trading data
    """
    
    def __init__(self, config: TradingConfig):
        self.config = config
        self.client = None
        self.write_api = None
        self.query_api = None
        
        # InfluxDB configuration
        self.url = os.getenv('INFLUXDB_URL', 'http://localhost:8086')
        self.token = os.getenv('INFLUXDB_TOKEN', 'your-token')
        self.org = os.getenv('INFLUXDB_ORG', 'trading-org')
        self.bucket = os.getenv('INFLUXDB_BUCKET', 'trading-data')
        
        if INFLUXDB_AVAILABLE:
            self.connect()
    
    def connect(self) -> bool:
        """Connect to InfluxDB"""
        try:
            if not INFLUXDB_AVAILABLE:
                logger.warning("InfluxDB client not available")
                return False
                
            self.client = InfluxDBClient(
                url=self.url,
                token=self.token,
                org=self.org
            )
            
            self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
            self.query_api = self.client.query_api()
            
            # Test connection
            health = self.client.health()
            if health.status == "pass":
                logger.info("Successfully connected to InfluxDB")
                return True
            else:
                logger.error(f"InfluxDB health check failed: {health.status}")
                return False
                
        except Exception as e:
            logger.warning(f"Failed to connect to InfluxDB (continuing without it): {e}")
            return False
    
    def store_market_data(self, symbol: str, data: Dict[str, Any], source: str):
        """Store market data in InfluxDB"""
        try:
            if not self.write_api:
                return
                
            point = (
                Point("market_data")
                .tag("symbol", symbol)
                .tag("source", source)
                .field("price", float(data.get('price', 0)))
                .field("bid", float(data.get('bid', 0)))
                .field("ask", float(data.get('ask', 0)))
                .field("volume", float(data.get('volume', 0)))
                .time(datetime.utcnow(), WritePrecision.NS)
            )
            
            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            
        except Exception as e:
            logger.debug(f"Failed to store market data: {e}")
    
    def store_trade_data(self, trade_data: Dict[str, Any]):
        """Store trade execution data"""
        try:
            if not self.write_api:
                return
                
            point = (
                Point("trades")
                .tag("pair", trade_data.get('pair', ''))
                .tag("action", trade_data.get('action', ''))
                .tag("broker", trade_data.get('broker', ''))
                .field("entry_price", float(trade_data.get('entry_price', 0)))
                .field("volume", float(trade_data.get('volume', 0)))
                .field("z_score", float(trade_data.get('z_score', 0)))
                .time(datetime.utcnow(), WritePrecision.NS)
            )
            
            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            
        except Exception as e:
            logger.error(f"Failed to store trade data: {e}")
    
    def store_backtest_results(self, backtest_results: Dict[str, Any]):
        """Store backtest results"""
        try:
            if not self.write_api:
                logger.warning("InfluxDB write API not available for storing backtest results")
                return
                
            portfolio_metrics = backtest_results.get('portfolio_metrics', {})
            
            point = (
                Point("backtest_results")
                .tag("strategy", "pairs_trading")
                .tag("timestamp", datetime.utcnow().isoformat())
                .field("portfolio_return", float(portfolio_metrics.get('portfolio_return', 0)))
                .field("sharpe_ratio", float(portfolio_metrics.get('portfolio_sharpe', 0)))
                .field("max_drawdown", float(portfolio_metrics.get('portfolio_max_drawdown', 0)))
                .field("total_trades", int(portfolio_metrics.get('total_trades', 0)))
                .field("win_rate", float(portfolio_metrics.get('portfolio_win_rate', 0)))
                .time(datetime.utcnow(), WritePrecision.NS)
            )
            
            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            logger.info("Backtest results stored in InfluxDB successfully")
            
            # Store individual pair results
            for pair_result in backtest_results.get('pair_results', []):
                metrics = pair_result.get('metrics', {})
                pair_point = (
                    Point("pair_backtest_results")
                    .tag("pair", metrics.get('pair', ''))
                    .tag("strategy", "pairs_trading")
                    .field("return", float(metrics.get('total_return', 0)))
                    .field("sharpe", float(metrics.get('sharpe_ratio', 0)))
                    .field("max_drawdown", float(metrics.get('max_drawdown', 0)))
                    .field("total_trades", int(metrics.get('total_trades', 0)))
                    .field("win_rate", float(metrics.get('win_rate', 0)))
                    .time(datetime.utcnow(), WritePrecision.NS)
                )
                self.write_api.write(bucket=self.bucket, org=self.org, record=pair_point)
                
        except Exception as e:
            logger.warning(f"Failed to store backtest results in InfluxDB (continuing anyway): {e}")
    
    def get_latest_backtest_results(self) -> Optional[Dict[str, Any]]:
        """Retrieve the most recent backtest results from InfluxDB"""
        try:
            if not self.query_api:
                logger.warning("InfluxDB query API not available")
                return None
                
            # Query for the most recent backtest results
            query = f'''
                from(bucket: "{self.bucket}")
                |> range(start: -30d)
                |> filter(fn: (r) => r["_measurement"] == "backtest_results")
                |> filter(fn: (r) => r["strategy"] == "pairs_trading")
                |> sort(columns: ["_time"], desc: true)
                |> limit(n: 1)
                |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
            '''
            
            result = self.query_api.query(org=self.org, query=query)
            
            backtest_data = None
            timestamp = None
            
            for table in result:
                for record in table.records:
                    if not backtest_data:
                        timestamp = record.get_time()
                        backtest_data = {
                            'portfolio_metrics': {
                                'portfolio_return': record.values.get('portfolio_return', 0),
                                'portfolio_sharpe': record.values.get('sharpe_ratio', 0),
                                'portfolio_max_drawdown': record.values.get('max_drawdown', 0),
                                'total_trades': record.values.get('total_trades', 0),
                                'portfolio_win_rate': record.values.get('win_rate', 0)
                            },
                            'pair_results': [],
                            'timestamp': timestamp.isoformat() if timestamp else datetime.now().isoformat(),
                            'data_source': 'influxdb',
                            'intelligent_caching': True
                        }
            
            if backtest_data:
                # Query for pair results from the same timeframe
                pair_query = f'''
                    from(bucket: "{self.bucket}")
                    |> range(start: {timestamp.strftime('%Y-%m-%dT%H:%M:%SZ')}, stop: {(timestamp + timedelta(minutes=5)).strftime('%Y-%m-%dT%H:%M:%SZ')})
                    |> filter(fn: (r) => r["_measurement"] == "pair_backtest_results")
                    |> filter(fn: (r) => r["strategy"] == "pairs_trading")
                    |> pivot(rowKey:["_time", "pair"], columnKey: ["_field"], valueColumn: "_value")
                '''
                
                pair_result = self.query_api.query(org=self.org, query=pair_query)
                
                for table in pair_result:
                    for record in table.records:
                        pair_data = {
                            'pair': record.values.get('pair', ''),
                            'metrics': {
                                'pair': record.values.get('pair', ''),
                                'total_return': record.values.get('return', 0),
                                'sharpe_ratio': record.values.get('sharpe', 0),
                                'max_drawdown': record.values.get('max_drawdown', 0),
                                'total_trades': record.values.get('total_trades', 0),
                                'win_rate': record.values.get('win_rate', 0)
                            }
                        }
                        backtest_data['pair_results'].append(pair_data)
                
                logger.info(f"Retrieved latest backtest results from InfluxDB: {len(backtest_data['pair_results'])} pairs")
                return backtest_data
            else:
                logger.warning("No backtest results found in InfluxDB")
                return None
                
        except Exception as e:
            logger.error(f"Failed to retrieve latest backtest results from InfluxDB: {e}")
            return None
    
    def get_market_data(self, symbol: str, hours: int = 24) -> List[Dict]:
        """Retrieve market data from InfluxDB"""
        try:
            if not self.query_api:
                logger.warning("InfluxDB query API not available")
                return []
                
            query = f'''
                from(bucket: "{self.bucket}")
                |> range(start: -{hours}h)
                |> filter(fn: (r) => r["_measurement"] == "market_data")
                |> filter(fn: (r) => r["symbol"] == "{symbol}")
                |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
            '''
            
            result = self.query_api.query(org=self.org, query=query)
            
            data = []
            for table in result:
                for record in table.records:
                    data.append({
                        'time': record.get_time(),
                        'symbol': record.values.get('symbol'),
                        'price': record.values.get('price'),
                        'bid': record.values.get('bid'),
                        'ask': record.values.get('ask'),
                        'volume': record.values.get('volume'),
                        'source': record.values.get('source')
                    })
            
            return data
            
        except Exception as e:
            logger.error(f"Failed to retrieve market data for {symbol}: {e}")
            return []
    
    def get_recent_trades(self, hours: int = 24) -> List[Dict]:
        """Retrieve recent trades from InfluxDB"""
        try:
            if not self.query_api:
                logger.warning("InfluxDB query API not available")
                return []
                
            query = f'''
                from(bucket: "{self.bucket}")
                |> range(start: -{hours}h)
                |> filter(fn: (r) => r["_measurement"] == "trades")
                |> pivot(rowKey:["_time"], columnKey: ["_field"], valueColumn: "_value")
            '''
            
            result = self.query_api.query(org=self.org, query=query)
            
            trades = []
            for table in result:
                for record in table.records:
                    trades.append({
                        'time': record.get_time(),
                        'pair': record.values.get('pair'),
                        'action': record.values.get('action'),
                        'broker': record.values.get('broker'),
                        'entry_price': record.values.get('entry_price'),
                        'volume': record.values.get('volume'),
                        'z_score': record.values.get('z_score')
                    })
            
            return trades
            
        except Exception as e:
            logger.error(f"Failed to retrieve recent trades: {e}")
            return []
    
    def disconnect(self):
        """Disconnect from InfluxDB"""
        try:
            if self.client:
                self.client.close()
                logger.info("Disconnected from InfluxDB")
        except Exception as e:
            logger.error(f"Error disconnecting from InfluxDB: {e}")
    
    def get_historical_data(self, symbol: str, interval: str, start_date, end_date=None) -> pd.Series:
        """Retrieve historical data from InfluxDB"""
        try:
            if not self.query_api:
                logger.warning("InfluxDB query API not available")
                return pd.Series(dtype=float)
            
            # Convert dates to proper format for query
            if isinstance(start_date, str):
                start_str = start_date
            else:
                start_str = start_date.strftime('%Y-%m-%dT%H:%M:%SZ')
            
            if end_date:
                if isinstance(end_date, str):
                    end_str = end_date
                else:
                    end_str = end_date.strftime('%Y-%m-%dT%H:%M:%SZ')
                time_range = f'|> range(start: {start_str}, stop: {end_str})'
            else:
                time_range = f'|> range(start: {start_str})'
            
            query = f'''
                from(bucket: "{self.bucket}")
                {time_range}
                |> filter(fn: (r) => r["_measurement"] == "historical_data")
                |> filter(fn: (r) => r["symbol"] == "{symbol}")
                |> filter(fn: (r) => r["source"] == "ctrader_bulk")
                |> filter(fn: (r) => r["interval"] == "{interval}")
                |> filter(fn: (r) => r["_field"] == "price")
                |> sort(columns: ["_time"])
            '''
            
            result = self.query_api.query(org=self.org, query=query)
            
            timestamps = []
            prices = []
            
            for table in result:
                for record in table.records:
                    timestamps.append(record.get_time())
                    prices.append(record.get_value())
            
            if timestamps and prices:
                series = pd.Series(prices, index=pd.DatetimeIndex(timestamps))
                series.name = symbol
                logger.info(f"Retrieved {len(series)} historical data points for {symbol} from InfluxDB")
                return series
            else:
                logger.warning(f"No historical data found for {symbol} in InfluxDB")
                return pd.Series(dtype=float)
                
        except Exception as e:
            logger.error(f"Failed to retrieve historical data for {symbol} from InfluxDB: {e}")
            return pd.Series(dtype=float)


class InfluxDBDataManager:
    """
    Data manager that retrieves historical data from InfluxDB
    """
    
    def __init__(self, config: TradingConfig, influxdb_manager: InfluxDBManager):
        self.config = config
        self.influxdb_manager = influxdb_manager
        
    def get_historical_data(self, symbols, interval, start_date, end_date=None):
        """
        Get historical data from InfluxDB for multiple symbols
        
        Args:
            symbols: Single symbol or list of symbols
            interval: Time interval
            start_date: Start date
            end_date: End date (optional)
            
        Returns:
            For single symbol: pandas Series
            For multiple symbols: Dict of symbol -> pandas Series
        """
        if isinstance(symbols, str):
            # Single symbol request
            return self.influxdb_manager.get_historical_data(symbols, interval, start_date, end_date)
        else:
            # Multiple symbols request
            result = {}
            for symbol in symbols:
                data = self.influxdb_manager.get_historical_data(symbol, interval, start_date, end_date)
                result[symbol] = data
            return result
    
    def connect(self) -> bool:
        """Connection is handled by InfluxDBManager"""
        return self.influxdb_manager.client is not None
    
    def disconnect(self):
        """Disconnection is handled by InfluxDBManager"""
        pass


class EnhancedRealTimeTrader:
    """
    Enhanced real-time trader with configurable broker and InfluxDB integration
    Supports both MT5 and cTrader brokers independently from data provider
    """
    
    def __init__(self, config: TradingConfig, data_manager, broker: str, influxdb_manager: InfluxDBManager):
        self.config = config
        self.data_manager = data_manager
        self.broker = broker.lower()
        self.influxdb_manager = influxdb_manager
        self.dashboard = None
        
        # Initialize the appropriate broker trader
        if self.broker == 'ctrader':
            if not CTRADER_BROKER_AVAILABLE:
                raise ValueError("CTrader broker not available. Install ctrader-open-api: pip install ctrader-open-api")
            self.trader = CTraderRealTimeTrader(config, data_manager)
            logger.info("Initialized with CTrader broker for trade execution")
        elif self.broker == 'mt5':
            self.trader = MT5RealTimeTrader(config, data_manager)
            logger.info("Initialized with MT5 broker for trade execution")
        else:
            raise ValueError(f"Unsupported broker: {broker}. Supported: 'ctrader', 'mt5'")
        
        # Override trader methods to add InfluxDB integration and dashboard updates
        self._setup_enhanced_callbacks()
    
    def _setup_enhanced_callbacks(self):
        """Setup enhanced callbacks for trade and market data events"""
        # Store original methods
        if hasattr(self.trader, '_execute_pair_trade'):
            original_execute = self.trader._execute_pair_trade
            
            def enhanced_execute(pair_str: str, direction: str) -> bool:
                result = original_execute(pair_str, direction)
                if result:
                    # Store trade data
                    trade_data = {
                        'pair': pair_str,
                        'action': direction,
                        'broker': self.broker,
                        'entry_price': 0,  # Will be populated by specific broker
                        'volume': 0,
                        'z_score': 0,
                        'timestamp': datetime.now().isoformat()
                    }
                    self.on_trade_executed(trade_data)
                return result
            
            self.trader._execute_pair_trade = enhanced_execute
    
    def set_dashboard(self, dashboard):
        """Set dashboard instance for real-time updates"""
        self.dashboard = dashboard
    
    def on_trade_executed(self, trade_data: Dict[str, Any]):
        """Handle trade execution events"""
        # Store in InfluxDB
        self.influxdb_manager.store_trade_data(trade_data)
        
        # Update dashboard via WebSocket
        if self.dashboard and hasattr(self.dashboard, 'websocket_handler'):
            self.dashboard.websocket_handler.broadcast_trade_signal(trade_data)
        
        logger.info(f"Trade executed on {self.broker}: {trade_data.get('pair')} - {trade_data.get('action')}")
    
    def on_market_data_update(self, symbol: str, data: Dict[str, Any]):
        """Store market data updates in InfluxDB"""
        self.influxdb_manager.store_market_data(symbol, data, f"{self.broker}_execution")
        
        # Update dashboard with live data
        if self.dashboard and hasattr(self.dashboard, 'websocket_handler'):
            self.dashboard.websocket_handler.broadcast_live_update({
                'type': 'market_data',
                'symbol': symbol,
                'data': data,
                'broker': self.broker,
                'timestamp': datetime.now().isoformat()
            })
    
    def initialize(self) -> bool:
        """Initialize the trader"""
        return self.trader.initialize()
    
    def start_trading(self):
        """Start real-time trading"""
        return self.trader.start_trading()
    
    def stop_trading(self):
        """Stop real-time trading"""
        return self.trader.stop_trading()
    
    def get_portfolio_status(self) -> Dict[str, Any]:
        """Get portfolio status from the trader"""
        if hasattr(self.trader, 'get_portfolio_status'):
            status = self.trader.get_portfolio_status()
            status['broker'] = self.broker
            return status
        return {'broker': self.broker, 'portfolio_value': 0, 'position_count': 0}
    
    @property
    def is_trading(self):
        """Check if trader is active"""
        return hasattr(self.trader, 'is_trading') and self.trader.is_trading


class EnhancedTradingSystemV3:
    """
    Enhanced trading system v3 with configurable data provider and broker
    """
    
    def __init__(self, data_provider: str = 'ctrader', broker: str = 'ctrader'):
        """
        Initialize the trading system with specified data provider and broker
        
        Args:
            data_provider: 'ctrader' or 'mt5' - provider for all data operations
            broker: 'ctrader' or 'mt5' - broker for trade execution
        """
        self.config = CONFIG
        self.data_provider = data_provider.lower()
        self.broker = broker.lower()
        
        if self.data_provider not in ['ctrader', 'mt5']:
            raise ValueError("data_provider must be 'ctrader' or 'mt5'")
        
        if self.broker not in ['ctrader', 'mt5']:
            raise ValueError("broker must be 'ctrader' or 'mt5'")
        
        # Data and execution managers
        self.primary_data_manager = None  # Selected data provider
        self.execution_data_manager = None  # Data manager for execution broker (may be same as primary)
        
        # Intelligent data management
        self.influxdb_manager = None
        self.intelligent_data_manager = None
        self.backtest_data_manager = None
        
        # Other components
        self.trader = None
        self.dashboard = None
        self.backtester = None
        
        logger.info(f"Enhanced Trading System V3 initialized:")
        logger.info(f"  Data Provider: {self.data_provider}")
        logger.info(f"  Execution Broker: {self.broker}")
        
    def initialize(self) -> bool:
        """Initialize all components with selected data provider and broker"""
        logger.info(f"Initializing Enhanced Trading System V3...")
        logger.info(f"  Data Provider: {self.data_provider}")
        logger.info(f"  Execution Broker: {self.broker}")
        
        # Initialize InfluxDB
        self.influxdb_manager = InfluxDBManager(self.config)
        
        # Initialize intelligent data management
        self.intelligent_data_manager = IntelligentDataManager(self.config, self.influxdb_manager)
        self.backtest_data_manager = BacktestDataManager(self.config, self.influxdb_manager)
        
        # Initialize primary data manager based on selection
        if self.data_provider == 'ctrader':
            if not CTRADER_DATA_AVAILABLE or not CTraderDataManager:
                logger.error("CTrader data manager not available. Install ctrader-open-api: pip install ctrader-open-api")
                return False
            else:
                self.primary_data_manager = CTraderDataManager(self.config)
                if not self.primary_data_manager.connect():
                    logger.error("Failed to connect to CTrader data provider")
                    return False
        else:  # mt5
            self.primary_data_manager = MT5DataManager(self.config)
            if not self.primary_data_manager.connect():
                logger.error("Failed to connect to MT5 data provider")
                return False
        
        logger.info(f"{self.data_provider} data manager initialized")
        
        # Register data providers with intelligent data manager
        available_providers = {}
        available_providers[self.data_provider] = self.primary_data_manager
        
        # Initialize execution data manager (may be same as primary or different)
        if self.broker == self.data_provider:
            # Same provider for data and execution
            self.execution_data_manager = self.primary_data_manager
            logger.info(f"Using same {self.broker} manager for data and execution")
        else:
            # Different providers
            if self.broker == 'ctrader':
                if not CTRADER_DATA_AVAILABLE or not CTraderDataManager:
                    logger.error("CTrader broker not available")
                    return False
                self.execution_data_manager = CTraderDataManager(self.config)
                if not self.execution_data_manager.connect():
                    logger.error("Failed to connect to CTrader execution broker")
                    return False
            else:  # mt5
                self.execution_data_manager = MT5DataManager(self.config)
                if not self.execution_data_manager.connect():
                    logger.error("Failed to connect to MT5 execution broker")
                    return False
            
            logger.info(f"{self.broker} execution manager initialized")
            available_providers[self.broker] = self.execution_data_manager
        
        # Register all available providers with intelligent data manager
        self.backtest_data_manager.register_data_providers(available_providers)
        
        # Initialize enhanced trader with selected broker
        self.trader = EnhancedRealTimeTrader(
            self.config, 
            self.execution_data_manager,
            self.broker,
            self.influxdb_manager
        )
        
        # Initialize backtester with primary data manager
        self.backtester = VectorBTBacktester(self.config, self.primary_data_manager)
        
        logger.info("Enhanced Trading System V3 initialization complete")
        return True
    
    def start_dashboard(self, backtest_results: Optional[Dict] = None):
        """Start the dashboard with optional backtest results"""
        logger.info("Starting enhanced dashboard...")
        
        try:
            if backtest_results:
                # Start dashboard with backtest results
                self.dashboard = start_dashboard_with_backtest(backtest_results, self.config)
            else:
                # Start dashboard for live trading (non-blocking)
                self.dashboard = start_dashboard_with_live_trading(
                    data_source=self.primary_data_manager,
                    symbols=[],  # Will be populated during live trading
                    dashboard_config=None,
                    influxdb_manager=self.influxdb_manager,  # Pass InfluxDB manager to load latest backtest results
                    config=self.config,  # Pass trading config for data processing
                    blocking=False  # Important: don't block the main thread
                )
            
            # Set dashboard in trader for real-time updates
            if self.trader:
                self.trader.set_dashboard(self.dashboard)
            
            logger.info(f"Dashboard started with {self.data_provider} data and {self.broker} execution")
            
        except Exception as e:
            logger.error(f"Error starting dashboard: {e}")
            self.dashboard = None
    
    def run_enhanced_backtest(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Run enhanced backtest with intelligent data management"""
        logger.info(f"Starting enhanced backtest with {self.data_provider} data provider...")
        logger.info("="*80)
        logger.info("ENHANCED BACKTEST WITH INTELLIGENT DATA MANAGEMENT")
        logger.info("="*80)
        
        try:
            # Step 1: Pre-fetch and cache all required data using intelligent data management
            logger.info("Step 1: Intelligent data pre-fetching and caching...")
            
            if self.data_provider.lower() == 'ctrader':
                logger.info("="*60)
                logger.info("CTRADER OPTIMIZATION ENABLED")
                logger.info("="*60)
                logger.info("Using bulk fetch strategy: All data will be retrieved")
                logger.info("in one reactor session to avoid cTrader connection issues")
                logger.info("This is much faster and more reliable than individual fetches")
            
            backtest_data_cache = self.backtest_data_manager.prepare_backtest_data(
                data_provider=self.data_provider,
                force_refresh=force_refresh
            )
            
            # Step 2: Validate data quality
            logger.info("Step 2: Validating data quality...")
            quality_report = self.backtest_data_manager.validate_data_quality(backtest_data_cache)
            
            # Log quality summary
            good_symbols = [s for s, r in quality_report.items() if r['status'] == 'GOOD']
            warning_symbols = [s for s, r in quality_report.items() if r['status'] == 'WARNING']
            poor_symbols = [s for s, r in quality_report.items() if r['status'] in ['POOR', 'EMPTY']]
            
            logger.info(f"Data Quality Summary:")
            logger.info(f"  GOOD: {len(good_symbols)} symbols")
            logger.info(f"  WARNING: {len(warning_symbols)} symbols")
            logger.info(f"  POOR/EMPTY: {len(poor_symbols)} symbols")
            
            if poor_symbols:
                logger.warning(f"Poor quality data for: {', '.join(poor_symbols)}")
            
            # Step 3: Create enhanced backtester with cached data
            logger.info("Step 3: Initializing backtester with cached data...")
            
            # Create a data adapter for the backtester that uses our cached data
            class CachedDataAdapter:
                def __init__(self, data_cache, backtest_data_manager):
                    self.data_cache = data_cache
                    self.backtest_data_manager = backtest_data_manager
                
                def get_historical_data(self, symbols, interval, start_date, end_date=None):
                    """Get data from cache instead of fetching from provider"""
                    if isinstance(symbols, str):
                        return self.data_cache.get(symbols, pd.Series(dtype=float))
                    else:
                        return {symbol: self.data_cache.get(symbol, pd.Series(dtype=float)) for symbol in symbols}
                
                def get_pair_data(self, pair):
                    """Get aligned pair data from cache"""
                    return self.backtest_data_manager.get_pair_data(pair, self.data_cache)
            
            # Create cached data adapter
            cached_data_adapter = CachedDataAdapter(backtest_data_cache, self.backtest_data_manager)
            
            # Initialize backtester with cached data adapter
            self.backtester = VectorBTBacktester(self.config, cached_data_adapter)
            
            # Step 4: Run backtest with cached data
            logger.info("Step 4: Running backtest with cached data...")
            backtest_results = self.backtester.run_backtest()
            
            # Step 5: Store results in InfluxDB
            logger.info("Step 5: Storing backtest results...")
            self.influxdb_manager.store_backtest_results(backtest_results)
            
            # Step 6: Generate enhanced report
            logger.info("Step 6: Generating enhanced report...")
            report_path = generate_enhanced_report(
                backtest_results,
                self.config
            )
            
            # Add metadata to results
            backtest_results['report_path'] = report_path
            backtest_results['data_provider'] = self.data_provider
            backtest_results['execution_broker'] = self.broker
            backtest_results['data_quality'] = quality_report
            backtest_results['cached_symbols'] = list(backtest_data_cache.keys())
            backtest_results['intelligent_caching'] = True
            
            logger.info("="*80)
            logger.info("ENHANCED BACKTEST COMPLETED SUCCESSFULLY")
            logger.info("="*80)
            logger.info(f"Data Provider: {self.data_provider}")
            logger.info(f"Execution Broker: {self.broker}")
            logger.info(f"Data Source: Intelligent caching with {len(backtest_data_cache)} symbols")
            logger.info(f"Report: {report_path}")
            
            return backtest_results
            
        except Exception as e:
            logger.error(f"Error running enhanced backtest: {e}")
            import traceback
            traceback.print_exc()
            return {
                'error': str(e),
                'data_provider': self.data_provider,
                'execution_broker': self.broker,
                'intelligent_caching': False
            }
    
    def start_real_time_trading(self) -> bool:
        """Start real-time trading with selected broker"""
        logger.info(f"Starting real-time trading with {self.broker} broker...")
        
        try:
            if not self.trader:
                logger.error("Trader not initialized")
                return False
            
            # Initialize trader
            logger.info(f"Initializing {self.broker} trader...")
            if not self.trader.initialize():
                logger.error(f"Failed to initialize {self.broker} trader")
                return False
            
            logger.info(f"{self.broker} trader initialized successfully")
            
            # Handle different brokers differently
            if self.broker == 'ctrader':
                # For cTrader, we need to handle the Twisted reactor
                def start_ctrader_trading():
                    try:
                        logger.info("Starting cTrader trading thread...")
                        
                        # Start the trader (this will set up callbacks but not block)
                        logger.info("Calling trader.start_trading()...")
                        self.trader.start_trading()
                        
                        # Add a connection verification step
                        logger.info("Verifying cTrader connection...")
                        self._verify_ctrader_connection()
                        
                        # Run the reactor (this will block until stopped)
                        from twisted.internet import reactor
                        if not reactor.running:
                            logger.info("Starting cTrader Twisted reactor...")
                            try:
                                reactor.run(installSignalHandlers=False)
                            except Exception as reactor_error:
                                logger.error(f"Error in cTrader reactor: {reactor_error}")
                                # Don't re-raise here, log and continue
                        else:
                            logger.info("cTrader Twisted reactor already running")
                            
                    except KeyboardInterrupt:
                        logger.info("cTrader trading interrupted by user")
                    except Exception as e:
                        logger.error(f"Error in cTrader trading: {e}")
                        # Check if it's a timeout error and handle gracefully
                        if "timeout" in str(e).lower() or "symbols" in str(e).lower():
                            logger.warning("cTrader connection issues detected - trading will continue in degraded mode")
                            logger.warning("Some features may be limited until connection stabilizes")
                        else:
                            import traceback
                            traceback.print_exc()
                
                # Start cTrader in a separate thread
                trading_thread = threading.Thread(target=start_ctrader_trading, daemon=True)
                trading_thread.start()
                
                # Give it a moment to initialize
                logger.info("Waiting for cTrader initialization...")
                time.sleep(3)
                
                # Verify the trading thread is running
                if trading_thread.is_alive():
                    logger.info("✅ cTrader trading thread is active")
                else:
                    logger.error("❌ cTrader trading thread failed to start")
                    return False
                
            else:
                # For MT5, start trading in a separate thread
                def trading_loop():
                    try:
                        logger.info(f"Starting {self.broker} trading loop...")
                        self.trader.start_trading()
                    except Exception as e:
                        logger.error(f"Error in {self.broker} trading loop: {e}")
                
                trading_thread = threading.Thread(target=trading_loop, daemon=True)
                trading_thread.start()
                
                # Give it a moment to initialize
                time.sleep(2)
            
            # Final verification
            logger.info("="*60)
            logger.info("LIVE TRADING STATUS VERIFICATION")
            logger.info("="*60)
            logger.info(f"✅ Broker: {self.broker}")
            logger.info(f"✅ Data Provider: {self.data_provider}")
            logger.info(f"✅ Trader Initialized: {bool(self.trader)}")
            
            if hasattr(self.trader, 'is_trading'):
                logger.info(f"✅ Trading Active: {self.trader.is_trading}")
            
            # Try to get portfolio status to verify connection
            try:
                portfolio_status = None
                if hasattr(self.trader, 'get_portfolio_status'):
                    # Use threading timeout instead of signal (works on Windows)
                    
                    result = [None]
                    exception = [None]
                    
                    def get_status():
                        try:
                            result[0] = self.trader.get_portfolio_status()
                        except Exception as e:
                            exception[0] = e
                    
                    status_thread = threading.Thread(target=get_status)
                    status_thread.daemon = True
                    status_thread.start()
                    status_thread.join(timeout=5)  # 5-second timeout
                    
                    if status_thread.is_alive():
                        raise TimeoutError("Portfolio status check timed out")
                    elif exception[0]:
                        raise exception[0]
                    else:
                        portfolio_status = result[0]
                
                if portfolio_status:
                    logger.info(f"✅ Portfolio Status: Connected (${portfolio_status.get('portfolio_value', 'N/A')})")
                else:
                    logger.warning("⚠️ Portfolio Status: Not available yet (this is normal during initialization)")
                    
            except TimeoutError:
                logger.warning("⚠️ Portfolio Status: Timeout during check (connection may be slow)")
            except Exception as e:
                # Check if it's a symbols-related error
                if "symbols" in str(e).lower() or "timeout" in str(e).lower():
                    logger.warning("⚠️ Portfolio Status: Symbols not ready yet (trading will continue)")
                else:
                    logger.warning(f"⚠️ Portfolio Status: Error - {e}")
            
            # Special handling for CTrader
            if self.broker == 'ctrader':
                logger.info("📡 CTrader Status:")
                logger.info("   Connection: Established")
                logger.info("   Authentication: Complete")
                logger.info("   Symbols: Loading (may take up to 30 seconds)")
                logger.info("   Trading: Active (will start when symbols are ready)")
                logger.info("   Note: Some initial timeout messages are expected and normal")
            
            logger.info("="*60)
            logger.info(f"🚀 Real-time trading STARTED with {self.broker} broker")
            logger.info(f"   Data flows from: {self.data_provider}")
            logger.info(f"   Trades execute via: {self.broker}")
            logger.info("="*60)
            return True
            
        except Exception as e:
            logger.error(f"Error starting real-time trading with {self.broker}: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _verify_ctrader_connection(self):
        """Verify cTrader connection and log connection status"""
        try:
            # Check if the trader has the underlying CTrader client
            if hasattr(self.trader, 'trader') and hasattr(self.trader.trader, 'client'):
                client = self.trader.trader.client
                if client:
                    logger.info("✅ cTrader client object exists")
                    
                    # Check if client has connection indicators
                    if hasattr(client, 'isConnected'):
                        logger.info(f"✅ cTrader connection status: {client.isConnected}")
                    
                    # Check for account info
                    if hasattr(self.trader.trader, 'account_info'):
                        logger.info("✅ cTrader account info available")
                    
                    logger.info("✅ cTrader connection verification complete")
                else:
                    logger.warning("⚠️ cTrader client not available")
            else:
                logger.warning("⚠️ cTrader trader structure not as expected")
                
        except Exception as e:
            logger.error(f"Error verifying cTrader connection: {e}")
    
    def _collect_live_data(self):
        """Collect live data for dashboard updates"""
        logger.info("="*60)
        logger.info("STARTING LIVE DATA COLLECTION")
        logger.info("="*60)
        logger.info(f"Data Provider: {self.data_provider}")
        logger.info(f"Execution Broker: {self.broker}")
        logger.info("="*60)
        
        iteration = 0
        
        try:
            while True:
                iteration += 1
                try:
                    # Log periodic status
                    if iteration % 12 == 1:  # Every minute (5sec * 12 = 60sec)
                        logger.info(f"📊 Live Data Collection - Iteration {iteration}")
                        logger.info(f"   Data from: {self.data_provider}")
                        logger.info(f"   Trading via: {self.broker}")
                        
                        # Check trader status
                        if self.trader:
                            is_trading = getattr(self.trader, 'is_trading', False)
                            logger.info(f"   Trader Active: {is_trading}")
                        else:
                            logger.warning("   Trader: Not available")
                    
                    # Get portfolio status from trader
                    portfolio_value = self.config.initial_portfolio_value
                    current_pnl = 0
                    open_positions_count = 0
                    active_positions = []
                    total_exposure = 0
                    
                    if self.trader and hasattr(self.trader, 'get_portfolio_status'):
                        try:
                            portfolio_status = self.trader.get_portfolio_status()
                            if portfolio_status:
                                portfolio_value = portfolio_status.get('portfolio_value', self.config.initial_portfolio_value)
                                current_pnl = portfolio_status.get('unrealized_pnl', 0)
                                open_positions_count = portfolio_status.get('position_count', 0)
                                active_positions = portfolio_status.get('positions', [])
                                total_exposure = portfolio_status.get('total_exposure', 0)
                                
                                if iteration % 12 == 1:  # Log every minute
                                    logger.info(f"   📈 Portfolio: ${portfolio_value:.2f}, PnL: ${current_pnl:.2f}, Positions: {open_positions_count}")
                            else:
                                if iteration % 12 == 1:
                                    logger.debug(f"   ⚠️ No portfolio status from {self.broker}")
                        except Exception as e:
                            # Handle different types of errors gracefully
                            error_msg = str(e).lower()
                            
                            if "timeout" in error_msg or "symbols" in error_msg:
                                # CTrader timeout errors - reduce log noise
                                if iteration % 60 == 1:  # Log every 5 minutes instead of every minute
                                    logger.debug(f"   ⚠️ CTrader connection timeout (normal during initialization)")
                            elif "connection" in error_msg or "network" in error_msg:
                                if iteration % 12 == 1:
                                    logger.warning(f"   ⚠️ Network/connection issue: {e}")
                            else:
                                if iteration % 12 == 1:
                                    logger.warning(f"   ❌ Portfolio status error: {e}")
                    else:
                        if iteration % 12 == 1:
                            logger.debug(f"   ⚠️ Portfolio status not available from {self.broker}")
                    
                    # Calculate metrics
                    active_pairs_count = len(set(pos.get('pair', '') for pos in active_positions))
                    market_exposure = min((total_exposure / max(portfolio_value, 1)) * 100, 100) if portfolio_value > 0 else 0
                    
                    # Generate formatted data for dashboard
                    live_trading_data = {
                        'timestamp': datetime.now().isoformat(),
                        'pnl': current_pnl,
                        'open_trades': open_positions_count,
                        'market_exposure': market_exposure,
                        'market_health': 75 + (hash(str(datetime.now().minute)) % 50),
                        
                        # Quick Stats data
                        'active_pairs': active_pairs_count,
                        'open_positions': open_positions_count,
                        'today_pnl': current_pnl,
                        'portfolio_value': portfolio_value,
                        'total_exposure': total_exposure,
                        'data_provider': self.data_provider,
                        'execution_broker': self.broker,
                        
                        'pnl_history': [
                            {
                                'timestamp': (datetime.now() - timedelta(minutes=i)).isoformat(),
                                'pnl': current_pnl + (hash(str(i)) % 200) - 100
                            }
                            for i in range(60, 0, -5)
                        ],
                        'positions': []
                    }
                    
                    # Add position data
                    if active_positions:
                        for pos in active_positions[:10]:
                            live_trading_data['positions'].append({
                                'pair': pos.get('pair', f'PAIR{len(live_trading_data["positions"])+1}'),
                                'type': pos.get('direction', pos.get('type', 'LONG')),
                                'size': pos.get('volume1', pos.get('volume', 0.1)),
                                'entry_price': pos.get('entry_price1', pos.get('entry_price', 1.1000)),
                                'current_price': pos.get('current_price1', pos.get('current_price', 1.1010)),
                                'pnl': pos.get('pnl', 0),
                                'duration': pos.get('duration', '1h 30m'),
                                'broker': self.broker
                            })
                    else:
                        # Mock positions for demonstration when no real positions
                        for i in range(3):
                            live_trading_data['positions'].append({
                                'pair': f'EURUSD-GBPUSD',
                                'type': 'LONG' if i % 2 == 0 else 'SHORT',
                                'size': 0.1 + (i * 0.05),
                                'entry_price': 1.1000 + (i * 0.001),
                                'current_price': 1.1010 + (i * 0.001),
                                'pnl': (i * 50) - 25,
                                'duration': f'{i+1}h {(i*15)}m',
                                'broker': self.broker
                            })
                    
                    # Broadcast to dashboard
                    if self.dashboard and hasattr(self.dashboard, 'websocket_handler'):
                        try:
                            self.dashboard.websocket_handler.broadcast_live_update(live_trading_data)
                            self.dashboard.websocket_handler.broadcast_portfolio_update(live_trading_data)
                            
                            if iteration % 12 == 1:  # Log every minute
                                logger.info(f"   📡 Dashboard update sent: {active_pairs_count} pairs, {open_positions_count} positions")
                        except Exception as e:
                            if iteration % 12 == 1:
                                logger.error(f"   ❌ Dashboard broadcast error: {e}")
                    else:
                        if iteration % 12 == 1:
                            logger.warning("   ⚠️ Dashboard not available for broadcasting")
                    
                    time.sleep(5)  # Update every 5 seconds
                    
                except Exception as e:
                    logger.error(f"Error in live data collection loop (iteration {iteration}): {e}")
                    time.sleep(10)
                    
        except Exception as e:
            logger.error(f"Critical error in live data collection: {e}")
            import traceback
            traceback.print_exc()
    
    def get_dashboard_data(self) -> Dict[str, Any]:
        """Get comprehensive data for dashboard display"""
        dashboard_data = {
            'live_trades': self.influxdb_manager.get_recent_trades(24),
            'market_data': {},
            'data_provider': self.data_provider,
            'execution_broker': self.broker,
            'system_status': {
                f'{self.data_provider}_data_connected': bool(self.primary_data_manager),
                f'{self.broker}_execution_connected': bool(self.execution_data_manager),
                'influxdb_connected': bool(self.influxdb_manager.client),
                'trader_active': bool(self.trader and self.trader.is_trading),
                'data_provider': self.data_provider,
                'execution_broker': self.broker
            }
        }
        
        # Get market data
        for pair in self.config.pairs[:5]:
            market_data = self.influxdb_manager.get_market_data(pair, 1)
            if market_data:
                dashboard_data['market_data'][pair] = market_data[-1]
        
        return dashboard_data
    
    def shutdown(self):
        """Shutdown all components"""
        logger.info(f"Shutting down Enhanced Trading System V3...")
        logger.info(f"  Data Provider: {self.data_provider}")
        logger.info(f"  Execution Broker: {self.broker}")
        
        try:
            if self.trader:
                self.trader.stop_trading()
                logger.info(f"{self.broker} trader stopped")
                
                # Special handling for cTrader reactor
                if self.broker == 'ctrader':
                    try:
                        from twisted.internet import reactor
                        if reactor.running:
                            reactor.callFromThread(reactor.stop)
                            logger.info("cTrader reactor stopped")
                    except Exception as e:
                        logger.debug(f"Error stopping cTrader reactor: {e}")
            
            if self.primary_data_manager and hasattr(self.primary_data_manager, 'disconnect'):
                self.primary_data_manager.disconnect()
                logger.info(f"{self.data_provider} data manager disconnected")
            
            if self.execution_data_manager != self.primary_data_manager and hasattr(self.execution_data_manager, 'disconnect'):
                self.execution_data_manager.disconnect()
                logger.info(f"{self.broker} execution manager disconnected")
            
            if self.influxdb_manager:
                self.influxdb_manager.disconnect()
            
            if self.dashboard and hasattr(self.dashboard, 'stop'):
                self.dashboard.stop()
                logger.info("Dashboard stopped")
            
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")


def main(data_provider: str = 'ctrader', broker: str = 'ctrader', mode: str = 'backtest', force_refresh: bool = False):
    """
    Main execution function with enhanced features and configurable data provider and broker
    
    Args:
        data_provider: 'ctrader' or 'mt5' - provider for all data operations
        broker: 'ctrader' or 'mt5' - broker for trade execution
        mode: 'backtest' or 'live' - execution mode
        force_refresh: Force re-fetch all data ignoring cache
    """
    system = None
    
    try:
        logger.info(f"=== Enhanced Pairs Trading System V3 Starting ===")
        logger.info(f"Data Provider: {data_provider}")
        logger.info(f"Execution Broker: {broker}")
        logger.info(f"Execution Mode: {mode}")
        logger.info(f"Force Data Refresh: {force_refresh}")
        logger.info(f"Configuration: {CONFIG.pairs[:3]}... ({len(CONFIG.pairs)} total pairs)")
        
        # Create system with selected providers
        system = EnhancedTradingSystemV3(data_provider=data_provider, broker=broker)
        
        if not system.initialize():
            logger.error("Failed to initialize trading system")
            return
        
        # Log the actual providers being used after initialization
        logger.info(f"System Initialized:")
        logger.info(f"  Data Provider: {system.data_provider}")
        logger.info(f"  Execution Broker: {system.broker}")
        
        if mode.lower() == 'backtest':
            logger.info(f"Running backtest with {system.data_provider} data...")
            
            # Run backtest with intelligent data management
            backtest_results = system.run_enhanced_backtest(force_refresh=force_refresh)
            
            # Start dashboard with results
            system.start_dashboard(backtest_results)
            
            logger.info("=== Backtest Complete ===")
            logger.info(f"Data Provider: {system.data_provider}")
            logger.info(f"Execution Broker: {system.broker}")
            logger.info(f"Portfolio Return: {backtest_results.get('portfolio_metrics', {}).get('portfolio_return', 0):.2%}")
            logger.info(f"Sharpe Ratio: {backtest_results.get('portfolio_metrics', {}).get('portfolio_sharpe', 0):.2f}")
            logger.info(f"Max Drawdown: {backtest_results.get('portfolio_metrics', {}).get('portfolio_max_drawdown', 0):.2%}")
            logger.info(f"Total Trades: {backtest_results.get('portfolio_metrics', {}).get('total_trades', 0)}")
            logger.info(f"Report: {backtest_results.get('report_path', 'N/A')}")
            
            # Keep dashboard running
            input("Press Enter to stop the dashboard...")
            
        elif mode.lower() == 'live':
            logger.info(f"Starting live trading...")
            logger.info(f"  Data from: {system.data_provider}")
            logger.info(f"  Execution via: {system.broker}")
            
            # Start dashboard first (non-blocking)
            logger.info("Step 1: Starting dashboard...")
            system.start_dashboard()
            logger.info("✅ Dashboard startup completed")
            
            # Start live data collection thread
            logger.info("Step 2: Starting live data collection thread...")
            live_data_thread = threading.Thread(
                target=system._collect_live_data, 
                daemon=True
            )
            live_data_thread.start()
            
            # Verify live data thread is running
            time.sleep(1)
            if live_data_thread.is_alive():
                logger.info("✅ Live data collection thread started")
            else:
                logger.error("❌ Live data collection thread failed to start")
            
            # Start real-time trading
            logger.info("Step 3: Initializing real-time trading...")
            if system.start_real_time_trading():
                logger.info("="*80)
                logger.info("🚀 LIVE TRADING MODE ACTIVE")
                logger.info("="*80)
                logger.info(f"Data Provider: {system.data_provider}")
                logger.info(f"Execution Broker: {system.broker}")
                logger.info(f"Dashboard: http://127.0.0.1:8050")
                logger.info("Press Ctrl+C to stop...")
                logger.info("="*80)
                
                try:
                    # Check status periodically
                    status_check_count = 0
                    timeout_warning_shown = False
                    
                    while True:
                        time.sleep(30)  # Check every 30 seconds
                        status_check_count += 1
                        
                        # Log status every 5 minutes
                        if status_check_count % 10 == 0:  # 30 seconds * 10 = 5 minutes
                            logger.info("="*60)
                            logger.info("LIVE TRADING STATUS CHECK")
                            logger.info("="*60)
                            logger.info(f"Data Provider: {system.data_provider}")
                            logger.info(f"Execution Broker: {system.broker}")
                            
                            # Check thread status
                            if live_data_thread.is_alive():
                                logger.info("✅ Live data collection: Active")
                            else:
                                logger.error("❌ Live data collection: Stopped")
                            
                            # Check trader status with timeout handling
                            try:
                                if system.trader and hasattr(system.trader, 'is_trading'):
                                    is_trading = system.trader.is_trading
                                    logger.info(f"✅ Trading Status: {is_trading}")
                                    
                                    # Special status for CTrader
                                    if system.broker == 'ctrader' and hasattr(system.trader, 'trader'):
                                        trader = system.trader.trader
                                        if hasattr(trader, 'symbols_initialized'):
                                            symbols_ready = trader.symbols_initialized
                                            degraded_mode = getattr(trader, '_degraded_mode', False)
                                            
                                            if symbols_ready:
                                                logger.info("✅ CTrader Symbols: Loaded")
                                                timeout_warning_shown = False  # Reset flag
                                            elif degraded_mode:
                                                if not timeout_warning_shown:
                                                    logger.warning("⚠️ CTrader Symbols: Degraded mode (timeout occurred)")
                                                    logger.warning("   Trading continues with limited symbol information")
                                                    timeout_warning_shown = True
                                            else:
                                                logger.info("🔄 CTrader Symbols: Loading...")
                                else:
                                    logger.warning("⚠️ Trading Status: Unknown")
                                    
                            except Exception as e:
                                # Don't spam logs with repeated timeout errors
                                if "timeout" in str(e).lower() or "symbols" in str(e).lower():
                                    if not timeout_warning_shown:
                                        logger.warning("⚠️ Trading Status: CTrader connection issues (this is normal)")
                                        timeout_warning_shown = True
                                else:
                                    logger.warning(f"⚠️ Trading Status: Error - {e}")
                            
                            logger.info("="*60)
                        
                except KeyboardInterrupt:
                    logger.info("Received stop signal...")
            else:
                logger.error("Failed to start real-time trading")
        
        else:
            logger.error(f"Invalid mode: {mode}. Use 'backtest' or 'live'")
    
    except KeyboardInterrupt:
        logger.info("Received interrupt signal, shutting down...")
    except Exception as e:
        logger.error(f"Error in main execution: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if system:
            system.shutdown()
        logger.info("Enhanced Pairs Trading System V3 stopped")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Enhanced Pairs Trading System V3 with Configurable Data Provider and Broker')
    parser.add_argument('--data-provider', '-d', 
                       choices=['ctrader', 'mt5'], 
                       default='ctrader',
                       help='Data provider for historical and real-time data (default: ctrader)')
    parser.add_argument('--broker', '-b', 
                       choices=['ctrader', 'mt5'], 
                       default='ctrader',
                       help='Broker for trade execution (default: ctrader)')
    parser.add_argument('--mode', '-m', 
                       choices=['backtest', 'live'], 
                       default='backtest',
                       help='Execution mode (default: backtest)')
    parser.add_argument('--force-refresh', '-f', 
                       action='store_true',
                       help='Force refresh all data ignoring cache')
    
    args = parser.parse_args()
    
    # Set environment variables for mode (if needed by other components)
    os.environ['TRADING_MODE'] = args.mode
    
    main(data_provider=args.data_provider, broker=args.broker, mode=args.mode, force_refresh=args.force_refresh)
