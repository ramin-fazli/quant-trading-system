import sys
import os
# Ensure project root is in sys.path for imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)
import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller
from statsmodels.tsa.vector_ar.vecm import coint_johansen
import warnings
import os
from dotenv import load_dotenv
import datetime
import time
import logging
import threading
from collections import deque, defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Union
import json
import MetaTrader5 as mt5
import matplotlib.pyplot as plt
import seaborn as sns
import xlsxwriter
import vectorbt as vbt
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiprocessing
from reporting.report_generator import generate_enhanced_report  # Import the report generator
from config import TradingConfig, get_config, force_config_update
from data.mt5 import MT5DataManager
from brokers.mt5 import MT5RealTimeTrader
from data.ctrader import CTraderDataManager
from backtesting.vectorbt import VectorBTBacktester  # Import vectorbt backtester

# Load environment variables
# load_dotenv()

# Get configuration from centralized manager
force_config_update()
CONFIG = get_config()

# Setup optimized logging with proper log file path
log_file_path = os.path.join(CONFIG.logs_dir, "pairs_trading.log")
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

# === MAIN EXECUTION ===
def main():
    """Main execution function"""

    # if CONFIG.data_provider == 'ctrader':
    #     DataManager = CTraderDataManager
    # elif CONFIG.data_provider == 'mt5':
    #     DataManager = MT5DataManager
    
    # Initialize MT5 connection
    data_manager = MT5DataManager(CONFIG)
    if not data_manager.connect():
        logger.error("Failed to connect to MT5")
        return
    
    try:
        # Check mode
        mode = os.getenv('TRADING_MODE', 'realtime').lower()
        
        if mode == 'realtime':
            # Real-time trading mode
            logger.info("Starting real-time trading mode")
            
            trader = MT5RealTimeTrader(CONFIG, data_manager)
            
            if trader.initialize():
                trader.start_trading()
            else:
                logger.error("Failed to initialize real-time trader")
        
        else:
            # Backtesting mode
            logger.info("Starting backtesting mode")
            
            # Initialize vectorbt backtester
            backtester = VectorBTBacktester(CONFIG, data_manager)
            
            # Run comprehensive backtest
            backtest_results = backtester.run_backtest()
            
            # Generate enhanced report
            generate_enhanced_report(backtest_results, CONFIG)
            
            # Print summary
            portfolio_metrics = backtest_results.get('portfolio_metrics', {})
            logger.info("=== BACKTEST SUMMARY ===")
            logger.info(f"Total Pairs: {portfolio_metrics.get('total_pairs', 0)}")
            logger.info(f"Total Trades: {portfolio_metrics.get('total_trades', 0)}")
            logger.info(f"Portfolio Return: {portfolio_metrics.get('portfolio_return', 0):.2f}%")
            logger.info(f"Sharpe Ratio: {portfolio_metrics.get('portfolio_sharpe', 0):.2f}")
            logger.info(f"Max Drawdown: {portfolio_metrics.get('portfolio_max_drawdown', 0):.2f}%")
            logger.info(f"Max Concurrent Positions: {portfolio_metrics.get('max_concurrent_positions', 0)}")
            
            # Show top performing pairs
            pair_results = backtest_results.get('pair_results', [])
            if pair_results:
                logger.info("\n=== TOP 10 PERFORMING PAIRS ===")
                for i, result in enumerate(pair_results[:10]):
                    metrics = result['metrics']
                    logger.info(f"{i+1:2d}. {metrics['pair']:<20} | "
                               f"Return: {metrics['total_return']:.2f}% | "
                               f"Sharpe: {metrics['sharpe_ratio']:.2f} | "
                               f"Trades: {metrics['total_trades']:3d} | "
                               f"Score: {metrics['composite_score']:.2f}")
            
            logger.info("========================")

    finally:
        data_manager.disconnect()

if __name__ == "__main__":
    main()
