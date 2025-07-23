"""
Base Strategy Interface
======================

Abstract base class that defines the interface for all trading strategies.
This ensures strategy-agnostic broker implementation and easy strategy swapping.

Author: Trading System v3.0
Date: July 2025
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Tuple, Optional, Union
import pandas as pd
from config import TradingConfig


class BaseStrategy(ABC):
    """
    Abstract base class for all trading strategies.
    
    This interface ensures that all strategies implement the required methods
    for broker-agnostic operation.
    """
    
    def __init__(self, config: TradingConfig, data_manager=None):
        """
        Initialize the strategy.
        
        Args:
            config: Trading configuration
            data_manager: Data manager instance (optional)
        """
        self.config = config
        self.data_manager = data_manager
    
    @abstractmethod
    def calculate_indicators(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate technical indicators for the strategy.
        
        Args:
            market_data: Dictionary containing market data for analysis
                        Format depends on strategy type (e.g., for pairs: {'symbol1': pd.Series, 'symbol2': pd.Series})
        
        Returns:
            Dictionary containing calculated indicators
        """
        pass
    
    @abstractmethod
    def generate_signals(self, indicators: Dict[str, Any], **kwargs) -> pd.DataFrame:
        """
        Generate trading signals based on indicators.
        
        Args:
            indicators: Dictionary of calculated indicators
            **kwargs: Additional strategy-specific parameters
        
        Returns:
            DataFrame with trading signals containing at minimum:
            - 'long_entry': Boolean series for long entry signals
            - 'short_entry': Boolean series for short entry signals  
            - 'long_exit': Boolean series for long exit signals
            - 'short_exit': Boolean series for short exit signals
            - 'suitable': Boolean series indicating if conditions are suitable for trading
        """
        pass
    
    @abstractmethod
    def get_required_symbols(self) -> List[str]:
        """
        Get the list of symbols required by this strategy.
        
        Returns:
            List of symbol names required for the strategy
        """
        pass
    
    @abstractmethod
    def get_tradeable_instruments(self) -> List[Union[str, Tuple[str, ...]]]:
        """
        Get the list of tradeable instruments (symbols or symbol combinations).
        
        Returns:
            List of tradeable instruments. For single-symbol strategies, returns list of strings.
            For pairs/multi-symbol strategies, returns list of tuples.
        """
        pass
    
    def get_minimum_data_points(self) -> int:
        """
        Get the minimum number of data points required for strategy calculation.
        
        Returns:
            Minimum number of data points needed
        """
        if not hasattr(self.config, 'min_data_points'):
            import logging
            logger = logging.getLogger(__name__)
            logger.error("Missing required configuration field: min_data_points")
            return 0  # Return 0 to indicate validation failure
            
        if not isinstance(self.config.min_data_points, int) or self.config.min_data_points <= 0:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Invalid min_data_points: {self.config.min_data_points}. Must be a positive integer.")
            return 0  # Return 0 to indicate validation failure
            
        return self.config.min_data_points
    
    def validate_market_data(self, market_data: Dict[str, Any]) -> bool:
        """
        Validate that market data is sufficient for strategy calculations.
        
        Args:
            market_data: Market data dictionary
            
        Returns:
            True if data is valid, False otherwise
        """
        min_points = self.get_minimum_data_points()
        
        for key, data in market_data.items():
            if isinstance(data, pd.Series) and len(data) < min_points:
                return False
            elif hasattr(data, '__len__') and len(data) < min_points:
                return False
        
        return True
    
    def get_strategy_info(self) -> Dict[str, Any]:
        """
        Get information about the strategy.
        
        Returns:
            Dictionary containing strategy metadata
        """
        # Validate strategy_type exists
        if not hasattr(self, 'strategy_type'):
            import logging
            logger = logging.getLogger(__name__)
            logger.error("Strategy missing required strategy_type attribute")
            strategy_type = None  # Indicate missing type
        else:
            strategy_type = self.strategy_type
            
        return {
            'name': self.__class__.__name__,
            'type': strategy_type,
            'min_data_points': self.get_minimum_data_points(),
            'required_symbols': self.get_required_symbols(),
            'tradeable_instruments': self.get_tradeable_instruments()
        }


class PairsStrategyInterface(BaseStrategy):
    """
    Interface specifically for pairs trading strategies.
    
    Extends BaseStrategy with pairs-specific methods and standardizes
    the data format for pairs strategies.
    """
    
    def __init__(self, config: TradingConfig, data_manager=None):
        super().__init__(config, data_manager)
        self.strategy_type = 'pairs'
    
    def calculate_indicators(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate indicators for pairs strategy.
        
        Args:
            market_data: Dictionary with 'price1' and 'price2' keys containing pd.Series
        
        Returns:
            Dictionary containing calculated indicators
        """
        if 'price1' not in market_data or 'price2' not in market_data:
            raise ValueError("Pairs strategy requires 'price1' and 'price2' in market_data")
        
        return self.calculate_indicators_vectorized(market_data['price1'], market_data['price2'])
    
    @abstractmethod
    def calculate_indicators_vectorized(self, price1: pd.Series, price2: pd.Series) -> Dict[str, Any]:
        """
        Calculate indicators for two price series (pairs specific).
        
        Args:
            price1: First symbol price series
            price2: Second symbol price series
            
        Returns:
            Dictionary containing calculated indicators
        """
        pass
    
    def generate_signals(self, indicators: Dict[str, Any], **kwargs) -> pd.DataFrame:
        """
        Generate signals for pairs strategy.
        
        Args:
            indicators: Dictionary of calculated indicators
            **kwargs: May contain 'symbol1' and 'symbol2' for session filtering
            
        Returns:
            DataFrame with trading signals
        """
        symbol1 = kwargs.get('symbol1')
        symbol2 = kwargs.get('symbol2')
        return self.generate_signals_vectorized(indicators, symbol1, symbol2)
    
    @abstractmethod
    def generate_signals_vectorized(self, indicators: Dict[str, Any], 
                                   symbol1: str = None, symbol2: str = None) -> pd.DataFrame:
        """
        Generate signals for pairs strategy (pairs specific).
        
        Args:
            indicators: Dictionary of calculated indicators
            symbol1: First symbol name (optional, for session filtering)
            symbol2: Second symbol name (optional, for session filtering)
            
        Returns:
            DataFrame with trading signals
        """
        pass
    
    def get_pairs_from_config(self) -> List[Tuple[str, str]]:
        """
        Extract pairs from configuration.
        
        Returns:
            List of (symbol1, symbol2) tuples
        """
        if not hasattr(self.config, 'pairs'):
            import logging
            logger = logging.getLogger(__name__)
            logger.error("Missing required configuration field: pairs")
            return []  # Return empty list to indicate validation failure
            
        if not isinstance(self.config.pairs, (list, tuple)):
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Invalid pairs configuration: {type(self.config.pairs)}. Must be a list or tuple.")
            return []  # Return empty list to indicate validation failure
        
        pairs = []
        for pair_str in self.config.pairs:
            try:
                if not isinstance(pair_str, str):
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.warning(f"Invalid pair format: {pair_str}. Expected string, got {type(pair_str)}")
                    continue
                    
                if '-' not in pair_str:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.warning(f"Invalid pair format: {pair_str}. Expected format 'SYMBOL1-SYMBOL2'")
                    continue
                    
                symbol1, symbol2 = pair_str.split('-', 1)  # Split only on first dash
                symbol1, symbol2 = symbol1.strip(), symbol2.strip()
                
                if not symbol1 or not symbol2:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.warning(f"Invalid pair format: {pair_str}. Empty symbols after splitting")
                    continue
                    
                pairs.append((symbol1, symbol2))
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Error parsing pair {pair_str}: {e}")
                continue
                
        return pairs
    
    def get_required_symbols(self) -> List[str]:
        """Get all unique symbols from configured pairs."""
        symbols = set()
        for symbol1, symbol2 in self.get_pairs_from_config():
            symbols.add(symbol1)
            symbols.add(symbol2)
        return list(symbols)
    
    def get_tradeable_instruments(self) -> List[Tuple[str, str]]:
        """Get pairs as tradeable instruments."""
        return self.get_pairs_from_config()


class SingleSymbolStrategyInterface(BaseStrategy):
    """
    Interface for single-symbol trading strategies.
    
    Extends BaseStrategy for strategies that trade individual symbols.
    """
    
    def __init__(self, config: TradingConfig, data_manager=None):
        super().__init__(config, data_manager)
        self.strategy_type = 'single_symbol'
    
    def calculate_indicators(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Calculate indicators for single symbol strategy.
        
        Args:
            market_data: Dictionary with 'price' key containing pd.Series
        
        Returns:
            Dictionary containing calculated indicators
        """
        if 'price' not in market_data:
            raise ValueError("Single symbol strategy requires 'price' in market_data")
        
        return self.calculate_indicators_for_symbol(market_data['price'])
    
    @abstractmethod
    def calculate_indicators_for_symbol(self, price: pd.Series) -> Dict[str, Any]:
        """
        Calculate indicators for a single price series.
        
        Args:
            price: Price series for the symbol
            
        Returns:
            Dictionary containing calculated indicators
        """
        pass
    
    def generate_signals(self, indicators: Dict[str, Any], **kwargs) -> pd.DataFrame:
        """
        Generate signals for single symbol strategy.
        
        Args:
            indicators: Dictionary of calculated indicators
            **kwargs: May contain 'symbol' for session filtering
            
        Returns:
            DataFrame with trading signals
        """
        symbol = kwargs.get('symbol')
        return self.generate_signals_for_symbol(indicators, symbol)
    
    @abstractmethod
    def generate_signals_for_symbol(self, indicators: Dict[str, Any], 
                                   symbol: str = None) -> pd.DataFrame:
        """
        Generate signals for single symbol strategy.
        
        Args:
            indicators: Dictionary of calculated indicators
            symbol: Symbol name (optional, for session filtering)
            
        Returns:
            DataFrame with trading signals
        """
        pass
    
    def get_symbols_from_config(self) -> List[str]:
        """
        Extract symbols from configuration.
        
        Returns:
            List of symbol names
        """
        if not hasattr(self.config, 'symbols'):
            import logging
            logger = logging.getLogger(__name__)
            logger.error("Missing required configuration field: symbols")
            return []  # Return empty list to indicate validation failure
            
        if not isinstance(self.config.symbols, (list, tuple)):
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Invalid symbols configuration: {type(self.config.symbols)}. Must be a list or tuple.")
            return []  # Return empty list to indicate validation failure
        
        symbols = []
        for symbol in self.config.symbols:
            if not isinstance(symbol, str):
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Invalid symbol format: {symbol}. Expected string, got {type(symbol)}")
                continue
                
            if not symbol.strip():
                import logging
                logger = logging.getLogger(__name__)
                logger.warning("Empty symbol found in configuration")
                continue
                
            symbols.append(symbol.strip())
            
        return symbols
    
    def get_required_symbols(self) -> List[str]:
        """Get symbols from configuration."""
        return self.get_symbols_from_config()
    
    def get_tradeable_instruments(self) -> List[str]:
        """Get symbols as tradeable instruments."""
        return self.get_symbols_from_config()
