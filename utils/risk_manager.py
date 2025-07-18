"""
Risk Management Module for Trading Systems
==========================================

This module provides shared risk management functionality that can be used
across different broker implementations (MT5, CTrader, etc.).

Features:
- Drawdown tracking (portfolio and pair level)
- Position limits and exposure controls
- Risk assessment and suspension logic
- Currency conversion utilities

Author: Trading System v3.0
Date: July 2025
"""

import logging
from typing import Dict, List, Tuple, Optional, Any
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class RiskLimits:
    """Configuration for risk limits"""
    max_open_positions: int
    max_monetary_exposure: float
    max_portfolio_drawdown_perc: float
    max_pair_drawdown_perc: float
    monetary_value_tolerance: float
    initial_portfolio_value: float


class CurrencyConverter:
    """
    Shared currency conversion functionality
    """
    
    def __init__(self, account_currency: str = "USD"):
        self.account_currency = account_currency
        self._fx_cache = {}
        self._cache_timeout = 300  # 5 minutes
        self._last_update = {}
    
    @abstractmethod
    def get_fx_rate_from_broker(self, from_currency: str, to_currency: str) -> float:
        """
        Get FX rate from broker - must be implemented by each broker
        """
        pass
    
    def get_fx_rate(self, from_currency: str, to_currency: str) -> float:
        """
        Get FX rate with caching
        """
        if from_currency == to_currency:
            return 1.0
        
        cache_key = f"{from_currency}{to_currency}"
        current_time = datetime.now().timestamp()
        
        # Check cache
        if (cache_key in self._fx_cache and 
            cache_key in self._last_update and
            current_time - self._last_update[cache_key] < self._cache_timeout):
            return self._fx_cache[cache_key]
        
        # Get fresh rate from broker
        rate = self.get_fx_rate_from_broker(from_currency, to_currency)
        
        # Update cache
        self._fx_cache[cache_key] = rate
        self._last_update[cache_key] = current_time
        
        return rate
    
    def get_symbol_currency(self, symbol: str, symbol_info_cache: Dict) -> str:
        """
        Get the profit currency for a symbol - to be overridden by broker implementations
        """
        info = symbol_info_cache.get(symbol, {})
        return info.get('currency_profit', self.account_currency)


class DrawdownTracker:
    """
    Shared drawdown tracking functionality
    """
    
    def __init__(self, risk_limits: RiskLimits):
        self.risk_limits = risk_limits
        self.portfolio_peak_value = risk_limits.initial_portfolio_value
        self.pair_peak_values = {}
        self.suspended_pairs = set()
        self.portfolio_trading_suspended = False
    
    def check_drawdown_limits(self, current_portfolio_value: float, 
                            pair_pnl_data: Optional[Dict[str, Tuple[float, float]]] = None) -> bool:
        """
        Check if trading should be allowed based on drawdown limits
        
        Args:
            current_portfolio_value: Current portfolio value
            pair_pnl_data: Optional dict of pair_str -> (current_pnl, initial_value)
            
        Returns:
            True if trading is allowed, False if suspended
        """
        # Check portfolio-level drawdown
        if current_portfolio_value > self.portfolio_peak_value:
            self.portfolio_peak_value = current_portfolio_value
        
        portfolio_drawdown = ((self.portfolio_peak_value - current_portfolio_value) / 
                            self.portfolio_peak_value) * 100
        
        # Update portfolio suspension status
        if portfolio_drawdown > self.risk_limits.max_portfolio_drawdown_perc:
            if not self.portfolio_trading_suspended:
                logger.warning(f"Portfolio drawdown limit exceeded: {portfolio_drawdown:.2f}% > "
                             f"{self.risk_limits.max_portfolio_drawdown_perc:.2f}%")
                logger.warning("All new trading suspended until drawdown improves")
                self.portfolio_trading_suspended = True
            return False
        elif (self.portfolio_trading_suspended and 
              portfolio_drawdown <= self.risk_limits.max_portfolio_drawdown_perc * 0.8):  # 80% recovery
            logger.info(f"Portfolio drawdown improved to {portfolio_drawdown:.2f}%. Resuming trading")
            self.portfolio_trading_suspended = False
        
        # Check pair-level drawdowns if provided
        if pair_pnl_data:
            for pair_str, (current_pnl, initial_value) in pair_pnl_data.items():
                if pair_str not in self.pair_peak_values:
                    self.pair_peak_values[pair_str] = 0
                
                # Update pair peak value
                if current_pnl > self.pair_peak_values[pair_str]:
                    self.pair_peak_values[pair_str] = current_pnl
                
                # Calculate pair drawdown
                if initial_value > 0:
                    pair_drawdown = ((self.pair_peak_values[pair_str] - current_pnl) / 
                                   initial_value) * 100
                    
                    # Check pair-level drawdown
                    if pair_drawdown > self.risk_limits.max_pair_drawdown_perc:
                        if pair_str not in self.suspended_pairs:
                            logger.warning(f"Pair {pair_str} drawdown limit exceeded: "
                                         f"{pair_drawdown:.2f}% > {self.risk_limits.max_pair_drawdown_perc:.2f}%")
                            logger.warning(f"Suspending trading for {pair_str} until drawdown improves")
                            self.suspended_pairs.add(pair_str)
                    elif (pair_str in self.suspended_pairs and 
                          pair_drawdown <= self.risk_limits.max_pair_drawdown_perc * 0.8):  # 80% recovery
                        logger.info(f"Pair {pair_str} drawdown improved to {pair_drawdown:.2f}%. "
                                  f"Resuming trading")
                        self.suspended_pairs.remove(pair_str)
        
        return True
    
    def is_pair_suspended(self, pair_str: str) -> bool:
        """Check if a specific pair is suspended due to drawdown"""
        return pair_str in self.suspended_pairs
    
    def get_suspended_pairs_status(self) -> Dict[str, Any]:
        """Get status of suspended pairs"""
        return {
            'portfolio_suspended': self.portfolio_trading_suspended,
            'suspended_pairs': list(self.suspended_pairs),
            'portfolio_peak_value': self.portfolio_peak_value,
            'pair_peak_values': self.pair_peak_values.copy()
        }


class PositionSizeManager:
    """
    Shared position sizing and exposure management
    """
    
    def __init__(self, risk_limits: RiskLimits):
        self.risk_limits = risk_limits
    
    def can_open_new_position(self, current_positions: int, 
                            current_exposure: float,
                            estimated_position_size: Optional[float] = None) -> Tuple[bool, str]:
        """
        Check if we can open a new position based on portfolio limits
        
        Args:
            current_positions: Number of currently open positions
            current_exposure: Current monetary exposure
            estimated_position_size: Estimated size of new position
            
        Returns:
            Tuple of (can_open, reason)
        """
        # Check position count limit
        if current_positions >= self.risk_limits.max_open_positions:
            return False, (f"Position count limit reached: "
                         f"{current_positions}/{self.risk_limits.max_open_positions}")
        
        # Check monetary exposure limit
        if estimated_position_size:
            projected_exposure = current_exposure + estimated_position_size
            if projected_exposure > self.risk_limits.max_monetary_exposure:
                return False, (f"Monetary exposure limit would be exceeded: "
                             f"${projected_exposure:.2f} > ${self.risk_limits.max_monetary_exposure:.2f}")
        
        return True, (f"OK - Positions: {current_positions}/{self.risk_limits.max_open_positions}, "
                     f"Exposure: ${current_exposure:.2f}/${self.risk_limits.max_monetary_exposure:.2f}")


class VolumeBalancer:
    """
    Enhanced shared volume balancing functionality for pair trades
    Based on CTrader implementation with comprehensive validation and balance optimization
    """
    
    def __init__(self, monetary_value_tolerance: float = 0.05):
        self.monetary_value_tolerance = monetary_value_tolerance
    
    def calculate_balanced_volumes(self, symbol1: str, symbol2: str, 
                                 price1: float, price2: float,
                                 symbol_details1: Dict[str, Any], symbol_details2: Dict[str, Any],
                                 max_position_size: float) -> Optional[Tuple[float, float, float, float]]:
        """
        Calculate balanced volumes for a pair trade with comprehensive validation
        
        Args:
            symbol1, symbol2: Symbol names
            price1, price2: Current prices
            symbol_details1, symbol_details2: Complete symbol details including lot_size and volume constraints
            max_position_size: Maximum position size
            
        Returns:
            Tuple of (volume1, volume2, monetary_value1, monetary_value2) or None
        """
        try:
            # Validate symbol details availability
            if not symbol_details1:
                logger.error(f"No symbol details available for {symbol1}")
                return None
            
            if not symbol_details2:
                logger.error(f"No symbol details available for {symbol2}")
                return None
            
            # Ensure required fields are available with proper fallbacks
            required_fields = ['digits', 'min_volume', 'step_volume', 'lot_size']
            
            for symbol, details in [(symbol1, symbol_details1), (symbol2, symbol_details2)]:
                for field in required_fields:
                    if field not in details or details[field] is None:
                        logger.warning(f"Missing or null field '{field}' for symbol {symbol}")
            
            logger.debug(f"Volume calculation for {symbol1}-{symbol2}:")
            logger.debug(f"  {symbol1}: price={price1:.5f}, min_volume={symbol_details1.get('min_volume', 'MISSING')}, step_volume={symbol_details1.get('step_volume', 'MISSING')}")
            logger.debug(f"  {symbol2}: price={price2:.5f}, min_volume={symbol_details2.get('min_volume', 'MISSING')}, step_volume={symbol_details2.get('step_volume', 'MISSING')}")
            
            # Calculate target monetary value per leg (half of max position size for each leg)
            target_monetary_value = max_position_size / 2
            
            logger.debug(f"  Target monetary value per leg: ${target_monetary_value:.2f}")
            
            # Get lot sizes from symbol details (contract sizes)
            lot_size1 = symbol_details1.get('lot_size')
            lot_size2 = symbol_details2.get('lot_size')
            
            if lot_size1 is None:
                logger.error(f"No lot size information available for {symbol1}")
                return None
                
            if lot_size2 is None:
                logger.error(f"No lot size information available for {symbol2}")
                return None
            
            # Use lot sizes as contract sizes
            contract_size1 = lot_size1
            contract_size2 = lot_size2
            
            logger.debug(f"  Contract sizes: {symbol1}={contract_size1}, {symbol2}={contract_size2}")
            
            # Calculate required volumes for target monetary value
            # Formula: volume = target_value / (price * contract_size)
            volume1_raw = target_monetary_value / (price1 * contract_size1)
            volume2_raw = target_monetary_value / (price2 * contract_size2)
            
            logger.debug(f"  Raw volumes: {symbol1}={volume1_raw:.6f}, {symbol2}={volume2_raw:.6f}")
            
            # Apply volume constraints
            volume_constraints1 = {
                'volume_min': symbol_details1.get('min_volume', 0.01),
                'volume_max': symbol_details1.get('max_volume', 100.0),
                'volume_step': symbol_details1.get('step_volume', 0.01)
            }
            volume_constraints2 = {
                'volume_min': symbol_details2.get('min_volume', 0.01),
                'volume_max': symbol_details2.get('max_volume', 100.0),
                'volume_step': symbol_details2.get('step_volume', 0.01)
            }
            
            volume1 = self._normalize_volume(volume1_raw, volume_constraints1)
            volume2 = self._normalize_volume(volume2_raw, volume_constraints2)
            
            if volume1 is None or volume2 is None:
                logger.error(f"Failed to normalize volumes for {symbol1}-{symbol2}")
                return None
            
            logger.debug(f"  Normalized volumes: {symbol1}={volume1:.6f}, {symbol2}={volume2:.6f}")
            
            # Calculate actual monetary values with normalized volumes and contract sizes
            monetary_value1 = volume1 * price1 * contract_size1
            monetary_value2 = volume2 * price2 * contract_size2
            
            logger.debug(f"  Initial monetary values: {symbol1}=${monetary_value1:.2f}, {symbol2}=${monetary_value2:.2f}")
            
            # Try iterative adjustment for better balance
            best_volume1, best_volume2 = volume1, volume2
            best_monetary1, best_monetary2 = monetary_value1, monetary_value2
            best_diff = abs(monetary_value1 - monetary_value2) / max(monetary_value1, monetary_value2)
            
            # Try small adjustments to improve balance
            for multiplier in [0.95, 0.98, 1.02, 1.05]:
                try:
                    # Adjust the larger volume down or smaller volume up for better balance
                    if monetary_value1 > monetary_value2:
                        test_volume1 = self._normalize_volume(volume1_raw * multiplier, volume_constraints1)
                        test_volume2 = volume2
                    else:
                        test_volume1 = volume1
                        test_volume2 = self._normalize_volume(volume2_raw * multiplier, volume_constraints2)
                    
                    if test_volume1 is not None and test_volume2 is not None:
                        test_monetary1 = test_volume1 * price1 * contract_size1
                        test_monetary2 = test_volume2 * price2 * contract_size2
                        test_diff = abs(test_monetary1 - test_monetary2) / max(test_monetary1, test_monetary2)
                        
                        if test_diff < best_diff:
                            best_volume1, best_volume2 = test_volume1, test_volume2
                            best_monetary1, best_monetary2 = test_monetary1, test_monetary2
                            best_diff = test_diff
                            logger.debug(f"  Improved balance with multiplier {multiplier}: diff={test_diff:.4f}")
                except Exception as e:
                    logger.debug(f"  Adjustment failed for multiplier {multiplier}: {e}")
                    continue
            
            volume1, volume2 = best_volume1, best_volume2
            monetary_value1, monetary_value2 = best_monetary1, best_monetary2
            
            # Final validation against monetary value tolerance
            final_diff_pct = abs(monetary_value1 - monetary_value2) / max(monetary_value1, monetary_value2)
            
            logger.debug(f"  Final monetary values: {symbol1}=${monetary_value1:.2f}, {symbol2}=${monetary_value2:.2f}")
            logger.debug(f"  Final difference: {final_diff_pct:.4f} vs tolerance: {self.monetary_value_tolerance:.4f}")
            
            if final_diff_pct > self.monetary_value_tolerance:
                logger.warning(f"Monetary value difference ({final_diff_pct:.4f}) exceeds tolerance ({self.monetary_value_tolerance:.4f}) for {symbol1}-{symbol2}")
                return None
            
            logger.debug(f"  Successfully calculated balanced volumes with {final_diff_pct:.4f} difference")
            return volume1, volume2, monetary_value1, monetary_value2
            
        except Exception as e:
            logger.error(f"Error calculating balanced volumes for {symbol1}-{symbol2}: {e}")
            return None
    
    def _normalize_volume(self, volume_raw: float, constraints: Dict[str, float]) -> Optional[float]:
        """
        Enhanced volume normalization with comprehensive validation
        
        Args:
            volume_raw: Raw calculated volume
            constraints: Dict with 'volume_min', 'volume_max', 'volume_step'
            
        Returns:
            Normalized volume or None if invalid
        """
        try:
            # Validate that required volume constraints are available
            required_fields = ['volume_min', 'volume_step']
            for field in required_fields:
                if field not in constraints:
                    logger.error(f"Missing required volume constraint '{field}'")
                    return None
            
            min_vol = constraints['volume_min']
            max_vol = constraints.get('volume_max')
            step = constraints['volume_step']
            
            logger.debug(f"Normalizing volume: raw={volume_raw:.6f}, min={min_vol}, max={max_vol or 'None'}, step={step}")
            
            # Handle edge case where raw volume is 0 or negative
            if volume_raw <= 0:
                logger.warning(f"Invalid raw volume: {volume_raw}")
                return None
            
            # Round to valid step increments
            if step > 0:
                volume = round(volume_raw / step) * step
            else:
                volume = volume_raw
            
            # Apply minimum constraint
            volume = max(min_vol, volume)
            
            # Apply maximum constraint if available
            if max_vol is not None:
                volume = min(max_vol, volume)
            
            # Validate minimum volume requirement
            if volume < min_vol:
                logger.warning(f"Volume {volume:.6f} below minimum {min_vol}")
                return None
            
            # Validate maximum volume requirement if available
            if max_vol is not None and volume > max_vol:
                logger.warning(f"Volume {volume:.6f} exceeds maximum {max_vol}")
                volume = max_vol
            
            # Additional validation: ensure volume is not zero after rounding
            if volume == 0:
                logger.warning(f"Volume became zero after normalization (raw: {volume_raw:.6f})")
                return None
            
            logger.debug(f"Normalized volume: {volume:.6f}")
            return volume
            
        except Exception as e:
            logger.error(f"Error normalizing volume: {e}")
            return None


class RiskManager:
    """
    Main risk management coordinator that combines all risk management components
    """
    
    def __init__(self, risk_limits: RiskLimits, account_currency: str = "USD"):
        self.risk_limits = risk_limits
        self.drawdown_tracker = DrawdownTracker(risk_limits)
        self.position_size_manager = PositionSizeManager(risk_limits)
        self.volume_balancer = VolumeBalancer(risk_limits.monetary_value_tolerance)
        self.account_currency = account_currency
    
    def check_trading_allowed(self, current_portfolio_value: float,
                            current_positions: int, current_exposure: float,
                            estimated_position_size: Optional[float] = None,
                            pair_str: Optional[str] = None,
                            pair_pnl_data: Optional[Dict[str, Tuple[float, float]]] = None) -> Tuple[bool, str]:
        """
        Comprehensive check if trading is allowed
        
        Returns:
            Tuple of (allowed, reason)
        """
        # Check drawdown limits
        if not self.drawdown_tracker.check_drawdown_limits(current_portfolio_value, pair_pnl_data):
            if self.drawdown_tracker.portfolio_trading_suspended:
                return False, "Trading suspended due to portfolio drawdown limit"
            elif pair_str and self.drawdown_tracker.is_pair_suspended(pair_str):
                return False, f"Trading suspended for {pair_str} due to pair drawdown limit"
        
        # Check position limits
        can_open, reason = self.position_size_manager.can_open_new_position(
            current_positions, current_exposure, estimated_position_size)
        
        if not can_open:
            return False, reason
        
        return True, "Trading allowed"
    
    def get_risk_status_report(self) -> Dict[str, Any]:
        """Get comprehensive risk status report"""
        return {
            'timestamp': datetime.now().isoformat(),
            'drawdown_status': self.drawdown_tracker.get_suspended_pairs_status(),
            'risk_limits': {
                'max_open_positions': self.risk_limits.max_open_positions,
                'max_monetary_exposure': self.risk_limits.max_monetary_exposure,
                'max_portfolio_drawdown_perc': self.risk_limits.max_portfolio_drawdown_perc,
                'max_pair_drawdown_perc': self.risk_limits.max_pair_drawdown_perc,
                'monetary_value_tolerance': self.risk_limits.monetary_value_tolerance
            }
        }
