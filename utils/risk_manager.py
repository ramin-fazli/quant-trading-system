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
        if not symbol_info_cache:
            logger.error("No symbol_info_cache provided for currency lookup")
            return None
            
        if symbol not in symbol_info_cache:
            logger.error(f"Symbol {symbol} not found in symbol_info_cache")
            return None
            
        info = symbol_info_cache[symbol]
        if not isinstance(info, dict):
            logger.error(f"Invalid symbol info format for {symbol}: expected dict, got {type(info)}")
            return None
            
        if 'currency_profit' not in info:
            logger.error(f"Missing currency_profit field for symbol {symbol}")
            return None
            
        currency = info['currency_profit']
        if not currency or not isinstance(currency, str):
            logger.error(f"Invalid currency_profit for symbol {symbol}: {currency}")
            return None
            
        return currency


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
        Calculate balanced volumes for a pair trade using margin-based calculations with symbol leverage
        
        Args:
            symbol1, symbol2: Symbol names
            price1, price2: Current prices
            symbol_details1, symbol_details2: Complete symbol details including lot_size, volume constraints, and leverage info
            max_position_size: Maximum total margin used for the pair position (half margin for each leg)
            
        Returns:
            Tuple of (volume1, volume2, monetary_value1, monetary_value2) or None
            
        Notes:
            - max_position_size is interpreted as total margin requirement for the pair
            - Each leg uses half the margin (max_position_size / 2)
            - Monetary value for each leg = margin * symbol_leverage
            - Symbol leverage is extracted from available fields: leverageTiers, leverageInCents, maxLeverage, or defaults to 30x
            - Volumes are calculated to achieve balanced monetary exposure using leverage
        """
        try:
            # Validate symbol details availability
            if not symbol_details1:
                logger.error(f"No symbol details available for {symbol1}")
                return None
            
            if not symbol_details2:
                logger.error(f"No symbol details available for {symbol2}")
                return None
            
            # Validate all required fields are available and valid
            required_fields = ['digits', 'min_volume', 'step_volume', 'lot_size']
            
            for symbol, details in [(symbol1, symbol_details1), (symbol2, symbol_details2)]:
                for field in required_fields:
                    if field not in details or details[field] is None:
                        logger.error(f"Missing or null required field '{field}' for symbol {symbol}")
                        return None
            
            logger.debug(f"Volume calculation for {symbol1}-{symbol2}:")
            logger.debug(f"  {symbol1}: price={price1:.5f}, min_volume={symbol_details1.get('min_volume') if 'min_volume' in symbol_details1 else 'NOT_SET'}, step_volume={symbol_details1.get('step_volume') if 'step_volume' in symbol_details1 else 'NOT_SET'}")
            logger.debug(f"  {symbol2}: price={price2:.5f}, min_volume={symbol_details2.get('min_volume') if 'min_volume' in symbol_details2 else 'NOT_SET'}, step_volume={symbol_details2.get('step_volume') if 'step_volume' in symbol_details2 else 'NOT_SET'}")
            
            # Calculate target margin per leg (half of max position size for each leg)
            # Note: We'll use this as an initial target, but will validate and adjust later
            target_margin_per_leg = max_position_size / 2
            
            logger.debug(f"  Initial target margin per leg: ${target_margin_per_leg:.2f}")
            logger.debug(f"  Total margin limit: ${max_position_size:.2f}")
            
            leverage1 = symbol_details1.get('symbol_leverage')
            leverage2 = symbol_details2.get('symbol_leverage')

            # Validate leverage data - NO defaults allowed, all leverage must be real API data
            if leverage1 is None or leverage2 is None:
                leverage1_source = symbol_details1.get('leverage_source', 'UNKNOWN')
                leverage2_source = symbol_details2.get('leverage_source', 'UNKNOWN')
                
                logger.error(f"Cannot calculate volumes for {symbol1}-{symbol2} - missing real leverage data")
                logger.error(f"  {symbol1}: leverage={leverage1}, source={leverage1_source}")
                logger.error(f"  {symbol2}: leverage={leverage2}, source={leverage2_source}")
                logger.error(f"  Trading requires actual leverage data from cTrader API")
                logger.error(f"  Wait for leverage extraction to complete or check API connectivity")
                return None
            
            # Validate leverage values are positive numbers
            if not isinstance(leverage1, (int, float)) or leverage1 <= 0:
                logger.error(f"Invalid leverage value for {symbol1}: {leverage1} (must be positive number)")
                return None
                
            if not isinstance(leverage2, (int, float)) or leverage2 <= 0:
                logger.error(f"Invalid leverage value for {symbol2}: {leverage2} (must be positive number)")
                return None
            
            logger.debug(f"  Symbol leverages: {symbol1}={leverage1}x, {symbol2}={leverage2}x")
            
            # Extract and validate lot sizes from symbol details (contract sizes)
            if 'lot_size' not in symbol_details1:
                logger.error(f"Missing lot_size field for symbol {symbol1}")
                return None
                
            if 'lot_size' not in symbol_details2:
                logger.error(f"Missing lot_size field for symbol {symbol2}")
                return None
                
            lot_size1 = symbol_details1['lot_size']
            lot_size2 = symbol_details2['lot_size']
            
            if lot_size1 is None or not isinstance(lot_size1, (int, float)) or lot_size1 <= 0:
                logger.error(f"Invalid lot_size for symbol {symbol1}: {lot_size1}")
                return None
                
            if lot_size2 is None or not isinstance(lot_size2, (int, float)) or lot_size2 <= 0:
                logger.error(f"Invalid lot_size for symbol {symbol2}: {lot_size2}")
                return None
            
            # Use lot sizes as contract sizes
            contract_size1 = lot_size1
            contract_size2 = lot_size2
            
            logger.debug(f"  Contract sizes: {symbol1}={contract_size1}, {symbol2}={contract_size2}")
            
            # Calculate target monetary values from margin and leverage
            # Formula: monetary_value = margin * leverage
            target_monetary_value1 = target_margin_per_leg * leverage1
            target_monetary_value2 = target_margin_per_leg * leverage2
            
            logger.debug(f"  Target monetary values: {symbol1}=${target_monetary_value1:.2f}, {symbol2}=${target_monetary_value2:.2f}")
            
            # Calculate required volumes for target monetary values
            # Formula: volume = target_monetary_value / (price * contract_size)
            volume1_raw = target_monetary_value1 / (price1 * contract_size1)
            volume2_raw = target_monetary_value2 / (price2 * contract_size2)
            
            logger.debug(f"  Raw volumes: {symbol1}={volume1_raw:.6f}, {symbol2}={volume2_raw:.6f}")
            
            # Validate and extract volume constraints
            def get_volume_constraints(symbol: str, details: Dict[str, Any]) -> Optional[Dict[str, float]]:
                """Extract volume constraints with validation"""
                required_fields = ['min_volume', 'step_volume']
                constraints = {}
                
                for field in required_fields:
                    if field not in details:
                        logger.error(f"Missing required volume constraint '{field}' for symbol {symbol}")
                        return None
                    
                    value = details[field]
                    if value is None or not isinstance(value, (int, float)) or value <= 0:
                        logger.error(f"Invalid {field} for symbol {symbol}: {value}")
                        return None
                    constraints[field.replace('min_volume', 'volume_min').replace('step_volume', 'volume_step')] = float(value)
                
                # max_volume is optional but must be valid if present
                if 'max_volume' in details and details['max_volume'] is not None:
                    max_vol = details['max_volume']
                    if isinstance(max_vol, (int, float)) and max_vol > 0:
                        constraints['volume_max'] = float(max_vol)
                    else:
                        logger.warning(f"Invalid max_volume for symbol {symbol}: {max_vol}. Ignoring.")
                
                return constraints
            
            volume_constraints1 = get_volume_constraints(symbol1, symbol_details1)
            volume_constraints2 = get_volume_constraints(symbol2, symbol_details2)
            
            if volume_constraints1 is None or volume_constraints2 is None:
                logger.error(f"Failed to extract volume constraints for {symbol1}-{symbol2}")
                return None
            
            # Early validation: Check if minimum volumes would exceed margin limit
            min_vol1 = volume_constraints1['volume_min']
            min_vol2 = volume_constraints2['volume_min']
            min_monetary1 = min_vol1 * price1 * contract_size1
            min_monetary2 = min_vol2 * price2 * contract_size2
            min_margin1 = min_monetary1 / leverage1 if leverage1 > 0 else 0
            min_margin2 = min_monetary2 / leverage2 if leverage2 > 0 else 0
            min_total_margin = min_margin1 + min_margin2
            
            logger.debug(f"  Minimum volume validation for {symbol1}-{symbol2}:")
            logger.debug(f"    Min volumes: {symbol1}={min_vol1:.6f}, {symbol2}={min_vol2:.6f}")
            logger.debug(f"    Prices: {symbol1}=${price1:.2f}, {symbol2}=${price2:.2f}")
            logger.debug(f"    Contract sizes: {symbol1}={contract_size1}, {symbol2}={contract_size2}")
            logger.debug(f"    Leverages: {symbol1}={leverage1}x, {symbol2}={leverage2}x")
            logger.debug(f"    Min margins: {symbol1}=${min_margin1:.2f}, {symbol2}=${min_margin2:.2f}")
            logger.debug(f"    Min total margin: ${min_total_margin:.2f} vs limit: ${max_position_size:.2f}")
            
            if min_total_margin > max_position_size:
                logger.error(f"❌ PAIR EXCLUDED: Minimum volume requirements for {symbol1}-{symbol2} exceed margin limit:")
                logger.error(f"  Minimum volumes: {symbol1}={min_vol1:.6f} lots, {symbol2}={min_vol2:.6f} lots")
                logger.error(f"  Minimum monetary values: {symbol1}=${min_monetary1:.2f}, {symbol2}=${min_monetary2:.2f}")
                logger.error(f"  Minimum margin required: ${min_total_margin:.2f} > ${max_position_size:.2f} (limit)")
                logger.error(f"  Consider increasing MAX_POSITION_SIZE to at least ${min_total_margin * 1.2:.0f} to trade this pair")
                return None
            
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
            
            # For leverage-based calculations, try to balance by adjusting one leg to match the other
            # Find which leg should be adjusted to achieve better balance
            if abs(monetary_value1 - monetary_value2) > 0:
                # Calculate what volume would be needed to match monetary values
                if monetary_value1 > monetary_value2:
                    # Reduce volume1 to match monetary_value2
                    target_volume1 = monetary_value2 / (price1 * contract_size1)
                    test_volume1 = self._normalize_volume(target_volume1, volume_constraints1)
                    if test_volume1 is not None:
                        test_monetary1 = test_volume1 * price1 * contract_size1
                        test_diff = abs(test_monetary1 - monetary_value2) / max(test_monetary1, monetary_value2)
                        if test_diff < best_diff:
                            best_volume1, best_volume2 = test_volume1, volume2
                            best_monetary1, best_monetary2 = test_monetary1, monetary_value2
                            best_diff = test_diff
                            logger.debug(f"  Improved balance by adjusting volume1: diff={test_diff:.4f}")
                else:
                    # Reduce volume2 to match monetary_value1
                    target_volume2 = monetary_value1 / (price2 * contract_size2)
                    test_volume2 = self._normalize_volume(target_volume2, volume_constraints2)
                    if test_volume2 is not None:
                        test_monetary2 = test_volume2 * price2 * contract_size2
                        test_diff = abs(monetary_value1 - test_monetary2) / max(monetary_value1, test_monetary2)
                        if test_diff < best_diff:
                            best_volume1, best_volume2 = volume1, test_volume2
                            best_monetary1, best_monetary2 = monetary_value1, test_monetary2
                            best_diff = test_diff
                            logger.debug(f"  Improved balance by adjusting volume2: diff={test_diff:.4f}")
                
                # After achieving balance, try to scale up both volumes proportionally to better utilize margin
                if best_diff <= self.monetary_value_tolerance:
                    current_margin1 = best_monetary1 / leverage1 if leverage1 > 0 else 0
                    current_margin2 = best_monetary2 / leverage2 if leverage2 > 0 else 0
                    current_total_margin = current_margin1 + current_margin2
                    
                    if current_total_margin > 0 and current_total_margin < max_position_size * 0.9:  # If using less than 90% of available margin
                        scale_factor = min(2.0, (max_position_size * 0.9) / current_total_margin)  # Don't scale beyond 2x or 90% of margin
                        
                        test_volume1 = self._normalize_volume(best_volume1 * scale_factor, volume_constraints1)
                        test_volume2 = self._normalize_volume(best_volume2 * scale_factor, volume_constraints2)
                        
                        if test_volume1 is not None and test_volume2 is not None:
                            test_monetary1 = test_volume1 * price1 * contract_size1
                            test_monetary2 = test_volume2 * price2 * contract_size2
                            test_margin1 = test_monetary1 / leverage1 if leverage1 > 0 else 0
                            test_margin2 = test_monetary2 / leverage2 if leverage2 > 0 else 0
                            test_total_margin = test_margin1 + test_margin2
                            test_diff = abs(test_monetary1 - test_monetary2) / max(test_monetary1, test_monetary2)
                            
                            # Accept the scaled volumes if they maintain balance and don't exceed margin
                            if test_diff <= self.monetary_value_tolerance and test_total_margin <= max_position_size:
                                best_volume1, best_volume2 = test_volume1, test_volume2
                                best_monetary1, best_monetary2 = test_monetary1, test_monetary2
                                best_diff = test_diff
                                logger.debug(f"  Scaled volumes by {scale_factor:.2f}x to better utilize margin: new_diff={test_diff:.4f}, new_margin=${test_total_margin:.2f}")
            
            # Try small adjustments to improve balance further
            for multiplier in [0.95, 0.98, 1.02, 1.05]:
                try:
                    # Adjust volumes proportionally while maintaining leverage-based calculations
                    test_volume1 = self._normalize_volume(volume1_raw * multiplier, volume_constraints1)
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
            
            # Calculate actual margin usage for verification
            actual_margin1 = monetary_value1 / leverage1 if leverage1 > 0 else 0
            actual_margin2 = monetary_value2 / leverage2 if leverage2 > 0 else 0
            total_margin_used = actual_margin1 + actual_margin2
            
            # If margin exceeds limit, scale down proportionally with a safety buffer
            if total_margin_used > max_position_size:
                # Use a slightly more aggressive scale-down to account for volume step constraints
                safety_factor = 0.9  # Target 90% of limit to ensure we stay under
                scale_down_factor = (max_position_size * safety_factor) / total_margin_used
                logger.warning(f"  Margin usage (${total_margin_used:.2f}) exceeds limit (${max_position_size:.2f}), scaling down by {scale_down_factor:.4f} with safety buffer")
                
                # Check if scaling would violate minimum volume constraints
                min_vol1 = volume_constraints1['volume_min']
                min_vol2 = volume_constraints2['volume_min']
                scaled_vol1_test = volume1 * scale_down_factor
                scaled_vol2_test = volume2 * scale_down_factor
                
                if scaled_vol1_test < min_vol1 or scaled_vol2_test < min_vol2:
                    logger.warning(f"  Scaling would violate minimum volume constraints:")
                    logger.warning(f"    {symbol1}: {scaled_vol1_test:.6f} < {min_vol1:.6f} (min)")
                    logger.warning(f"    {symbol2}: {scaled_vol2_test:.6f} < {min_vol2:.6f} (min)")
                    logger.error(f"Cannot scale volumes for {symbol1}-{symbol2} to fit within margin limit without violating minimum volume constraints")
                    return None
                
                # Scale down volumes proportionally
                scaled_volume1 = self._normalize_volume(volume1 * scale_down_factor, volume_constraints1)
                scaled_volume2 = self._normalize_volume(volume2 * scale_down_factor, volume_constraints2)
                
                if scaled_volume1 is not None and scaled_volume2 is not None:
                    # Recalculate with scaled volumes
                    volume1, volume2 = scaled_volume1, scaled_volume2
                    monetary_value1 = volume1 * price1 * contract_size1
                    monetary_value2 = volume2 * price2 * contract_size2
                    actual_margin1 = monetary_value1 / leverage1 if leverage1 > 0 else 0
                    actual_margin2 = monetary_value2 / leverage2 if leverage2 > 0 else 0
                    total_margin_used = actual_margin1 + actual_margin2
                    
                    # Recalculate final difference
                    final_diff_pct = abs(monetary_value1 - monetary_value2) / max(monetary_value1, monetary_value2)
                    logger.warning(f"  After scaling: margin=${total_margin_used:.2f}, diff={final_diff_pct:.4f}")
                    
                    # If still over limit after scaling with safety buffer, try more aggressive scaling
                    if total_margin_used > max_position_size:
                        aggressive_factor = 0.95  # Target 95% of limit
                        more_aggressive_scale = (max_position_size * aggressive_factor) / (total_margin_used / scale_down_factor)  # relative to original
                        
                        # Check if more aggressive scaling would violate minimum volume constraints
                        more_aggressive_vol1_test = volume1 * more_aggressive_scale / scale_down_factor
                        more_aggressive_vol2_test = volume2 * more_aggressive_scale / scale_down_factor
                        
                        if more_aggressive_vol1_test < min_vol1 or more_aggressive_vol2_test < min_vol2:
                            logger.warning(f"  More aggressive scaling would violate minimum volume constraints:")
                            logger.warning(f"    {symbol1}: {more_aggressive_vol1_test:.6f} < {min_vol1:.6f} (min)")
                            logger.warning(f"    {symbol2}: {more_aggressive_vol2_test:.6f} < {min_vol2:.6f} (min)")
                            logger.error(f"Cannot reduce volumes for {symbol1}-{symbol2} further without violating minimum volume constraints")
                            return None
                        
                        logger.warning(f"  Still over limit, trying more aggressive scaling: {more_aggressive_scale:.4f}")
                        
                        scaled_volume1 = self._normalize_volume(volume1 * more_aggressive_scale / scale_down_factor, volume_constraints1)
                        scaled_volume2 = self._normalize_volume(volume2 * more_aggressive_scale / scale_down_factor, volume_constraints2)
                        
                        if scaled_volume1 is not None and scaled_volume2 is not None:
                            volume1, volume2 = scaled_volume1, scaled_volume2
                            monetary_value1 = volume1 * price1 * contract_size1
                            monetary_value2 = volume2 * price2 * contract_size2
                            actual_margin1 = monetary_value1 / leverage1 if leverage1 > 0 else 0
                            actual_margin2 = monetary_value2 / leverage2 if leverage2 > 0 else 0
                            total_margin_used = actual_margin1 + actual_margin2
                            final_diff_pct = abs(monetary_value1 - monetary_value2) / max(monetary_value1, monetary_value2)
                            logger.warning(f"  After aggressive scaling: margin=${total_margin_used:.2f}, diff={final_diff_pct:.4f}")
            # Final validation: if margin still exceeds limit, force reduce volumes step by step
            if total_margin_used > max_position_size:
                logger.warning(f"  Final margin check: ${total_margin_used:.2f} > ${max_position_size:.2f}, forcing volume reduction")
                
                # Get minimum volume constraints for validation
                min_vol1 = volume_constraints1['volume_min']
                min_vol2 = volume_constraints2['volume_min']
                
                # Try reducing volumes in small steps until we're under the limit
                reduction_steps = [0.98, 0.96, 0.94, 0.92, 0.90]
                for reduction_factor in reduction_steps:
                    # Check if reduction would violate minimum volume constraints
                    test_vol1_raw = volume1 * reduction_factor
                    test_vol2_raw = volume2 * reduction_factor
                    
                    if test_vol1_raw < min_vol1 or test_vol2_raw < min_vol2:
                        logger.warning(f"  Reduction factor {reduction_factor:.2f} would violate minimum volume constraints:")
                        logger.warning(f"    {symbol1}: {test_vol1_raw:.6f} < {min_vol1:.6f} (min)")
                        logger.warning(f"    {symbol2}: {test_vol2_raw:.6f} < {min_vol2:.6f} (min)")
                        continue
                    
                    test_volume1 = self._normalize_volume(test_vol1_raw, volume_constraints1)
                    test_volume2 = self._normalize_volume(test_vol2_raw, volume_constraints2)
                    
                    if test_volume1 is not None and test_volume2 is not None:
                        test_monetary1 = test_volume1 * price1 * contract_size1
                        test_monetary2 = test_volume2 * price2 * contract_size2
                        test_margin1 = test_monetary1 / leverage1 if leverage1 > 0 else 0
                        test_margin2 = test_monetary2 / leverage2 if leverage2 > 0 else 0
                        test_total_margin = test_margin1 + test_margin2
                        
                        if test_total_margin <= max_position_size:
                            volume1, volume2 = test_volume1, test_volume2
                            monetary_value1, monetary_value2 = test_monetary1, test_monetary2
                            actual_margin1, actual_margin2 = test_margin1, test_margin2
                            total_margin_used = test_total_margin
                            final_diff_pct = abs(monetary_value1 - monetary_value2) / max(monetary_value1, monetary_value2)
                            logger.warning(f"  Forced volume reduction by {reduction_factor:.2f}: margin=${total_margin_used:.2f}, diff={final_diff_pct:.4f}")
                            break
                else:
                    logger.error(f"Could not reduce volumes for {symbol1}-{symbol2} to fit within margin limit without violating minimum volume constraints")
                    return None
            
            logger.debug(f"  Final monetary values: {symbol1}=${monetary_value1:.2f}, {symbol2}=${monetary_value2:.2f}")
            logger.debug(f"  Actual margin usage: {symbol1}=${actual_margin1:.2f}, {symbol2}=${actual_margin2:.2f}, total=${total_margin_used:.2f}")
            logger.debug(f"  Target margin was: ${max_position_size:.2f}")
            logger.debug(f"  Final difference: {final_diff_pct:.4f} vs tolerance: {self.monetary_value_tolerance:.4f}")
            
            if final_diff_pct > self.monetary_value_tolerance:
                # For leverage-based calculations, be more lenient with tolerance for very different leverages
                # Check if the difference is due to leverage mismatch rather than calculation error
                if leverage1 and leverage2 and abs(leverage1 - leverage2) / max(leverage1, leverage2) > 0.5:
                    # Large leverage difference - use a more lenient tolerance
                    lenient_tolerance = min(0.10, self.monetary_value_tolerance * 2.0)  # Max 10% or 2x normal tolerance
                    if final_diff_pct <= lenient_tolerance:
                        logger.debug(f"Accepting {final_diff_pct:.4f} difference due to large leverage difference ({leverage1}x vs {leverage2}x)")
                    else:
                        logger.warning(f"Monetary value difference ({final_diff_pct:.4f}) exceeds lenient tolerance ({lenient_tolerance:.4f}) for {symbol1}-{symbol2}")
                        return None
                else:
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
            step = constraints['volume_step']
            
            # Check if max_vol is provided
            max_vol = None
            if 'volume_max' in constraints:
                max_vol = constraints['volume_max']
            
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
