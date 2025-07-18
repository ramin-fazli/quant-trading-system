#!/usr/bin/env python3
"""
Refactoring Validation Script
============================

This script validates that the refactored trading system components
work correctly and that all shared modules integrate properly.

Author: Trading System v3.0
Date: July 18, 2025
"""

import sys
import traceback
from typing import Dict, Any


def test_shared_modules() -> Dict[str, Any]:
    """Test that all shared utility modules can be imported and instantiated"""
    results = {
        'risk_manager': False,
        'portfolio_manager': False,
        'signal_processor': False,
        'base_broker': False
    }
    
    try:
        # Test Risk Manager
        from utils.risk_manager import RiskManager, RiskLimits, CurrencyConverter
        risk_limits = RiskLimits(
            max_open_positions=5,
            max_monetary_exposure=100000,
            max_portfolio_drawdown_perc=0.1,
            max_pair_drawdown_perc=0.05,
            monetary_value_tolerance=0.02,
            initial_portfolio_value=50000
        )
        risk_manager = RiskManager(risk_limits, "USD")
        results['risk_manager'] = True
        print("✅ RiskManager: Successfully imported and instantiated")
        
    except Exception as e:
        print(f"❌ RiskManager: Failed - {e}")
        traceback.print_exc()
    
    try:
        # Test Portfolio Manager
        from utils.portfolio_manager import PortfolioManager
        portfolio_manager = PortfolioManager(50000, "USD")
        results['portfolio_manager'] = True
        print("✅ PortfolioManager: Successfully imported and instantiated")
        
    except Exception as e:
        print(f"❌ PortfolioManager: Failed - {e}")
        traceback.print_exc()
    
    try:
        # Test Signal Processor
        from utils.signal_processor import SignalProcessor
        # Note: Would need a strategy instance for full instantiation
        results['signal_processor'] = True
        print("✅ SignalProcessor: Successfully imported")
        
    except Exception as e:
        print(f"❌ SignalProcessor: Failed - {e}")
        traceback.print_exc()
    
    try:
        # Test Base Broker
        from brokers.base_broker import BaseBroker
        results['base_broker'] = True
        print("✅ BaseBroker: Successfully imported")
        
    except Exception as e:
        print(f"❌ BaseBroker: Failed - {e}")
        traceback.print_exc()
    
    return results


def test_broker_imports() -> Dict[str, Any]:
    """Test that both broker implementations can be imported"""
    results = {
        'mt5_broker': False,
        'ctrader_broker': False
    }
    
    try:
        # Test MT5 Broker
        from brokers.mt5 import MT5RealTimeTrader
        results['mt5_broker'] = True
        print("✅ MT5RealTimeTrader: Successfully imported")
        
    except Exception as e:
        print(f"❌ MT5RealTimeTrader: Failed - {e}")
        traceback.print_exc()
    
    try:
        # Test CTrader Broker
        from brokers.ctrader import CTraderRealTimeTrader
        results['ctrader_broker'] = True
        print("✅ CTraderRealTimeTrader: Successfully imported")
        
    except Exception as e:
        print(f"❌ CTraderRealTimeTrader: Failed - {e}")
        traceback.print_exc()
    
    return results


def test_integration() -> Dict[str, Any]:
    """Test that shared modules integrate properly with broker interfaces"""
    results = {
        'volume_balancer': False,
        'risk_checking': False,
        'portfolio_calculation': False
    }
    
    try:
        # Test Volume Balancer functionality
        from utils.risk_manager import VolumeBalancer
        balancer = VolumeBalancer()
        
        # Test volume normalization
        volume_constraints = {
            'volume_min': 0.01,
            'volume_max': 100.0,
            'volume_step': 0.01
        }
        normalized = balancer._normalize_volume(0.125, volume_constraints)
        if normalized == 0.12:  # Should round to step
            results['volume_balancer'] = True
            print("✅ VolumeBalancer: Volume normalization working correctly")
        else:
            print(f"❌ VolumeBalancer: Expected 0.12, got {normalized}")
            
    except Exception as e:
        print(f"❌ VolumeBalancer: Failed - {e}")
        traceback.print_exc()
    
    try:
        # Test risk checking
        from utils.risk_manager import RiskManager, RiskLimits
        risk_limits = RiskLimits(
            max_open_positions=5,
            max_monetary_exposure=100000,
            max_portfolio_drawdown_perc=0.1,
            max_pair_drawdown_perc=0.05,
            monetary_value_tolerance=0.02,
            initial_portfolio_value=50000
        )
        risk_manager = RiskManager(risk_limits, "USD")
        
        # Test basic risk check
        allowed, reason = risk_manager.check_trading_allowed(
            current_portfolio_value=50000,
            current_positions=0,
            current_exposure=0
        )
        if allowed:
            results['risk_checking'] = True
            print("✅ RiskManager: Risk checking working correctly")
        else:
            print(f"❌ RiskManager: Risk check failed: {reason}")
            
    except Exception as e:
        print(f"❌ RiskManager: Failed - {e}")
        traceback.print_exc()
    
    try:
        # Test portfolio calculation
        from utils.portfolio_manager import PortfolioCalculator
        calculator = PortfolioCalculator("USD")
        
        # Test basic calculation (would need real data for full test)
        results['portfolio_calculation'] = True
        print("✅ PortfolioCalculator: Successfully imported and instantiated")
        
    except Exception as e:
        print(f"❌ PortfolioCalculator: Failed - {e}")
        traceback.print_exc()
    
    return results


def main():
    """Run all validation tests"""
    print("=" * 60)
    print("Trading System Refactoring Validation")
    print("=" * 60)
    print()
    
    print("1. Testing Shared Utility Modules...")
    print("-" * 40)
    shared_results = test_shared_modules()
    print()
    
    print("2. Testing Broker Implementations...")
    print("-" * 40)
    broker_results = test_broker_imports()
    print()
    
    print("3. Testing Integration...")
    print("-" * 40)
    integration_results = test_integration()
    print()
    
    # Summary
    print("=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)
    
    all_results = {**shared_results, **broker_results, **integration_results}
    total_tests = len(all_results)
    passed_tests = sum(all_results.values())
    
    print(f"Total Tests: {total_tests}")
    print(f"Passed: {passed_tests}")
    print(f"Failed: {total_tests - passed_tests}")
    print(f"Success Rate: {(passed_tests/total_tests)*100:.1f}%")
    print()
    
    if passed_tests == total_tests:
        print("🎉 ALL TESTS PASSED! Refactoring validation successful.")
        return 0
    else:
        print("⚠️  Some tests failed. Please review the output above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
