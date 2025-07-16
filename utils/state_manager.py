"""
State Management Module for Pairs Trading System

This module provides a reusable state management system for saving and loading
trading states across different components of the trading system.
"""

import os
import json
import logging
import datetime
import pandas as pd
from typing import Dict, Any, Optional, Callable, Union
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)


class StateManager:
    """
    Generic state management class for saving and loading trading states.
    
    This class provides thread-safe state persistence with automatic backup,
    validation, and customizable serialization/deserialization handlers.
    """
    
    def __init__(self, state_file_path: str, lock: Optional[Lock] = None):
        """
        Initialize the StateManager.
        
        Args:
            state_file_path: Path to the state file
            lock: Optional threading lock for thread safety. If None, creates a new lock.
        """
        self.state_file_path = state_file_path
        self._lock = lock or Lock()
        self._serializers: Dict[str, Callable] = {}
        self._deserializers: Dict[str, Callable] = {}
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(state_file_path), exist_ok=True)
        
        # Register default serializers/deserializers
        self._register_default_handlers()
    
    def _register_default_handlers(self):
        """Register default serialization/deserialization handlers."""
        
        # DateTime serialization
        def serialize_datetime(obj):
            if isinstance(obj, datetime.datetime):
                return obj.isoformat()
            return obj
        
        def deserialize_datetime(obj):
            if isinstance(obj, str):
                try:
                    return datetime.datetime.fromisoformat(obj)
                except ValueError:
                    return obj
            return obj
        
        # Pandas DataFrame serialization
        def serialize_dataframe(obj):
            if isinstance(obj, pd.DataFrame):
                return {
                    'type': 'dataframe',
                    'index': [t.isoformat() if isinstance(t, datetime.datetime) else str(t) for t in obj.index],
                    'values': obj.values.tolist(),
                    'columns': obj.columns.tolist()
                }
            elif isinstance(obj, pd.Series):
                return {
                    'type': 'series',
                    'index': [t.isoformat() if isinstance(t, datetime.datetime) else str(t) for t in obj.index],
                    'values': obj.values.tolist(),
                    'name': obj.name
                }
            return obj
        
        def deserialize_dataframe(obj):
            if isinstance(obj, dict):
                if obj.get('type') == 'dataframe' and 'index' in obj and 'values' in obj:
                    try:
                        # Try to parse index as datetime
                        index = [datetime.datetime.fromisoformat(t) if isinstance(t, str) else t for t in obj['index']]
                    except ValueError:
                        # Fallback to string index
                        index = obj['index']
                    
                    return pd.DataFrame(obj['values'], index=index, columns=obj['columns'])
                    
                elif obj.get('type') == 'series' and 'index' in obj and 'values' in obj:
                    try:
                        # Try to parse index as datetime
                        index = [datetime.datetime.fromisoformat(t) if isinstance(t, str) else t for t in obj['index']]
                    except ValueError:
                        # Fallback to string index
                        index = obj['index']
                    
                    return pd.Series(obj['values'], index=index, name=obj.get('name'))
            return obj
        
        self.register_serializer('datetime', serialize_datetime)
        self.register_deserializer('datetime', deserialize_datetime)
        self.register_serializer('dataframe', serialize_dataframe)
        self.register_deserializer('dataframe', deserialize_dataframe)
    
    def register_serializer(self, name: str, func: Callable):
        """Register a custom serializer function."""
        self._serializers[name] = func
    
    def register_deserializer(self, name: str, func: Callable):
        """Register a custom deserializer function."""
        self._deserializers[name] = func
    
    def _serialize_object(self, obj: Any) -> Any:
        """Apply all registered serializers to an object recursively."""
        # Apply serializers first (for pandas objects, datetime, etc.)
        for serializer in self._serializers.values():
            serialized = serializer(obj)
            if serialized != obj:  # If serializer changed the object
                obj = serialized
                break
        
        # Then handle collections recursively
        if isinstance(obj, dict):
            return {key: self._serialize_object(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self._serialize_object(item) for item in obj]
        elif isinstance(obj, set):
            return list(obj)  # Convert sets to lists
        else:
            return obj
    
    def _deserialize_object(self, obj: Any) -> Any:
        """Apply all registered deserializers to an object recursively."""
        # Handle collections recursively first
        if isinstance(obj, dict):
            # Apply deserializers to the dict itself first
            for deserializer in self._deserializers.values():
                try:
                    deserialized = deserializer(obj)
                    if deserialized is not obj and not isinstance(deserialized, dict):  # If deserializer changed the object to non-dict
                        return deserialized
                except:
                    continue
            # If no deserializer handled it, recurse into the dict
            return {key: self._deserialize_object(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self._deserialize_object(item) for item in obj]
        else:
            # Apply deserializers to primitive types
            for deserializer in self._deserializers.values():
                try:
                    obj = deserializer(obj)
                except:
                    continue
            return obj
    
    def save_state(self, state: Dict[str, Any], add_timestamp: bool = True) -> bool:
        """
        Save state to file with automatic serialization and backup.
        
        Args:
            state: State dictionary to save
            add_timestamp: Whether to add a 'last_save_time' timestamp
            
        Returns:
            bool: True if successful, False otherwise
        """
        with self._lock:
            try:
                logger.debug(f"Saving state to {self.state_file_path}...")
                
                # Make a deep copy to avoid modifying original
                state_copy = dict(state)
                
                # Add timestamp if requested
                if add_timestamp:
                    state_copy['last_save_time'] = datetime.datetime.now().isoformat()
                
                # Serialize the state
                serialized_state = self._serialize_object(state_copy)
                
                # Save with proper encoding and formatting
                with open(self.state_file_path, 'w', encoding='utf-8') as f:
                    json.dump(serialized_state, f, indent=4, ensure_ascii=False)
                
                logger.debug("State saved successfully")
                
                # Verify the save was successful by trying to read it back
                with open(self.state_file_path, 'r', encoding='utf-8') as f:
                    _ = json.load(f)
                
                return True
                
            except Exception as e:
                logger.error(f"Failed to save state: {str(e)}")
                # If saving fails, try to create a backup of the current state file
                self._create_backup()
                return False
    
    def load_state(self) -> Optional[Dict[str, Any]]:
        """
        Load state from file with automatic deserialization.
        
        Returns:
            Dict containing the loaded state, or None if loading failed
        """
        if not os.path.exists(self.state_file_path):
            logger.info("No state file found.")
            return None
        
        try:
            logger.info(f"Loading state from {self.state_file_path}...")
            
            with open(self.state_file_path, 'r', encoding='utf-8') as f:
                raw_state = json.load(f)
            
            # Deserialize the state
            state = self._deserialize_object(raw_state)
            
            logger.info("State loaded successfully")
            return state
            
        except Exception as e:
            logger.error(f"Failed to load state: {e}")
            return None
    
    def _create_backup(self):
        """Create a backup of the current state file."""
        try:
            if os.path.exists(self.state_file_path):
                backup_file = f"{self.state_file_path}.backup"
                os.replace(self.state_file_path, backup_file)
                logger.info(f"Created backup of state file: {backup_file}")
        except Exception as backup_error:
            logger.error(f"Failed to create backup: {str(backup_error)}")
    
    def state_exists(self) -> bool:
        """Check if state file exists."""
        return os.path.exists(self.state_file_path)
    
    def delete_state(self) -> bool:
        """Delete the state file."""
        try:
            if os.path.exists(self.state_file_path):
                os.remove(self.state_file_path)
                logger.info(f"Deleted state file: {self.state_file_path}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete state file: {e}")
            return False
    
    def get_state_info(self) -> Dict[str, Any]:
        """Get information about the state file."""
        if not os.path.exists(self.state_file_path):
            return {'exists': False}
        
        try:
            stat = os.stat(self.state_file_path)
            return {
                'exists': True,
                'size': stat.st_size,
                'modified_time': datetime.datetime.fromtimestamp(stat.st_mtime),
                'created_time': datetime.datetime.fromtimestamp(stat.st_ctime),
            }
        except Exception as e:
            logger.error(f"Failed to get state info: {e}")
            return {'exists': True, 'error': str(e)}


class TradingStateManager(StateManager):
    """
    Specialized state manager for trading systems.
    
    This class extends StateManager with trading-specific functionality
    for handling positions, pair states, and portfolio information.
    """
    
    def __init__(self, state_file_path: str, lock: Optional[Lock] = None):
        super().__init__(state_file_path, lock)
        self._register_trading_handlers()
    
    def _register_trading_handlers(self):
        """Register trading-specific serialization handlers."""
        
        def serialize_position_data(data):
            """Serialize position data with special handling for entry_time."""
            if isinstance(data, dict) and 'entry_time' in data:
                data_copy = data.copy()
                if isinstance(data_copy['entry_time'], datetime.datetime):
                    data_copy['entry_time'] = data_copy['entry_time'].isoformat()
                return data_copy
            return data
        
        def deserialize_position_data(data):
            """Deserialize position data with special handling for entry_time."""
            if isinstance(data, dict) and 'entry_time' in data:
                if isinstance(data['entry_time'], str):
                    try:
                        data['entry_time'] = datetime.datetime.fromisoformat(data['entry_time'])
                    except ValueError:
                        pass
            return data
        
        self.register_serializer('position', serialize_position_data)
        self.register_deserializer('position', deserialize_position_data)
    
    def save_trading_state(self, 
                          active_positions: Dict[str, Any],
                          pair_states: Dict[str, Any],
                          portfolio_data: Optional[Dict[str, Any]] = None) -> bool:
        """
        Save trading state with positions and pair states.
        
        Args:
            active_positions: Dictionary of active trading positions
            pair_states: Dictionary of pair states with price data
            portfolio_data: Optional portfolio-level data (peak values, suspensions, etc.)
            
        Returns:
            bool: True if successful, False otherwise
        """
        
        # Prepare positions for serialization
        positions_to_save = {}
        for pair, pos_data in active_positions.items():
            positions_to_save[pair] = self._serialize_object(pos_data)
        
        # Prepare pair states for serialization
        pair_states_to_save = {}
        for pair, data in pair_states.items():
            pair_state = {
                'symbol1': data.get('symbol1'),
                'symbol2': data.get('symbol2'),
                'position': data.get('position'),
                'cooldown': data.get('cooldown', 0),
                'last_update': data.get('last_update'),
                'last_candle_time': data.get('last_candle_time')
            }
            
            # Handle price data if present
            if 'price1' in data and hasattr(data['price1'], 'index'):
                pair_state['price1'] = self._serializers['dataframe'](data['price1'])
            if 'price2' in data and hasattr(data['price2'], 'index'):
                pair_state['price2'] = self._serializers['dataframe'](data['price2'])
            
            pair_states_to_save[pair] = pair_state
        
        # Combine all state data
        state = {
            'active_positions': positions_to_save,
            'pair_states': pair_states_to_save,
        }
        
        # Add portfolio data if provided
        if portfolio_data:
            state.update(portfolio_data)
        
        return self.save_state(state)
    
    def load_trading_state(self) -> Optional[Dict[str, Any]]:
        """
        Load trading state and return structured data.
        
        Returns:
            Dictionary with 'active_positions', 'pair_states', and optional portfolio data
        """
        state = self.load_state()
        if state is None:
            return None
        
        # Deserialize positions
        active_positions = {}
        if 'active_positions' in state:
            for pair, pos_data in state['active_positions'].items():
                active_positions[pair] = self._deserialize_object(pos_data)
        
        # Deserialize pair states
        pair_states = {}
        if 'pair_states' in state:
            for pair, data in state['pair_states'].items():
                pair_state = dict(data)
                
                # Handle price data reconstruction
                if 'price1' in data:
                    pair_state['price1'] = self._deserializers['dataframe'](data['price1'])
                if 'price2' in data:
                    pair_state['price2'] = self._deserializers['dataframe'](data['price2'])
                
                pair_states[pair] = pair_state
        
        return {
            'active_positions': active_positions,
            'pair_states': pair_states,
            **{k: v for k, v in state.items() if k not in ['active_positions', 'pair_states']}
        }
    
    def get_portfolio_summary(self) -> Dict[str, Any]:
        """
        Get a summary of the portfolio state without loading full price data.
        
        Returns:
            Dictionary with portfolio summary information
        """
        if not self.state_exists():
            return {'exists': False}
        
        try:
            with open(self.state_file_path, 'r', encoding='utf-8') as f:
                raw_state = json.load(f)
            
            summary = {
                'exists': True,
                'active_positions_count': len(raw_state.get('active_positions', {})),
                'pair_states_count': len(raw_state.get('pair_states', {})),
                'last_save_time': raw_state.get('last_save_time'),
                'portfolio_peak_value': raw_state.get('portfolio_peak_value'),
                'portfolio_trading_suspended': raw_state.get('portfolio_trading_suspended', False),
                'suspended_pairs': raw_state.get('suspended_pairs', []),
            }
            
            # Add position details without price data
            if 'active_positions' in raw_state:
                positions_summary = {}
                for pair, pos_data in raw_state['active_positions'].items():
                    positions_summary[pair] = {
                        'direction': pos_data.get('direction'),
                        'entry_time': pos_data.get('entry_time'),
                        'symbol1': pos_data.get('symbol1'),
                        'symbol2': pos_data.get('symbol2')
                    }
                summary['positions_summary'] = positions_summary
            
            return summary
            
        except Exception as e:
            logger.error(f"Failed to get portfolio summary: {e}")
            return {'exists': True, 'error': str(e)}
