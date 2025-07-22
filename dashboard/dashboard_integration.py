"""
Dashboard Integration Script

Easy-to-use script for integrating the dashboard with existing trading systems.
Provides simple functions to start the dashboard and connect data sources.
"""

import logging
import sys
import os
from pathlib import Path
from typing import Dict, Any, Optional, Union

# Add project root to path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from dashboard.dashboard_manager import DashboardManager, DashboardConfig

logger = logging.getLogger(__name__)


def create_dashboard(config: Optional[Dict] = None, 
                    host: str = '0.0.0.0', 
                    port: int = 8050,
                    theme: str = 'dark',
                    auto_refresh: bool = True) -> DashboardManager:
    """
    Create and configure a trading dashboard
    
    Args:
        config: Dashboard configuration dictionary
        host: Server host address
        port: Server port
        theme: Dashboard theme ('dark' or 'light')
        auto_refresh: Enable auto-refresh of data
        
    Returns:
        Configured DashboardManager instance
    """
    
    # Create default config if none provided
    if config is None:
        config = {
            'host': host,
            'port': port,
            'theme': theme,
            'auto_refresh': auto_refresh,
            'websocket_enabled': True,
            'cors_enabled': True,
            'update_interval': 1.0,
            'chart_height': 400
        }
    
    # Initialize dashboard
    dashboard = DashboardManager(config)
    
    logger.info(f"Dashboard created at http://{config.get('host', '0.0.0.0')}:{port}")
    return dashboard


def start_dashboard_with_backtest(backtest_results: Dict, 
                                 config: Any = None,
                                 dashboard_config: Optional[Dict] = None,
                                 blocking: bool = True) -> DashboardManager:
    """
    Start dashboard with backtesting results
    
    Args:
        backtest_results: Results from backtesting system
        config: Trading configuration object
        dashboard_config: Dashboard-specific configuration
        blocking: Whether to run in blocking mode
        
    Returns:
        DashboardManager instance
    """
    
    try:
        # Create dashboard
        dashboard = create_dashboard(dashboard_config)
        
        # Load backtest data
        dashboard.load_backtest_data(backtest_results, config)
        
        # Start server
        dashboard.start(blocking=blocking)
        
        return dashboard
        
    except Exception as e:
        logger.error(f"Failed to start dashboard with backtest data: {e}")
        raise


def start_dashboard_with_live_trading(data_source: Any,
                                    symbols: Optional[list] = None,
                                    dashboard_config: Optional[Dict] = None,
                                    influxdb_manager: Any = None,
                                    config: Optional[Any] = None,
                                    blocking: bool = True) -> DashboardManager:
    """
    Start dashboard with live trading data
    
    Args:
        data_source: Live data source (MT5DataManager, etc.)
        symbols: List of symbols to monitor
        dashboard_config: Dashboard-specific configuration
        influxdb_manager: InfluxDB manager instance for loading backtest results
        config: Trading configuration object
        blocking: Whether to run in blocking mode
        
    Returns:
        DashboardManager instance
    """
    
    try:
        # Create dashboard
        dashboard = create_dashboard(dashboard_config)
        
        # Connect live data source
        dashboard.connect_live_data_source(data_source, symbols)
        
        # Load latest backtest results from InfluxDB if manager provided
        if influxdb_manager:
            latest_backtest_results = load_latest_backtest_from_influxdb(influxdb_manager)
            if latest_backtest_results:
                dashboard.load_backtest_data(latest_backtest_results, config)
                logger.info("Loaded latest backtest results from InfluxDB into dashboard")
            else:
                logger.warning("No recent backtest results found in InfluxDB - dashboard will show live data only")
        else:
            logger.warning("No InfluxDB manager provided - dashboard will show live data only")
        
        # Start server
        dashboard.start(blocking=blocking)
        
        return dashboard
        
    except Exception as e:
        logger.error(f"Failed to start dashboard with live data: {e}")
        raise


def start_comprehensive_dashboard(backtest_results: Optional[Dict] = None,
                                 live_data_source: Optional[Any] = None,
                                 symbols: Optional[list] = None,
                                 config: Optional[Any] = None,
                                 dashboard_config: Optional[Dict] = None,
                                 blocking: bool = True) -> DashboardManager:
    """
    Start comprehensive dashboard with both backtest and live data
    
    Args:
        backtest_results: Optional backtest results
        live_data_source: Optional live data source
        symbols: List of symbols for live data
        config: Trading configuration object
        dashboard_config: Dashboard-specific configuration
        blocking: Whether to run in blocking mode
        
    Returns:
        DashboardManager instance
    """
    
    try:
        # Create dashboard
        dashboard = create_dashboard(dashboard_config)
        
        # Load backtest data if provided
        if backtest_results:
            dashboard.load_backtest_data(backtest_results, config)
            logger.info("Loaded backtest data into dashboard")
        
        # Connect live data if provided
        if live_data_source:
            dashboard.connect_live_data_source(live_data_source, symbols)
            logger.info("Connected live data source to dashboard")
        
        # Start server
        dashboard.start(blocking=blocking)
        
        return dashboard
        
    except Exception as e:
        logger.error(f"Failed to start comprehensive dashboard: {e}")
        raise


def quick_start_dashboard(port: int = 8050, 
                         theme: str = 'dark',
                         open_browser: bool = True) -> DashboardManager:
    """
    Quick start dashboard with minimal configuration
    
    Args:
        port: Server port
        theme: Dashboard theme
        open_browser: Whether to open browser automatically
        
    Returns:
        DashboardManager instance
    """
    
    try:
        # Create simple dashboard
        dashboard = create_dashboard(
            config={
                'port': port,
                'theme': theme,
                'auto_refresh': True,
                'websocket_enabled': True
            }
        )
        
        # Open browser if requested
        if open_browser:
            import webbrowser
            webbrowser.open(f'http://127.0.0.1:{port}')
        
        # Start in non-blocking mode for quick start
        dashboard.start(blocking=False)
        
        # Log with the actual configured host
        actual_host = dashboard.config.host if hasattr(dashboard, 'config') and dashboard.config else '0.0.0.0'
        logger.info(f"Dashboard started at http://{actual_host}:{port}")
        return dashboard
        
    except Exception as e:
        logger.error(f"Failed to quick start dashboard: {e}")
        raise


# Example usage functions
def example_backtest_dashboard():
    """Example: Start dashboard with mock backtest data"""
    
    # Mock backtest results
    mock_results = {
        'portfolio_metrics': {
            'portfolio_return': 15.5,
            'portfolio_sharpe': 1.25,
            'portfolio_max_drawdown': -8.2,
            'total_trades': 150,
            'portfolio_win_rate': 62.5,
            'sortino_ratio': 1.8,
            'calmar_ratio': 1.9,
            'volatility': 12.3,
            'profit_factor': 1.45
        },
        'pair_results': [
            {
                'metrics': {
                    'pair': 'AAPL.US-MSFT.US',
                    'total_return': 12.5,
                    'sharpe_ratio': 1.15,
                    'max_drawdown': -5.2,
                    'total_trades': 25,
                    'win_rate': 68.0,
                    'profit_factor': 1.35
                }
            },
            {
                'metrics': {
                    'pair': 'NVDA.US-AMD.US',
                    'total_return': 18.3,
                    'sharpe_ratio': 1.42,
                    'max_drawdown': -7.1,
                    'total_trades': 30,
                    'win_rate': 73.3,
                    'profit_factor': 1.68
                }
            }
        ]
    }
    
    # Start dashboard
    dashboard = start_dashboard_with_backtest(
        backtest_results=mock_results,
        blocking=False
    )
    
    print(f"Example dashboard started at {dashboard.get_dashboard_url()}")
    return dashboard


def example_live_dashboard():
    """Example: Start dashboard with mock live data source"""
    
    # Mock data source class
    class MockDataSource:
        def get_current_price(self, symbol):
            import random
            return {
                'bid': random.uniform(100, 200),
                'ask': random.uniform(100, 200),
                'last': random.uniform(100, 200)
            }
        
        def get_portfolio_status(self):
            import random
            return {
                'equity': random.uniform(95000, 105000),
                'balance': 100000,
                'margin': random.uniform(1000, 5000),
                'free_margin': random.uniform(95000, 99000),
                'pnl': random.uniform(-1000, 1000),
                'daily_pnl': random.uniform(-500, 500),
                'positions': []
            }
    
    # Start dashboard
    dashboard = start_dashboard_with_live_trading(
        data_source=MockDataSource(),
        symbols=['AAPL.US', 'MSFT.US', 'NVDA.US', 'AMD.US'],
        blocking=False
    )
    
    print(f"Example live dashboard started at {dashboard.get_dashboard_url()}")
    return dashboard


def integration_example():
    """
    Example integration with existing trading system
    """
    
    print("Trading Dashboard Integration Example")
    print("====================================")
    
    # Method 1: Quick start (minimal setup)
    print("\n1. Quick Start Dashboard:")
    try:
        dashboard = quick_start_dashboard(port=8050, open_browser=False)
        print(f"   ✓ Dashboard started at {dashboard.get_dashboard_url()}")
        dashboard.stop()
    except Exception as e:
        print(f"   ✗ Failed: {e}")
    
    # Method 2: Backtest results dashboard
    print("\n2. Backtest Results Dashboard:")
    try:
        dashboard = example_backtest_dashboard()
        print(f"   ✓ Backtest dashboard started at {dashboard.get_dashboard_url()}")
        dashboard.stop()
    except Exception as e:
        print(f"   ✗ Failed: {e}")
    
    # Method 3: Live trading dashboard
    print("\n3. Live Trading Dashboard:")
    try:
        dashboard = example_live_dashboard()
        print(f"   ✓ Live dashboard started at {dashboard.get_dashboard_url()}")
        dashboard.stop()
    except Exception as e:
        print(f"   ✗ Failed: {e}")
    
    print("\nIntegration example completed!")


def load_latest_backtest_from_influxdb(influxdb_manager) -> Optional[Dict]:
    """
    Load the most recent backtest results from InfluxDB
    
    Args:
        influxdb_manager: InfluxDBManager instance
        
    Returns:
        Latest backtest results or None if not found
    """
    try:
        logger.info("Attempting to load latest backtest results from InfluxDB...")
        
        if not influxdb_manager:
            logger.warning("InfluxDB manager is None")
            return None
            
        if not hasattr(influxdb_manager, 'get_latest_backtest_results'):
            logger.warning("InfluxDB manager missing get_latest_backtest_results method")
            return None
        
        logger.info("Calling InfluxDB manager to get latest backtest results...")
        latest_results = influxdb_manager.get_latest_backtest_results()
        
        if latest_results:
            logger.info(f"Successfully loaded latest backtest results from InfluxDB: {len(latest_results.get('pair_results', []))} pairs")
            logger.info(f"Backtest timestamp: {latest_results.get('timestamp', 'Unknown')}")
            return latest_results
        else:
            logger.warning("No backtest results found in InfluxDB - this is normal if no backtests have been run recently")
            return None
            
    except Exception as e:
        logger.error(f"Failed to load latest backtest results from InfluxDB: {e}")
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        return None


if __name__ == "__main__":
    # Run integration example
    integration_example()
