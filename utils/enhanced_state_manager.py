"""
Enhanced Trading State Manager with Database Backend

This module provides a drop-in replacement for the existing StateManager
that uses the new database-backed state management system while maintaining
backward compatibility with the existing trading system.
"""

import asyncio
import logging
import threading
from typing import Dict, Any, Optional, List, Union
from datetime import datetime
import json
from pathlib import Path

from .state_database import (
    DatabaseStateManager, StateType, StateOperationType,
    create_state_manager, InfluxDBAdapter, PostgreSQLAdapter
)

logger = logging.getLogger(__name__)


class EnhancedTradingStateManager:
    """
    Enhanced state manager that provides database-backed state management
    with backward compatibility for the existing trading system.
    """
    
    def __init__(self, 
                 database_type: str = 'influxdb',
                 database_config: Optional[Dict[str, Any]] = None,
                 fallback_file_path: Optional[str] = None,
                 enable_file_fallback: bool = True):
        """
        Initialize the enhanced state manager.
        
        Args:
            database_type: Type of database ('influxdb', 'postgresql')
            database_config: Database configuration parameters
            fallback_file_path: Path for file-based fallback
            enable_file_fallback: Whether to enable file fallback for compatibility
        """
        self.database_type = database_type
        self.database_config = database_config or {}
        self.fallback_file_path = fallback_file_path
        self.enable_file_fallback = enable_file_fallback
        
        # State management components
        self.db_state_manager: Optional[DatabaseStateManager] = None
        self.file_state_manager: Optional['StateManager'] = None
        
        # Thread management for async operations
        self._loop = None
        self._thread = None
        self._lock = threading.RLock()
        self._initialized = False
        self._initializing = False
        
        # Cache for frequently accessed states
        self._cache: Dict[str, Any] = {}
        self._cache_ttl: Dict[str, datetime] = {}
        self._cache_duration = 300  # 5 minutes
        
    def _get_event_loop(self):
        """Get or create event loop for async operations."""
        if self._loop is None or self._loop.is_closed():
            try:
                # Try to get existing loop
                self._loop = asyncio.get_event_loop()
            except RuntimeError:
                # Create new loop
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
        return self._loop
    
    def _run_async(self, coro):
        """Run async coroutine in a thread-safe manner."""
        loop = self._get_event_loop()
        
        if loop.is_running():
            # If loop is already running, we need to run in a separate thread
            import concurrent.futures
            
            def run_in_thread():
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    return new_loop.run_until_complete(coro)
                finally:
                    new_loop.close()
            
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(run_in_thread)
                return future.result(timeout=30)  # 30 second timeout
        else:
            return loop.run_until_complete(coro)
    
    def initialize(self) -> bool:
        """Initialize the enhanced state manager."""
        with self._lock:
            if self._initialized:
                return True
            
            if self._initializing:
                # Wait for initialization to complete
                import time
                while self._initializing and not self._initialized:
                    time.sleep(0.1)
                return self._initialized
            
            self._initializing = True
            
            try:
                # Initialize database state manager
                async def init_db():
                    self.db_state_manager = await create_state_manager(
                        self.database_type,
                        **self.database_config
                    )
                    return True
                
                try:
                    success = self._run_async(init_db())
                    if success:
                        logger.info(f"Enhanced state manager initialized with {self.database_type}")
                    else:
                        logger.warning("Failed to initialize database state manager")
                        if self.enable_file_fallback:
                            success = self._initialize_file_fallback()
                except Exception as e:
                    logger.error(f"Database initialization failed: {e}")
                    if self.enable_file_fallback:
                        success = self._initialize_file_fallback()
                    else:
                        success = False
                
                self._initialized = success
                return success
                
            finally:
                self._initializing = False
    
    def _initialize_file_fallback(self) -> bool:
        """Initialize file-based fallback."""
        try:
            if self.fallback_file_path:
                from .state_manager import TradingStateManager
                self.file_state_manager = TradingStateManager(self.fallback_file_path)
                logger.info("File-based fallback state manager initialized")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to initialize file fallback: {e}")
            return False
    
    def _is_cache_valid(self, key: str) -> bool:
        """Check if cached value is still valid."""
        if key not in self._cache or key not in self._cache_ttl:
            return False
        
        return datetime.now() < self._cache_ttl[key]
    
    def _cache_set(self, key: str, value: Any):
        """Set value in cache with TTL."""
        self._cache[key] = value
        self._cache_ttl[key] = datetime.now().timestamp() + self._cache_duration
    
    def _cache_get(self, key: str) -> Any:
        """Get value from cache if valid."""
        if self._is_cache_valid(key):
            return self._cache[key]
        return None
    
    def save_trading_state(self, 
                          active_positions: Dict[str, Any],
                          pair_states: Dict[str, Any],
                          portfolio_data: Optional[Dict[str, Any]] = None,
                          user_id: Optional[str] = None) -> bool:
        """
        Save trading state using database backend with file fallback.
        
        Args:
            active_positions: Dictionary of active trading positions
            pair_states: Dictionary of pair states
            portfolio_data: Optional portfolio data
            user_id: User ID for audit trail
            
        Returns:
            bool: True if successful
        """
        if not self._initialized and not self.initialize():
            logger.error("State manager not initialized")
            return False
        
        try:
            async def save_async():
                success = True
                
                # Save positions
                for pair, position_data in active_positions.items():
                    version_id = await self.db_state_manager.save_state(
                        state_id=f"position_{pair}",
                        state_type=StateType.POSITION,
                        data=position_data,
                        operation=StateOperationType.UPDATE,
                        user_id=user_id,
                        description=f"Position update for {pair}"
                    )
                    if not version_id:
                        success = False
                
                # Save pair states
                for pair, pair_data in pair_states.items():
                    # Convert pandas objects to serializable format
                    serializable_data = self._prepare_pair_data_for_db(pair_data)
                    
                    version_id = await self.db_state_manager.save_state(
                        state_id=f"pair_{pair}",
                        state_type=StateType.PAIR_STATE,
                        data=serializable_data,
                        operation=StateOperationType.UPDATE,
                        user_id=user_id,
                        description=f"Pair state update for {pair}"
                    )
                    if not version_id:
                        success = False
                
                # Save portfolio data
                if portfolio_data:
                    version_id = await self.db_state_manager.save_state(
                        state_id="portfolio_main",
                        state_type=StateType.PORTFOLIO,
                        data=portfolio_data,
                        operation=StateOperationType.UPDATE,
                        user_id=user_id,
                        description="Portfolio state update"
                    )
                    if not version_id:
                        success = False
                
                return success
            
            if self.db_state_manager:
                success = self._run_async(save_async())
                
                # Clear relevant cache entries
                self._cache.clear()
                
                if success:
                    logger.debug("Trading state saved to database")
                    return True
                else:
                    logger.warning("Some states failed to save to database")
            
            # Fallback to file-based storage
            if self.file_state_manager:
                logger.info("Using file fallback for state saving")
                return self.file_state_manager.save_trading_state(
                    active_positions, pair_states, portfolio_data
                )
            
            return False
            
        except Exception as e:
            logger.error(f"Error saving trading state: {e}")
            
            # Try file fallback
            if self.file_state_manager:
                logger.info("Using file fallback due to error")
                return self.file_state_manager.save_trading_state(
                    active_positions, pair_states, portfolio_data
                )
            
            return False
    
    def load_trading_state(self) -> Optional[Dict[str, Any]]:
        """
        Load trading state from database with file fallback.
        
        Returns:
            Dictionary with trading state data or None
        """
        if not self._initialized and not self.initialize():
            logger.error("State manager not initialized")
            return None
        
        # Check cache first
        cache_key = "trading_state_full"
        cached = self._cache_get(cache_key)
        if cached:
            return cached
        
        try:
            async def load_async():
                result = {
                    'active_positions': {},
                    'pair_states': {}
                }
                
                # Load positions
                position_history = await self.db_state_manager.get_state_history(
                    state_type=StateType.POSITION,
                    limit=1000
                )
                
                # Get latest position for each pair
                latest_positions = {}
                for version in position_history:
                    if version.operation != StateOperationType.DELETE:
                        state_id = version.state_id
                        if state_id not in latest_positions or version.timestamp > latest_positions[state_id].timestamp:
                            latest_positions[state_id] = version
                
                for state_id, version in latest_positions.items():
                    if state_id.startswith("position_"):
                        pair = state_id[9:]  # Remove "position_" prefix
                        result['active_positions'][pair] = version.data
                
                # Load pair states
                pair_history = await self.db_state_manager.get_state_history(
                    state_type=StateType.PAIR_STATE,
                    limit=1000
                )
                
                # Get latest state for each pair
                latest_pairs = {}
                for version in pair_history:
                    if version.operation != StateOperationType.DELETE:
                        state_id = version.state_id
                        if state_id not in latest_pairs or version.timestamp > latest_pairs[state_id].timestamp:
                            latest_pairs[state_id] = version
                
                for state_id, version in latest_pairs.items():
                    if state_id.startswith("pair_"):
                        pair = state_id[5:]  # Remove "pair_" prefix
                        # Reconstruct pair data from database format
                        pair_data = self._reconstruct_pair_data_from_db(version.data)
                        result['pair_states'][pair] = pair_data
                
                # Load portfolio data
                portfolio_data = await self.db_state_manager.load_state(
                    "portfolio_main",
                    StateType.PORTFOLIO
                )
                
                if portfolio_data:
                    result.update(portfolio_data)
                
                return result
            
            if self.db_state_manager:
                result = self._run_async(load_async())
                if result and (result['active_positions'] or result['pair_states']):
                    # Cache the result
                    self._cache_set(cache_key, result)
                    logger.debug("Trading state loaded from database")
                    return result
            
            # Fallback to file-based storage
            if self.file_state_manager:
                logger.info("Using file fallback for state loading")
                result = self.file_state_manager.load_trading_state()
                if result:
                    self._cache_set(cache_key, result)
                return result
            
            return None
            
        except Exception as e:
            logger.error(f"Error loading trading state: {e}")
            
            # Try file fallback
            if self.file_state_manager:
                logger.info("Using file fallback due to error")
                return self.file_state_manager.load_trading_state()
            
            return None
    
    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Get portfolio summary with enhanced information."""
        if not self._initialized and not self.initialize():
            return {'exists': False, 'error': 'State manager not initialized'}
        
        try:
            async def get_summary_async():
                # Get portfolio state
                portfolio_data = await self.db_state_manager.load_state(
                    "portfolio_main",
                    StateType.PORTFOLIO
                )
                
                # Count active positions
                position_history = await self.db_state_manager.get_state_history(
                    state_type=StateType.POSITION,
                    limit=1000
                )
                
                active_positions = {}
                for version in position_history:
                    if version.operation != StateOperationType.DELETE:
                        state_id = version.state_id
                        if state_id not in active_positions or version.timestamp > active_positions[state_id].timestamp:
                            active_positions[state_id] = version
                
                # Count pair states
                pair_history = await self.db_state_manager.get_state_history(
                    state_type=StateType.PAIR_STATE,
                    limit=1000
                )
                
                active_pairs = {}
                for version in pair_history:
                    if version.operation != StateOperationType.DELETE:
                        state_id = version.state_id
                        if state_id not in active_pairs or version.timestamp > active_pairs[state_id].timestamp:
                            active_pairs[state_id] = version
                
                summary = {
                    'exists': True,
                    'active_positions_count': len(active_positions),
                    'pair_states_count': len(active_pairs),
                    'database_type': self.database_type,
                    'last_update': datetime.now().isoformat()
                }
                
                if portfolio_data:
                    summary.update(portfolio_data)
                
                return summary
            
            if self.db_state_manager:
                return self._run_async(get_summary_async())
            
            # Fallback to file manager
            if self.file_state_manager:
                summary = self.file_state_manager.get_portfolio_summary()
                summary['database_type'] = 'file_fallback'
                return summary
            
            return {'exists': False, 'error': 'No state manager available'}
            
        except Exception as e:
            logger.error(f"Error getting portfolio summary: {e}")
            
            # Try file fallback
            if self.file_state_manager:
                summary = self.file_state_manager.get_portfolio_summary()
                summary['error'] = str(e)
                summary['database_type'] = 'file_fallback'
                return summary
            
            return {'exists': False, 'error': str(e)}
    
    def _prepare_pair_data_for_db(self, pair_data: Dict[str, Any]) -> Dict[str, Any]:
        """Prepare pair data for database storage by serializing pandas objects."""
        serializable_data = {}
        
        for key, value in pair_data.items():
            if hasattr(value, 'to_dict'):  # pandas DataFrame/Series
                if hasattr(value, 'index'):
                    # Handle time series data
                    serializable_data[key] = {
                        'type': 'pandas_series' if hasattr(value, 'name') else 'pandas_dataframe',
                        'data': value.to_dict(),
                        'index': [str(idx) for idx in value.index]
                    }
                else:
                    serializable_data[key] = value.to_dict()
            elif isinstance(value, datetime):
                serializable_data[key] = value.isoformat()
            else:
                serializable_data[key] = value
        
        return serializable_data
    
    def _reconstruct_pair_data_from_db(self, db_data: Dict[str, Any]) -> Dict[str, Any]:
        """Reconstruct pair data from database format."""
        import pandas as pd
        
        reconstructed_data = {}
        
        for key, value in db_data.items():
            if isinstance(value, dict) and value.get('type') in ['pandas_series', 'pandas_dataframe']:
                # Reconstruct pandas objects
                try:
                    if value['type'] == 'pandas_series':
                        series_data = value['data']
                        index = value.get('index', [])
                        reconstructed_data[key] = pd.Series(series_data, index=index)
                    else:
                        df_data = value['data']
                        index = value.get('index', [])
                        reconstructed_data[key] = pd.DataFrame(df_data, index=index)
                except Exception as e:
                    logger.warning(f"Failed to reconstruct pandas object for {key}: {e}")
                    reconstructed_data[key] = value
            elif isinstance(value, str):
                try:
                    # Try to parse as datetime
                    reconstructed_data[key] = datetime.fromisoformat(value)
                except ValueError:
                    reconstructed_data[key] = value
            else:
                reconstructed_data[key] = value
        
        return reconstructed_data
    
    def create_snapshot(self, description: str = None) -> bool:
        """Create a snapshot of all trading states."""
        if not self._initialized and not self.initialize():
            return False
        
        try:
            async def create_snapshot_async():
                snapshot_id = f"trading_snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                return await self.db_state_manager.create_snapshot(snapshot_id, description)
            
            if self.db_state_manager:
                return self._run_async(create_snapshot_async())
            
            return False
            
        except Exception as e:
            logger.error(f"Error creating snapshot: {e}")
            return False
    
    def get_state_history(self, 
                         state_type: str = None,
                         limit: int = 100) -> List[Dict[str, Any]]:
        """Get state history for analysis."""
        if not self._initialized and not self.initialize():
            return []
        
        try:
            async def get_history_async():
                state_type_enum = None
                if state_type:
                    try:
                        state_type_enum = StateType(state_type.lower())
                    except ValueError:
                        logger.warning(f"Invalid state type: {state_type}")
                
                history = await self.db_state_manager.get_state_history(
                    state_type=state_type_enum,
                    limit=limit
                )
                
                return [
                    {
                        'version_id': version.version_id,
                        'state_id': version.state_id,
                        'state_type': version.state_type.value,
                        'operation': version.operation.value,
                        'timestamp': version.timestamp.isoformat(),
                        'user_id': version.user_id,
                        'description': version.description,
                        'data_size': len(str(version.data))
                    }
                    for version in history
                ]
            
            if self.db_state_manager:
                return self._run_async(get_history_async())
            
            return []
            
        except Exception as e:
            logger.error(f"Error getting state history: {e}")
            return []
    
    def cleanup(self):
        """Cleanup resources."""
        try:
            if self.db_state_manager:
                async def cleanup_async():
                    await self.db_state_manager.cleanup()
                
                self._run_async(cleanup_async())
            
            self._cache.clear()
            self._cache_ttl.clear()
            
            if self._loop and not self._loop.is_closed():
                self._loop.close()
                
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
    
    def __del__(self):
        """Destructor to ensure cleanup."""
        try:
            self.cleanup()
        except:
            pass


# Factory function for easy integration
def create_enhanced_trading_state_manager(
    database_type: str = 'influxdb',
    database_config: Optional[Dict[str, Any]] = None,
    fallback_file_path: Optional[str] = None
) -> EnhancedTradingStateManager:
    """
    Create an enhanced trading state manager with sensible defaults.
    
    Args:
        database_type: 'influxdb' or 'postgresql'
        database_config: Database configuration
        fallback_file_path: File path for fallback storage
        
    Returns:
        Configured EnhancedTradingStateManager
    """
    if database_config is None:
        if database_type == 'influxdb':
            database_config = {
                'url': 'http://localhost:8086',
                'token': 'your-token',
                'org': 'trading-org',
                'bucket': 'trading-states'
            }
        elif database_type == 'postgresql':
            database_config = {
                'connection_string': 'postgresql://user:password@localhost/trading_states'
            }
    
    return EnhancedTradingStateManager(
        database_type=database_type,
        database_config=database_config,
        fallback_file_path=fallback_file_path,
        enable_file_fallback=True
    )
