"""
Data Management Example and Testing Script
==========================================

This script demonstrates the intelligent data management capabilities
of the Enhanced Trading System V3. It shows how the system:

1. Checks existing data in InfluxDB
2. Detects gaps and missing data
3. Fetches only required missing data
4. Uses cached data for backtesting

Author: Trading System v3.0
Date: July 2025
"""

import os
import sys
import logging
from datetime import datetime, timedelta

# Ensure project root is in sys.path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from enhanced_main_v3 import EnhancedTradingSystemV3
from config import get_config

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def demonstrate_data_management():
    """Demonstrate intelligent data management capabilities"""
    
    print("="*80)
    print("INTELLIGENT DATA MANAGEMENT DEMONSTRATION")
    print("="*80)
    
    config = get_config()
    
    # Test different scenarios
    scenarios = [
        {
            'name': 'cTrader Data + cTrader Execution',
            'data_provider': 'ctrader',
            'broker': 'ctrader',
            'description': 'Both data and execution through cTrader'
        },
        {
            'name': 'cTrader Data + MT5 Execution', 
            'data_provider': 'ctrader',
            'broker': 'mt5',
            'description': 'Data from cTrader, execution through MT5'
        },
        {
            'name': 'MT5 Data + cTrader Execution',
            'data_provider': 'mt5',
            'broker': 'ctrader', 
            'description': 'Data from MT5, execution through cTrader'
        },
        {
            'name': 'MT5 Data + MT5 Execution',
            'data_provider': 'mt5',
            'broker': 'mt5',
            'description': 'Both data and execution through MT5'
        }
    ]
    
    for i, scenario in enumerate(scenarios, 1):
        print(f"\n{'='*60}")
        print(f"SCENARIO {i}: {scenario['name']}")
        print(f"{'='*60}")
        print(f"Description: {scenario['description']}")
        print(f"Data Provider: {scenario['data_provider']}")
        print(f"Execution Broker: {scenario['broker']}")
        
        try:
            # Create system
            system = EnhancedTradingSystemV3(
                data_provider=scenario['data_provider'],
                broker=scenario['broker']
            )
            
            if system.initialize():
                print("✅ System initialized successfully")
                
                # Demonstrate data coverage analysis
                print("\n📊 Analyzing data coverage...")
                
                # Get subset of symbols for demo
                demo_symbols = list(set([s for pair in config.pairs[:3] for s in pair.split('-')]))
                
                coverage_report = system.intelligent_data_manager.get_data_coverage_report(
                    symbols=demo_symbols,
                    interval=config.interval,
                    start_date=datetime.now() - timedelta(days=30),
                    end_date=datetime.now()
                )
                
                print(f"\nData Coverage Report:")
                for symbol, report in coverage_report.items():
                    print(f"  {symbol}: {report['coverage_percentage']:.1f}% coverage, {report['gaps_count']} gaps")
                
                # Demonstrate intelligent data fetching
                print("\n🧠 Testing intelligent data fetching...")
                
                test_data = system.intelligent_data_manager.get_historical_data_intelligent(
                    symbols=demo_symbols[:2],  # Test with 2 symbols
                    interval=config.interval,
                    start_date=datetime.now() - timedelta(days=7),
                    end_date=datetime.now(),
                    data_provider=scenario['data_provider'],
                    force_refresh=False
                )
                
                print(f"Fetched data for {len([s for s, d in test_data.items() if not d.empty])}/{len(demo_symbols[:2])} symbols")
                for symbol, data in test_data.items():
                    if not data.empty:
                        print(f"  {symbol}: {len(data)} points ({data.index.min()} to {data.index.max()})")
                    else:
                        print(f"  {symbol}: No data available")
                
                print("✅ Scenario completed successfully")
                
            else:
                print("❌ Failed to initialize system")
                
        except Exception as e:
            print(f"❌ Error in scenario: {e}")
        
        finally:
            try:
                if 'system' in locals():
                    system.shutdown()
            except:
                pass
    
    print(f"\n{'='*80}")
    print("DEMONSTRATION COMPLETED")
    print("="*80)


def test_data_gap_detection():
    """Test data gap detection functionality"""
    
    print("\n" + "="*60)
    print("DATA GAP DETECTION TEST")
    print("="*60)
    
    try:
        system = EnhancedTradingSystemV3(data_provider='ctrader', broker='ctrader')
        
        if system.initialize():
            # Test gap detection with a known symbol
            test_symbol = 'EURUSD'
            start_date = datetime.now() - timedelta(days=30)
            end_date = datetime.now()
            
            print(f"Testing gap detection for {test_symbol}")
            print(f"Date range: {start_date} to {end_date}")
            
            # Get existing data
            existing_data = system.intelligent_data_manager._get_existing_data_from_influx(
                test_symbol, 'M15', start_date, end_date
            )
            
            print(f"Existing data points: {len(existing_data)}")
            
            if not existing_data.empty:
                print(f"Data range: {existing_data.index.min()} to {existing_data.index.max()}")
            
            # Detect gaps
            gaps = system.intelligent_data_manager._detect_data_gaps(
                test_symbol, 'M15', start_date, end_date, existing_data
            )
            
            print(f"Detected gaps: {len(gaps)}")
            for i, gap in enumerate(gaps):
                print(f"  Gap {i+1}: {gap.start_date} to {gap.end_date}")
            
            system.shutdown()
        
    except Exception as e:
        print(f"Error in gap detection test: {e}")


def test_force_refresh():
    """Test force refresh functionality"""
    
    print("\n" + "="*60)
    print("FORCE REFRESH TEST")
    print("="*60)
    
    try:
        system = EnhancedTradingSystemV3(data_provider='ctrader', broker='ctrader')
        
        if system.initialize():
            demo_symbols = ['EURUSD', 'GBPUSD']
            
            print("Testing normal fetch (with caching)...")
            start_time = datetime.now()
            
            normal_data = system.intelligent_data_manager.get_historical_data_intelligent(
                symbols=demo_symbols,
                interval='M15',
                start_date=datetime.now() - timedelta(days=7),
                data_provider='ctrader',
                force_refresh=False
            )
            
            normal_time = (datetime.now() - start_time).total_seconds()
            print(f"Normal fetch completed in {normal_time:.2f} seconds")
            
            print("\nTesting force refresh (no caching)...")
            start_time = datetime.now()
            
            refresh_data = system.intelligent_data_manager.get_historical_data_intelligent(
                symbols=demo_symbols,
                interval='M15',
                start_date=datetime.now() - timedelta(days=7),
                data_provider='ctrader',
                force_refresh=True
            )
            
            refresh_time = (datetime.now() - start_time).total_seconds()
            print(f"Force refresh completed in {refresh_time:.2f} seconds")
            
            # Compare results
            print(f"\nComparison:")
            for symbol in demo_symbols:
                normal_count = len(normal_data.get(symbol, []))
                refresh_count = len(refresh_data.get(symbol, []))
                print(f"  {symbol}: Normal={normal_count}, Refresh={refresh_count}")
            
            system.shutdown()
        
    except Exception as e:
        print(f"Error in force refresh test: {e}")


def run_sample_backtest():
    """Run a sample backtest with intelligent data management"""
    
    print("\n" + "="*60)
    print("SAMPLE BACKTEST WITH INTELLIGENT DATA")
    print("="*60)
    
    try:
        system = EnhancedTradingSystemV3(data_provider='ctrader', broker='ctrader')
        
        if system.initialize():
            print("Running backtest with intelligent data management...")
            
            # Run backtest - this will automatically use intelligent data caching
            results = system.run_enhanced_backtest(force_refresh=False)
            
            if 'error' not in results:
                print("✅ Backtest completed successfully!")
                
                portfolio_metrics = results.get('portfolio_metrics', {})
                print(f"\nKey Results:")
                print(f"  Portfolio Return: {portfolio_metrics.get('portfolio_return', 0):.2%}")
                print(f"  Sharpe Ratio: {portfolio_metrics.get('portfolio_sharpe', 0):.2f}")
                print(f"  Max Drawdown: {portfolio_metrics.get('portfolio_max_drawdown', 0):.2%}")
                print(f"  Total Trades: {portfolio_metrics.get('total_trades', 0)}")
                
                if 'data_quality' in results:
                    quality = results['data_quality']
                    good_count = sum(1 for r in quality.values() if r['status'] == 'GOOD')
                    print(f"  Data Quality: {good_count}/{len(quality)} symbols with good quality")
                
                print(f"  Report: {results.get('report_path', 'N/A')}")
                
            else:
                print(f"❌ Backtest failed: {results['error']}")
            
            system.shutdown()
        
    except Exception as e:
        print(f"Error in sample backtest: {e}")


if __name__ == "__main__":
    
    print("Enhanced Trading System V3 - Data Management Demo")
    print("Choose a test to run:")
    print("1. Full data management demonstration")
    print("2. Data gap detection test")
    print("3. Force refresh test")
    print("4. Sample backtest with intelligent data")
    print("5. Run all tests")
    
    choice = input("\nEnter your choice (1-5): ").strip()
    
    if choice == '1':
        demonstrate_data_management()
    elif choice == '2':
        test_data_gap_detection()
    elif choice == '3':
        test_force_refresh()
    elif choice == '4':
        run_sample_backtest()
    elif choice == '5':
        demonstrate_data_management()
        test_data_gap_detection()
        test_force_refresh()
        run_sample_backtest()
    else:
        print("Invalid choice. Running full demonstration...")
        demonstrate_data_management()
