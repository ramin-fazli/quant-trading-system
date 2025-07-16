# State Management Module

## Overview

The state management module (`utils/state_manager.py`) provides a reusable, thread-safe solution for persisting and loading trading states across different components of the pairs trading system. This module was refactored from the original MT5 trader implementation to make state management functionality available to other components and scripts.

## Features

### Core Features
- **Thread-safe operations** with automatic locking
- **Automatic serialization/deserialization** of complex objects
- **Backup creation** on save failures
- **Robust error handling** with graceful fallbacks
- **Customizable serializers/deserializers** for specific data types

### Supported Data Types
- **DateTime objects** - Automatically serialized to ISO format
- **Pandas DataFrames and Series** - Including datetime indexes
- **Nested dictionaries and lists**
- **Sets** (converted to lists for JSON compatibility)
- **Custom objects** via extensible serializer system

## Classes

### StateManager

Base class for generic state management.

```python
from utils.state_manager import StateManager

# Initialize with state file path
state_manager = StateManager("/path/to/state.json")

# Save state
state = {'key': 'value', 'timestamp': datetime.now()}
success = state_manager.save_state(state)

# Load state
loaded_state = state_manager.load_state()

# Get state file information
info = state_manager.get_state_info()
```

### TradingStateManager

Specialized state manager for trading systems, extends StateManager with trading-specific functionality.

```python
from utils.state_manager import TradingStateManager

# Initialize with state file path
trading_state_manager = TradingStateManager("/path/to/trading_state.json")

# Save trading state
success = trading_state_manager.save_trading_state(
    active_positions=positions_dict,
    pair_states=pair_states_dict,
    portfolio_data=portfolio_dict
)

# Load trading state
loaded_state = trading_state_manager.load_trading_state()

# Get portfolio summary (without loading full price data)
summary = trading_state_manager.get_portfolio_summary()
```

## Integration with MT5 Trader

The MT5 trader has been refactored to use the new state manager:

### Before (Old Implementation)
```python
# Manual JSON serialization/deserialization
def _save_state(self):
    with self._update_lock:
        # Manual datetime conversion
        positions_to_save = {}
        for pair, pos_data in self.active_positions.items():
            pos_copy = pos_data.copy()
            if 'entry_time' in pos_copy:
                pos_copy['entry_time'] = pos_copy['entry_time'].isoformat()
            positions_to_save[pair] = pos_copy
        
        # Manual pandas serialization
        pair_states_to_save = {}
        for pair, data in self.pair_states.items():
            pair_states_to_save[pair] = {
                'price1': {
                    'index': [t.isoformat() for t in data['price1'].index],
                    'values': data['price1'].values.tolist()
                }
                # ... more manual serialization
            }
        
        # Manual file operations
        with open(self.config.state_file, 'w') as f:
            json.dump(state, f, indent=4)
```

### After (New Implementation)
```python
# Automatic serialization with state manager
def _save_state(self):
    portfolio_data = {
        'portfolio_peak_value': self.portfolio_peak_value,
        'pair_peak_values': self.pair_peak_values,
        'suspended_pairs': list(self.suspended_pairs),
        'portfolio_trading_suspended': self.portfolio_trading_suspended,
    }
    
    success = self.state_manager.save_trading_state(
        active_positions=self.active_positions,
        pair_states=self.pair_states,
        portfolio_data=portfolio_data
    )
```

## Configuration Integration

The state manager integrates seamlessly with the existing configuration system:

```python
from config import TradingConfig
from utils.state_manager import TradingStateManager

# Initialize with config
config = TradingConfig()
state_manager = TradingStateManager(config.state_file)
```

## Extensibility

### Custom Serializers

You can register custom serializers for specific data types:

```python
def custom_serializer(obj):
    if isinstance(obj, MyCustomClass):
        return {'type': 'MyCustomClass', 'data': obj.to_dict()}
    return obj

def custom_deserializer(obj):
    if isinstance(obj, dict) and obj.get('type') == 'MyCustomClass':
        return MyCustomClass.from_dict(obj['data'])
    return obj

state_manager.register_serializer('custom', custom_serializer)
state_manager.register_deserializer('custom', custom_deserializer)
```

## Error Handling

The state manager includes comprehensive error handling:

- **Save failures**: Automatic backup creation
- **Load failures**: Graceful fallback to empty state
- **Serialization errors**: Individual object handling with fallbacks
- **File system errors**: Detailed logging and error reporting

## Thread Safety

All operations are thread-safe using threading locks:

```python
# You can share a lock between state manager and other components
import threading

lock = threading.Lock()
state_manager = TradingStateManager(state_file, lock)
```

## Usage Examples

### Basic Usage
```python
from utils.state_manager import TradingStateManager

# Initialize
state_manager = TradingStateManager("trading_state.json")

# Save state
positions = {'EURUSD-GBPUSD': {...}}
pair_states = {'EURUSD-GBPUSD': {...}}
portfolio = {'portfolio_peak_value': 100000}

success = state_manager.save_trading_state(positions, pair_states, portfolio)

# Load state
if state_manager.state_exists():
    state = state_manager.load_trading_state()
    if state:
        positions = state['active_positions']
        pair_states = state['pair_states']
        portfolio_peak = state.get('portfolio_peak_value')
```

### Integration with Existing Components
```python
class MyTradingComponent:
    def __init__(self, config):
        self.config = config
        self.state_manager = TradingStateManager(config.state_file)
        self.load_state()
    
    def save_state(self):
        success = self.state_manager.save_trading_state(
            active_positions=self.positions,
            pair_states=self.pair_states,
            portfolio_data=self.get_portfolio_data()
        )
        return success
    
    def load_state(self):
        state = self.state_manager.load_trading_state()
        if state:
            self.positions = state['active_positions']
            self.pair_states = state['pair_states']
            # Load other state data...
```

## Migration Guide

### For Existing Components

1. **Import the new module**:
   ```python
   from utils.state_manager import TradingStateManager
   ```

2. **Initialize in your class**:
   ```python
   self.state_manager = TradingStateManager(config.state_file, self._lock)
   ```

3. **Replace save operations**:
   ```python
   # Old: Manual JSON operations
   # New: Simple method call
   self.state_manager.save_trading_state(positions, pair_states, portfolio)
   ```

4. **Replace load operations**:
   ```python
   # Old: Manual JSON parsing
   # New: Structured return
   state = self.state_manager.load_trading_state()
   ```

## Benefits

### Code Reusability
- **Single implementation** used across multiple components
- **Consistent behavior** for all state operations
- **Reduced code duplication**

### Maintainability
- **Centralized bug fixes** benefit all components
- **Single point** for serialization improvements
- **Easier testing** of state management logic

### Robustness
- **Built-in error handling** and recovery
- **Automatic backup** creation
- **Thread-safe** operations by default

### Performance
- **Optimized serialization** for trading data
- **Efficient pandas** handling
- **Lazy loading** options available

## Testing

The module includes comprehensive test coverage:

- Basic state persistence
- DateTime handling
- Pandas DataFrame/Series serialization
- Trading-specific state management
- Configuration integration
- Error handling scenarios

To run tests, use the included test script or integrate with your testing framework.

## Future Enhancements

Potential future improvements:

1. **Compression**: Add optional state file compression
2. **Versioning**: Support for state schema versioning
3. **Remote storage**: Support for cloud-based state storage
4. **Encryption**: Optional state file encryption
5. **Delta saves**: Save only changed state components
6. **State validation**: Schema validation for loaded states

## Conclusion

The state management module provides a robust, reusable foundation for persisting trading states across the pairs trading system. It eliminates code duplication, improves maintainability, and provides a consistent interface for all components that need state persistence.
