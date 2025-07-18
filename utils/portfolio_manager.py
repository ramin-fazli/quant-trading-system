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
                    current_market_price1 = bid1 if position['order1_type'] == 'buy' else ask1
                    current_market_price2 = bid2 if position['order2_type'] == 'buy' else ask2
                    
                    # Get contract sizes
                    contract_size1 = symbol_info_cache[symbol1]['trade_contract_size']
                    contract_size2 = symbol_info_cache[symbol2]['trade_contract_size']
                    
                    # Calculate current and entry values
                    current_value1 = position['volume1'] * current_market_price1 * contract_size1
                    current_value2 = position['volume2'] * current_market_price2 * contract_size2
                    
                    entry_value1 = position['volume1'] * position['entry_exec_price1'] * contract_size1
                    entry_value2 = position['volume2'] * position['entry_exec_price2'] * contract_size2
                    
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
            
            # Get current bid/ask prices
            bid_ask_prices = price_provider.get_bid_ask_prices([symbol1, symbol2])
            if symbol1 not in bid_ask_prices or symbol2 not in bid_ask_prices:
                return 0.0
            
            bid1, ask1 = bid_ask_prices[symbol1]
            bid2, ask2 = bid_ask_prices[symbol2]
            
            # Determine close prices based on order types
            close_price1 = bid1 if position['order1_type'] == 'buy' else ask1
            close_price2 = bid2 if position['order2_type'] == 'buy' else ask2
            
            # Calculate values
            contract_size1 = symbol_info_cache[symbol1]['trade_contract_size']
            contract_size2 = symbol_info_cache[symbol2]['trade_contract_size']
            
            entry_value1 = position['volume1'] * position['entry_exec_price1'] * contract_size1
            entry_value2 = position['volume2'] * position['entry_exec_price2'] * contract_size2
            close_value1 = position['volume1'] * close_price1 * contract_size1
            close_value2 = position['volume2'] * close_price2 * contract_size2
            
            if position['direction'] == 'LONG':
                pnl_dollar = (close_value1 - entry_value1) + (entry_value2 - close_value2)
            else:
                pnl_dollar = (entry_value1 - close_value1) + (close_value2 - entry_value2)
            
            return pnl_dollar
            
        except Exception as e:
            logger.error(f"Error calculating position P&L: {e}")
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
                # Add both legs of the position to total exposure
                leg1_exposure = position.get('monetary_value1', 0)
                leg2_exposure = position.get('monetary_value2', 0)
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
            volume1=position['volume1'],
            volume2=position['volume2'],
            entry_price1=position['entry_exec_price1'],
            entry_price2=position['entry_exec_price2'],
            entry_time=position['entry_time'],
            order1_type=position['order1_type'],
            order2_type=position['order2_type'],
            monetary_value1=position.get('monetary_value1', 0),
            monetary_value2=position.get('monetary_value2', 0),
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
                    'current_price1': position_info.current_price1 or 0,
                    'current_price2': position_info.current_price2 or 0,
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
            
            # Use account info if available, otherwise use calculated values
            if account_info:
                balance = account_info.get('balance', current_value)
                equity = account_info.get('equity', current_value)
                free_margin = account_info.get('free_margin', 0)
                margin_level = account_info.get('margin_level', 0)
                realized_pnl = account_info.get('profit', 0)
            else:
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
