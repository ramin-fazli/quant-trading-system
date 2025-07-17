# Strategy-Agnostic CTrader Broker Implementation

## Overview

The CTrader broker has been enhanced to be strategy-agnostic, allowing it to work with any trading strategy that implements the `BaseStrategy` interface. This design promotes clean separation of concerns and makes it easy to swap strategies without modifying broker code.

## Key Features

### 1. Strategy Interface Design
- **BaseStrategy**: Abstract base class defining the core interface
- **PairsStrategyInterface**: Specialized interface for pairs trading strategies
- **SingleSymbolStrategyInterface**: Interface for single-symbol strategies

### 2. Strategy-Agnostic Broker
- Works with any strategy implementing `BaseStrategy`
- Delegates all trading decisions to the strategy
- Handles broker-specific functionality (API communication, order execution)
- Maintains clean separation between strategy logic and execution logic

### 3. Backwards Compatibility
- Existing code continues to work unchanged
- Default strategy is used if none specified
- Gradual migration path for existing strategies

## Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Strategy      │    │     Broker      │    │   Data Manager  │
│  (Any Type)     │◄──►│  (CTrader)      │◄──►│    (CTrader)    │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│ • Indicators    │    │ • Order Exec    │    │ • Market Data   │
│ • Signals       │    │ • Risk Mgmt     │    │ • Real-time     │
│ • Validation    │    │ • Position Mgmt │    │ • Historical    │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

## Usage Examples

### 1. Using with Default Strategy (Backwards Compatible)
```python
from brokers.ctrader import CTraderRealTimeTrader
from config import TradingConfig

# Old way - still works
trader = CTraderRealTimeTrader(config, data_manager)
```

### 2. Using with Custom Strategy
```python
from brokers.ctrader import CTraderRealTimeTrader
from strategies.my_strategy import MyCustomStrategy
from config import TradingConfig

# Create custom strategy
strategy = MyCustomStrategy(config, data_manager)

# Pass strategy to broker
trader = CTraderRealTimeTrader(config, data_manager, strategy)
```

### 3. Command Line Usage
```bash
# Default pairs strategy
python enhanced_main_v3.py --broker ctrader --strategy pairs

# Future: Custom strategies (when implemented)
python enhanced_main_v3.py --broker ctrader --strategy momentum
```

## Strategy Interface Requirements

### Base Strategy Methods
All strategies must implement:

```python
class MyStrategy(BaseStrategy):
    def calculate_indicators(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        """Calculate technical indicators"""
        pass
    
    def generate_signals(self, indicators: Dict[str, Any], **kwargs) -> pd.DataFrame:
        """Generate trading signals"""
        pass
    
    def get_required_symbols(self) -> List[str]:
        """Return required symbols"""
        pass
    
    def get_tradeable_instruments(self) -> List[Union[str, Tuple[str, ...]]]:
        """Return tradeable instruments"""
        pass
```

### Pairs Strategy Interface
For pairs strategies, inherit from `PairsStrategyInterface`:

```python
class MyPairsStrategy(PairsStrategyInterface):
    def calculate_indicators_vectorized(self, price1: pd.Series, price2: pd.Series) -> Dict:
        """Calculate pairs-specific indicators"""
        pass
    
    def generate_signals_vectorized(self, indicators: Dict, symbol1: str, symbol2: str) -> pd.DataFrame:
        """Generate pairs-specific signals"""
        pass
```

## Signal Format

The broker expects signals in a specific DataFrame format:

```python
signals = pd.DataFrame({
    'long_entry': boolean_series,    # True when long entry signal
    'short_entry': boolean_series,   # True when short entry signal  
    'long_exit': boolean_series,     # True when long exit signal
    'short_exit': boolean_series,    # True when short exit signal
    'suitable': boolean_series       # True when conditions suitable for trading
})
```

## Market Data Format

### Pairs Strategy Market Data
```python
market_data = {
    'price1': pd.Series([...]),  # First symbol prices
    'price2': pd.Series([...])   # Second symbol prices
}
```

### Single Symbol Market Data
```python
market_data = {
    'price': pd.Series([...])    # Symbol prices
}
```

## Risk Management

The broker handles risk management at the portfolio level:
- Position count limits
- Drawdown limits
- Monetary exposure limits
- Symbol availability checks

Strategies focus on signal generation and market analysis.

## Benefits

### 1. Modularity
- Easy to test strategies in isolation
- Clear separation of concerns
- Reusable broker code

### 2. Flexibility
- Support multiple strategy types
- Easy strategy switching
- Custom strategy development

### 3. Maintainability
- Broker code independent of strategy logic
- Easier debugging and testing
- Clear interfaces

### 4. Extensibility
- Add new strategy types easily
- Extend existing strategies
- Plugin-like architecture

## Migration Guide

### For Existing Strategies
1. Make your strategy inherit from appropriate base class
2. Implement required methods
3. Update method signatures if needed
4. Test with strategy-agnostic broker

### For New Strategies
1. Choose appropriate base class (`BaseStrategy`, `PairsStrategyInterface`, etc.)
2. Implement required methods
3. Follow signal format requirements
4. Test with provided broker

## Future Enhancements

1. **More Strategy Types**: Support for momentum, mean reversion, arbitrage strategies
2. **Strategy Composition**: Combine multiple strategies
3. **Dynamic Strategy Selection**: Switch strategies based on market conditions
4. **Strategy Performance Tracking**: Per-strategy metrics and analytics
5. **Strategy Configuration**: Strategy-specific parameter management

## Files

- `strategies/base_strategy.py` - Base strategy interfaces
- `brokers/ctrader.py` - Updated strategy-agnostic broker
- `brokers/ctrader_strategy_agnostic.py` - Full rewrite example
- `strategies/pairs_trading.py` - Updated pairs strategy with interface
- `scripts/pair_trading/enhanced_main_v3.py` - Updated main script

This implementation provides a solid foundation for strategy-agnostic trading while maintaining backwards compatibility and enabling future extensibility.
