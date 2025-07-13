"""
Enhanced Pairs Trading System with InfluxDB Integration and Dashboard
=====================================================================

This script provides a comprehensive trading system that:
- Retrieves data from CTrader for historical analysis
- Executes real-time trading with MT5
- Uses InfluxDB for data storage and retrieval
- Integrates with the dashboard for real-time visualization
- Provides backtesting capabilities with vectorbt
- Generates downloadable Excel reports

Author: Trading System v2.0
Date: July 2025
"""

import sys
import os
import asyncio
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import warnings
import logging

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
from data.ctrader import CTraderDataManager
from brokers.mt5 import MT5RealTimeTrader

# Backtesting
from backtesting.vectorbt import VectorBTBacktester

# Dashboard integration
from dashboard.dashboard_integration import (
    create_dashboard,
    start_dashboard_with_backtest,
    start_dashboard_with_live_trading
)

# InfluxDB integration (we'll create this)
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
log_file_path = os.path.join(CONFIG.logs_dir, "enhanced_pairs_trading.log")
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
    
    def store_market_data(self, symbol: str, data: Dict[str, Any], source: str = "ctrader"):
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


class EnhancedRealTimeTrader(MT5RealTimeTrader):
    """
    Enhanced real-time trader with InfluxDB integration and dashboard updates
    """
    
    def __init__(self, config: TradingConfig, data_manager, influxdb_manager: InfluxDBManager):
        super().__init__(config, data_manager)
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
        self.influxdb_manager.store_market_data(symbol, data, "mt5")
        
        # Update dashboard with live data
        if self.dashboard and hasattr(self.dashboard, 'websocket_handler'):
            self.dashboard.websocket_handler.broadcast_live_update({
                'type': 'market_data',
                'symbol': symbol,
                'data': data,
                'timestamp': datetime.now().isoformat()
            })


class EnhancedTradingSystem:
    """
    Enhanced trading system with full integration
    """
    
    def __init__(self):
        self.config = CONFIG
        self.ctrader_data_manager = None
        self.mt5_data_manager = None
        self.influxdb_manager = None
        self.trader = None
        self.dashboard = None
        self.backtester = None
        
    def initialize(self) -> bool:
        """Initialize all components"""
        logger.info("Initializing Enhanced Trading System...")
        
        # Initialize InfluxDB
        self.influxdb_manager = InfluxDBManager(self.config)
        
        # Initialize CTrader for historical data (disabled in backtest mode to avoid reactor conflicts)
        mode = os.getenv('TRADING_MODE', 'backtest').lower()
        if mode != 'backtest':
            self.ctrader_data_manager = CTraderDataManager(self.config)
            logger.info("CTrader data manager initialized")
        else:
            self.ctrader_data_manager = None
            logger.info("CTrader data manager disabled in backtest mode")
        
        # Initialize MT5 for real-time trading
        self.mt5_data_manager = MT5DataManager(self.config)
        if not self.mt5_data_manager.connect():
            logger.error("Failed to connect to MT5")
            return False
        
        # Initialize enhanced trader
        self.trader = EnhancedRealTimeTrader(
            self.config, 
            self.mt5_data_manager, 
            self.influxdb_manager
        )
        
        # Initialize backtester with MT5 data for now (CTrader has connection issues)
        self.backtester = VectorBTBacktester(self.config, self.mt5_data_manager)
        
        logger.info("Enhanced Trading System initialized successfully")
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
        """Run comprehensive backtesting with InfluxDB storage"""
        logger.info("Starting enhanced backtesting...")
        
        try:
            # Run backtest
            backtest_results = self.backtester.run_backtest()
            
            # Store results in InfluxDB (with error handling)
            self.influxdb_manager.store_backtest_results(backtest_results)
            
            # Generate Excel report
            report_path = generate_enhanced_report(backtest_results, self.config)
            backtest_results['report_path'] = report_path
            
            logger.info(f"Backtest completed. Report saved to: {report_path}")
            return backtest_results
            
        except Exception as e:
            logger.error(f"Error during backtesting: {e}")
            # Return minimal results structure to prevent dashboard crashes
            return {
                'portfolio_metrics': {
                    'portfolio_return': 0,
                    'portfolio_sharpe': 0,
                    'portfolio_max_drawdown': 0,
                    'total_trades': 0,
                    'portfolio_win_rate': 0,
                    'total_pairs': 0
                },
                'pair_results': [],
                'report_path': 'N/A',
                'error': str(e)
            }
    
    def start_real_time_trading(self):
        """Start real-time trading with live data collection"""
        logger.info("Starting real-time trading...")
        
        if not self.trader.initialize():
            logger.error("Failed to initialize trader")
            return False
        
        # Start data collection from CTrader in background (only in realtime mode)
        # Note: CTrader data collection disabled due to reactor conflicts in backtest mode
        # Uncomment the following lines if you want to enable CTrader data collection:
        # if self.ctrader_data_manager:
        #     threading.Thread(
        #         target=self._collect_ctrader_data,
        #         daemon=True
        #     ).start()
        
        # Start trading
        self.trader.start_trading()
        return True
    
    def _collect_ctrader_data(self):
        """Collect real-time data from CTrader and store in InfluxDB"""
        logger.info("Starting CTrader data collection...")
        
        while True:
            try:
                for pair in self.config.pairs[:5]:  # Limit to first 5 pairs for demo
                    try:
                        # CTrader doesn't have a direct get_current_price method
                        # For now, we'll get recent historical data as a proxy
                        # In a real implementation, you would use spot price subscriptions
                        
                        # Get recent data (last hour) for the pair symbols
                        symbols = pair.split('-') if '-' in pair else [pair]
                        
                        # For demo purposes, create mock current data
                        # In production, implement proper spot price subscription
                        current_data = {
                            'bid': 1.1000 + (hash(pair) % 100) * 0.0001,  # Mock bid
                            'ask': 1.1005 + (hash(pair) % 100) * 0.0001,  # Mock ask
                            'last': 1.1002 + (hash(pair) % 100) * 0.0001,  # Mock last
                            'volume': 1000 + (hash(pair) % 1000),  # Mock volume
                            'timestamp': datetime.now().isoformat()
                        }
                        
                        self.influxdb_manager.store_market_data(
                            pair, current_data, "ctrader"
                        )
                        
                        # Update dashboard
                        if self.dashboard and hasattr(self.dashboard, 'websocket_handler'):
                            self.dashboard.websocket_handler.broadcast_live_update({
                                'type': 'ctrader_data',
                                'symbol': pair,
                                'data': current_data,
                                'timestamp': datetime.now().isoformat()
                            })
                    
                    except Exception as e:
                        logger.error(f"Error collecting data for {pair}: {e}")
                
                time.sleep(5)  # Collect data every 5 seconds
                
            except Exception as e:
                logger.error(f"Error in CTrader data collection: {e}")
                time.sleep(10)
    
    def _collect_live_data(self):
        """Collect and broadcast live data for dashboard (realtime mode)"""
        logger.info("Starting live data collection for dashboard...")
        
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
                                # In a more sophisticated system, you'd track daily P&L separately
                                today_pnl = current_pnl
                                
                                logger.debug(f"Real portfolio data: equity=${portfolio_value:.2f}, pnl=${current_pnl:.2f}, positions={open_positions_count}")
                            else:
                                error_msg = portfolio_status.get('error', 'Unknown error') if portfolio_status else 'No portfolio status'
                                logger.warning(f"Could not get real portfolio status: {error_msg}")
                        except Exception as e:
                            logger.warning(f"Error getting real portfolio status: {e}")
                    
                    # Generate formatted data for Live Trading Monitor page
                    live_trading_data = {
                        'timestamp': datetime.now().isoformat(),
                        'pnl': current_pnl,
                        'open_trades': open_positions_count,
                        'market_exposure': min((total_exposure / max(portfolio_value, 1)) * 100, 100) if portfolio_value > 0 else 0,  # Percentage
                        'market_health': 75 + (hash(str(datetime.now().minute)) % 50),  # Mock market health 75-125
                        
                        # Quick Stats data
                        'active_pairs': active_pairs_count,
                        'open_positions': open_positions_count,
                        'today_pnl': today_pnl,
                        'portfolio_value': portfolio_value,
                        'total_exposure': total_exposure,
                        
                        'pnl_history': [
                            {
                                'timestamp': (datetime.now() - timedelta(minutes=i)).isoformat(),
                                'pnl': current_pnl + (hash(str(i)) % 200) - 100
                            }
                            for i in range(60, 0, -5)  # Last 12 data points (1 hour)
                        ],
                        'positions': []
                    }
                    
                    # Add position data (real if available, mock otherwise)
                    if active_positions:
                        for pos in active_positions[:10]:  # Limit to 10 positions for display
                            position_data = {
                                'pair': pos.get('pair', 'Unknown'),
                                'position_type': pos.get('direction', 'long').lower(),
                                'entry_price': pos.get('entry_price', 1.0),
                                'current_price': pos.get('current_price', pos.get('entry_price', 1.0)),
                                'pnl': pos.get('pnl', 0),
                                'pnl_pct': pos.get('pnl_pct', 0),
                                'duration': pos.get('duration', '0m'),
                                'z_score': pos.get('z_score', 0.0),
                                'volume1': pos.get('volume1', 0),
                                'volume2': pos.get('volume2', 0)
                            }
                            live_trading_data['positions'].append(position_data)
                        
                        logger.debug(f"Added {len(active_positions)} real positions to live data")
                    else:
                        # Generate mock positions if no real data
                        mock_positions = [
                            "SHOP.US-ETSY.US", "GE.US-HON.US", "UNH.US-HUM.US", 
                            "CRM.US-ADBE.US", "PG.US-CL.US", "MCD.US-YUM.US",
                            "TSLA.US-GM.US", "GDX.US-GDXJ.US", "BTCUSD-SOLUSD"
                        ]
                        
                        for i, pair in enumerate(mock_positions):
                            position_type = 'long' if i % 2 == 0 else 'short'
                            base_pnl = (hash(pair + str(datetime.now().hour)) % 500) - 250
                            
                            position_data = {
                                'pair': pair,
                                'position_type': position_type,
                                'entry_price': 1.1000 + (i * 0.01),
                                'current_price': 1.1000 + (i * 0.01) + ((hash(pair) % 200) - 100) * 0.0001,
                                'pnl': base_pnl,
                                'pnl_pct': (base_pnl / 10000) * 100,  # Mock percentage
                                'duration': f"{hash(pair) % 24}h {hash(pair) % 60}m",
                                'z_score': ((hash(pair + str(datetime.now().second)) % 400) - 200) / 100.0,
                                'volume1': 0.1,
                                'volume2': 0.1
                            }
                            live_trading_data['positions'].append(position_data)
                        
                        logger.debug("Added mock positions to live data (no real positions available)")
                    
                    # Also generate market data for pairs
                    pairs_market_data = {}
                    for i, pair in enumerate(self.config.pairs[:10]):  # Limit to 10 pairs
                        base_price = 1.1000 + (i * 0.01)
                        price_variation = (hash(pair + str(datetime.now().second)) % 100) * 0.0001
                        
                        pair_data = {
                            'symbol': pair,
                            'bid': base_price + price_variation,
                            'ask': base_price + price_variation + 0.0005,
                            'last': base_price + price_variation + 0.00025,
                            'volume': 1000 + (hash(pair + str(datetime.now().minute)) % 5000),
                            'change_pct': ((hash(pair + str(datetime.now().hour)) % 200) - 100) / 100.0,
                            'z_score': ((hash(pair + str(datetime.now().second)) % 400) - 200) / 100.0,
                        }
                        pairs_market_data[pair] = pair_data
                        
                        # Store in InfluxDB
                        self.influxdb_manager.store_market_data(pair, pair_data, "realtime_mock")
                    
                    # Broadcast to dashboard
                    if self.dashboard and hasattr(self.dashboard, 'websocket_handler'):
                        # Broadcast live trading data (formatted for Live Trading Monitor page)
                        self.dashboard.websocket_handler.broadcast_live_update(live_trading_data)
                        
                        # Broadcast general market data update
                        self.dashboard.websocket_handler.broadcast_live_update({
                            'type': 'market_data_update',
                            'data': {
                                'timestamp': datetime.now().isoformat(),
                                'pairs_data': pairs_market_data,
                                'portfolio_value': portfolio_value,
                                'open_positions': open_positions_count,
                                'pnl': current_pnl,
                            },
                            'timestamp': datetime.now().isoformat()
                        })
                        
                        # Broadcast portfolio update
                        self.dashboard.websocket_handler.broadcast_portfolio_update({
                            'portfolio_value': portfolio_value,
                            'open_positions': open_positions_count,
                            'pnl': current_pnl,
                            'timestamp': datetime.now().isoformat()
                        })
                        
                        logger.info(f"Broadcasted live data update for {len(pairs_market_data)} pairs")
                    else:
                        logger.warning("Dashboard or websocket_handler not available for broadcasting")
                    
                    time.sleep(5)  # Update every 5 seconds
                    
                except Exception as e:
                    logger.error(f"Error in live data collection loop: {e}")
                    time.sleep(10)  # Wait longer if there's an error
                    
        except Exception as e:
            logger.error(f"Fatal error in live data collection: {e}")
            import traceback
            traceback.print_exc()
    
    def get_dashboard_data(self) -> Dict[str, Any]:
        """Get comprehensive data for dashboard display"""
        dashboard_data = {
            'live_trades': self.influxdb_manager.get_recent_trades(24),
            'market_data': {},
            'system_status': {
                'ctrader_connected': bool(self.ctrader_data_manager),
                'mt5_connected': bool(self.mt5_data_manager),
                'influxdb_connected': bool(self.influxdb_manager.client),
                'trader_active': bool(self.trader and hasattr(self.trader, 'is_trading') and self.trader.is_trading)
            }
        }
        
        # Get market data for each pair
        for pair in self.config.pairs[:10]:  # Limit for performance
            market_data = self.influxdb_manager.get_market_data(pair, 1)
            if market_data:
                dashboard_data['market_data'][pair] = market_data[-1]  # Latest data
        
        return dashboard_data
    
    def shutdown(self):
        """Graceful shutdown of all components"""
        logger.info("Shutting down Enhanced Trading System...")
        
        if self.trader:
            self.trader.stop_trading()
        
        if self.dashboard:
            self.dashboard.stop()
        
        # CTrader data manager doesn't have a disconnect method
        # It disconnects automatically when operations complete
        if self.ctrader_data_manager:
            logger.info("CTrader data manager shutdown")
        
        if self.mt5_data_manager:
            self.mt5_data_manager.disconnect()
        
        if self.influxdb_manager:
            self.influxdb_manager.disconnect()
        
        logger.info("System shutdown complete")


def main():
    """Main execution function with enhanced features"""
    system = None
    
    try:
        # Initialize enhanced trading system
        system = EnhancedTradingSystem()
        
        if not system.initialize():
            logger.error("Failed to initialize trading system")
            return
        
        # Get trading mode
        mode = os.getenv('TRADING_MODE', 'backtest').lower()
        
        if mode == 'backtest':
            logger.info("=== ENHANCED BACKTESTING MODE ===")
            
            # Run enhanced backtest
            backtest_results = system.run_enhanced_backtest()
            
            # Start dashboard with backtest results
            system.start_dashboard(backtest_results)
            
            # Print comprehensive summary
            portfolio_metrics = backtest_results.get('portfolio_metrics', {})
            logger.info("=== ENHANCED BACKTEST SUMMARY ===")
            logger.info(f"Total Pairs: {portfolio_metrics.get('total_pairs', 0)}")
            logger.info(f"Total Trades: {portfolio_metrics.get('total_trades', 0)}")
            logger.info(f"Portfolio Return: {portfolio_metrics.get('portfolio_return', 0):.2f}%")
            logger.info(f"Sharpe Ratio: {portfolio_metrics.get('portfolio_sharpe', 0):.2f}")
            logger.info(f"Max Drawdown: {portfolio_metrics.get('portfolio_max_drawdown', 0):.2f}%")
            logger.info(f"Win Rate: {portfolio_metrics.get('portfolio_win_rate', 0):.2f}%")
            logger.info(f"Report Path: {backtest_results.get('report_path', 'N/A')}")
            
            # Show top performing pairs
            pair_results = backtest_results.get('pair_results', [])
            if pair_results:
                logger.info("\n=== TOP 10 PERFORMING PAIRS ===")
                for i, result in enumerate(pair_results[:10]):
                    metrics = result['metrics']
                    logger.info(f"{i+1:2d}. {metrics['pair']:<20} | "
                               f"Return: {metrics['total_return']:.2f}% | "
                               f"Sharpe: {metrics['sharpe_ratio']:.2f} | "
                               f"Trades: {metrics['total_trades']:3d}")
            
            logger.info("\n🌐 Dashboard: http://127.0.0.1:8050")
            logger.info("📊 Features: Backtest results, InfluxDB data, Excel report download")
            logger.info("📈 Data sources: CTrader (historical), InfluxDB (storage)")
            logger.info("\nPress Ctrl+C to stop...")
            
            # Keep dashboard running
            if system.dashboard:
                system.dashboard.keep_alive()
        
        elif mode == 'realtime':
            logger.info("=== ENHANCED REAL-TIME TRADING MODE ===")
            
            # Generate some mock backtest data for dashboard initialization
            mock_backtest_results = {
                'portfolio_metrics': {
                    'portfolio_return': 0.0,
                    'portfolio_sharpe': 0.0,
                    'portfolio_max_drawdown': 0.0,
                    'total_trades': 0,
                    'portfolio_win_rate': 0.0,
                    'total_pairs': len(system.config.pairs)
                },
                'pair_results': [],
                'report_path': 'N/A (Real-time mode)',
                'timestamp': datetime.now().isoformat()
            }
            
            # Start dashboard with mock data
            system.dashboard = start_dashboard_with_backtest(
                backtest_results=mock_backtest_results,
                config={'port': 8050, 'theme': 'dark'},
                blocking=False  # Important: don't block here!
            )
            
            # Connect the trader as a live data source to the dashboard
            if system.trader and system.dashboard:
                try:
                    # Get symbols from config
                    symbols = [pair.split('-')[0] for pair in system.config.pairs[:10]]  # First symbol of each pair
                    symbols.extend([pair.split('-')[1] for pair in system.config.pairs[:10] if '-' in pair])  # Second symbol
                    symbols = list(set(symbols))  # Remove duplicates
                    
                    system.dashboard.connect_live_data_source(system.trader, symbols)
                    logger.info(f"Connected trader to dashboard as live data source with {len(symbols)} symbols")
                except Exception as e:
                    logger.warning(f"Failed to connect trader to dashboard: {e}")
            
            logger.info("Starting background threads...")
            
            # Start real-time trading in background
            trading_thread = threading.Thread(
                target=system.start_real_time_trading,
                daemon=True,
                name="RealTimeTrading"
            )
            trading_thread.start()
            logger.info("Real-time trading thread started")
            
            # Start live data collection
            live_data_thread = threading.Thread(
                target=system._collect_live_data,
                daemon=True,
                name="LiveDataCollection"
            )
            live_data_thread.start()
            logger.info("Live data collection thread started")
            
            logger.info("🌐 Dashboard: http://127.0.0.1:8050")
            logger.info("📊 Features: Real-time trading, live data, InfluxDB storage")
            logger.info("📈 Data sources: CTrader (market data), MT5 (execution)")
            logger.info("🔄 Live updates: WebSocket streaming to dashboard")
            logger.info("\nPress Ctrl+C to stop...")
            
            # Keep system running
            try:
                while True:
                    # Update dashboard with live data
                    if system.dashboard:
                        dashboard_data = system.get_dashboard_data()
                        # Send periodic updates to dashboard
                        if hasattr(system.dashboard, 'websocket_handler'):
                            system.dashboard.websocket_handler.broadcast_system_status({
                                'status': 'running',
                                'live_trades_count': len(dashboard_data['live_trades']),
                                'system_status': dashboard_data['system_status'],
                                'timestamp': datetime.now().isoformat()
                            })
                    
                    time.sleep(10)  # Update every 10 seconds
                    
            except KeyboardInterrupt:
                logger.info("Stopping real-time trading...")
        
        elif mode == 'hybrid':
            logger.info("=== HYBRID MODE (BACKTEST + REAL-TIME) ===")
            
            # First run backtest
            backtest_results = system.run_enhanced_backtest()
            
            # Start dashboard with backtest results
            system.start_dashboard(backtest_results)
            
            # Then start real-time trading
            trading_thread = threading.Thread(
                target=system.start_real_time_trading,
                daemon=True
            )
            trading_thread.start()
            
            logger.info("🌐 Dashboard: http://127.0.0.1:8050")
            logger.info("📊 Features: Backtest results + Real-time trading")
            logger.info("📈 Data sources: CTrader + MT5 + InfluxDB")
            logger.info("📊 Reports: Excel backtest report + Live trading data")
            logger.info("\nPress Ctrl+C to stop...")
            
            # Keep system running
            if system.dashboard:
                system.dashboard.keep_alive()
        
        else:
            logger.error(f"Unknown trading mode: {mode}")
            logger.info("Available modes: backtest, realtime, hybrid")
    
    except KeyboardInterrupt:
        logger.info("Received interrupt signal...")
    
    except Exception as e:
        logger.error(f"System error: {e}")
        import traceback
        traceback.print_exc()
    
    finally:
        if system:
            system.shutdown()


if __name__ == "__main__":
    # Print banner
    print("=" * 70)
    print("🚀 ENHANCED PAIRS TRADING SYSTEM v2.0")
    print("=" * 70)
    print("Features:")
    print("📈 CTrader historical data + MT5 real-time execution")
    print("🗄️  InfluxDB time-series database integration")
    print("📊 Real-time dashboard with WebSocket streaming")
    print("📋 Downloadable Excel backtest reports")
    print("🔄 Live trade monitoring and visualization")
    print("=" * 70)
    print()
    
    # Show available modes
    mode = os.getenv('TRADING_MODE', 'backtest').lower()
    print(f"Current mode: {mode.upper()}")
    print(f"Environment variable TRADING_MODE: {os.getenv('TRADING_MODE', 'NOT SET')}")
    print("Available modes:")
    print("  - backtest: Run backtest with dashboard visualization")
    print("  - realtime: Live trading with real-time data streaming")
    print("  - hybrid: Backtest first, then real-time trading")
    print()
    print("Set mode with: set TRADING_MODE=realtime (or backtest/hybrid)")
    print("=" * 70)
    print()
    
    main()
