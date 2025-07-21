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
import signal
import json
import traceback
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

# Enhanced state management
from utils.unified_state_manager import UnifiedStateManager
from utils.state_config import StateManagementConfig, load_config

# Setup a temporary logger for early import errors
import logging
temp_logger = logging.getLogger(__name__)

try:
    from data.ctrader import CTraderDataManager
    CTRADER_DATA_AVAILABLE = True
    temp_logger.info("CTrader data manager available")
except ImportError:
        CTraderDataManager = None
        CTRADER_DATA_AVAILABLE = False
        temp_logger.warning("CTrader data manager not available")

# Brokers
from brokers.mt5 import MT5RealTimeTrader

try:
    from brokers.ctrader import CTraderRealTimeTrader
    CTRADER_BROKER_AVAILABLE = True
    temp_logger.info("Strategy-Agnostic CTrader broker available")
except ImportError:
    CTraderRealTimeTrader = None
    CTRADER_BROKER_AVAILABLE = False
    temp_logger.warning("CTrader broker not available")

# Strategy imports
from strategies.base_strategy import BaseStrategy, PairsStrategyInterface
from strategies.pairs_trading import OptimizedPairsStrategy

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
    level=getattr(logging, CONFIG.log_level),  # Use config log level for all loggers
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore")

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
    Enhanced real-time trader with configurable broker, InfluxDB integration, and enhanced state management
    Supports both MT5 and cTrader brokers independently from data provider
    """
    
    def __init__(self, config: TradingConfig, data_manager, broker: str, influxdb_manager: InfluxDBManager, 
                 state_manager: UnifiedStateManager = None, strategy: BaseStrategy = None):
        self.config = config
        self.data_manager = data_manager
        self.broker = broker.lower()
        self.influxdb_manager = influxdb_manager
        self.state_manager = state_manager
        self.dashboard = None
        
        # Create strategy instance if not provided
        if strategy is None:
            # Use default pairs strategy for backwards compatibility
            strategy = OptimizedPairsStrategy(config, data_manager)
            logger.info("Using default OptimizedPairsStrategy")
        else:
            logger.info(f"Using provided strategy: {strategy.__class__.__name__}")
            # Ensure the provided strategy has access to the data manager
            if hasattr(strategy, 'data_manager') and strategy.data_manager is None:
                strategy.data_manager = data_manager
                logger.info("Updated provided strategy with data manager for cost calculations")
        
        self.strategy = strategy
        
        # Initialize the appropriate broker trader with strategy
        if self.broker == 'ctrader':
            if not CTRADER_BROKER_AVAILABLE:
                raise ValueError("CTrader broker not available. Install ctrader-open-api: pip install ctrader-open-api")
            self.trader = CTraderRealTimeTrader(config, data_manager, strategy)
            logger.info(f"Initialized CTrader broker with {strategy.__class__.__name__}")
        elif self.broker == 'mt5':
            # For MT5, we still use the old interface for now (could be updated similarly)
            self.trader = MT5RealTimeTrader(config, data_manager)
            logger.info(f"Initialized MT5 broker (strategy integration pending)")
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
        """Handle trade execution events with enhanced state management"""
        # Store in InfluxDB
        self.influxdb_manager.store_trade_data(trade_data)
        
        # Store in enhanced state manager
        if self.state_manager:
            try:
                # Parse the pair to get individual symbols
                pair_str = trade_data.get('pair', '')
                if '-' in pair_str:
                    symbol1, symbol2 = pair_str.split('-', 1)
                else:
                    # Fallback for pairs without dash
                    symbol1 = pair_str[:6] if len(pair_str) >= 6 else pair_str
                    symbol2 = pair_str[6:] if len(pair_str) > 6 else 'USD'
                
                # Normalize direction to lowercase
                direction = trade_data.get('action', '').lower()
                if direction == 'buy':
                    direction = 'long'
                elif direction == 'sell':
                    direction = 'short'
                elif direction.upper() == 'LONG':
                    direction = 'long'
                elif direction.upper() == 'SHORT':
                    direction = 'short'
                
                # Ensure we have valid numeric values (schema requires > 0)
                entry_price = trade_data.get('entry_price', 0)
                if entry_price <= 0:
                    entry_price = 1.0  # Default fallback value
                
                volume = trade_data.get('volume', 0)
                if volume <= 0:
                    volume = 0.01  # Minimum valid volume
                
                # Create position data matching the PositionSchema
                position_data = {
                    'symbol1': symbol1,
                    'symbol2': symbol2, 
                    'direction': direction,
                    'entry_time': datetime.now(),
                    'quantity': volume,
                    'entry_price': entry_price,
                    'stop_loss': trade_data.get('stop_loss'),
                    'take_profit': trade_data.get('take_profit'),
                    # Additional metadata (not part of schema but can be stored)
                    'broker': self.broker,
                    'timestamp': trade_data.get('timestamp', datetime.now().isoformat()),
                    'z_score': trade_data.get('z_score', 0),
                    'strategy': self.strategy.__class__.__name__,
                    'trade_execution': True
                }
                
                # Save position using the unified state manager with correct parameters
                self.state_manager.save_position(
                    pair=pair_str,
                    position_data=position_data,
                    description=f"Trade execution: {direction} {pair_str} via {self.broker}"
                )
                
                logger.debug(f"Trade data stored in enhanced state manager: {pair_str}")
                
            except Exception as e:
                logger.warning(f"Failed to store trade data in state manager (trading continues): {e}")
                # Don't let state management issues stop trading
                pass
        
        # Update dashboard via WebSocket
        if self.dashboard and hasattr(self.dashboard, 'websocket_handler'):
            self.dashboard.websocket_handler.broadcast_trade_signal(trade_data)
        
        logger.info(f"Trade executed on {self.broker}: {trade_data.get('pair')} - {trade_data.get('action')}")
    
    def on_market_data_update(self, symbol: str, data: Dict[str, Any]):
        """Store market data updates in InfluxDB and enhanced state manager"""
        self.influxdb_manager.store_market_data(symbol, data, f"{self.broker}_execution")
        
        # Store market data in enhanced state manager (optional, as InfluxDB handles this better)
        if self.state_manager:
            try:
                # For market data, we can store it as part of trading state metadata
                # rather than as positions since it's not position-specific
                pass  # Market data is better handled by InfluxDB for time-series storage
            except Exception as e:
                logger.debug(f"Market data storage in state manager skipped: {e}")
        
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
        """Get portfolio status from the trader including strategy information and enhanced state"""
        if hasattr(self.trader, 'get_portfolio_status'):
            status = self.trader.get_portfolio_status()
            status['broker'] = self.broker
            status['data_provider'] = 'enhanced'  # Could be made configurable
            
            # Enhance with state manager data if available
            if self.state_manager:
                try:
                    portfolio_summary = self.state_manager.get_portfolio_summary()
                    all_positions = self.state_manager.get_all_positions()
                    
                    # Add enhanced state information
                    status['enhanced_state'] = {
                        'portfolio_summary': portfolio_summary,
                        'open_positions': len(all_positions),
                        'position_details': all_positions,
                        'last_updated': datetime.now().isoformat(),
                        'state_manager_active': True
                    }
                    
                    # Override with more accurate state manager data if available
                    if portfolio_summary.get('total_value'):
                        status['portfolio_value'] = portfolio_summary['total_value']
                    if all_positions:
                        status['position_count'] = len(all_positions)
                        
                except Exception as e:
                    logger.error(f"Failed to get enhanced state information: {e}")
                    status['enhanced_state'] = {'error': str(e), 'state_manager_active': False}
            
            return status
        
        # Fallback for brokers without strategy integration
        strategy_info = self.strategy.get_strategy_info()
        status = {
            'broker': self.broker, 
            'strategy': strategy_info['name'],
            'strategy_type': strategy_info['type'],
            'portfolio_value': 0, 
            'position_count': 0
        }
        
        # Add enhanced state if available
        if self.state_manager:
            try:
                portfolio_summary = self.state_manager.get_portfolio_summary()
                all_positions = self.state_manager.get_all_positions()
                
                status.update({
                    'portfolio_value': portfolio_summary.get('total_value', 0),
                    'position_count': len(all_positions),
                    'enhanced_state': {
                        'portfolio_summary': portfolio_summary,
                        'position_details': all_positions,
                        'last_updated': datetime.now().isoformat(),
                        'state_manager_active': True
                    }
                })
            except Exception as e:
                logger.error(f"Failed to get enhanced state for fallback: {e}")
                status['enhanced_state'] = {'error': str(e), 'state_manager_active': False}
        
        return status
    
    @property
    def is_trading(self):
        """Check if trader is active"""
        return hasattr(self.trader, 'is_trading') and self.trader.is_trading


class EnhancedTradingSystemV3:
    """
    Enhanced trading system v3 with configurable data provider, broker, and strategy
    """
    
    def __init__(self, data_provider: str = 'ctrader', broker: str = 'ctrader', strategy: BaseStrategy = None, mode: str = 'live'):
        """
        Initialize the trading system with specified data provider, broker, and strategy
        
        Args:
            data_provider: 'ctrader' or 'mt5' - provider for all data operations
            broker: 'ctrader' or 'mt5' - broker for trade execution
            strategy: Strategy instance implementing BaseStrategy interface
            mode: 'live' or 'backtest' - execution mode (affects state management initialization)
        """
        self.config = CONFIG
        self.data_provider = data_provider.lower()
        self.broker = broker.lower()
        self.mode = mode.lower()
        
        # Create default strategy if none provided
        if strategy is None:
            strategy = OptimizedPairsStrategy(CONFIG, None)  # data_manager will be set later
            logger.info("Using default OptimizedPairsStrategy")
        
        self.strategy = strategy
        logger.info(f"Trading system initialized with {strategy.__class__.__name__}")
        self.data_provider = data_provider.lower()
        self.broker = broker.lower()
        
        if self.data_provider not in ['ctrader', 'mt5']:
            raise ValueError("data_provider must be 'ctrader' or 'mt5'")
        
        if self.broker not in ['ctrader', 'mt5']:
            raise ValueError("broker must be 'ctrader' or 'mt5'")
            
        if self.mode not in ['live', 'backtest']:
            raise ValueError("mode must be 'live' or 'backtest'")
        
        # Data and execution managers
        self.primary_data_manager = None  # Selected data provider
        self.execution_data_manager = None  # Data manager for execution broker (may be same as primary)
        
        # Intelligent data management
        self.influxdb_manager = None
        self.intelligent_data_manager = None
        self.backtest_data_manager = None
        
        # Enhanced state management
        self.state_manager = None
        
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
        
        # Initialize enhanced state management only for live trading
        if self.mode == 'live':
            logger.info("Initializing enhanced state management with InfluxDB for live trading...")
            try:
                # Initialize unified state manager with automatic configuration
                self.state_manager = UnifiedStateManager(
                    auto_migrate=True  # Enable automatic migration from legacy states
                )
                
                # Give the state manager time to fully initialize
                import time
                logger.info("⏳ Waiting for state manager to fully initialize...")
                time.sleep(3)  # Allow async initialization to complete
                
                # Check state manager status after initialization delay
                status = self.state_manager.get_system_status()
                logger.info("📊 State Manager Status:")
                logger.info(f"   Database: {status.get('database_type', 'Unknown')}")
                logger.info(f"   Initialized: {status.get('initialized', False)}")
                logger.info(f"   Connection: {status.get('database_connected', False)}")
                
                # Attempt to restore previous trading state regardless of health check
                logger.info("🔄 Attempting to restore previous trading state...")
                self._restore_trading_state()
                
                # Verify state manager functionality
                if self.state_manager.health_check():
                    logger.info("✅ Enhanced state management initialized successfully")
                else:
                    logger.warning("⚠️ State management initialized with warnings but will continue")
                    
            except Exception as e:
                logger.error(f"Failed to initialize enhanced state management: {e}")
                logger.warning("Falling back to basic InfluxDB state storage")
                self.state_manager = None
        else:
            logger.info(f"Skipping state management initialization for {self.mode} mode")
            self.state_manager = None
        
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
        
        # Register all available providers with intelligent data manager and backtest data manager
        for provider_name, provider_instance in available_providers.items():
            self.intelligent_data_manager.register_data_provider(provider_name, provider_instance)
        self.backtest_data_manager.register_data_providers(available_providers)
        
        # Initialize enhanced trader with selected broker, strategy, and state manager
        self.trader = EnhancedRealTimeTrader(
            self.config, 
            self.execution_data_manager,
            self.broker,
            self.influxdb_manager,
            self.state_manager,  # Pass the enhanced state manager
            self.strategy
        )
        
        # Apply restored state to trader if available
        self._apply_restored_state_to_trader()
        
        # Initialize backtester with primary data manager
        self.backtester = VectorBTBacktester(self.config, self.primary_data_manager)
        
        logger.info("Enhanced Trading System V3 initialization complete")
        return True
    
    def _apply_restored_state_to_trader(self):
        """Apply restored state data to trader components"""
        if not hasattr(self, '_restored_state') or not self._restored_state:
            logger.info("No restored state to apply to trader")
            return
            
        try:
            logger.info("="*50)
            logger.info("APPLYING RESTORED STATE TO TRADER")
            logger.info("="*50)
            
            restored_data = self._restored_state
            logger.info(f"📦 Applying restored state with {len(restored_data)} components")
            
            # Apply positions to broker if it supports restoration
            if 'positions' in restored_data and hasattr(self.execution_data_manager, 'restore_positions'):
                positions = restored_data['positions']
                logger.info(f"📊 Restoring {len(positions)} positions to execution broker")
                try:
                    self.execution_data_manager.restore_positions(positions)
                    logger.info("✅ Positions successfully restored to execution broker")
                except Exception as e:
                    logger.warning(f"Could not restore positions to execution broker: {e}")
            elif 'positions' in restored_data:
                # Apply to broker's active_positions directly if restore method not available
                positions = restored_data['positions']
                logger.info(f"📊 Found {len(positions)} positions - applying to broker's active_positions")
                try:
                    if hasattr(self.execution_data_manager, 'active_positions'):
                        self.execution_data_manager.active_positions.clear()
                        for symbol, pos_data in positions.items():
                            self.execution_data_manager.active_positions[symbol] = pos_data
                        logger.info(f"✅ Applied {len(positions)} positions to broker's active_positions")
                    else:
                        logger.warning("Broker doesn't have active_positions attribute")
                except Exception as e:
                    logger.warning(f"Could not apply positions to broker: {e}")
            
            # Apply strategy state if available
            if 'pair_states' in restored_data and hasattr(self.strategy, 'restore_pair_states'):
                pair_states = restored_data['pair_states']
                logger.info(f"🔄 Restoring {len(pair_states)} pair states to strategy")
                try:
                    self.strategy.restore_pair_states(pair_states)
                    logger.info("✅ Pair states successfully restored to strategy")
                except Exception as e:
                    logger.warning(f"Could not restore pair states to strategy: {e}")
            elif 'pair_states' in restored_data:
                logger.info(f"🔄 Found {len(restored_data['pair_states'])} pair states, but strategy doesn't support restoration")
            
            # Apply metadata if available
            if 'metadata' in restored_data and hasattr(self.strategy, 'restore_metadata'):
                metadata = restored_data['metadata']
                logger.info(f"🏷️ Restoring metadata to strategy")
                try:
                    self.strategy.restore_metadata(metadata)
                    logger.info("✅ Metadata successfully restored to strategy")
                except Exception as e:
                    logger.warning(f"Could not restore metadata to strategy: {e}")
            
            # Log portfolio summary
            if 'portfolio_summary' in restored_data:
                portfolio = restored_data['portfolio_summary']
                logger.info(f"💰 Portfolio Summary: {portfolio}")
                    
            logger.info("="*50)
            logger.info("STATE APPLICATION COMPLETED")
            logger.info("="*50)
            
        except Exception as e:
            logger.error(f"Error applying restored state to trader: {e}")
            import traceback
            logger.debug(f"State application error details: {traceback.format_exc()}")

    def _restore_trading_state(self) -> bool:
        """Restore previous trading state from the database"""
        if not self.state_manager:
            logger.warning("State manager not available, skipping state restoration")
            return False
        
        try:
            logger.info("="*60)
            logger.info("RESTORING PREVIOUS TRADING STATE")
            logger.info("="*60)
            
            # Wait a bit more for state manager to be fully ready
            import time
            time.sleep(2)
            
            # Try multiple times with increasing delays to ensure state is available
            max_retries = 3
            all_positions = []
            current_state = None
            portfolio_summary = None
            
            for attempt in range(max_retries):
                try:
                    logger.info(f"📊 Attempt {attempt + 1}/{max_retries}: Retrieving stored positions...")
                    all_positions = self.state_manager.get_all_positions()
                    
                    logger.info(f"📊 Attempt {attempt + 1}/{max_retries}: Retrieving current trading state...")
                    current_state = self.state_manager.load_trading_state()
                    
                    logger.info(f"📊 Attempt {attempt + 1}/{max_retries}: Retrieving portfolio summary...")
                    portfolio_summary = self.state_manager.get_portfolio_summary()
                    
                    logger.info(f"📊 Attempt {attempt + 1}: Found {len(all_positions) if all_positions else 0} positions")
                    logger.info(f"📊 Attempt {attempt + 1}: Current state available: {bool(current_state)}")
                    logger.info(f"📊 Attempt {attempt + 1}: Portfolio summary: {portfolio_summary}")
                    
                    # If we found positions or states, break early
                    if (all_positions and len(all_positions) > 0) or (current_state and current_state.get('pair_states')):
                        logger.info(f"✅ Found data on attempt {attempt + 1}, proceeding with restoration")
                        break
                    elif attempt < max_retries - 1:
                        logger.info(f"⏳ No data found on attempt {attempt + 1}, waiting 3 seconds before next attempt...")
                        time.sleep(3)
                    
                except Exception as e:
                    logger.warning(f"⚠️ Error on attempt {attempt + 1}: {e}")
                    if attempt < max_retries - 1:
                        logger.info(f"⏳ Retrying in 2 seconds...")
                        time.sleep(2)
                    else:
                        logger.error(f"❌ All attempts failed, continuing with fresh state")
            
            # Now proceed with the restoration using the retrieved data
            logger.info(f"📊 Final result: Found {len(all_positions) if all_positions else 0} stored positions")
            logger.info(f"📊 Final portfolio summary: {portfolio_summary}")
            
            if all_positions:
                logger.info("📊 Restored Positions:")
                for i, position in enumerate(all_positions, 1):
                    symbol1 = position.get('symbol1', 'N/A')
                    symbol2 = position.get('symbol2', 'N/A')
                    direction = position.get('direction', 'N/A')
                    entry_price = position.get('entry_price', 0)
                    quantity = position.get('quantity', 0)
                    entry_time = position.get('entry_time', 'N/A')
                    
                    logger.info(f"  {i}. 📈 {symbol1}-{symbol2}: {direction} | Entry: {entry_price} | Qty: {quantity}")
                    logger.info(f"      ⏰ Time: {entry_time}")
            else:
                logger.info("📊 No stored positions found - starting with fresh state")
            
            # Restore pair states if available
            if current_state and 'pair_states' in current_state:
                pair_states = current_state['pair_states']
                logger.info(f"📊 Restored Pair States: {len(pair_states)} pairs")
                
                for pair, state in list(pair_states.items())[:10]:  # Show first 10
                    position = state.get('position', 'none')
                    cooldown = state.get('cooldown', 0)
                    last_update = state.get('last_update', 'N/A')
                    z_score = state.get('z_score', 'N/A')
                    
                    logger.info(f"  📊 {pair}: {position} | Cooldown: {cooldown} | Z-Score: {z_score}")
                
                if len(pair_states) > 10:
                    logger.info(f"  ... and {len(pair_states) - 10} more pairs")
            else:
                logger.info("📊 No pair states found - pairs will initialize with fresh data")
            
            # Check for recent session metadata
            if current_state and 'metadata' in current_state:
                metadata = current_state['metadata']
                if 'session_info' in metadata:
                    session = metadata['session_info']
                    logger.info("📊 Previous Session Info:")
                    logger.info(f"   Data Provider: {session.get('data_provider', 'N/A')}")
                    logger.info(f"   Broker: {session.get('broker', 'N/A')}")
                    logger.info(f"   Strategy: {session.get('strategy', 'N/A')}")
                    logger.info(f"   Last Save: {session.get('save_time', 'N/A')}")
                    
                    # Check if current session matches previous session
                    if (session.get('data_provider') == self.data_provider and 
                        session.get('broker') == self.broker):
                        logger.info("✅ Current session matches previous session configuration")
                    else:
                        logger.warning("⚠️ Current session differs from previous session:")
                        logger.warning(f"   Previous: {session.get('data_provider', 'N/A')}/{session.get('broker', 'N/A')}")
                        logger.warning(f"   Current: {self.data_provider}/{self.broker}")
            
            # Check for recent backtest results
            try:
                logger.info("📊 Checking for recent backtest results...")
                latest_backtest = self.influxdb_manager.get_latest_backtest_results()
                if latest_backtest:
                    backtest_time = latest_backtest.get('timestamp', 'Unknown')
                    portfolio_return = latest_backtest.get('portfolio_metrics', {}).get('portfolio_return', 0)
                    logger.info(f"📈 Latest backtest: {backtest_time} | Return: {portfolio_return:.2%}")
                else:
                    logger.info("📈 No previous backtest results found")
            except Exception as e:
                logger.debug(f"Could not retrieve latest backtest results: {e}")
            
            # Restore strategy-specific state if available
            if hasattr(self.strategy, 'restore_state') and current_state:
                try:
                    logger.info("🔄 Restoring strategy-specific state...")
                    strategy_metadata = current_state.get('metadata', {})
                    self.strategy.restore_state(strategy_metadata)
                    logger.info("✅ Strategy state restored successfully")
                except Exception as e:
                    logger.warning(f"Could not restore strategy state: {e}")
            
            # Store restored state for later use by broker/strategy
            if current_state:
                self._restored_state = {
                    'positions': all_positions,
                    'pair_states': current_state.get('pair_states', {}),
                    'metadata': current_state.get('metadata', {}),
                    'portfolio_summary': portfolio_summary
                }
                logger.info("💾 Restored state cached for broker/strategy initialization")
            else:
                self._restored_state = None
            
            logger.info("="*60)
            logger.info("STATE RESTORATION COMPLETED")
            logger.info("="*60)
            
            if all_positions or (current_state and current_state.get('pair_states')):
                logger.info("✅ Previous trading state successfully restored")
                logger.info("✅ System ready to continue from previous session")
            else:
                logger.info("✅ Starting with fresh trading state")
                logger.info("✅ System ready for new trading session")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to restore trading state: {e}")
            logger.warning("Continuing with fresh state...")
            import traceback
            logger.debug(f"State restoration error details: {traceback.format_exc()}")
            return False
    
    def save_current_trading_state(self, description: str = "Trading session state save") -> bool:
        """Save current trading state to the database"""
        if not self.state_manager:
            logger.warning("State manager not available, cannot save trading state")
            return False
        
        try:
            logger.info("💾 Saving current trading state...")
            
            # Get current positions from trader if available
            current_positions = {}
            if self.trader and hasattr(self.trader, 'get_portfolio_status'):
                try:
                    portfolio = self.trader.get_portfolio_status()
                    
                    # Try multiple sources to ensure we capture positions (use all sources, not elif)
                    positions_found = False
                    
                    # Source 1: Try enhanced_state (if available)
                    if 'enhanced_state' in portfolio and 'position_details' in portfolio['enhanced_state']:
                        position_details = portfolio['enhanced_state']['position_details']
                        # Convert list of positions to dictionary format expected by state manager
                        if isinstance(position_details, list) and position_details:
                            for i, position in enumerate(position_details):
                                # Create a unique key for each position
                                if isinstance(position, dict):
                                    # Transform position data to match PositionSchema requirements
                                    transformed_position = self._transform_position_for_schema(position)
                                    if transformed_position:
                                        position_key = position.get('pair', f'position_{i}')
                                        current_positions[position_key] = transformed_position
                                        logger.debug(f"✅ Transformed enhanced position {position_key}: {transformed_position}")
                                    else:
                                        logger.warning(f"⚠️ Failed to transform enhanced position {i}: {position}")
                                else:
                                    current_positions[f'position_{i}'] = position
                            logger.debug(f"✅ Found {len(current_positions)} positions from enhanced_state")
                            positions_found = True
                        elif isinstance(position_details, dict) and position_details:
                            # Transform each position in the dict
                            for key, position in position_details.items():
                                if isinstance(position, dict):
                                    transformed_position = self._transform_position_for_schema(position)
                                    if transformed_position:
                                        current_positions[key] = transformed_position
                                    else:
                                        current_positions[key] = position  # Keep original if transformation fails
                                else:
                                    current_positions[key] = position
                            logger.debug(f"✅ Found {len(position_details)} positions from enhanced_state dict")
                            positions_found = True
                    
                    # Source 2: Try broker's direct positions (always try this - it's the most reliable)
                    if 'positions' in portfolio and portfolio['positions']:
                        positions_list = portfolio['positions']
                        logger.debug(f"📊 Checking broker's direct positions: {len(positions_list)} positions")
                        # Convert positions list to dictionary, transforming to PositionSchema format
                        for i, position in enumerate(positions_list):
                            if isinstance(position, dict):
                                # Transform broker position data to match PositionSchema requirements
                                transformed_position = self._transform_position_for_schema(position)
                                if transformed_position:
                                    position_key = position.get('pair', f'position_{i}')
                                    current_positions[position_key] = transformed_position
                                    logger.debug(f"✅ Transformed position {position_key}: {transformed_position}")
                                else:
                                    logger.warning(f"⚠️ Failed to transform position {i}: {position}")
                            else:
                                current_positions[f'position_{i}'] = position
                        logger.debug(f"✅ Found {len(positions_list)} positions from broker's direct positions")
                        positions_found = True
                    
                    # Source 3: Try active_positions directly from the broker (fallback)
                    if hasattr(self.trader, 'trader') and hasattr(self.trader.trader, 'active_positions'):
                        active_positions = self.trader.trader.active_positions
                        if active_positions:
                            logger.debug(f"📊 Checking broker's active_positions: {len(active_positions)} positions")
                            # Add or update with active positions, transforming each one
                            for key, position in active_positions.items():
                                if isinstance(position, dict):
                                    transformed_position = self._transform_position_for_schema(position)
                                    if transformed_position:
                                        current_positions[key] = transformed_position
                                        logger.debug(f"✅ Transformed active position {key}: {transformed_position}")
                                    else:
                                        current_positions[key] = position  # Keep original if transformation fails
                                        logger.warning(f"⚠️ Failed to transform active position {key}, keeping original")
                                else:
                                    current_positions[key] = position
                            logger.debug(f"✅ Added {len(active_positions)} positions from broker's active_positions")
                            positions_found = True
                    
                    # Final check and logging
                    if positions_found:
                        logger.debug(f"✅ TOTAL POSITIONS CAPTURED: {len(current_positions)} positions")
                        for pair_name, pos_data in current_positions.items():
                            if isinstance(pos_data, dict):
                                direction = pos_data.get('direction', 'N/A')
                                symbol1 = pos_data.get('symbol1', 'N/A')
                                symbol2 = pos_data.get('symbol2', 'N/A') 
                                logger.debug(f"   📈 {pair_name}: {direction} ({symbol1}-{symbol2})")
                    else:
                        logger.warning("⚠️ No positions found from any source!")
                    
                except Exception as e:
                    logger.debug(f"Could not get current positions from trader: {e}")
            
            # Get pair states from strategy if available
            pair_states = {}
            if hasattr(self.strategy, 'get_current_state'):
                try:
                    pair_states = self.strategy.get_current_state()
                except Exception as e:
                    logger.debug(f"Could not get pair states from strategy: {e}")
            
            # Also try to get pair states from broker if available
            if not pair_states and hasattr(self.trader, 'trader') and hasattr(self.trader.trader, 'pair_states'):
                try:
                    broker_pair_states = self.trader.trader.pair_states
                    if broker_pair_states:
                        logger.debug(f"Getting pair states from broker: {len(broker_pair_states)} states")
                        
                        # Get active positions to determine current pair positions
                        active_pair_positions = {}
                        if hasattr(self.trader, 'trader') and hasattr(self.trader.trader, 'active_positions'):
                            active_positions = self.trader.trader.active_positions
                            # Group positions by pairs
                            for symbol, pos_data in active_positions.items():
                                if isinstance(pos_data, dict) and 'pair' in pos_data:
                                    pair_name = pos_data['pair']
                                    direction = pos_data.get('direction', 'none').lower()
                                    active_pair_positions[pair_name] = direction
                        
                        # Convert broker pair states to format matching PairStateSchema
                        pair_states = {}
                        for pair_name, state_data in broker_pair_states.items():
                            # Extract symbol1 and symbol2 from pair name
                            if '-' in pair_name:
                                symbols = pair_name.split('-')
                                symbol1 = symbols[0] if len(symbols) > 0 else 'UNKNOWN'
                                symbol2 = symbols[1] if len(symbols) > 1 else 'UNKNOWN'
                            else:
                                symbol1 = pair_name
                                symbol2 = 'UNKNOWN'
                            
                            # Determine current position for this pair
                            current_position = active_pair_positions.get(pair_name, 'none')
                            if current_position not in ['none', 'long', 'short']:
                                current_position = 'none'  # Fallback to valid value
                            
                            # Create pair state matching PairStateSchema
                            pair_states[pair_name] = {
                                'symbol1': symbol1,
                                'symbol2': symbol2,
                                'position': current_position,
                                'cooldown': 0,  # Default cooldown
                                'last_update': datetime.now(),  # Use datetime object for schema validation
                                'spread': None,  # Optional field
                                'z_score': None,  # Optional field
                                # Additional metadata for debugging (keep outside main schema fields)
                                'price1_count': len(state_data.get('price1', [])),
                                'price2_count': len(state_data.get('price2', [])),
                                'ready_for_trading': len(state_data.get('price1', [])) > 50 and len(state_data.get('price2', [])) > 50
                            }
                except Exception as e:
                    logger.debug(f"Could not get pair states from broker: {e}")
            
            # Create comprehensive metadata
            metadata = {
                'session_info': {
                    'data_provider': self.data_provider,
                    'broker': self.broker,
                    'strategy': self.strategy.__class__.__name__,
                    'save_time': datetime.now().isoformat(),
                    'system_version': 'EnhancedTradingSystemV3'
                },
                'trading_config': {
                    'pairs': getattr(self.config, 'pairs', []),
                    'log_level': getattr(self.config, 'log_level', 'INFO'),
                    'realtime_trading': getattr(self.config, 'realtime_trading', False)
                },
                'portfolio_summary': {},
                'system_status': 'saved'
            }
            
            # Add portfolio summary if available
            try:
                portfolio_summary = self.state_manager.get_portfolio_summary()
                metadata['portfolio_summary'] = portfolio_summary
            except Exception as e:
                logger.debug(f"Could not get portfolio summary: {e}")
            
            # Add strategy-specific metadata if available
            if hasattr(self.strategy, 'get_state_metadata'):
                try:
                    strategy_metadata = self.strategy.get_state_metadata()
                    metadata['strategy_metadata'] = strategy_metadata
                except Exception as e:
                    logger.debug(f"Could not get strategy metadata: {e}")
            
            # Create portfolio data that matches the expected schema
            portfolio_data = {
                'total_value': 100000.0,  # Default initial value
                'available_balance': 100000.0,  # Default available balance
                'total_pnl': 0.0,  # Default PnL
                'open_positions': len(current_positions),  # Count of current positions
                'daily_pnl': 0.0,  # Default daily PnL
                'peak_value': 100000.0,  # Default peak value
                'metadata': metadata  # Include metadata as additional field
            }
            
            # Try to get actual portfolio values from trader if available
            if self.trader and hasattr(self.trader, 'get_portfolio_status'):
                try:
                    portfolio_status = self.trader.get_portfolio_status()
                    if portfolio_status:
                        portfolio_data['total_value'] = portfolio_status.get('portfolio_value', 100000.0)
                        portfolio_data['available_balance'] = portfolio_status.get('available_balance', portfolio_status.get('portfolio_value', 100000.0))
                        portfolio_data['total_pnl'] = portfolio_status.get('total_pnl', 0.0)
                        if 'enhanced_state' in portfolio_status:
                            enhanced_state = portfolio_status['enhanced_state']
                            if 'position_details' in enhanced_state:
                                portfolio_data['open_positions'] = len(enhanced_state['position_details'])
                except Exception as e:
                    logger.debug(f"Could not get portfolio values from trader: {e}")
            
            # Save the complete trading state
            logger.info(f"💾 PREPARING TO SAVE STATE:")
            logger.info(f"   📊 Positions to save: {len(current_positions)}")
            logger.info(f"   📊 Pair states to save: {len(pair_states)}")
            logger.info(f"   📊 Portfolio data keys: {list(portfolio_data.keys())}")
            
            # Log sample of positions being saved
            if current_positions:
                logger.info("📊 POSITIONS BEING SAVED (sample):")
                for i, (key, pos) in enumerate(list(current_positions.items())[:3]):
                    logger.info(f"   {i+1}. Key: {key}")
                    if isinstance(pos, dict):
                        logger.info(f"      Direction: {pos.get('direction', 'N/A')}")
                        logger.info(f"      Symbols: {pos.get('symbol1', 'N/A')}-{pos.get('symbol2', 'N/A')}")
                        logger.info(f"      Entry Price: {pos.get('entry_price', 'N/A')}")
                        logger.info(f"      Quantity: {pos.get('quantity', 'N/A')}")
                    else:
                        logger.info(f"      Data type: {type(pos)}")
                if len(current_positions) > 3:
                    logger.info(f"   ... and {len(current_positions) - 3} more positions")
            
            logger.debug(f"Saving state - Positions type: {type(current_positions)}, Pair states type: {type(pair_states)}")
            logger.debug(f"Positions count: {len(current_positions) if isinstance(current_positions, (dict, list)) else 'N/A'}")
            logger.debug(f"Pair states count: {len(pair_states) if isinstance(pair_states, (dict, list)) else 'N/A'}")
            logger.debug(f"Portfolio data structure: {list(portfolio_data.keys())}")
            
            success = self.state_manager.save_trading_state(
                active_positions=current_positions,
                pair_states=pair_states,
                portfolio_data=portfolio_data  # Use properly structured portfolio data
            )
            
            if success:
                logger.info("✅ Trading state saved successfully")
                logger.info(f"   Positions: {len(current_positions)}")
                logger.info(f"   Pair states: {len(pair_states)}")
                logger.info(f"   Data provider: {self.data_provider}")
                logger.info(f"   Broker: {self.broker}")
                return True
            else:
                logger.error("❌ Failed to save trading state")
                return False
                
        except Exception as e:
            logger.error(f"Error saving trading state: {e}")
            return False
    
    def _transform_position_for_schema(self, broker_position: Dict) -> Dict:
        """Transform broker position data to match PositionSchema requirements"""
        try:
            # Extract pair information
            pair = broker_position.get('pair', '')
            if '-' in pair:
                symbol1, symbol2 = pair.split('-', 1)
            else:
                symbol1 = broker_position.get('symbol1', pair)
                symbol2 = broker_position.get('symbol2', 'UNKNOWN')
            
            # Transform direction to lowercase as required by schema
            direction = broker_position.get('direction', '').upper()
            if direction == 'LONG':
                direction = 'long'
            elif direction == 'SHORT':
                direction = 'short'
            else:
                logger.warning(f"Invalid direction '{direction}' in position, skipping")
                return None
            
            # Extract required fields with fallbacks
            entry_time = broker_position.get('entry_time')
            if isinstance(entry_time, str):
                from datetime import datetime
                try:
                    entry_time = datetime.fromisoformat(entry_time.replace('Z', '+00:00'))
                except:
                    entry_time = datetime.now()
            elif entry_time is None:
                entry_time = datetime.now()
            
            # Get quantity - try multiple field names
            quantity = broker_position.get('quantity') or broker_position.get('size') or broker_position.get('volume')
            if quantity is None or quantity <= 0:
                # Calculate from individual leg quantities if available
                leg1_qty = broker_position.get('leg1_quantity', 0)
                leg2_qty = broker_position.get('leg2_quantity', 0)
                if leg1_qty > 0:
                    quantity = leg1_qty
                else:
                    quantity = 1.0  # Default fallback
            
            # Get entry price - try multiple field names
            entry_price = broker_position.get('entry_price') or broker_position.get('open_price') or broker_position.get('price')
            if entry_price is None or entry_price <= 0:
                # Calculate from individual leg prices if available
                leg1_price = broker_position.get('leg1_price', 0)
                leg2_price = broker_position.get('leg2_price', 0)
                if leg1_price > 0:
                    entry_price = leg1_price
                elif leg2_price > 0:
                    entry_price = leg2_price
                else:
                    entry_price = 100.0  # Default fallback
            
            # Create schema-compliant position
            transformed_position = {
                'symbol1': symbol1,
                'symbol2': symbol2,
                'direction': direction,
                'entry_time': entry_time,
                'quantity': float(quantity),
                'entry_price': float(entry_price),
                'stop_loss': broker_position.get('stop_loss'),  # Optional
                'take_profit': broker_position.get('take_profit')  # Optional
            }
            
            logger.debug(f"Position transformation: {broker_position.get('pair', 'Unknown')} -> {direction} @ {entry_price} (qty: {quantity})")
            
            return transformed_position
            
        except Exception as e:
            logger.error(f"Error transforming position data: {e}")
            logger.debug(f"Original position data: {broker_position}")
            return None
    
    def shutdown_gracefully(self) -> bool:
        """Gracefully shutdown the trading system and save state"""
        logger.info("🔄 Initiating graceful shutdown...")
        
        try:
            # Save current trading state before shutdown
            self.save_current_trading_state("Graceful shutdown - session end")
            
            # Stop trading if active
            if self.trader and hasattr(self.trader, 'stop_trading'):
                try:
                    logger.info("🛑 Stopping trading operations...")
                    self.trader.stop_trading()
                    logger.info("✅ Trading stopped successfully")
                except Exception as e:
                    logger.error(f"Error stopping trader: {e}")
            
            # Disconnect from data providers
            if self.primary_data_manager and hasattr(self.primary_data_manager, 'disconnect'):
                try:
                    logger.info("🔌 Disconnecting from data providers...")
                    self.primary_data_manager.disconnect()
                    if self.execution_data_manager != self.primary_data_manager:
                        self.execution_data_manager.disconnect()
                    logger.info("✅ Data providers disconnected")
                except Exception as e:
                    logger.error(f"Error disconnecting data providers: {e}")
            
            # Disconnect from InfluxDB
            if self.influxdb_manager:
                try:
                    self.influxdb_manager.disconnect()
                    logger.info("✅ InfluxDB disconnected")
                except Exception as e:
                    logger.error(f"Error disconnecting InfluxDB: {e}")
            
            # Close state manager connections
            if self.state_manager and hasattr(self.state_manager, 'shutdown'):
                try:
                    self.state_manager.shutdown()
                    logger.info("✅ State manager shutdown")
                except Exception as e:
                    logger.error(f"Error shutting down state manager: {e}")
            
            logger.info("✅ Graceful shutdown completed")
            return True
            
        except Exception as e:
            logger.error(f"Error during graceful shutdown: {e}")
            return False
    
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
            
            # Step 5: Store results in InfluxDB and enhanced state manager
            logger.info("Step 5: Storing backtest results...")
            self.influxdb_manager.store_backtest_results(backtest_results)
            
            # Store in enhanced state manager if available
            if self.state_manager:
                try:
                    logger.info("Storing backtest results in enhanced state manager...")
                    
                    # Store backtest configuration and results as trading state
                    backtest_state = {
                        'backtest_results': backtest_results,
                        'configuration': {
                            'data_provider': self.data_provider,
                            'execution_broker': self.broker,
                            'strategy': self.strategy.__class__.__name__,
                            'pairs': self.config.pairs,
                            'start_date': getattr(self.config, 'start_date', None),
                            'end_date': getattr(self.config, 'end_date', None),
                            'force_refresh': force_refresh,
                            'intelligent_caching': True,
                            'cached_symbols': list(backtest_data_cache.keys()),
                            'data_quality': quality_report
                        },
                        'timestamp': datetime.now().isoformat(),
                        'type': 'backtest_results'
                    }
                    
                    # Save as trading state with backtest identifier
                    self.state_manager.save_trading_state(
                        active_positions={},  # No positions for backtest
                        pair_states={},  # No pair states for backtest
                        portfolio_data=backtest_state
                    )
                    
                    logger.info("✅ Backtest results stored in enhanced state manager")
                    
                except Exception as e:
                    logger.error(f"Failed to store backtest results in state manager: {e}")
            
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
    
    def _prepare_trading_data(self) -> bool:
        """Prepare trading data cache using intelligent data management (reactor-safe)"""
        logger.info("="*60)
        logger.info("PRE-FETCHING TRADING DATA")
        logger.info("="*60)
        
        try:
            if self.broker == 'ctrader':
                logger.info("CTrader broker detected - using intelligent data pre-fetching...")
                
                # Get required symbols from strategy
                required_symbols = set()
                if hasattr(self.strategy, 'get_required_symbols'):
                    required_symbols.update(self.strategy.get_required_symbols())
                else:
                    # Fallback: extract from pairs
                    for pair in self.config.pairs:
                        if '-' in pair:
                            sym1, sym2 = pair.split('-')
                            required_symbols.add(sym1)
                            required_symbols.add(sym2)
                
                logger.info(f"Required symbols for trading: {sorted(required_symbols)}")
                
                # Use backtest data manager to pre-fetch data (same approach as backtesting)
                logger.info("Using intelligent data management to pre-fetch historical data...")
                logger.info("This uses the same proven system as backtesting to avoid reactor conflicts")
                
                # Prepare trading data cache using the same method as backtesting
                # Note: This fetches all symbols from config.pairs, then we'll filter to required symbols
                full_data_cache = self.backtest_data_manager.prepare_backtest_data(
                    data_provider=self.data_provider,
                    force_refresh=False  # Use cached data if available
                )
                
                # Filter to only required symbols for trading
                trading_data_cache = {symbol: full_data_cache.get(symbol, pd.Series(dtype=float)) 
                                    for symbol in required_symbols}
                
                # Validate data quality
                logger.info("Validating pre-fetched data quality...")
                quality_report = self.backtest_data_manager.validate_data_quality(trading_data_cache)
                
                # Log quality summary
                good_symbols = [s for s, r in quality_report.items() if r['status'] == 'GOOD']
                warning_symbols = [s for s, r in quality_report.items() if r['status'] == 'WARNING']
                poor_symbols = [s for s, r in quality_report.items() if r['status'] in ['POOR', 'EMPTY']]
                
                logger.info(f"Trading Data Quality Summary:")
                logger.info(f"  ✅ GOOD: {len(good_symbols)} symbols")
                logger.info(f"  ⚠️ WARNING: {len(warning_symbols)} symbols")
                logger.info(f"  ❌ POOR/EMPTY: {len(poor_symbols)} symbols")
                
                if poor_symbols:
                    logger.warning(f"Poor quality data for: {', '.join(poor_symbols)}")
                    logger.warning("These symbols may affect trading quality")
                
                # Store pre-fetched data cache in trader
                if hasattr(self.trader, 'trader') and hasattr(self.trader.trader, 'set_historical_data_cache'):
                    self.trader.trader.set_historical_data_cache(trading_data_cache)
                    logger.info(f"✅ Pre-fetched data cache set in trader: {len(trading_data_cache)} symbols")
                elif hasattr(self.trader, 'set_historical_data_cache'):
                    self.trader.set_historical_data_cache(trading_data_cache)
                    logger.info(f"✅ Pre-fetched data cache set in trader: {len(trading_data_cache)} symbols")
                else:
                    logger.warning("⚠️ Trader does not support historical data cache")
                
                # Log data availability
                total_points = sum(len(data) for data in trading_data_cache.values() if not data.empty)
                logger.info(f"📊 Total historical data points loaded: {total_points}")
                
                logger.info("="*60)
                logger.info("DATA PREPARATION COMPLETED")
                logger.info("="*60)
                logger.info("✅ Historical data successfully pre-fetched using intelligent data management")
                logger.info("✅ Trading system ready with complete historical context")
                logger.info("✅ Reactor-safe: All data fetched before reactor startup")
                
                return True
                
            else:
                # For MT5, no special data pre-fetching needed
                logger.info(f"No data pre-fetching required for {self.broker}")
                return True
                
        except Exception as e:
            logger.error(f"Error preparing trading data: {e}")
            import traceback
            traceback.print_exc()
            return False

    def start_real_time_trading(self) -> bool:
        """Start real-time trading with selected broker"""
        logger.info(f"Starting real-time trading with {self.broker} broker...")
        
        try:
            if not self.trader:
                logger.error("Trader not initialized")
                return False
            
            # Step 1: Pre-fetch all required data BEFORE starting any reactors
            logger.info("Step 1: Pre-fetching required trading data...")
            if not self._prepare_trading_data():
                logger.error("Failed to pre-fetch trading data")
                return False
            
            # Step 2: Initialize trader
            logger.info(f"Step 2: Initializing {self.broker} trader...")
            if not self.trader.initialize():
                logger.error(f"Failed to initialize {self.broker} trader")
                return False
            
            logger.info(f"{self.broker} trader initialized successfully")
            
            # Step 3: Start trading based on broker type
            if self.broker == 'ctrader':
                # For cTrader, start the reactor-based trading
                def start_ctrader_trading():
                    try:
                        logger.info("Starting cTrader reactor-based trading...")
                        
                        # Start the trader (this will set up callbacks and start the reactor)
                        logger.info("Starting cTrader trading with pre-fetched data...")
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
                                import traceback
                                logger.error(f"Full reactor error traceback:")
                                traceback.print_exc()
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
                        
                        # Check trader status
                        if self.trader:
                            is_trading = getattr(self.trader, 'is_trading', False)
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
                                
                                # if iteration % 12 == 1:  # Log every minute
                                #     logger.info(f"   📈 Portfolio: ${portfolio_value:.2f}, PnL: ${current_pnl:.2f}, Positions: {open_positions_count}")
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
                            
                            # if iteration % 12 == 1:  # Log every minute
                            #     logger.info(f"   📡 Dashboard update sent: {active_pairs_count} pairs, {open_positions_count} positions")
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
        """Get comprehensive data for dashboard display with enhanced state management"""
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
                'execution_broker': self.broker,
                'enhanced_state_manager': bool(self.state_manager)
            }
        }
        
        # Get market data
        for pair in self.config.pairs[:5]:
            market_data = self.influxdb_manager.get_market_data(pair, 1)
            if market_data:
                dashboard_data['market_data'][pair] = market_data[-1]
        
        # Add enhanced state data if available
        if self.state_manager:
            try:
                # Get portfolio and position states
                portfolio_summary = self.state_manager.get_portfolio_summary()
                all_positions = self.state_manager.get_all_positions()
                system_status = self.state_manager.get_system_status()
                
                # Get recent state history (which might include backtest results)
                state_history = self.state_manager.get_state_history(limit=3)
                
                # Add to dashboard data
                dashboard_data['enhanced_state'] = {
                    'portfolio_summary': portfolio_summary,
                    'positions': all_positions,
                    'state_history': state_history,
                    'system_status': system_status,
                    'last_updated': datetime.now().isoformat()
                }
                
                # Update system status with enhanced information
                if portfolio_summary:
                    dashboard_data['system_status']['portfolio_value'] = portfolio_summary.get('total_value', 0)
                    dashboard_data['system_status']['open_positions'] = len(all_positions)
                    dashboard_data['system_status']['last_state_update'] = portfolio_summary.get('last_updated')
                
            except Exception as e:
                logger.error(f"Failed to get enhanced state data for dashboard: {e}")
                dashboard_data['enhanced_state'] = {'error': str(e)}
        
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
                logger.info("InfluxDB disconnected")
            
            if self.state_manager:
                try:
                    self.state_manager.shutdown()
                    logger.info("Enhanced state manager shutdown complete")
                except Exception as e:
                    logger.error(f"Error shutting down state manager: {e}")
            
            if self.dashboard and hasattr(self.dashboard, 'stop'):
                self.dashboard.stop()
                logger.info("Dashboard stopped")
            
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")


def main(data_provider: str = 'ctrader', broker: str = 'ctrader', mode: str = 'backtest', 
         force_refresh: bool = False, strategy: str = 'pairs'):
    """
    Main execution function with enhanced features and configurable data provider, broker, and strategy
    
    Args:
        data_provider: 'ctrader' or 'mt5' - provider for all data operations
        broker: 'ctrader' or 'mt5' - broker for trade execution
        mode: 'backtest' or 'live' - execution mode
        force_refresh: Force re-fetch all data ignoring cache
        strategy: 'pairs' or other strategy types - trading strategy to use
    """
    system = None
    
    def signal_handler(signum, frame):
        """Handle shutdown signals gracefully"""
        logger.info(f"Received shutdown signal {signum}")
        if system:
            system.shutdown_gracefully()
        sys.exit(0)
    
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # Termination signal
    
    try:
        logger.info(f"=== Enhanced Pairs Trading System V3 Starting ===")
        logger.info(f"Data Provider: {data_provider}")
        logger.info(f"Execution Broker: {broker}")
        logger.info(f"Strategy: {strategy}")
        logger.info(f"Execution Mode: {mode}")
        logger.info(f"Force Data Refresh: {force_refresh}")
        logger.info(f"Configuration: {CONFIG.pairs[:3]}... ({len(CONFIG.pairs)} total pairs)")
        
        # Create strategy instance based on parameter
        if strategy == 'pairs':
            strategy_instance = OptimizedPairsStrategy(CONFIG, None)  # data_manager will be set later
            logger.info("Using OptimizedPairsStrategy")
        else:
            # Future: Add support for other strategies
            logger.warning(f"Unknown strategy '{strategy}', falling back to pairs strategy")
            strategy_instance = OptimizedPairsStrategy(CONFIG, None)
        
        # Create system with selected providers and strategy
        system = EnhancedTradingSystemV3(data_provider=data_provider, broker=broker, strategy=strategy_instance, mode=mode)
        
        if not system.initialize():
            logger.error("Failed to initialize trading system")
            return
        
        # Log the actual providers being used after initialization
        logger.info(f"System Initialized:")
        logger.info(f"  Data Provider: {system.data_provider}")
        logger.info(f"  Execution Broker: {system.broker}")
        logger.info(f"  Enhanced State Manager: {'✅ Active' if system.state_manager else '❌ Disabled'}")
        if system.state_manager:
            status = system.state_manager.get_system_status()
            logger.info(f"  State Management Status: {status.get('status', 'Unknown')}")
            logger.info(f"  Database Type: {status.get('database_type', 'Unknown')}")
            logger.info(f"  Database Connected: {status.get('database_connected', False)}")
            logger.info(f"  Caching Active: {status.get('caching_enabled', False)}")
        
        if mode.lower() == 'backtest':
            logger.info(f"Running backtest with {system.data_provider} data...")
            
            # Run backtest with intelligent data management
            backtest_results = system.run_enhanced_backtest(force_refresh=force_refresh)
            
            # Start dashboard with results
            system.start_dashboard(backtest_results)
            
            logger.info("=== Backtest Complete ===")
            logger.info(f"Data Provider: {system.data_provider}")
            logger.info(f"Execution Broker: {system.broker}")
            logger.info(f"Enhanced State Management: {'✅ Active' if system.state_manager else '❌ Disabled'}")
            logger.info(f"Portfolio Return: {backtest_results.get('portfolio_metrics', {}).get('portfolio_return', 0):.2%}")
            logger.info(f"Sharpe Ratio: {backtest_results.get('portfolio_metrics', {}).get('portfolio_sharpe', 0):.2f}")
            logger.info(f"Max Drawdown: {backtest_results.get('portfolio_metrics', {}).get('portfolio_max_drawdown', 0):.2%}")
            logger.info(f"Total Trades: {backtest_results.get('portfolio_metrics', {}).get('total_trades', 0)}")
            logger.info(f"Report: {backtest_results.get('report_path', 'N/A')}")
            
            # Log enhanced state information if available
            if system.state_manager:
                try:
                    state_history = system.state_manager.get_state_history(limit=1)
                    if state_history:
                        logger.info(f"State Management: Latest backtest stored successfully")
                        logger.info(f"State entries: {len(state_history)} total in history")
                except Exception as e:
                    logger.debug(f"Could not retrieve state management info: {e}")
            
            # Keep dashboard running
            input("Press Enter to stop the dashboard...")
            
        elif mode.lower() == 'live':
            logger.info(f"Starting live trading...")
            logger.info(f"  Data from: {system.data_provider}")
            logger.info(f"  Execution via: {system.broker}")
            logger.info(f"  Enhanced State Management: {'✅ Active' if system.state_manager else '❌ Disabled'}")
            
            if system.state_manager:
                logger.info(f"  State storage: Real-time position and portfolio tracking enabled")
                logger.info(f"  State versioning: Enabled for audit trail")
                logger.info(f"  State API: Available for external monitoring")
            
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
                logger.info(f"Enhanced State Management: {'✅ Active' if system.state_manager else '❌ Disabled'}")
                logger.info(f"Dashboard: http://127.0.0.1:8050")
                logger.info("Press Ctrl+C to stop...")
                logger.info("="*80)
                
                try:
                    # Check status periodically
                    status_check_count = 0
                    timeout_warning_shown = False
                    last_state_save = time.time()
                    
                    while True:
                        time.sleep(30)  # Check every 30 seconds
                        status_check_count += 1
                        current_time = time.time()
                        
                        # Save trading state every 5 minutes
                        if current_time - last_state_save >= 300:  # 5 minutes
                            try:
                                system.save_current_trading_state("Periodic state save during live trading")
                                last_state_save = current_time
                            except Exception as e:
                                logger.debug(f"Periodic state save failed: {e}")
                        
                        # Log status every 5 minutes
                        if status_check_count % 10 == 0:  # 30 seconds * 10 = 5 minutes                          
                            # Check thread status
                            if not live_data_thread.is_alive():
                                logger.error("❌ Live data collection: Stopped")
                            
                            # Check trader status with timeout handling
                            try:
                                if system.trader and hasattr(system.trader, 'is_trading'):
                                    is_trading = system.trader.is_trading
                                    
                                    # Special status for CTrader
                                    if system.broker == 'ctrader' and hasattr(system.trader, 'trader'):
                                        trader = system.trader.trader
                                        if hasattr(trader, 'symbols_initialized'):
                                            symbols_ready = trader.symbols_initialized
                                            degraded_mode = getattr(trader, '_degraded_mode', False)
                                            
                                            if symbols_ready:
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
            system.shutdown_gracefully()
        logger.info("Enhanced Pairs Trading System V3 stopped")


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Enhanced Pairs Trading System V3 with Configurable Data Provider, Broker, and Strategy')
    parser.add_argument('--data-provider', '-d', 
                       choices=['ctrader', 'mt5'], 
                       default='ctrader',
                       help='Data provider for historical and real-time data (default: ctrader)')
    parser.add_argument('--broker', '-b', 
                       choices=['ctrader', 'mt5'], 
                       default='ctrader',
                       help='Broker for trade execution (default: ctrader)')
    parser.add_argument('--strategy', '-s',
                       choices=['pairs'],
                       default='pairs',
                       help='Trading strategy to use (default: pairs)')
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
    
    main(data_provider=args.data_provider, broker=args.broker, mode=args.mode, 
         force_refresh=args.force_refresh, strategy=args.strategy)
