"""
Integrated State Management System for Trading System

This module provides a unified interface for the enhanced state management system,
making it easy to integrate with the existing trading system while providing
all the benefits of database-backed, versioned, and scalable state management.
"""

import os
import json
import logging
import asyncio
import threading
from typing import Dict, Any, Optional, List, Union
from datetime import datetime
from contextlib import asynccontextmanager

from .state_config import StateManagementConfig, load_config, setup_logging
from .enhanced_state_manager import EnhancedTradingStateManager, create_enhanced_trading_state_manager
from .state_migration import StateMigrator, BatchMigrator, migrate_existing_state

# Legacy compatibility - import with fallback for missing dependencies
try:
    from .state_manager import TradingStateManager
    LEGACY_STATE_MANAGER_AVAILABLE = True
except ImportError:
    TradingStateManager = None
    LEGACY_STATE_MANAGER_AVAILABLE = False

logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)


class UnifiedStateManager:
    """
    Unified state manager that provides a single interface for all state management needs.
    
    This class automatically handles:
    - Configuration loading from environment or files
    - Database initialization and fallback management
    - Migration from legacy file-based states
    - Thread-safe operations for sync/async environments
    - Caching and performance optimization
    """
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls, *args, **kwargs):
        """Singleton pattern to ensure single instance across the application."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self, 
                 config_path: Optional[str] = None,
                 auto_migrate: bool = True,
                 legacy_state_path: Optional[str] = None):
        """
        Initialize the unified state manager.
        
        Args:
            config_path: Path to configuration file (optional)
            auto_migrate: Whether to automatically migrate legacy states
            legacy_state_path: Path to legacy state file for migration
        """
        # Prevent re-initialization
        if hasattr(self, '_initialized') and self._initialized:
            return
        
        self.config = load_config(config_path)
        self.auto_migrate = auto_migrate
        self.legacy_state_path = legacy_state_path
        
        # Setup logging
        setup_logging(self.config)
        
        # State management components
        self.enhanced_manager: Optional[EnhancedTradingStateManager] = None
        self.legacy_manager: Optional[Any] = None  # TradingStateManager if available
        self.api_server = None
        
        # Runtime state
        self._initialized = False
        self._api_running = False
        self._shutdown_event = threading.Event()
        
        logger.info("Unified state manager created")
    
    def initialize(self) -> bool:
        """Initialize all state management components."""
        try:
            with self._lock:
                if self._initialized:
                    return True
                
                logger.info("Initializing unified state manager...")
                
                # Create enhanced state manager with better error handling
                try:
                    self.enhanced_manager = create_enhanced_trading_state_manager(
                        database_type=self.config.database.database_type,
                        database_config=self.config.database.get_connection_config(),
                        fallback_file_path=os.path.join(
                            self.config.fallback_directory,
                            'trading_state.json'
                        ) if self.config.enable_file_fallback else None
                    )
                    
                    # Initialize enhanced manager
                    if not self.enhanced_manager.initialize():
                        logger.warning("Enhanced state manager initialization failed, checking for authentication issues...")
                        
                        # Check if it's an InfluxDB authentication issue
                        if self.config.database.database_type.lower() == 'influxdb':
                            logger.warning("InfluxDB authentication may be failing. Please check:")
                            logger.warning("1. INFLUXDB_TOKEN environment variable is set correctly")
                            logger.warning("2. InfluxDB server is running and accessible")
                            logger.warning("3. Token has proper permissions")
                            
                            # Try to continue with file fallback if enabled
                            if self.config.enable_file_fallback:
                                logger.info("Continuing with file fallback for state management")
                            else:
                                logger.error("No fallback enabled. State management will be limited.")
                        else:
                            logger.error("Failed to initialize enhanced state manager")
                            return False
                
                except Exception as e:
                    logger.error(f"Exception during enhanced state manager creation: {e}")
                    if self.config.enable_file_fallback:
                        logger.info("Continuing with limited functionality using fallback")
                    else:
                        return False
                
                # Auto-migrate legacy state if configured
                if self.auto_migrate and self.legacy_state_path:
                    self._perform_auto_migration()
                
                # Initialize API if configured
                if self.config.api.enabled:
                    self._initialize_api()
                
                self._initialized = True
                logger.info("Unified state manager initialized successfully")
                return True
                
        except Exception as e:
            logger.error(f"Failed to initialize unified state manager: {e}")
            return False
    
    def _perform_auto_migration(self):
        """Perform automatic migration from legacy state."""
        try:
            if not os.path.exists(self.legacy_state_path):
                logger.info("No legacy state file found for auto-migration")
                return
            
            logger.info(f"Auto-migrating legacy state from {self.legacy_state_path}")
            
            migrator = StateMigrator(
                self.legacy_state_path,
                self.enhanced_manager,
                user_id=self.config.migration_user_id
            )
            
            success = migrator.migrate_trading_state(backup_original=True)
            
            if success:
                logger.info("Auto-migration completed successfully")
                
                # Optionally rename legacy file to indicate migration
                migrated_path = f"{self.legacy_state_path}.migrated"
                os.rename(self.legacy_state_path, migrated_path)
                logger.info(f"Legacy file renamed to {migrated_path}")
                
            else:
                logger.warning("Auto-migration completed with some failures")
                
        except Exception as e:
            logger.error(f"Auto-migration failed: {e}")
    
    def _initialize_api(self):
        """Initialize the REST API server."""
        try:
            from .state_api import StateAPI, TradingStateAPI
            
            # Create API components
            state_api = StateAPI(self.enhanced_manager.db_state_manager)
            trading_api = TradingStateAPI(state_api)
            
            self.api_server = state_api
            self.trading_api = trading_api
            
            logger.info("API components initialized")
            
        except ImportError:
            logger.warning("FastAPI not available, API server disabled")
        except Exception as e:
            logger.error(f"Failed to initialize API: {e}")
    
    async def start_api_server(self):
        """Start the API server (async)."""
        if not self.config.api.enabled or not self.api_server:
            logger.warning("API server not configured or not available")
            return
        
        try:
            logger.info(f"Starting API server on {self.config.api.host}:{self.config.api.port}")
            
            await self.api_server.start_server(
                host=self.config.api.host,
                port=self.config.api.port
            )
            
            self._api_running = True
            
        except Exception as e:
            logger.error(f"Failed to start API server: {e}")
    
    def start_api_server_sync(self):
        """Start the API server in a separate thread (sync interface)."""
        if not self.config.api.enabled or not self.api_server:
            return
        
        def run_api():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self.start_api_server())
            except Exception as e:
                logger.error(f"API server thread error: {e}")
            finally:
                loop.close()
        
        api_thread = threading.Thread(target=run_api, daemon=True)
        api_thread.start()
        logger.info("API server started in background thread")
    
    # Trading State Management Interface (backward compatible)
    
    def save_trading_state(self, 
                          active_positions: Dict[str, Any],
                          pair_states: Dict[str, Any],
                          portfolio_data: Optional[Dict[str, Any]] = None,
                          user_id: Optional[str] = None) -> bool:
        """Save trading state using enhanced state manager."""
        if not self._initialized and not self.initialize():
            return False
        
        return self.enhanced_manager.save_trading_state(
            active_positions, pair_states, portfolio_data, user_id
        )
    
    def load_trading_state(self) -> Optional[Dict[str, Any]]:
        """Load trading state using enhanced state manager."""
        if not self._initialized and not self.initialize():
            return None
        
        return self.enhanced_manager.load_trading_state()
    
    def get_portfolio_summary(self) -> Dict[str, Any]:
        """Get portfolio summary with enhanced information."""
        if not self._initialized and not self.initialize():
            return {'exists': False, 'error': 'Manager not initialized'}
        
        return self.enhanced_manager.get_portfolio_summary()
    
    def create_snapshot(self, description: str = None) -> bool:
        """Create a snapshot of all trading states."""
        if not self._initialized and not self.initialize():
            return False
        
        return self.enhanced_manager.create_snapshot(description)
    
    def get_state_history(self, 
                         state_type: str = None,
                         limit: int = 100) -> List[Dict[str, Any]]:
        """Get state history for analysis."""
        if not self._initialized and not self.initialize():
            return []
        
        return self.enhanced_manager.get_state_history(state_type, limit)
    
    # Advanced State Management Features
    
    def save_position(self, pair: str, position_data: Dict[str, Any],
                     user_id: str = None, description: str = None) -> bool:
        """Save a single position state with enhanced error handling."""
        if not self._initialized and not self.initialize():
            logger.warning("State manager not initialized, attempting to save to fallback")
            return self._save_to_fallback('position', pair, position_data)
        
        try:
            async def save_async():
                if hasattr(self.enhanced_manager, 'trading_api') and self.enhanced_manager.trading_api:
                    version_id = await self.enhanced_manager.trading_api.save_position(
                        pair, position_data, user_id, description
                    )
                    return version_id is not None
                
                # Fallback to direct state manager
                from .state_database import StateType, StateOperationType
                version_id = await self.enhanced_manager.db_state_manager.save_state(
                    state_id=f"position_{pair}",
                    state_type=StateType.POSITION,
                    data=position_data,
                    operation=StateOperationType.UPDATE,
                    user_id=user_id,
                    description=description or f"Position update for {pair}"
                )
                return version_id is not None
            
            return self.enhanced_manager._run_async(save_async())
            
        except Exception as e:
            logger.warning(f"Failed to save position {pair} to database: {e}")
            # Try fallback
            return self._save_to_fallback('position', pair, position_data)
    
    def _save_to_fallback(self, data_type: str, key: str, data: Dict[str, Any]) -> bool:
        """Save data to fallback file when database is unavailable."""
        try:
            if not self.config.enable_file_fallback:
                logger.error("Database unavailable and file fallback is disabled")
                return False
            
            fallback_dir = self.config.fallback_directory
            os.makedirs(fallback_dir, exist_ok=True)
            
            fallback_file = os.path.join(fallback_dir, f'{data_type}_fallback.json')
            
            # Load existing data
            fallback_data = {}
            if os.path.exists(fallback_file):
                try:
                    with open(fallback_file, 'r') as f:
                        fallback_data = json.load(f)
                except:
                    fallback_data = {}
            
            # Add timestamp and save
            data['timestamp'] = datetime.now().isoformat()
            fallback_data[key] = data
            
            with open(fallback_file, 'w') as f:
                json.dump(fallback_data, f, indent=2)
            
            logger.info(f"Saved {data_type} {key} to fallback file")
            return True
            
        except Exception as e:
            logger.error(f"Failed to save to fallback: {e}")
            return False
    
    def load_position(self, pair: str) -> Optional[Dict[str, Any]]:
        """Load a single position state."""
        if not self._initialized and not self.initialize():
            return None
        
        try:
            async def load_async():
                if hasattr(self.enhanced_manager, 'trading_api') and self.enhanced_manager.trading_api:
                    return await self.enhanced_manager.trading_api.load_position(pair)
                
                # Fallback to direct state manager
                from .state_database import StateType
                return await self.enhanced_manager.db_state_manager.load_state(
                    state_id=f"position_{pair}",
                    state_type=StateType.POSITION
                )
            
            return self.enhanced_manager._run_async(load_async())
            
        except Exception as e:
            logger.error(f"Failed to load position {pair}: {e}")
            return None
    
    def get_all_positions(self) -> List[Dict[str, Any]]:
        """Get all active positions."""
        if not self._initialized and not self.initialize():
            return []
        
        try:
            async def get_all_async():
                if hasattr(self.enhanced_manager, 'trading_api') and self.enhanced_manager.trading_api:
                    return await self.enhanced_manager.trading_api.get_all_positions()
                
                # Fallback implementation
                from .state_database import StateType
                history = await self.enhanced_manager.db_state_manager.get_state_history(
                    state_type=StateType.POSITION,
                    limit=1000
                )
                
                # Get latest for each position
                latest_positions = {}
                for version in history:
                    state_id = version.state_id
                    if state_id not in latest_positions or version.timestamp > latest_positions[state_id].timestamp:
                        latest_positions[state_id] = version
                
                return [version.data for version in latest_positions.values()]
            
            return self.enhanced_manager._run_async(get_all_async())
            
        except Exception as e:
            logger.error(f"Failed to get all positions: {e}")
            return []
    
    # Migration and Maintenance
    
    def migrate_from_file(self, file_path: str, backup_original: bool = True) -> bool:
        """Migrate a legacy state file to the database."""
        if not self._initialized and not self.initialize():
            return False
        
        try:
            migrator = StateMigrator(
                file_path,
                self.enhanced_manager,
                user_id=self.config.migration_user_id
            )
            
            return migrator.migrate_trading_state(backup_original)
            
        except Exception as e:
            logger.error(f"Migration failed: {e}")
            return False
    
    def batch_migrate_directory(self, directory_path: str, 
                               file_pattern: str = "*.json") -> bool:
        """Migrate all state files in a directory."""
        if not self._initialized and not self.initialize():
            return False
        
        try:
            batch_migrator = BatchMigrator(self.enhanced_manager)
            return batch_migrator.migrate_directory(directory_path, file_pattern)
            
        except Exception as e:
            logger.error(f"Batch migration failed: {e}")
            return False
    
    def cleanup_old_states(self, days_to_keep: int = None) -> bool:
        """Cleanup old state versions (if supported by database)."""
        if not self._initialized and not self.initialize():
            return False
        
        days_to_keep = days_to_keep or self.config.backup_retention_days
        
        # Implementation depends on database type
        # For now, just log the request
        logger.info(f"Cleanup request for states older than {days_to_keep} days")
        return True
    
    # System Management
    
    def get_system_status(self) -> Dict[str, Any]:
        """Get comprehensive system status."""
        status = {
            'initialized': self._initialized,
            'database_type': self.config.database.database_type if self.config else 'unknown',
            'api_enabled': self.config.api.enabled if self.config else False,
            'api_running': self._api_running,
            'cache_enabled': self.config.cache.enabled if self.config else False,
            'file_fallback_enabled': self.config.enable_file_fallback if self.config else False,
            'timestamp': datetime.now().isoformat()
        }
        
        if self._initialized and self.enhanced_manager:
            # Add database-specific status
            try:
                portfolio_summary = self.get_portfolio_summary()
                status.update({
                    'active_positions': portfolio_summary.get('active_positions_count', 0),
                    'pair_states': portfolio_summary.get('pair_states_count', 0),
                    'last_update': portfolio_summary.get('last_update')
                })
            except Exception as e:
                status['status_error'] = str(e)
        
        return status
    
    def health_check(self) -> bool:
        """Perform a health check of all components."""
        try:
            if not self._initialized:
                return False
            
            # Test database connectivity
            test_state = self.get_portfolio_summary()
            if 'error' in test_state:
                return False
            
            # Test API if enabled
            if self.config.api.enabled and not self._api_running:
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False
    
    def shutdown(self):
        """Gracefully shutdown the state manager."""
        try:
            logger.info("Shutting down unified state manager...")
            
            self._shutdown_event.set()
            
            if self.enhanced_manager:
                self.enhanced_manager.cleanup()
            
            self._api_running = False
            self._initialized = False
            
            logger.info("Unified state manager shutdown complete")
            
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
    
    def __enter__(self):
        """Context manager entry."""
        self.initialize()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.shutdown()


# Convenience functions for easy integration

def get_state_manager(config_path: Optional[str] = None,
                     auto_migrate: bool = True,
                     legacy_state_path: Optional[str] = None) -> UnifiedStateManager:
    """
    Get or create the unified state manager singleton.
    
    Args:
        config_path: Path to configuration file
        auto_migrate: Whether to auto-migrate legacy states
        legacy_state_path: Path to legacy state file
        
    Returns:
        UnifiedStateManager instance
    """
    return UnifiedStateManager(config_path, auto_migrate, legacy_state_path)


@asynccontextmanager
async def state_manager_context(config_path: Optional[str] = None):
    """Async context manager for state management."""
    manager = get_state_manager(config_path)
    try:
        manager.initialize()
        yield manager
    finally:
        manager.shutdown()


def migrate_legacy_system(legacy_state_path: str,
                         config_path: Optional[str] = None) -> bool:
    """
    One-shot function to migrate an entire legacy state system.
    
    Args:
        legacy_state_path: Path to legacy state file or directory
        config_path: Path to configuration file
        
    Returns:
        bool: Success status
    """
    try:
        with UnifiedStateManager(config_path, auto_migrate=False) as manager:
            if os.path.isfile(legacy_state_path):
                return manager.migrate_from_file(legacy_state_path)
            elif os.path.isdir(legacy_state_path):
                return manager.batch_migrate_directory(legacy_state_path)
            else:
                logger.error(f"Legacy path does not exist: {legacy_state_path}")
                return False
                
    except Exception as e:
        logger.error(f"Legacy system migration failed: {e}")
        return False


# Integration with existing trading system

class StateManagerAdapter:
    """
    Adapter class to integrate with existing trading system code
    that expects the old StateManager interface.
    """
    
    def __init__(self, unified_manager: UnifiedStateManager):
        self.unified_manager = unified_manager
        
    def save_trading_state(self, active_positions, pair_states, portfolio_data=None):
        """Backward compatible method."""
        return self.unified_manager.save_trading_state(
            active_positions, pair_states, portfolio_data
        )
    
    def load_trading_state(self):
        """Backward compatible method."""
        return self.unified_manager.load_trading_state()
    
    def get_portfolio_summary(self):
        """Backward compatible method."""
        return self.unified_manager.get_portfolio_summary()
    
    def state_exists(self):
        """Backward compatible method."""
        summary = self.unified_manager.get_portfolio_summary()
        return summary.get('exists', False)


def create_backward_compatible_manager(state_file_path: str = None) -> StateManagerAdapter:
    """
    Create a backward compatible state manager for existing code.
    
    Args:
        state_file_path: Legacy state file path (for migration)
        
    Returns:
        StateManagerAdapter that works with existing code
    """
    unified_manager = get_state_manager(
        legacy_state_path=state_file_path,
        auto_migrate=True
    )
    
    return StateManagerAdapter(unified_manager)


# Example usage and testing

def example_usage():
    """Example of how to use the unified state manager."""
    
    # Basic usage with auto-configuration
    with UnifiedStateManager() as manager:
        # Save some state
        positions = {"EURUSD": {"direction": "long", "quantity": 100000}}
        pair_states = {"EURUSD": {"position": "long", "cooldown": 0}}
        
        success = manager.save_trading_state(positions, pair_states)
        print(f"Save success: {success}")
        
        # Load state
        loaded_state = manager.load_trading_state()
        print(f"Loaded state: {loaded_state}")
        
        # Get system status
        status = manager.get_system_status()
        print(f"System status: {status}")
    
    # Migration example
    legacy_file = "old_trading_state.json"
    if os.path.exists(legacy_file):
        success = migrate_legacy_system(legacy_file)
        print(f"Migration success: {success}")
    
    # Backward compatibility example
    adapter = create_backward_compatible_manager("legacy_state.json")
    state = adapter.load_trading_state()
    print(f"Backward compatible load: {state}")


if __name__ == "__main__":
    example_usage()
