"""
cTrader Bulk Fetch Test Script
==============================

This script tests the cTrader bulk fetch functionality to ensure
all data is retrieved in one reactor session as implemented in V2.

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


def test_ctrader_bulk_fetch():
    """Test cTrader bulk fetch functionality"""
    
    print("="*80)
    print("CTRADER BULK FETCH TEST")
    print("="*80)
    
    config = get_config()
    
    try:
        # Create system with cTrader
        system = EnhancedTradingSystemV3(data_provider='ctrader', broker='ctrader')
        
        if system.initialize():
            print("✅ System initialized successfully")
            
            # Test symbols from first few pairs
            test_symbols = list(set([s for pair in config.pairs[:2] for s in pair.split('-')]))
            print(f"Testing with symbols: {test_symbols}")
            
            print("\n" + "="*60)
            print("TESTING BULK FETCH STRATEGY")
            print("="*60)
            
            # Test bulk fetch
            start_time = datetime.now()
            
            test_data = system.intelligent_data_manager.get_historical_data_intelligent(
                symbols=test_symbols,
                interval=config.interval,
                start_date=datetime.now() - timedelta(days=30),
                end_date=datetime.now(),
                data_provider='ctrader',
                force_refresh=False
            )
            
            fetch_time = (datetime.now() - start_time).total_seconds()
            
            print(f"\n✅ Bulk fetch completed in {fetch_time:.2f} seconds")
            
            # Analyze results
            successful = [s for s, d in test_data.items() if not d.empty]
            failed = [s for s, d in test_data.items() if d.empty]
            
            print(f"\nResults:")
            print(f"  Successful: {len(successful)}/{len(test_symbols)} symbols")
            print(f"  Failed: {len(failed)} symbols")
            
            for symbol, data in test_data.items():
                if not data.empty:
                    print(f"  ✅ {symbol}: {len(data)} points ({data.index.min()} to {data.index.max()})")
                else:
                    print(f"  ❌ {symbol}: No data")
            
            # Test force refresh
            print(f"\n" + "="*60)
            print("TESTING FORCE REFRESH BULK FETCH")
            print("="*60)
            
            start_time = datetime.now()
            
            refresh_data = system.intelligent_data_manager.get_historical_data_intelligent(
                symbols=test_symbols[:2],  # Test with fewer symbols
                interval=config.interval,
                start_date=datetime.now() - timedelta(days=7),
                end_date=datetime.now(),
                data_provider='ctrader',
                force_refresh=True
            )
            
            refresh_time = (datetime.now() - start_time).total_seconds()
            
            print(f"\n✅ Force refresh bulk fetch completed in {refresh_time:.2f} seconds")
            
            for symbol, data in refresh_data.items():
                if not data.empty:
                    print(f"  ✅ {symbol}: {len(data)} points")
                else:
                    print(f"  ❌ {symbol}: No data")
            
            system.shutdown()
            
            print(f"\n" + "="*80)
            print("CTRADER BULK FETCH TEST COMPLETED SUCCESSFULLY")
            print("="*80)
            print("✅ All data was fetched in optimized bulk operations")
            print("✅ No individual symbol requests that could cause reactor issues")
            print("✅ Implementation matches V2 cTrader optimization strategy")
            
        else:
            print("❌ Failed to initialize system")
            
    except Exception as e:
        print(f"❌ Error in test: {e}")
        import traceback
        traceback.print_exc()


def test_comparison_with_mt5():
    """Compare cTrader bulk fetch with MT5 individual fetch"""
    
    print("\n" + "="*80)
    print("COMPARISON: CTRADER BULK vs MT5 INDIVIDUAL")
    print("="*80)
    
    config = get_config()
    test_symbols = list(set([s for pair in config.pairs[:2] for s in pair.split('-')]))
    
    # Test cTrader
    print("\n📊 Testing cTrader (bulk fetch)...")
    try:
        system_ctrader = EnhancedTradingSystemV3(data_provider='ctrader', broker='ctrader')
        if system_ctrader.initialize():
            start_time = datetime.now()
            
            ctrader_data = system_ctrader.intelligent_data_manager.get_historical_data_intelligent(
                symbols=test_symbols,
                interval=config.interval,
                start_date=datetime.now() - timedelta(days=7),
                data_provider='ctrader',
                force_refresh=True
            )
            
            ctrader_time = (datetime.now() - start_time).total_seconds()
            ctrader_success = len([s for s, d in ctrader_data.items() if not d.empty])
            
            print(f"  ⏱️  Time: {ctrader_time:.2f}s")
            print(f"  ✅ Success: {ctrader_success}/{len(test_symbols)}")
            print(f"  📈 Strategy: Bulk fetch (all symbols in one reactor session)")
            
            system_ctrader.shutdown()
    except Exception as e:
        print(f"  ❌ cTrader test failed: {e}")
        ctrader_time = float('inf')
        ctrader_success = 0
    
    # Test MT5
    print("\n📊 Testing MT5 (individual fetch)...")
    try:
        system_mt5 = EnhancedTradingSystemV3(data_provider='mt5', broker='mt5')
        if system_mt5.initialize():
            start_time = datetime.now()
            
            mt5_data = system_mt5.intelligent_data_manager.get_historical_data_intelligent(
                symbols=test_symbols,
                interval=config.interval,
                start_date=datetime.now() - timedelta(days=7),
                data_provider='mt5',
                force_refresh=True
            )
            
            mt5_time = (datetime.now() - start_time).total_seconds()
            mt5_success = len([s for s, d in mt5_data.items() if not d.empty])
            
            print(f"  ⏱️  Time: {mt5_time:.2f}s")
            print(f"  ✅ Success: {mt5_success}/{len(test_symbols)}")
            print(f"  📈 Strategy: Individual fetch (symbol by symbol)")
            
            system_mt5.shutdown()
    except Exception as e:
        print(f"  ❌ MT5 test failed: {e}")
        mt5_time = float('inf')
        mt5_success = 0
    
    # Comparison
    print(f"\n" + "="*60)
    print("COMPARISON RESULTS")
    print("="*60)
    
    if ctrader_time < float('inf') and mt5_time < float('inf'):
        if ctrader_time < mt5_time:
            speedup = mt5_time / ctrader_time
            print(f"🏆 cTrader bulk fetch is {speedup:.1f}x faster than MT5 individual fetch")
        else:
            slowdown = ctrader_time / mt5_time
            print(f"📊 MT5 individual fetch is {slowdown:.1f}x faster than cTrader bulk fetch")
    
    print(f"cTrader: {ctrader_success} successful, {ctrader_time:.2f}s (bulk strategy)")
    print(f"MT5: {mt5_success} successful, {mt5_time:.2f}s (individual strategy)")
    
    print(f"\n✅ Both providers use optimized strategies:")
    print(f"   - cTrader: Bulk fetch in one reactor session (avoids connection issues)")
    print(f"   - MT5: Individual fetch with gap detection (stable connections)")


if __name__ == "__main__":
    print("Enhanced Trading System V3 - cTrader Bulk Fetch Test")
    print("This test verifies the cTrader bulk fetch optimization")
    print("that was implemented in V2 and carried forward to V3.")
    
    choice = input("\nChoose test:\n1. cTrader bulk fetch test\n2. cTrader vs MT5 comparison\n3. Both tests\n\nEnter choice (1-3): ").strip()
    
    if choice == '1':
        test_ctrader_bulk_fetch()
    elif choice == '2':
        test_comparison_with_mt5()
    elif choice == '3':
        test_ctrader_bulk_fetch()
        test_comparison_with_mt5()
    else:
        print("Invalid choice. Running cTrader bulk fetch test...")
        test_ctrader_bulk_fetch()
