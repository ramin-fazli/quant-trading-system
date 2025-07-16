#!/usr/bin/env python3
"""
Demo script to showcase Enhanced Trading System V2 with configurable data providers
"""

import os
import sys

# Add project root to path
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, project_root)

def demo_data_provider_selection():
    """
    Demonstrate the difference between V1 and V2 of the Enhanced Trading System
    """
    print("=" * 80)
    print("Enhanced Pairs Trading System - Data Provider Comparison Demo")
    print("=" * 80)
    print()
    
    print("📊 VERSION 1 (enhanced_main.py):")
    print("   - Fixed data provider configuration")
    print("   - CTrader for historical data (when available)")
    print("   - MT5 for real-time execution")
    print("   - No runtime selection of data provider")
    print()
    
    print("🚀 VERSION 2 (enhanced_main_v2.py):")
    print("   - Configurable data provider via command line")
    print("   - Unified data provider for ALL data operations")
    print("   - MT5 always used for trade execution (regardless of data provider)")
    print("   - Runtime selection: --data-provider {ctrader,mt5}")
    print()
    
    print("🔧 USAGE EXAMPLES:")
    print()
    print("   # Version 2 with CTrader data provider (backtest):")
    print("   python enhanced_main_v2.py --data-provider ctrader --mode backtest")
    print()
    print("   # Version 2 with MT5 data provider (backtest):")
    print("   python enhanced_main_v2.py --data-provider mt5 --mode backtest")
    print()
    print("   # Version 2 with CTrader data provider (live trading):")
    print("   python enhanced_main_v2.py --data-provider ctrader --mode live")
    print()
    print("   # Version 2 with MT5 data provider (live trading):")
    print("   python enhanced_main_v2.py --data-provider mt5 --mode live")
    print()
    
    print("💡 KEY DIFFERENCES:")
    print()
    print("   1. DATA SOURCE FLEXIBILITY:")
    print("      V1: Fixed data sources (CTrader historical + MT5 real-time)")
    print("      V2: Choose one provider for ALL data (historical + real-time)")
    print()
    print("   2. EXECUTION BROKER:")
    print("      V1: Always MT5")
    print("      V2: Always MT5 (unchanged)")
    print()
    print("   3. RUNTIME CONFIGURATION:")
    print("      V1: Hardcoded in initialization")
    print("      V2: Command line arguments")
    print()
    print("   4. USE CASES:")
    print("      V1: Best for mixed data sources")
    print("      V2: Best for consistent single data provider")
    print()
    
    print("🎯 BENEFITS OF V2:")
    print("   ✅ Consistent data source across all operations")
    print("   ✅ Easier debugging with single data provider")
    print("   ✅ Better data quality control")
    print("   ✅ Simplified configuration management")
    print("   ✅ Flexible deployment options")
    print()
    
    print("=" * 80)

if __name__ == "__main__":
    demo_data_provider_selection()
