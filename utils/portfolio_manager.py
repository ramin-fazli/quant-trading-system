"""
Portfolio Management Module for Trading Systems
===============================================

This module provides shared portfolio calculation and monitoring functionality
that can be used across different broker implementations (MT5, CTrader, etc.).

Features:
- Portfolio value calculations with P&L
- Position monitoring and analysis
- Exposure calculations
- Performance metrics

Author: Trading System v3.0
Date: July 2025
"""

import logging
from typing import Dict, List, Tuple, Optional, Any
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class PositionInfo:
    """Information about a trading position"""
    pair_str: str
    symbol1: str
    symbol2: str
    direction: str  # 'LONG' or 'SHORT'
    volume1: float
    volume2: float
    entry_price1: float
    entry_price2: float
    entry_time: str
    order1_type: str  # 'buy' or 'sell'
    order2_type: str  # 'buy' or 'sell'
    monetary_value1: float
    monetary_value2: float
    current_pnl: Optional[float] = None
    current_price1: Optional[float] = None
    current_price2: Optional[float] = None


@dataclass
class PortfolioSnapshot:
    """Snapshot of portfolio status"""
    timestamp: str
    total_value: float
    balance: float
    equity: float
    unrealized_pnl: float
    realized_pnl: float
    position_count: int
    total_exposure: float
    free_margin: float
    margin_level: float
    positions: List[Dict[str, Any]]


class PriceProvider(ABC):
    """Abstract interface for price data providers"""
    
    @abstractmethod
    def get_current_prices(self, symbols: List[str]) -> Dict[str, float]:
        """Get current prices for symbols"""
        pass
    
    @abstractmethod
    def get_bid_ask_prices(self, symbols: List[str]) -> Dict[str, Tuple[float, float]]:
        """Get bid/ask prices for symbols"""
        pass


class PortfolioCalculator:
    """
    Shared portfolio calculation functionality
    """
    
    def __init__(self, initial_portfolio_value: float, account_currency: str = "USD"):
        self.initial_portfolio_value = initial_portfolio_value
        self.account_currency = account_currency
    
    def calculate_portfolio_current_value(self, active_positions: Dict[str, Dict], 
                                        price_provider: PriceProvider,
                                        symbol_info_cache: Dict[str, Dict]) -> float:
        """
        Calculate current portfolio value including unrealized P&L
        
        Args:
            active_positions: Dict of pair_str -> position info
            price_provider: Provider for current market prices
            symbol_info_cache: Cache of symbol information
            
        Returns:
            Current portfolio value
        """
        current_value = self.initial_portfolio_value
        
        if not active_positions:
            return current_value
        
        # Get all symbols needed for price updates
        all_symbols = set()
        for position in active_positions.values():
            all_symbols.add(position['symbol1'])
            all_symbols.add(position['symbol2'])
        
        try:
            # Get current bid/ask prices
            bid_ask_prices = price_provider.get_bid_ask_prices(list(all_symbols))
            
            for pair_str, position in active_positions.items():
                try:
                    symbol1, symbol2 = position['symbol1'], position['symbol2']
                    
                    if symbol1 not in bid_ask_prices or symbol2 not in bid_ask_prices:
                        logger.warning(f"Missing price data for {pair_str}")
                        continue
                    
                    bid1, ask1 = bid_ask_prices[symbol1]
                    bid2, ask2 = bid_ask_prices[symbol2]
                    
                    # Calculate P&L based on position direction and order types
                    if 'order1_type' in position and 'order2_type' in position:
                        # Use explicit order types if available (MT5 style)
                        current_market_price1 = bid1 if position['order1_type'] == 'buy' else ask1
                        current_market_price2 = bid2 if position['order2_type'] == 'buy' else ask2
                    else:
                        # Infer order types from position direction (CTrader style)
                        direction = position.get('direction', 'LONG')
                        if direction == 'LONG':
                            # LONG: buy symbol1, sell symbol2
                            current_market_price1 = bid1  # Close buy position at bid
                            current_market_price2 = ask2  # Close sell position at ask
                        else:
                            # SHORT: sell symbol1, buy symbol2
                            current_market_price1 = ask1  # Close sell position at ask
                            current_market_price2 = bid2  # Close buy position at bid
                    
                    # Get contract sizes with validation
                    symbol1_info = symbol_info_cache.get(symbol1, {})
                    symbol2_info = symbol_info_cache.get(symbol2, {})
                    
                    if not symbol1_info:
                        logger.error(f"Missing symbol info for {symbol1} in cache - skipping position {pair_str}")
                        continue
                    if not symbol2_info:
                        logger.error(f"Missing symbol info for {symbol2} in cache - skipping position {pair_str}")
                        continue
                    
                    contract_size1 = symbol1_info.get('lot_size')
                    contract_size2 = symbol2_info.get('lot_size')
                    
                    if contract_size1 is None or contract_size1 <= 0:
                        logger.error(f"Invalid or missing lot_size for {symbol1}: {contract_size1} - skipping position {pair_str}")
                        continue
                    if contract_size2 is None or contract_size2 <= 0:
                        logger.error(f"Invalid or missing lot_size for {symbol2}: {contract_size2} - skipping position {pair_str}")
                        continue
                    
                    # Get entry prices with validation
                    entry_price1 = position.get('entry_exec_price1') or position.get('entry_price1')
                    entry_price2 = position.get('entry_exec_price2') or position.get('entry_price2')
                    
                    if not entry_price1 or entry_price1 <= 0:
                        logger.error(f"Invalid or missing entry price for {symbol1}: {entry_price1} - skipping position {pair_str}")
                        continue
                    if not entry_price2 or entry_price2 <= 0:
                        logger.error(f"Invalid or missing entry price for {symbol2}: {entry_price2} - skipping position {pair_str}")
                        continue
                    
                    # Validate position volumes
                    volume1 = position.get('volume1')
                    volume2 = position.get('volume2')
                    if volume1 is None or volume1 <= 0:
                        logger.error(f"Invalid or missing volume1 for position {pair_str}: {volume1} - skipping")
                        continue
                    if volume2 is None or volume2 <= 0:
                        logger.error(f"Invalid or missing volume2 for position {pair_str}: {volume2} - skipping")
                        continue
                    
                    # Calculate current and entry values using validated volumes
                    current_value1 = volume1 * current_market_price1 * contract_size1
                    current_value2 = volume2 * current_market_price2 * contract_size2
                    
                    entry_value1 = volume1 * entry_price1 * contract_size1
                    entry_value2 = volume2 * entry_price2 * contract_size2
                    
                    # Calculate P&L based on direction
                    if position['direction'] == 'LONG':
                        pnl = (current_value1 - entry_value1) + (entry_value2 - current_value2)
                    else:
                        pnl = (entry_value1 - current_value1) + (current_value2 - entry_value2)
                    
                    current_value += pnl
                    
                except Exception as e:
                    logger.error(f"Error calculating P&L for {pair_str}: {e}")
        
        except Exception as e:
            logger.error(f"Error calculating portfolio value: {e}")
        
        return current_value
    
    def calculate_position_pnl(self, position: Dict[str, Any], 
                             price_provider: PriceProvider,
                             symbol_info_cache: Dict[str, Dict]) -> float:
        """
        Calculate the current P&L for a given position in dollar terms
        
        Args:
            position: Position information dictionary
            price_provider: Provider for current market prices
            symbol_info_cache: Cache of symbol information
            
        Returns:
            Current P&L in account currency
        """
        try:
            symbol1, symbol2 = position['symbol1'], position['symbol2']
            
            # Validate position volumes
            volume1 = position.get('volume1')
            volume2 = position.get('volume2')
            if volume1 is None or volume1 <= 0:
                logger.error(f"Invalid or missing volume1 for position {symbol1}-{symbol2}: {volume1}")
                return 0.0
            if volume2 is None or volume2 <= 0:
                logger.error(f"Invalid or missing volume2 for position {symbol1}-{symbol2}: {volume2}")
                return 0.0
            
            # Get current bid/ask prices
            bid_ask_prices = price_provider.get_bid_ask_prices([symbol1, symbol2])
            if symbol1 not in bid_ask_prices or symbol2 not in bid_ask_prices:
                return 0.0
            
            bid1, ask1 = bid_ask_prices[symbol1]
            bid2, ask2 = bid_ask_prices[symbol2]
            
            # Determine close prices based on order types or infer from direction
            if 'order1_type' in position and 'order2_type' in position:
                # Use explicit order types if available (MT5 style)
                close_price1 = bid1 if position['order1_type'] == 'buy' else ask1
                close_price2 = bid2 if position['order2_type'] == 'buy' else ask2
            else:
                # Infer order types from position direction (CTrader style)
                direction = position.get('direction', 'LONG')
                if direction == 'LONG':
                    # LONG: buy symbol1, sell symbol2
                    close_price1 = bid1  # Close buy position at bid
                    close_price2 = ask2  # Close sell position at ask
                else:
                    # SHORT: sell symbol1, buy symbol2
                    close_price1 = ask1  # Close sell position at ask
                    close_price2 = bid2  # Close buy position at bid
            
            # Get contract sizes with validation
            symbol1_info = symbol_info_cache.get(symbol1, {})
            symbol2_info = symbol_info_cache.get(symbol2, {})
            
            if not symbol1_info:
                logger.error(f"Missing symbol info for {symbol1} in cache")
                return 0.0
            if not symbol2_info:
                logger.error(f"Missing symbol info for {symbol2} in cache")
                return 0.0
            
            contract_size1 = symbol1_info.get('lot_size')
            contract_size2 = symbol2_info.get('lot_size')
            
            if contract_size1 is None or contract_size1 <= 0:
                logger.error(f"Invalid or missing lot_size for {symbol1}: {contract_size1}")
                return 0.0
            if contract_size2 is None or contract_size2 <= 0:
                logger.error(f"Invalid or missing lot_size for {symbol2}: {contract_size2}")
                return 0.0
            
            # Get entry prices with validation
            entry_price1 = position.get('entry_exec_price1') or position.get('entry_price1')
            entry_price2 = position.get('entry_exec_price2') or position.get('entry_price2')
            
            if not entry_price1 or entry_price1 <= 0:
                logger.error(f"Invalid or missing entry price for {symbol1}: {entry_price1}")
                return 0.0
            if not entry_price2 or entry_price2 <= 0:
                logger.error(f"Invalid or missing entry price for {symbol2}: {entry_price2}")
                return 0.0
            
            # Calculate values using validated volumes
            entry_value1 = volume1 * entry_price1 * contract_size1
            entry_value2 = volume2 * entry_price2 * contract_size2
            close_value1 = volume1 * close_price1 * contract_size1
            close_value2 = volume2 * close_price2 * contract_size2
            
            # Calculate P&L based on direction
            direction = position.get('direction', 'LONG')
            if direction == 'LONG':
                pnl_dollar = (close_value1 - entry_value1) + (entry_value2 - close_value2)
            else:
                pnl_dollar = (entry_value1 - close_value1) + (close_value2 - entry_value2)
            # Log validation info for P&L calculation
            # logger.info(f"P&L calculation for {symbol1}-{symbol2}: "
            #        f"Direction={direction}, "
            #        f"Entry: {entry_price1:.5f}/{entry_price2:.5f}, "
            #        f"Close: {close_price1:.5f}/{close_price2:.5f}, "
            #        f"Volumes: {volume1}/{volume2}, "
            #        f"P&L: ${pnl_dollar:.2f}")            
            return pnl_dollar

        except Exception as e:
            logger.error(f"Error calculating position P&L: {e}")
            return 0.0
    
    def calculate_position_net_pnl(self, position: Dict[str, Any], 
                                  price_provider: PriceProvider,
                                  symbol_info_cache: Dict[str, Dict]) -> Tuple[float, float]:
        """
        Calculate the net P&L for a given position accounting for cTrader commission fees
        
        This method calculates both gross P&L and net P&L after deducting commissions
        for both opening and closing the position legs.
        
        Args:
            position: Position information dictionary
            price_provider: Provider for current market prices
            symbol_info_cache: Cache of symbol information including commission details
            
        Returns:
            Tuple[float, float]: (gross_pnl, net_pnl) in account currency
        """
        try:
            # First calculate gross P&L using existing method
            gross_pnl = self.calculate_position_pnl(position, price_provider, symbol_info_cache)
            
            symbol1, symbol2 = position['symbol1'], position['symbol2']
            
            # Validate position volumes
            volume1 = position.get('volume1')
            volume2 = position.get('volume2')
            if volume1 is None or volume1 <= 0 or volume2 is None or volume2 <= 0:
                logger.error(f"Invalid volumes for position {symbol1}-{symbol2}: {volume1}, {volume2}")
                return gross_pnl
            
            # Get symbol information for commission calculation
            symbol1_info = symbol_info_cache.get(symbol1, {})
            symbol2_info = symbol_info_cache.get(symbol2, {})
            
            if not symbol1_info or not symbol2_info:
                logger.warning(f"Missing symbol info for commission calculation: {symbol1}, {symbol2}")
                return gross_pnl
            
            # Calculate total commission for both legs (open + close)
            total_commission = 0.0
            
            # Calculate commission for symbol1
            commission1_open = self._calculate_trade_commission(
                symbol1_info, volume1, position.get('entry_exec_price1') or position.get('entry_price1'))
            commission1_close = self._calculate_trade_commission(
                symbol1_info, volume1, self._get_current_close_price(symbol1, position, price_provider))
            
            # Calculate commission for symbol2
            commission2_open = self._calculate_trade_commission(
                symbol2_info, volume2, position.get('entry_exec_price2') or position.get('entry_price2'))
            commission2_close = self._calculate_trade_commission(
                symbol2_info, volume2, self._get_current_close_price(symbol2, position, price_provider))
            
            total_commission = commission1_open + commission1_close + commission2_open + commission2_close
            
            # Calculate net P&L
            net_pnl = gross_pnl - total_commission
            
            # logger.info(f"P&L breakdown for {symbol1}-{symbol2}: "
            #             f"Gross=${gross_pnl:.2f}, Commission=${total_commission:.2f}, Net=${net_pnl:.2f}")
            # Log validation info for net P&L calculation
            # logger.info(f"Net P&L calculation for {symbol1}-{symbol2}: "
            #             f"Gross=${gross_pnl:.2f}, "
            #             f"Commission breakdown: "
            #             f"Open({commission1_open + commission2_open:.2f}) + "
            #             f"Close({commission1_close + commission2_close:.2f}) = "
            #             f"Total({total_commission:.2f}), "
            #             f"Net=${net_pnl:.2f}")
            return net_pnl

        except Exception as e:
            logger.error(f"Error calculating net position P&L: {e}")
            return 0.0, 0.0
    
    def _calculate_trade_commission(self, symbol_info: Dict[str, Any], volume: float, price: float) -> float:
        """
        Calculate commission for a single trade leg based on cTrader symbol information
        
        Args:
            symbol_info: Symbol information from cTrader including commission details
            volume: Trade volume in lots
            price: Trade price
            
        Returns:
            Commission amount in account currency
        """
        try:
            # Get commission configuration with detailed error handling
            symbol_name = symbol_info.get('symbol_name', 'Unknown')
            
            commission_type = symbol_info.get('commissionType')
            commission_rate = symbol_info.get('preciseTradingCommissionRate', 0)
            min_commission = symbol_info.get('preciseMinCommission', 0)
            lot_size = symbol_info.get('lot_size', 100000)  # Default to 100k for forex
            
            # Detailed logging for commission configuration debugging
            # logger.info(f"Commission calculation for {symbol_name}:")
            # logger.info(f"  Available fields: {list(symbol_info.keys())}")
            # logger.info(f"  Commission type: {commission_type}")
            # logger.info(f"  Commission rate: {commission_rate}")
            # logger.info(f"  Min commission: {min_commission}")
            # logger.info(f"  Lot size: {lot_size}")
            
            # Check for missing commission fields and handle gracefully
            if commission_type is None:
                logger.warning(f"Missing commissionType for {symbol_name} - commission calculation will return 0")
                return 0.0
            
            commission_amount = 0.0
            
            # Calculate commission based on type according to cTrader documentation
            if commission_type == 1:  # USD_PER_MILLION_USD
                # Commission is USD per million USD volume
                # preciseTradingCommissionRate is multiplied by 10^8 for non-percentage types
                notional_value = volume * lot_size * price
                commission_amount = (notional_value / 1_000_000) * (commission_rate / 100_000_000)
                
            elif commission_type == 2:  # USD_PER_LOT
                # Commission is USD per 1 lot
                # preciseTradingCommissionRate is multiplied by 10^8 for non-percentage types
                commission_amount = volume * (commission_rate / 100_000_000)
                
            elif commission_type == 3:  # PERCENTAGE_OF_VALUE
                # Commission is percentage of trading volume
                # For percentage type, preciseTradingCommissionRate is multiplied by 10^5
                notional_value = volume * lot_size * price
                commission_percentage = commission_rate / 100_000  # Convert from 10^5 multiplied value
                commission_amount = notional_value * (commission_percentage / 100)
                
            elif commission_type == 4:  # QUOTE_CCY_PER_LOT
                # Commission is quote currency per 1 lot
                # preciseTradingCommissionRate is multiplied by 10^8 for non-percentage types
                commission_amount = volume * (commission_rate / 100_000_000)
                # Note: This is in quote currency, may need conversion to account currency
                
            # Apply minimum commission if specified
            if min_commission > 0:
                min_commission_amount = min_commission / 100_000_000  # Convert from 10^8 multiplied value
                commission_amount = max(commission_amount, min_commission_amount)
            
            # logger.info(f"Commission calculation result for {symbol_name}: "
            #             f"type={commission_type}, rate={commission_rate}, "
            #             f"volume={volume}, price={price}, commission=${commission_amount:.4f}")
            
            return commission_amount

        except Exception as e:
            symbol_name = symbol_info.get('symbol_name', 'Unknown')
            logger.error(f"Error calculating trade commission for {symbol_name}: {e}")
            return 0.0
    
    def _get_current_close_price(self, symbol: str, position: Dict[str, Any], 
                                price_provider: PriceProvider) -> float:
        """
        Get the current close price for a symbol based on position direction
        
        Args:
            symbol: Symbol name
            position: Position information
            price_provider: Price provider for current prices
            
        Returns:
            Current close price for the symbol
        """
        try:
            bid_ask_prices = price_provider.get_bid_ask_prices([symbol])
            if symbol not in bid_ask_prices:
                return 0.0
            
            bid, ask = bid_ask_prices[symbol]
            
            # Determine close price based on order type or direction
            if 'order1_type' in position and 'order2_type' in position:
                # Use explicit order types if available (MT5 style)
                if symbol == position['symbol1']:
                    return bid if position['order1_type'] == 'buy' else ask
                else:  # symbol2
                    return bid if position['order2_type'] == 'buy' else ask
            else:
                # Infer order types from position direction (CTrader style)
                direction = position.get('direction', 'LONG')
                if symbol == position['symbol1']:
                    return bid if direction == 'LONG' else ask  # LONG: close buy at bid, SHORT: close sell at ask
                else:  # symbol2
                    return ask if direction == 'LONG' else bid  # LONG: close sell at ask, SHORT: close buy at bid
                    
        except Exception as e:
            logger.error(f"Error getting close price for {symbol}: {e}")
            return 0.0
    
    def calculate_total_exposure(self, active_positions: Dict[str, Dict]) -> float:
        """
        Calculate total monetary exposure across all active positions
        
        Args:
            active_positions: Dict of pair_str -> position info
            
        Returns:
            Total exposure amount
        """
        total_exposure = 0.0
        
        try:
            for pair_str, position in active_positions.items():
                # Validate monetary values exist and are valid
                leg1_exposure = position.get('monetary_value1')
                leg2_exposure = position.get('monetary_value2')
                
                if leg1_exposure is None:
                    logger.warning(f"Missing monetary_value1 for position {pair_str} - skipping from exposure calculation")
                    continue
                if leg2_exposure is None:
                    logger.warning(f"Missing monetary_value2 for position {pair_str} - skipping from exposure calculation")
                    continue
                
                if not isinstance(leg1_exposure, (int, float)) or leg1_exposure < 0:
                    logger.warning(f"Invalid monetary_value1 for position {pair_str}: {leg1_exposure} - skipping from exposure calculation")
                    continue
                if not isinstance(leg2_exposure, (int, float)) or leg2_exposure < 0:
                    logger.warning(f"Invalid monetary_value2 for position {pair_str}: {leg2_exposure} - skipping from exposure calculation")
                    continue
                
                position_exposure = leg1_exposure + leg2_exposure
                total_exposure += position_exposure
                
        except Exception as e:
            logger.error(f"Error calculating total exposure: {e}")
            
        return total_exposure


class PositionMonitor:
    """
    Shared position monitoring functionality
    """
    
    def __init__(self):
        self.last_position_check = datetime.now()
    
    def create_position_info(self, pair_str: str, position: Dict[str, Any], 
                           price_provider: PriceProvider = None) -> PositionInfo:
        """
        Create a standardized PositionInfo object from position data
        
        Args:
            pair_str: Pair string identifier
            position: Position dictionary
            price_provider: Optional price provider for current prices
            
        Returns:
            PositionInfo object
        """
        # Validate essential position data
        if not position.get('symbol1'):
            logger.error(f"Missing symbol1 for position {pair_str}")
            raise ValueError(f"Missing symbol1 for position {pair_str}")
        if not position.get('symbol2'):
            logger.error(f"Missing symbol2 for position {pair_str}")
            raise ValueError(f"Missing symbol2 for position {pair_str}")
        if not position.get('direction'):
            logger.error(f"Missing direction for position {pair_str}")
            raise ValueError(f"Missing direction for position {pair_str}")
        
        # Validate volume data
        volume1 = position.get('volume1')
        volume2 = position.get('volume2')
        if volume1 is None or volume1 <= 0:
            logger.error(f"Invalid or missing volume1 for position {pair_str}: {volume1}")
            raise ValueError(f"Invalid volume1 for position {pair_str}")
        if volume2 is None or volume2 <= 0:
            logger.error(f"Invalid or missing volume2 for position {pair_str}: {volume2}")
            raise ValueError(f"Invalid volume2 for position {pair_str}")
        
        # Validate entry prices
        entry_price1 = position.get('entry_exec_price1') or position.get('entry_price1')
        entry_price2 = position.get('entry_exec_price2') or position.get('entry_price2')
        if not entry_price1 or entry_price1 <= 0:
            logger.error(f"Invalid or missing entry price1 for position {pair_str}: {entry_price1}")
            raise ValueError(f"Invalid entry price1 for position {pair_str}")
        if not entry_price2 or entry_price2 <= 0:
            logger.error(f"Invalid or missing entry price2 for position {pair_str}: {entry_price2}")
            raise ValueError(f"Invalid entry price2 for position {pair_str}")
        
        # Validate entry time
        entry_time = position.get('entry_time')
        if not entry_time:
            logger.error(f"Missing entry_time for position {pair_str}")
            raise ValueError(f"Missing entry_time for position {pair_str}")
        
        # Validate monetary values
        monetary_value1 = position.get('monetary_value1')
        monetary_value2 = position.get('monetary_value2')
        if monetary_value1 is None:
            logger.warning(f"Missing monetary_value1 for position {pair_str}")
        if monetary_value2 is None:
            logger.warning(f"Missing monetary_value2 for position {pair_str}")
        
        # Get current prices if provider is available
        current_price1, current_price2 = None, None
        current_pnl = None
        
        if price_provider:
            try:
                current_prices = price_provider.get_current_prices([position['symbol1'], position['symbol2']])
                current_price1 = current_prices.get(position['symbol1'])
                current_price2 = current_prices.get(position['symbol2'])
            except Exception as e:
                logger.debug(f"Could not get current prices for {pair_str}: {e}")
        
        return PositionInfo(
            pair_str=pair_str,
            symbol1=position['symbol1'],
            symbol2=position['symbol2'],
            direction=position['direction'],
            volume1=volume1,
            volume2=volume2,
            entry_price1=entry_price1,
            entry_price2=entry_price2,
            entry_time=entry_time,
            order1_type=position.get('order1_type', 'buy' if position.get('direction') == 'LONG' else 'sell'),
            order2_type=position.get('order2_type', 'sell' if position.get('direction') == 'LONG' else 'buy'),
            monetary_value1=monetary_value1 or 0.0,
            monetary_value2=monetary_value2 or 0.0,
            current_pnl=current_pnl,
            current_price1=current_price1,
            current_price2=current_price2
        )
    
    def calculate_position_duration(self, position: Dict[str, Any]) -> str:
        """
        Calculate how long a position has been open
        
        Args:
            position: Position information dictionary
            
        Returns:
            Formatted duration string
        """
        try:
            entry_time_str = position.get('entry_time', '')
            if not entry_time_str:
                return 'Unknown'
            
            # Parse entry time (assuming ISO format)
            entry_time = datetime.fromisoformat(entry_time_str.replace('Z', '+00:00'))
            current_time = datetime.now(entry_time.tzinfo)
            
            duration = current_time - entry_time
            
            # Format duration
            days = duration.days
            hours, remainder = divmod(duration.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            
            if days > 0:
                return f"{days}d {hours}h {minutes}m"
            elif hours > 0:
                return f"{hours}h {minutes}m"
            else:
                return f"{minutes}m"
                
        except Exception as e:
            logger.debug(f"Error calculating position duration: {e}")
            return 'Unknown'
    
    def generate_positions_summary(self, active_positions: Dict[str, Dict],
                                 price_provider: PriceProvider = None,
                                 portfolio_calculator: PortfolioCalculator = None) -> List[Dict[str, Any]]:
        """
        Generate a summary of all positions
        
        Args:
            active_positions: Dict of pair_str -> position info
            price_provider: Optional price provider for current data
            portfolio_calculator: Optional calculator for P&L
            
        Returns:
            List of position summary dictionaries
        """
        positions_summary = []
        
        for pair_str, position in active_positions.items():
            try:
                # Validate that position contains required data
                if not position or not isinstance(position, dict):
                    logger.error(f"Invalid position data for {pair_str}: {type(position)}")
                    continue
                
                # Validate essential fields exist
                required_fields = ['symbol1', 'symbol2', 'direction', 'volume1', 'volume2', 'entry_time']
                missing_fields = [field for field in required_fields if field not in position or position[field] is None]
                if missing_fields:
                    logger.error(f"Missing required fields for position {pair_str}: {missing_fields}")
                    continue
                
                position_info = self.create_position_info(pair_str, position, price_provider)
                
                # Calculate P&L if possible
                current_pnl = 0.0
                if portfolio_calculator and price_provider:
                    try:
                        # This would need symbol_info_cache passed in for full implementation
                        # current_pnl = portfolio_calculator.calculate_position_pnl(position, price_provider, symbol_info_cache)
                        pass
                    except Exception as e:
                        logger.debug(f"Could not calculate P&L for {pair_str}: {e}")
                
                position_summary = {
                    'pair': pair_str,
                    'symbol1': position_info.symbol1,
                    'symbol2': position_info.symbol2,
                    'direction': position_info.direction,
                    'volume1': position_info.volume1,
                    'volume2': position_info.volume2,
                    'entry_price1': position_info.entry_price1,
                    'entry_price2': position_info.entry_price2,
                    'current_price1': position_info.current_price1,  # Can be None if unavailable
                    'current_price2': position_info.current_price2,  # Can be None if unavailable
                    'entry_time': position_info.entry_time,
                    'duration': self.calculate_position_duration(position),
                    'monetary_value1': position_info.monetary_value1,
                    'monetary_value2': position_info.monetary_value2,
                    'current_pnl': current_pnl,
                    'order1_type': position_info.order1_type,
                    'order2_type': position_info.order2_type
                }
                
                positions_summary.append(position_summary)
                
            except Exception as e:
                logger.error(f"Error processing position {pair_str}: {e}")
        
        return positions_summary


class PortfolioManager:
    """
    Main portfolio management coordinator that combines all portfolio components
    """
    
    def __init__(self, initial_portfolio_value: float, account_currency: str = "USD"):
        self.portfolio_calculator = PortfolioCalculator(initial_portfolio_value, account_currency)
        self.position_monitor = PositionMonitor()
        self.account_currency = account_currency
    
    def get_portfolio_status(self, active_positions: Dict[str, Dict],
                           price_provider: PriceProvider,
                           symbol_info_cache: Dict[str, Dict],
                           account_info: Optional[Dict] = None) -> PortfolioSnapshot:
        """
        Get comprehensive portfolio status
        
        Args:
            active_positions: Dict of pair_str -> position info
            price_provider: Provider for current market prices
            symbol_info_cache: Cache of symbol information
            account_info: Optional account information from broker
            
        Returns:
            PortfolioSnapshot object
        """
        try:
            # Calculate portfolio metrics
            current_value = self.portfolio_calculator.calculate_portfolio_current_value(
                active_positions, price_provider, symbol_info_cache)
            
            total_exposure = self.portfolio_calculator.calculate_total_exposure(active_positions)
            
            # Generate positions summary
            positions_summary = self.position_monitor.generate_positions_summary(
                active_positions, price_provider, self.portfolio_calculator)
            
            # Calculate unrealized P&L
            unrealized_pnl = current_value - self.portfolio_calculator.initial_portfolio_value
            
            # Use account info if available and valid, otherwise use calculated values
            if account_info and isinstance(account_info, dict):
                balance = account_info.get('balance')
                equity = account_info.get('equity')
                free_margin = account_info.get('free_margin')
                margin_level = account_info.get('margin_level')
                realized_pnl = account_info.get('profit')
                
                # Validate account info values
                if balance is None or not isinstance(balance, (int, float)):
                    logger.warning(f"Invalid balance in account_info: {balance}, using calculated portfolio value")
                    balance = current_value
                if equity is None or not isinstance(equity, (int, float)):
                    logger.warning(f"Invalid equity in account_info: {equity}, using calculated portfolio value")
                    equity = current_value
                if free_margin is None or not isinstance(free_margin, (int, float)):
                    logger.warning(f"Invalid free_margin in account_info: {free_margin}, setting to 0")
                    free_margin = 0
                if margin_level is None or not isinstance(margin_level, (int, float)):
                    logger.warning(f"Invalid margin_level in account_info: {margin_level}, setting to 0")
                    margin_level = 0
                if realized_pnl is None or not isinstance(realized_pnl, (int, float)):
                    logger.warning(f"Invalid profit in account_info: {realized_pnl}, setting to 0")
                    realized_pnl = 0
            else:
                if account_info is not None:
                    logger.warning(f"Invalid account_info provided: {type(account_info)}, using calculated values")
                balance = current_value
                equity = current_value
                free_margin = 0
                margin_level = 0
                realized_pnl = 0
            
            return PortfolioSnapshot(
                timestamp=datetime.now().isoformat(),
                total_value=current_value,
                balance=balance,
                equity=equity,
                unrealized_pnl=unrealized_pnl,
                realized_pnl=realized_pnl,
                position_count=len(active_positions),
                total_exposure=total_exposure,
                free_margin=free_margin,
                margin_level=margin_level,
                positions=positions_summary
            )
            
        except Exception as e:
            logger.error(f"Error getting portfolio status: {e}")
            # Return minimal portfolio status on error
            return PortfolioSnapshot(
                timestamp=datetime.now().isoformat(),
                total_value=0,
                balance=0,
                equity=0,
                unrealized_pnl=0,
                realized_pnl=0,
                position_count=len(active_positions) if active_positions else 0,
                total_exposure=0,
                free_margin=0,
                margin_level=0,
                positions=[]
            )
    
    def calculate_portfolio_net_pnl(self, active_positions: Dict[str, Dict],
                                   price_provider: PriceProvider,
                                   symbol_info_cache: Dict[str, Dict]) -> Dict[str, float]:
        """
        Calculate portfolio-wide P&L metrics including commission costs
        
        Args:
            active_positions: Dict of pair_str -> position info
            price_provider: Provider for current market prices
            symbol_info_cache: Cache of symbol information including commission details
            
        Returns:
            Dict with portfolio P&L metrics: {
                'total_gross_pnl': float,
                'total_net_pnl': float,
                'total_commission': float,
                'commission_percentage': float
            }
        """
        try:
            total_gross_pnl = 0.0
            total_net_pnl = 0.0
            position_details = []
            
            for pair_str, position in active_positions.items():
                try:
                    gross_pnl, net_pnl = self.portfolio_calculator.calculate_position_net_pnl(
                        position, price_provider, symbol_info_cache)
                    
                    total_gross_pnl += gross_pnl
                    total_net_pnl += net_pnl
                    
                    position_details.append({
                        'pair': pair_str,
                        'gross_pnl': gross_pnl,
                        'net_pnl': net_pnl,
                        'commission': gross_pnl - net_pnl
                    })
                    
                except Exception as e:
                    logger.error(f"Error calculating net P&L for position {pair_str}: {e}")
            
            total_commission = total_gross_pnl - total_net_pnl
            commission_percentage = (total_commission / abs(total_gross_pnl) * 100) if total_gross_pnl != 0 else 0
            
            return {
                'total_gross_pnl': total_gross_pnl,
                'total_net_pnl': total_net_pnl,
                'total_commission': total_commission,
                'commission_percentage': commission_percentage,
                'position_details': position_details
            }
            
        except Exception as e:
            logger.error(f"Error calculating portfolio net P&L: {e}")
            return {
                'total_gross_pnl': 0.0,
                'total_net_pnl': 0.0,
                'total_commission': 0.0,
                'commission_percentage': 0.0,
                'position_details': []
            }
