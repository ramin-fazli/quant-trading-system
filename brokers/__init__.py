"""
Brokers module for the pairs trading system.

This module contains broker implementations and shared functionality for:
- Base broker interface
- MT5 trading implementation
- CTrader trading implementation
"""

from .base_broker import BaseBroker

# Import broker implementations with error handling
try:
    from .mt5 import MT5RealTimeTrader
    MT5_AVAILABLE = True
except ImportError:
    MT5RealTimeTrader = None
    MT5_AVAILABLE = False

try:
    from .ctrader import CTraderRealTimeTrader
    CTRADER_AVAILABLE = True
except ImportError:
    CTraderRealTimeTrader = None
    CTRADER_AVAILABLE = False

__all__ = ['BaseBroker']

if MT5_AVAILABLE:
    __all__.append('MT5RealTimeTrader')

if CTRADER_AVAILABLE:
    __all__.append('CTraderRealTimeTrader')
