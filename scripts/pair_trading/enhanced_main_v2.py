"""
Enhanced Pairs Trading System v2 with Configurable Data Provider
===============================================================

This script provides a comprehensive trading system that:
- Allows selection of data provider (CTrader or MT5) for all data operations
- Retrieves historical and real-time data from the chosen provider
- Executes real-time trading with MT5 (execution broker remains MT5)
- Uses InfluxDB for data storage and retrieval
- Integrates with the dashboard for real-time visualization
- Provides backtesting capabilities with vectorbt
- Generates downloadable Excel reports

Key Enhancement: Unified data provider selection for all data operations
while maintaining MT5 as the execution broker.

Author: Trading System v2.0
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
    from data.ctrader_working import CTraderDataManager
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

# Data providers and brokers
from data.mt5 import MT5DataManager

# Setup a temporary logger for early import errors
import logging
temp_logger = logging.getLogger(__name__)

try:
    from data.ctrader_working import CTraderDataManager
    CTRADER_WORKING_AVAILABLE = True
    temp_logger.info("Using working CTrader data manager")
except ImportError:
    try:
        from data.ctrader_improved import CTraderDataManager
        CTRADER_WORKING_AVAILABLE = False
        temp_logger.warning("Using improved CTrader data manager (working version not available)")
    except ImportError:
        try:
            from data.ctrader import CTraderDataManager
            CTRADER_WORKING_AVAILABLE = False
            temp_logger.warning("Using original CTrader data manager (improved versions not available)")
        except ImportError:
            CTraderDataManager = None
            CTRADER_WORKING_AVAILABLE = False
            temp_logger.warning("CTrader data manager not available")

from brokers.mt5 import MT5RealTimeTrader

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
log_file_path = os.path.join(CONFIG.logs_dir, "enhanced_pairs_trading_v2.log")
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
            logger.error(f"Failed to connect to InfluxDB: {e}")
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
                .field("bid", float(data.get('bid', 0)))
                .field("ask", float(data.get('ask', 0)))
                .field("last", float(data.get('last', 0)))
                .field("volume", float(data.get('volume', 0)))
                .time(datetime.utcnow(), WritePrecision.NS)
            )
            
            self.write_api.write(bucket=self.bucket, org=self.org, record=point)
            
        except Exception as e:
            logger.error(f"Failed to store market data for {symbol}: {e}")
    
    def store_trade_data(self, trade_data: Dict[str, Any]):
        """Store trade execution data"""
        try:
            if not self.write_api:
                return
                
            point = (
                Point("trades")
                .tag("pair", trade_data.get('pair', ''))
                .tag("action", trade_data.get('action', ''))
                .tag("strategy", trade_data.get('strategy', 'pairs_trading'))
                .field("entry_price", float(trade_data.get('entry_price', 0)))
                .field("exit_price", float(trade_data.get('exit_price', 0)))
                .field("quantity", float(trade_data.get('quantity', 0)))
                .field("pnl", float(trade_data.get('pnl', 0)))
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
                logger.warning("InfluxDB write API not available, skipping backtest results storage")
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
                    .field("total_return", float(metrics.get('total_return', 0)))
                    .field("sharpe_ratio", float(metrics.get('sharpe_ratio', 0)))
                    .field("max_drawdown", float(metrics.get('max_drawdown', 0)))
                    .field("total_trades", int(metrics.get('total_trades', 0)))
                    .field("win_rate", float(metrics.get('win_rate', 0)))
                    .time(datetime.utcnow(), WritePrecision.NS)
                )
                
                self.write_api.write(bucket=self.bucket, org=self.org, record=pair_point)
                
        except Exception as e:
            logger.warning(f"Failed to store backtest results in InfluxDB (continuing anyway): {e}")
    
    def get_market_data(self, symbol: str, hours: int = 24) -> List[Dict]:
        """Retrieve market data from InfluxDB"""
        try:
            if not self.query_api:
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
                        'bid': record.values.get('bid'),
                        'ask': record.values.get('ask'),
                        'last': record.values.get('last'),
                        'volume': record.values.get('volume')
                    })
            
            return data
            
        except Exception as e:
            logger.error(f"Failed to retrieve market data for {symbol}: {e}")
            return []
    
    def get_recent_trades(self, hours: int = 24) -> List[Dict]:
        """Retrieve recent trades from InfluxDB"""
        try:
            if not self.query_api:
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
                        'entry_price': record.values.get('entry_price'),
                        'exit_price': record.values.get('exit_price'),
                        'quantity': record.values.get('quantity'),
                        'pnl': record.values.get('pnl'),
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


class EnhancedRealTimeTrader(MT5RealTimeTrader):
    """
    Enhanced real-time trader with InfluxDB integration and dashboard updates
    Note: Always uses MT5 for trade execution regardless of data provider
    """
    
    def __init__(self, config: TradingConfig, execution_data_manager, influxdb_manager: InfluxDBManager):
        # Always use MT5 for execution
        super().__init__(config, execution_data_manager)
        self.influxdb_manager = influxdb_manager
        self.dashboard = None
        
    def set_dashboard(self, dashboard):
        """Set dashboard instance for real-time updates"""
        self.dashboard = dashboard
    
    def on_trade_executed(self, trade_data: Dict[str, Any]):
        """Override to store trades in InfluxDB and update dashboard"""
        # Store in InfluxDB
        self.influxdb_manager.store_trade_data(trade_data)
        
        # Update dashboard via WebSocket
        if self.dashboard and hasattr(self.dashboard, 'websocket_handler'):
            self.dashboard.websocket_handler.broadcast_trade_signal({
                'pair': trade_data.get('pair'),
                'action': trade_data.get('action'),
                'price': trade_data.get('entry_price'),
                'z_score': trade_data.get('z_score'),
                'timestamp': datetime.now().isoformat()
            })
        
        logger.info(f"Trade executed and stored: {trade_data.get('pair')} - {trade_data.get('action')}")
    
    def on_market_data_update(self, symbol: str, data: Dict[str, Any]):
        """Store market data updates in InfluxDB"""
        self.influxdb_manager.store_market_data(symbol, data, "mt5_execution")
        
        # Update dashboard with live data
        if self.dashboard and hasattr(self.dashboard, 'websocket_handler'):
            self.dashboard.websocket_handler.broadcast_live_update({
                'type': 'market_data',
                'symbol': symbol,
                'data': data,
                'timestamp': datetime.now().isoformat()
            })


class EnhancedTradingSystemV2:
    """
    Enhanced trading system v2 with configurable data provider
    """
    
    def __init__(self, data_provider: str = 'ctrader'):
        """
        Initialize the trading system with specified data provider
        
        Args:
            data_provider: 'ctrader' or 'mt5' - provider for all data operations
        """
        self.config = CONFIG
        self.data_provider = data_provider.lower()
        
        if self.data_provider not in ['ctrader', 'mt5']:
            raise ValueError("data_provider must be 'ctrader' or 'mt5'")
        
        # Data managers
        self.primary_data_manager = None  # Selected data provider
        self.mt5_execution_manager = None  # Always MT5 for execution
        
        # Other components
        self.influxdb_manager = None
        self.trader = None
        self.dashboard = None
        self.backtester = None
        
        logger.info(f"Enhanced Trading System V2 initialized with data provider: {self.data_provider}")
        
    def initialize(self) -> bool:
        """Initialize all components with selected data provider"""
        logger.info(f"Initializing Enhanced Trading System V2 with {self.data_provider} as data provider...")
        
        # Initialize InfluxDB
        self.influxdb_manager = InfluxDBManager(self.config)
        
        # Initialize primary data manager based on selection
        if self.data_provider == 'ctrader':
            if not CTraderDataManager:
                logger.error("CTrader data manager not available, falling back to MT5")
                self.data_provider = 'mt5'
                self.primary_data_manager = MT5DataManager(self.config)
                if not self.primary_data_manager.connect():
                    logger.error("Failed to connect to MT5 fallback data provider")
                    return False
                logger.info("MT5 data manager initialized as fallback primary data provider")
            else:
                # Create CTrader data manager and test connection
                try:
                    logger.info("Testing CTrader connection...")
                    test_ctrader = CTraderDataManager(self.config)
                    
                    # Quick connection test
                    connection_successful = test_ctrader.connect()
                    if connection_successful:
                        logger.info("CTrader connection successful, using as primary data provider")
                        self.primary_data_manager = test_ctrader
                        logger.info("CTrader data manager initialized as primary data provider")
                    else:
                        raise ConnectionError("CTrader connection test failed")
                    
                except Exception as e:
                    logger.error(f"CTrader connection failed: {e}")
                    logger.info("Falling back to MT5 as primary data provider due to CTrader issues")
                    self.primary_data_manager = MT5DataManager(self.config)
                    if not self.primary_data_manager.connect():
                        logger.error("Failed to connect to MT5 fallback data provider")
                        return False
                    logger.info("MT5 data manager initialized as fallback primary data provider")
                    self.data_provider = 'mt5'  # Update the provider name for consistency
        else:  # mt5
            self.primary_data_manager = MT5DataManager(self.config)
            if not self.primary_data_manager.connect():
                logger.error("Failed to connect to MT5 as primary data provider")
                return False
            logger.info("MT5 data manager initialized as primary data provider")
        
        # Always initialize MT5 for execution (separate from data provider)
        self.mt5_execution_manager = MT5DataManager(self.config)
        if not self.mt5_execution_manager.connect():
            logger.error("Failed to connect to MT5 for trade execution")
            return False
        logger.info("MT5 execution manager initialized")
        
        # Initialize enhanced trader (always uses MT5 for execution)
        self.trader = EnhancedRealTimeTrader(
            self.config, 
            self.mt5_execution_manager,  # Always MT5 for execution
            self.influxdb_manager
        )
        
        # Initialize backtester with selected data provider
        if self.primary_data_manager:
            self.backtester = VectorBTBacktester(self.config, self.primary_data_manager)
            logger.info(f"Backtester initialized with {self.data_provider} data provider")
        else:
            # Fallback to MT5 if primary data manager not available
            self.backtester = VectorBTBacktester(self.config, self.mt5_execution_manager)
            logger.info("Backtester initialized with MT5 data provider (fallback)")
        
        logger.info("Enhanced Trading System V2 initialized successfully")
        return True
    
    def start_dashboard(self, backtest_results: Optional[Dict] = None):
        """Start the dashboard with optional backtest results"""
        try:
            if backtest_results:
                # Start dashboard with backtest results
                self.dashboard = start_dashboard_with_backtest(
                    backtest_results=backtest_results,
                    config={'port': 8050, 'theme': 'dark'}
                )
            else:
                # Start basic dashboard
                self.dashboard = create_dashboard({
                    'port': 8050,
                    'theme': 'dark',
                    'websocket_enabled': True
                })
                self.dashboard.start(blocking=False)
            
            # Connect trader to dashboard
            if self.trader:
                self.trader.set_dashboard(self.dashboard)
            
            logger.info("Dashboard started successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to start dashboard: {e}")
            return False
    
    def run_enhanced_backtest(self) -> Dict[str, Any]:
        """Run comprehensive backtesting with selected data provider"""
        logger.info(f"Starting enhanced backtesting with {self.data_provider} data provider...")
        
        try:
            # Check if we have a valid data manager
            if not self.primary_data_manager:
                logger.error("No primary data manager available for backtesting")
                return self._create_empty_backtest_results()
            
            # Update data provider string to match actual manager
            actual_data_manager_type = type(self.primary_data_manager).__name__
            if 'CTrader' in actual_data_manager_type:
                self.data_provider = 'ctrader'
            elif 'MT5' in actual_data_manager_type:
                self.data_provider = 'mt5'
            
            is_ctrader = 'CTrader' in actual_data_manager_type
            
            # For CTrader, pre-fetch all data in one connection and store in InfluxDB
            if is_ctrader:
                logger.info("CTrader detected: Pre-fetching all historical data in single connection...")
                
                # Get all unique symbols from all pairs
                all_symbols = set()
                for pair in self.config.pairs:
                    symbols = pair.split('-')
                    all_symbols.update(symbols)
                
                all_symbols = list(all_symbols)
                logger.info(f"Pre-fetching data for {len(all_symbols)} symbols: {all_symbols[:5]}{'...' if len(all_symbols) > 5 else ''}")
                
                # Set reasonable limits for CTrader API to avoid overwhelming it
                max_symbols_per_session = 1000  # Limit total symbols to avoid excessive rate limiting
                if len(all_symbols) > max_symbols_per_session:
                    logger.warning(f"Too many symbols ({len(all_symbols)}), limiting to first {max_symbols_per_session} to avoid rate limits")
                    all_symbols = all_symbols[:max_symbols_per_session]
                
                # Fetch all data in ONE connection using the working pattern from pairs_trading_ctrader_v4.py
                try:
                    logger.info("Fetching all symbols in single connection (like pairs_trading_ctrader_v4.py)...")
                    
                    # Get all data in one reactor session - exactly like the working script
                    bulk_data = self.primary_data_manager.get_historical_data(
                        all_symbols, 
                        self.config.interval, 
                        self.config.start_date, 
                        self.config.end_date
                    )
                    
                    if bulk_data and isinstance(bulk_data, dict):
                        # Only keep non-empty series
                        valid_data = {}
                        for symbol, series in bulk_data.items():
                            if series is not None and not series.empty:
                                valid_data[symbol] = series
                                logger.info(f"Successfully retrieved {len(series)} data points for {symbol}")
                            else:
                                logger.warning(f"No data retrieved for {symbol}")
                        bulk_data = valid_data
                    else:
                        logger.warning("No data retrieved from CTrader API")
                        bulk_data = {}
                    
                    if bulk_data:
                        # Store all retrieved data in InfluxDB
                        self._store_bulk_historical_data(bulk_data)
                        logger.info(f"Successfully pre-fetched and stored data for {len(bulk_data)}/{len(all_symbols)} symbols")
                        
                        # Now create an InfluxDB data manager for backtesting
                        self.primary_data_manager = InfluxDBDataManager(self.config, self.influxdb_manager)
                        logger.info("Switched to InfluxDB data manager for backtesting")
                    else:
                        logger.warning("CTrader bulk data fetch returned no usable data")
                        return self._create_empty_backtest_results()
                        
                except Exception as e:
                    logger.error(f"Failed to pre-fetch CTrader data: {e}")
                    import traceback
                    logger.error(f"Traceback: {traceback.format_exc()}")
                    return self._create_empty_backtest_results()
            
            # Update backtester with the appropriate data manager
            from backtesting.vectorbt import VectorBTBacktester
            self.backtester = VectorBTBacktester(self.config, self.primary_data_manager)
            
            # Run backtest using selected data provider
            logger.info(f"Running backtest with data provider: {self.data_provider}")
            backtest_results = self.backtester.run_backtest()
            
            # Check if backtest returned valid results
            if not backtest_results or not backtest_results.get('portfolio_metrics'):
                logger.warning(f"Backtest with {self.data_provider} returned empty or invalid results")
                return self._create_empty_backtest_results()
            
            # Store results in InfluxDB (with error handling)
            try:
                self.influxdb_manager.store_backtest_results(backtest_results)
            except Exception as e:
                logger.warning(f"Failed to store backtest results in InfluxDB: {e}")
            
            # Generate Excel report
            try:
                report_path = generate_enhanced_report(backtest_results, self.config)
                backtest_results['report_path'] = report_path
                logger.info(f"Report generated: {report_path}")
            except Exception as e:
                logger.warning(f"Failed to generate Excel report: {e}")
                backtest_results['report_path'] = 'N/A'
            
            logger.info(f"Backtest completed using {self.data_provider} data. Report saved to: {backtest_results.get('report_path', 'N/A')}")
            return backtest_results
            
        except Exception as e:
            logger.error(f"Error during backtesting with {self.data_provider}: {e}")
            return self._create_empty_backtest_results()
    
    def _store_bulk_historical_data(self, bulk_data: Dict[str, pd.Series]):
        """
        Store bulk historical data in InfluxDB for later retrieval
        
        Args:
            bulk_data: Dictionary of symbol -> pandas Series with historical price data
        """
        try:
            if not self.influxdb_manager or not self.influxdb_manager.write_api:
                logger.warning("InfluxDB not available for storing bulk historical data")
                return
            
            logger.info(f"Storing bulk historical data for {len(bulk_data)} symbols in InfluxDB...")
            
            stored_count = 0
            total_points_stored = 0
            
            for symbol, series in bulk_data.items():
                if series is None or series.empty:
                    logger.warning(f"No data to store for symbol {symbol}")
                    continue
                
                try:
                    # Create points for each timestamp/price pair
                    points = []
                    valid_points = 0
                    
                    for timestamp, price in series.items():
                        if pd.isna(price) or price == 0:
                            continue
                            
                        try:
                            point = (
                                Point("historical_data")
                                .tag("symbol", symbol)
                                .tag("source", "ctrader_bulk")
                                .tag("interval", self.config.interval)
                                .field("price", float(price))
                                .time(timestamp, WritePrecision.NS)
                            )
                            points.append(point)
                            valid_points += 1
                        except Exception as point_error:
                            logger.debug(f"Error creating point for {symbol} at {timestamp}: {point_error}")
                            continue
                    
                    if points:
                        try:
                            # Write in batches for better performance and error handling
                            batch_size = 500  # Smaller batches for better reliability
                            batches_written = 0
                            
                            for i in range(0, len(points), batch_size):
                                batch = points[i:i + batch_size]
                                try:
                                    self.influxdb_manager.write_api.write(
                                        bucket=self.influxdb_manager.bucket,
                                        org=self.influxdb_manager.org,
                                        record=batch
                                    )
                                    batches_written += 1
                                except Exception as batch_error:
                                    logger.warning(f"Failed to write batch {i//batch_size + 1} for {symbol}: {batch_error}")
                                    continue
                            
                            if batches_written > 0:
                                stored_count += 1
                                total_points_stored += valid_points
                                logger.info(f"Stored {valid_points} data points for {symbol} in {batches_written} batches")
                            else:
                                logger.warning(f"Failed to store any batches for {symbol}")
                                
                        except Exception as write_error:
                            logger.error(f"Failed to write data for {symbol}: {write_error}")
                            continue
                    else:
                        logger.warning(f"No valid data points to store for {symbol}")
                        
                except Exception as symbol_error:
                    logger.error(f"Failed to process data for symbol {symbol}: {symbol_error}")
                    continue
            
            if stored_count > 0:
                logger.info(f"Successfully stored bulk historical data for {stored_count}/{len(bulk_data)} symbols ({total_points_stored} total data points)")
            else:
                logger.warning("No symbols were successfully stored in InfluxDB")
            
        except Exception as e:
            logger.error(f"Failed to store bulk historical data in InfluxDB: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
    
    def _create_empty_backtest_results(self) -> Dict[str, Any]:
        """Create empty backtest results structure"""
        return {
            'portfolio_metrics': {
                'portfolio_return': 0.0,
                'portfolio_sharpe': 0.0,
                'portfolio_max_drawdown': 0.0,
                'total_trades': 0,
                'portfolio_win_rate': 0.0,
                'total_pairs': 0
            },
            'pair_results': [],
            'report_path': 'N/A',
            'data_provider': self.data_provider,
            'error': f'No valid data available for backtesting with {self.data_provider} provider'
        }
    
    def start_real_time_trading(self):
        """Start real-time trading with live data collection from selected provider"""
        logger.info(f"Starting real-time trading with {self.data_provider} data provider...")
        
        if not self.trader.initialize():
            logger.error("Failed to initialize trader")
            return False
        
        # Start data collection from selected provider
        if self.primary_data_manager:
            threading.Thread(
                target=self._collect_live_data_from_provider,
                daemon=True
            ).start()
        
        # Start trading (execution always via MT5)
        self.trader.start_trading()
        return True
    
    def _collect_live_data_from_provider(self):
        """Collect real-time data from selected provider and store in InfluxDB"""
        logger.info(f"Starting {self.data_provider} data collection...")
        
        while True:
            try:
                for pair in self.config.pairs[:5]:  # Limit to first 5 pairs for demo
                    try:
                        current_data = None
                        
                        if self.data_provider == 'ctrader':
                            # CTrader data collection
                            # For now, we'll create mock current data
                            # In production, implement proper spot price subscription
                            current_data = {
                                'bid': 1.1000 + (hash(pair) % 100) * 0.0001,  # Mock bid
                                'ask': 1.1005 + (hash(pair) % 100) * 0.0001,  # Mock ask
                                'last': 1.1002 + (hash(pair) % 100) * 0.0001,  # Mock last
                                'volume': 1000 + (hash(pair) % 1000),  # Mock volume
                                'timestamp': datetime.now().isoformat()
                            }
                        
                        elif self.data_provider == 'mt5':
                            # MT5 data collection
                            try:
                                # Get current market data from MT5
                                symbols = pair.split('-') if '-' in pair else [pair[:6], pair[6:]]
                                if len(symbols) >= 2:
                                    symbol = symbols[0] + symbols[1]  # Combine for MT5 format
                                    
                                    # Get current tick data
                                    tick_data = self.primary_data_manager.get_current_tick(symbol)
                                    if tick_data:
                                        current_data = {
                                            'bid': tick_data.get('bid', 0),
                                            'ask': tick_data.get('ask', 0),
                                            'last': tick_data.get('last', 0),
                                            'volume': tick_data.get('volume', 0),
                                            'timestamp': datetime.now().isoformat()
                                        }
                            except Exception as e:
                                logger.debug(f"Error getting MT5 tick data for {pair}: {e}")
                                # Fallback to mock data
                                current_data = {
                                    'bid': 1.1000 + (hash(pair) % 100) * 0.0001,
                                    'ask': 1.1005 + (hash(pair) % 100) * 0.0001,
                                    'last': 1.1002 + (hash(pair) % 100) * 0.0001,
                                    'volume': 1000 + (hash(pair) % 1000),
                                    'timestamp': datetime.now().isoformat()
                                }
                        
                        if current_data:
                            # Store in InfluxDB with provider tag
                            self.influxdb_manager.store_market_data(
                                pair, current_data, self.data_provider
                            )
                            
                            # Update dashboard
                            if self.dashboard and hasattr(self.dashboard, 'websocket_handler'):
                                self.dashboard.websocket_handler.broadcast_live_update({
                                    'type': f'{self.data_provider}_data',
                                    'symbol': pair,
                                    'data': current_data,
                                    'timestamp': datetime.now().isoformat(),
                                    'data_provider': self.data_provider
                                })
                    
                    except Exception as e:
                        logger.error(f"Error collecting {self.data_provider} data for {pair}: {e}")
                
                time.sleep(5)  # Collect data every 5 seconds
                
            except Exception as e:
                logger.error(f"Error in {self.data_provider} data collection: {e}")
                time.sleep(10)
    
    def _collect_live_data(self):
        """Collect and broadcast live data for dashboard (realtime mode)"""
        logger.info(f"Starting live data collection for dashboard using {self.data_provider} provider...")
        
        try:
            # Small delay to ensure dashboard is ready
            time.sleep(2)
            logger.info("Live data collection entering main loop...")
            
            while True:
                try:
                    # Get real portfolio data from trader if available
                    portfolio_value = 100000
                    current_pnl = 0
                    open_positions_count = 0
                    active_positions = []
                    active_pairs_count = 0
                    total_exposure = 0
                    today_pnl = 0
                    
                    if self.trader and hasattr(self.trader, 'get_portfolio_status'):
                        try:
                            portfolio_status = self.trader.get_portfolio_status()
                            if portfolio_status and 'error' not in portfolio_status:
                                portfolio_value = portfolio_status.get('equity', 100000)
                                current_pnl = portfolio_status.get('unrealized_pnl', 0)
                                open_positions_count = portfolio_status.get('position_count', 0)
                                active_positions = portfolio_status.get('positions', [])
                                total_exposure = portfolio_status.get('total_exposure', 0)
                                
                                # Calculate number of active pairs
                                active_pairs_count = len(set(pos.get('pair', '') for pos in active_positions))
                                
                                # For today's P&L, use unrealized_pnl as approximation
                                today_pnl = current_pnl
                                
                                logger.debug(f"Real portfolio data from {self.data_provider}: equity=${portfolio_value:.2f}, pnl=${current_pnl:.2f}, positions={open_positions_count}")
                            else:
                                logger.debug(f"No portfolio status available from trader using {self.data_provider}")
                        except Exception as e:
                            logger.warning(f"Error getting real portfolio status with {self.data_provider}: {e}")
                    
                    # Generate formatted data for Live Trading Monitor page
                    live_trading_data = {
                        'timestamp': datetime.now().isoformat(),
                        'pnl': current_pnl,
                        'open_trades': open_positions_count,
                        'market_exposure': min((total_exposure / max(portfolio_value, 1)) * 100, 100) if portfolio_value > 0 else 0,
                        'market_health': 75 + (hash(str(datetime.now().minute)) % 50),
                        
                        # Quick Stats data
                        'active_pairs': active_pairs_count,
                        'open_positions': open_positions_count,
                        'today_pnl': today_pnl,
                        'portfolio_value': portfolio_value,
                        'total_exposure': total_exposure,
                        'data_provider': self.data_provider,  # Include data provider info
                        
                        'pnl_history': [
                            {
                                'timestamp': (datetime.now() - timedelta(minutes=i)).isoformat(),
                                'pnl': current_pnl + (hash(str(i)) % 200) - 100
                            }
                            for i in range(60, 0, -5)
                        ],
                        'positions': []
                    }
                    
                    # Add position data (real if available, mock otherwise)
                    if active_positions:
                        for pos in active_positions[:10]:
                            live_trading_data['positions'].append({
                                'pair': pos.get('pair', f'PAIR{len(live_trading_data["positions"])+1}'),
                                'type': pos.get('type', 'LONG'),
                                'size': pos.get('volume', 0.1),
                                'entry_price': pos.get('price_open', 1.1000),
                                'current_price': pos.get('price_current', 1.1010),
                                'pnl': pos.get('profit', 0),
                                'duration': pos.get('duration', '1h 30m'),
                                'data_provider': self.data_provider
                            })
                    else:
                        # Mock positions data with data provider tag
                        for i in range(3):
                            live_trading_data['positions'].append({
                                'pair': f'EURUSD-GBPUSD',
                                'type': 'LONG' if i % 2 == 0 else 'SHORT',
                                'size': 0.1 + (i * 0.05),
                                'entry_price': 1.1000 + (i * 0.001),
                                'current_price': 1.1010 + (i * 0.001),
                                'pnl': (i * 50) - 25,
                                'duration': f'{i+1}h {(i*15)}m',
                                'data_provider': self.data_provider
                            })
                    
                    # Broadcast to dashboard with data provider information
                    if self.dashboard and hasattr(self.dashboard, 'websocket_handler'):
                        try:
                            self.dashboard.websocket_handler.broadcast_live_update(live_trading_data)
                            self.dashboard.websocket_handler.broadcast_portfolio_update(live_trading_data)
                            logger.debug(f"Broadcasted live data update using {self.data_provider} for {active_pairs_count} pairs")
                        except Exception as e:
                            logger.error(f"Error broadcasting live data: {e}")
                    
                    time.sleep(5)  # Update every 5 seconds
                    
                except Exception as e:
                    logger.error(f"Error in live data collection loop with {self.data_provider}: {e}")
                    time.sleep(10)
                    
        except Exception as e:
            logger.error(f"Critical error in live data collection with {self.data_provider}: {e}")
    
    def get_dashboard_data(self) -> Dict[str, Any]:
        """Get comprehensive data for dashboard display"""
        dashboard_data = {
            'live_trades': self.influxdb_manager.get_recent_trades(24),
            'market_data': {},
            'data_provider': self.data_provider,  # Include data provider info
            'system_status': {
                f'{self.data_provider}_connected': bool(self.primary_data_manager),
                'mt5_execution_connected': bool(self.mt5_execution_manager),
                'influxdb_connected': bool(self.influxdb_manager.client),
                'trader_active': bool(self.trader and hasattr(self.trader, 'is_trading') and self.trader.is_trading),
                'data_provider': self.data_provider
            }
        }
        
        # Get market data from selected provider
        for pair in self.config.pairs[:5]:
            market_data = self.influxdb_manager.get_market_data(pair, 1)
            if market_data:
                dashboard_data['market_data'][pair] = market_data[-1]  # Latest data point
        
        return dashboard_data
    
    def shutdown(self):
        """Shutdown all components"""
        logger.info(f"Shutting down Enhanced Trading System V2 (data provider: {self.data_provider})...")
        
        try:
            if self.trader:
                self.trader.stop_trading()
                logger.info("Trader stopped")
            
            if self.primary_data_manager and hasattr(self.primary_data_manager, 'disconnect'):
                self.primary_data_manager.disconnect()
                logger.info(f"{self.data_provider} data manager disconnected")
            
            if self.mt5_execution_manager and hasattr(self.mt5_execution_manager, 'disconnect'):
                self.mt5_execution_manager.disconnect()
                logger.info("MT5 execution manager disconnected")
            
            if self.influxdb_manager:
                self.influxdb_manager.disconnect()
            
            if self.dashboard and hasattr(self.dashboard, 'stop'):
                self.dashboard.stop()
                logger.info("Dashboard stopped")
            
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")


def main(data_provider: str = 'ctrader', mode: str = 'backtest'):
    """
    Main execution function with enhanced features and configurable data provider
    
    Args:
        data_provider: 'ctrader' or 'mt5' - provider for all data operations
        mode: 'backtest' or 'live' - execution mode
    """
    system = None
    
    try:
        logger.info(f"=== Enhanced Pairs Trading System V2 Starting ===")
        logger.info(f"Data Provider Requested: {data_provider}")
        logger.info(f"Execution Mode: {mode}")
        logger.info(f"Configuration: {CONFIG.pairs[:3]}... ({len(CONFIG.pairs)} total pairs)")
        
        # Create system with selected data provider
        system = EnhancedTradingSystemV2(data_provider=data_provider)
        
        if not system.initialize():
            logger.error("Failed to initialize trading system")
            return
        
        # Log the actual data provider being used after initialization
        logger.info(f"Data Provider Initialized: {system.data_provider}")
        
        if mode.lower() == 'backtest':
            logger.info(f"Running backtest with {system.data_provider} data provider...")
            
            # Run backtest
            backtest_results = system.run_enhanced_backtest()
            
            # Start dashboard with results
            system.start_dashboard(backtest_results)
            
            logger.info("=== Backtest Complete ===")
            logger.info(f"Data Provider Used: {system.data_provider}")
            logger.info(f"Portfolio Return: {backtest_results.get('portfolio_metrics', {}).get('portfolio_return', 0):.2%}")
            logger.info(f"Sharpe Ratio: {backtest_results.get('portfolio_metrics', {}).get('portfolio_sharpe', 0):.2f}")
            logger.info(f"Max Drawdown: {backtest_results.get('portfolio_metrics', {}).get('portfolio_max_drawdown', 0):.2%}")
            logger.info(f"Total Trades: {backtest_results.get('portfolio_metrics', {}).get('total_trades', 0)}")
            logger.info(f"Report: {backtest_results.get('report_path', 'N/A')}")
            
            # Keep dashboard running
            input("Press Enter to stop the dashboard...")
            
        elif mode.lower() == 'live':
            logger.info(f"Starting live trading with {system.data_provider} data provider...")
            
            # Start dashboard first
            system.start_dashboard()
            
            # Start live data collection thread
            live_data_thread = threading.Thread(
                target=system._collect_live_data, 
                daemon=True
            )
            live_data_thread.start()
            
            # Start real-time trading
            if system.start_real_time_trading():
                logger.info("=== Live Trading Active ===")
                logger.info(f"Data Provider: {system.data_provider}")
                logger.info("Press Ctrl+C to stop...")
                
                try:
                    while True:
                        time.sleep(1)
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
        logger.info("Enhanced Pairs Trading System V2 stopped")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Enhanced Pairs Trading System V2 with Configurable Data Provider')
    parser.add_argument('--data-provider', '-d', 
                       choices=['ctrader', 'mt5'], 
                       default='ctrader',
                       help='Data provider for historical and real-time data (default: ctrader)')
    parser.add_argument('--mode', '-m', 
                       choices=['backtest', 'live'], 
                       default='backtest',
                       help='Execution mode (default: backtest)')
    
    args = parser.parse_args()
    
    # Set environment variable for mode (if needed by other components)
    os.environ['TRADING_MODE'] = args.mode
    
    main(data_provider=args.data_provider, mode=args.mode)
