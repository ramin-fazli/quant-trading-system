"""
Base Broker Interface for Trading Systems
==========================================

This module provides a base interface and shared functionality that all broker
implementations should inherit from. It promotes code reuse and standardization
across different broker implementations (MT5, CTrader, etc.).

Features:
- Abstract base class for broker implementations
- Shared broker functionality
- Standardized interfaces for portfolio management, risk management
- Integration with shared utility modules

Author: Trading System v3.0
Date: July 2025
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Tuple, Optional, Any, Union
from datetime import datetime
import threading

from config import TradingConfig
from strategies.base_strategy import BaseStrategy
from utils.risk_manager import RiskManager, RiskLimits
from utils.portfolio_manager import PortfolioManager, PriceProvider, PortfolioSnapshot
from utils.signal_processor import SignalProcessor, PairState, TradingSignal
from utils.state_manager import TradingStateManager

logger = logging.getLogger(__name__)


class BaseBroker(PriceProvider, ABC):
    """
    Abstract base class for all broker implementations
    
    This class defines the common interface and provides shared functionality
    that all broker implementations should use. It integrates with the shared
    utility modules for risk management, portfolio management, and signal processing.
    """
    
    def __init__(self, config: TradingConfig, data_manager, strategy: BaseStrategy = None):
        self.config = config
        self.data_manager = data_manager
        
        # Strategy handling
        if strategy is None:
            from strategies.pairs_trading import OptimizedPairsStrategy
            self.strategy = OptimizedPairsStrategy(config, data_manager)
        else:
            self.strategy = strategy
        
        # Core trading state
        self.active_positions = {}
        self.pair_states = {}
        self.is_trading = False
        self.last_update = {}
        
        # Threading
        self._update_lock = threading.Lock()
        
        # Account information (set default first)
        self.account_currency = "USD"  # To be updated by broker implementations
        
        # Initialize shared utility modules (after account_currency is set)
        self._initialize_shared_modules()
        
        logger.info(f"Base broker initialized with {self.strategy.__class__.__name__}")
    
    def _initialize_shared_modules(self):
        """Initialize shared utility modules"""
        # Risk management
        risk_limits = RiskLimits(
            max_open_positions=self.config.max_open_positions,
            max_monetary_exposure=self.config.max_monetary_exposure,
            max_portfolio_drawdown_perc=self.config.max_portfolio_drawdown_perc,
            max_pair_drawdown_perc=self.config.max_pair_drawdown_perc,
            monetary_value_tolerance=self.config.monetary_value_tolerance,
            initial_portfolio_value=self.config.initial_portfolio_value
        )
        self.risk_manager = RiskManager(risk_limits, self.account_currency)
        
        # Portfolio management
        self.portfolio_manager = PortfolioManager(
            self.config.initial_portfolio_value, 
            self.account_currency
        )
        
        # Signal processing
        self.signal_processor = SignalProcessor(
            strategy=self.strategy,
            min_check_interval=getattr(self.config, 'min_signal_check_interval', 0.5),
            default_cooldown_bars=getattr(self.config, 'cooldown_bars', 5)
        )
        
        # State management
        self.state_manager = TradingStateManager(self.config.state_file, self._update_lock)
    
    # Abstract methods that must be implemented by broker subclasses
    
    @abstractmethod
    def initialize(self) -> bool:
        """Initialize the broker connection and trading system"""
        pass
    
    @abstractmethod
    def start_trading(self):
        """Start the real-time trading loop"""
        pass
    
    @abstractmethod
    def stop_trading(self):
        """Stop trading and cleanup resources"""
        pass
    
    @abstractmethod
    def _execute_trade(self, signal: TradingSignal) -> bool:
        """Execute a trading signal - broker-specific implementation"""
        pass
    
    @abstractmethod
    def get_current_prices(self, symbols: List[str]) -> Dict[str, float]:
        """Get current prices for symbols - required by PriceProvider interface"""
        pass
    
    @abstractmethod
    def get_bid_ask_prices(self, symbols: List[str]) -> Dict[str, Tuple[float, float]]:
        """Get bid/ask prices for symbols - required by PriceProvider interface"""
        pass
    
    @abstractmethod
    def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """Get symbol information including contract size, currency, etc."""
        pass
    
    @abstractmethod
    def get_account_info(self) -> Dict[str, Any]:
        """Get account information from broker"""
        pass
    
    # Shared functionality using utility modules
    
    def check_trading_allowed(self, pair_str: Optional[str] = None, 
                            estimated_position_size: Optional[float] = None) -> Tuple[bool, str]:
        """
        Check if trading is allowed based on risk management rules
        
        Args:
            pair_str: Optional specific pair to check
            estimated_position_size: Optional estimated position size
            
        Returns:
            Tuple of (allowed, reason)
        """
        try:
            # Calculate current portfolio metrics
            current_portfolio_value = self.portfolio_manager.portfolio_calculator.calculate_portfolio_current_value(
                self.active_positions, self, self.get_symbol_info_cache())
            
            current_exposure = self.portfolio_manager.portfolio_calculator.calculate_total_exposure(
                self.active_positions)
            
            # Prepare pair P&L data for drawdown checking
            pair_pnl_data = {}
            if pair_str and pair_str in self.active_positions:
                position = self.active_positions[pair_str]
                current_pnl = self.portfolio_manager.portfolio_calculator.calculate_position_pnl(
                    position, self, self.get_symbol_info_cache())
                initial_value = (position.get('monetary_value1', 0) + position.get('monetary_value2', 0)) / 2
                pair_pnl_data[pair_str] = (current_pnl, initial_value)
            
            # Use risk manager for comprehensive check
            return self.risk_manager.check_trading_allowed(
                current_portfolio_value=current_portfolio_value,
                current_positions=len(self.active_positions),
                current_exposure=current_exposure,
                estimated_position_size=estimated_position_size,
                pair_str=pair_str,
                pair_pnl_data=pair_pnl_data
            )
            
        except Exception as e:
            logger.error(f"Error checking trading permissions: {e}")
            return False, f"Risk check error: {e}"
    
    def get_portfolio_status(self) -> Dict[str, Any]:
        """
        Get comprehensive portfolio status using shared portfolio manager
        
        Returns:
            Portfolio status dictionary
        """
        try:
            # Get account info from broker
            account_info = self.get_account_info()
            
            # Get portfolio snapshot
            portfolio_snapshot = self.portfolio_manager.get_portfolio_status(
                active_positions=self.active_positions,
                price_provider=self,
                symbol_info_cache=self.get_symbol_info_cache(),
                account_info=account_info
            )
            
            # Convert to dictionary format
            portfolio_dict = {
                'timestamp': portfolio_snapshot.timestamp,
                'total_value': portfolio_snapshot.total_value,
                'balance': portfolio_snapshot.balance,
                'equity': portfolio_snapshot.equity,
                'unrealized_pnl': portfolio_snapshot.unrealized_pnl,
                'realized_pnl': portfolio_snapshot.realized_pnl,
                'position_count': portfolio_snapshot.position_count,
                'total_exposure': portfolio_snapshot.total_exposure,
                'free_margin': portfolio_snapshot.free_margin,
                'margin_level': portfolio_snapshot.margin_level,
                'positions': portfolio_snapshot.positions,
                'broker': self.__class__.__name__.replace('RealTimeTrader', '').lower(),
                'strategy': self.strategy.__class__.__name__,
                'account_currency': self.account_currency,
                'risk_status': self.risk_manager.get_risk_status_report()
            }
            
            return portfolio_dict
            
        except Exception as e:
            logger.error(f"Error getting portfolio status: {e}")
            return {
                'timestamp': datetime.now().isoformat(),
                'error': str(e),
                'broker': self.__class__.__name__.replace('RealTimeTrader', '').lower(),
                'strategy': self.strategy.__class__.__name__ if self.strategy else 'Unknown'
            }
    
    def process_trading_signals(self) -> Dict[str, Any]:
        """
        Process trading signals for all pairs using shared signal processor
        
        Returns:
            Signal processing summary
        """
        try:
            # Risk check callback
            def risk_check_callback(pair_str: str) -> Tuple[bool, str]:
                return self.check_trading_allowed(pair_str)
            
            # Execution callback
            def execution_callback(signal: TradingSignal) -> bool:
                return self._execute_trade(signal)
            
            # Process signals
            return self.signal_processor.process_signals(
                pair_states=self.pair_states,
                risk_check_callback=risk_check_callback,
                execution_callback=execution_callback
            )
            
        except Exception as e:
            logger.error(f"Error processing trading signals: {e}")
            return {
                'timestamp': datetime.now().isoformat(),
                'error': str(e),
                'processed_count': 0,
                'executed_count': 0
            }
    
    def update_pair_on_new_candle(self, pair_str: str):
        """Update pair state when new candle arrives"""
        if pair_str in self.pair_states:
            self.signal_processor.update_pair_on_new_candle(pair_str, self.pair_states[pair_str])
    
    def save_state(self) -> bool:
        """Save current trading state"""
        try:
            state_data = {
                'active_positions': self.active_positions,
                'pair_states_summary': self._get_pair_states_summary(),
                'risk_status': self.risk_manager.get_risk_status_report(),
                'portfolio_status': self.get_portfolio_status(),
                'timestamp': datetime.now().isoformat()
            }
            return self.state_manager.save_state(state_data)
        except Exception as e:
            logger.error(f"Error saving state: {e}")
            return False
    
    def load_state(self) -> bool:
        """Load previous trading state"""
        try:
            state_data = self.state_manager.load_state()
            if state_data:
                self.active_positions = state_data.get('active_positions', {})
                # Note: pair_states would need special handling for full restoration
                logger.info(f"Loaded state with {len(self.active_positions)} active positions")
                return True
            return False
        except Exception as e:
            logger.error(f"Error loading state: {e}")
            return False
    
    def get_state_info(self) -> Dict[str, Any]:
        """Get trading state information"""
        try:
            return self.state_manager.get_state_info()
        except Exception as e:
            logger.error(f"Error getting state info: {e}")
            return {'error': str(e)}
    
    def delete_state(self) -> bool:
        """Delete the current state file"""
        try:
            return self.state_manager.delete_state()
        except Exception as e:
            logger.error(f"Error deleting state: {e}")
            return False
    
    # Helper methods
    
    def get_symbol_info_cache(self) -> Dict[str, Dict]:
        """Get symbol info cache - to be implemented by broker subclasses"""
        if hasattr(self.data_manager, 'symbol_info_cache'):
            return self.data_manager.symbol_info_cache
        return {}
    
    def _get_pair_states_summary(self) -> Dict[str, Any]:
        """Get summary of pair states for saving"""
        summary = {}
        for pair_str, state in self.pair_states.items():
            summary[pair_str] = {
                'symbol1': state.symbol1,
                'symbol2': state.symbol2,
                'position': state.position,
                'last_signal': state.last_signal,
                'cooldown': state.cooldown,
                'last_update': state.last_update.isoformat() if state.last_update else None,
                'last_candle_time': state.last_candle_time.isoformat() if state.last_candle_time else None,
                'price_history_length1': len(state.price_history1),
                'price_history_length2': len(state.price_history2)
            }
        return summary
    
    def get_trading_summary(self) -> Dict[str, Any]:
        """Get comprehensive trading summary"""
        try:
            portfolio_status = self.get_portfolio_status()
            risk_status = self.risk_manager.get_risk_status_report()
            signal_status = self.signal_processor.get_processing_status()
            
            return {
                'timestamp': datetime.now().isoformat(),
                'broker': self.__class__.__name__.replace('RealTimeTrader', '').lower(),
                'strategy': self.strategy.__class__.__name__,
                'is_trading': self.is_trading,
                'portfolio': portfolio_status,
                'risk_management': risk_status,
                'signal_processing': signal_status,
                'pair_count': len(self.pair_states),
                'active_positions': len(self.active_positions)
            }
            
        except Exception as e:
            logger.error(f"Error getting trading summary: {e}")
            return {
                'timestamp': datetime.now().isoformat(),
                'error': str(e)
            }
