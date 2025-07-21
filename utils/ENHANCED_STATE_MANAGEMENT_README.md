# Enhanced Database-Backed State Management System

A comprehensive, scalable, and flexible state management system for trading applications that provides database-backed storage, versioning, schema validation, and API accessibility.

## 🚀 Features

### Core Features
- **Database-Agnostic**: Support for InfluxDB, PostgreSQL, and easily extensible to other databases
- **Versioning**: Complete audit trail with versioned state changes
- **Schema Validation**: Pydantic-based validation for data integrity
- **Thread-Safe**: Concurrent access support with proper locking
- **Caching**: Intelligent caching for performance optimization
- **Migration Tools**: Seamless migration from file-based to database systems

### Advanced Features
- **REST API**: FastAPI-based REST interface with real-time WebSocket updates
- **Snapshots**: Point-in-time snapshots and restore functionality
- **Auto-Migration**: Automatic migration from legacy file-based states
- **Fallback Support**: File-based fallback for high availability
- **Configuration Management**: Environment-based and file-based configuration

### Integration Features
- **Backward Compatibility**: Drop-in replacement for existing StateManager
- **Unified Interface**: Single interface for all state management needs
- **Health Monitoring**: Comprehensive system health and status monitoring
- **Batch Operations**: Efficient batch migration and operations

## 📋 Requirements

### Core Dependencies
```
python >= 3.8
pandas
datetime
threading
asyncio
```

### Database Dependencies
```bash
# For InfluxDB support
pip install influxdb-client

# For PostgreSQL support  
pip install asyncpg

# For schema validation
pip install pydantic

# For REST API (optional)
pip install fastapi uvicorn websockets

# For migration utilities
pip install python-dotenv
```

## 🛠️ Installation

1. **Install Dependencies**:
```bash
pip install influxdb-client asyncpg pydantic fastapi uvicorn websockets python-dotenv
```

2. **Copy Files**: Copy all the state management files to your `utils/` directory:
   - `state_database.py` - Core database abstraction layer
   - `state_api.py` - REST API implementation
   - `enhanced_state_manager.py` - Enhanced trading state manager
   - `state_migration.py` - Migration utilities
   - `state_config.py` - Configuration management
   - `unified_state_manager.py` - Unified interface

## ⚙️ Configuration

### Environment Variables

Set these environment variables for configuration:

```bash
# Database Configuration
TRADING_STATE_DB_TYPE=influxdb  # or postgresql
TRADING_STATE_INFLUXDB_URL=http://localhost:8086
TRADING_STATE_INFLUXDB_TOKEN=your-token-here
TRADING_STATE_INFLUXDB_ORG=trading-org
TRADING_STATE_INFLUXDB_BUCKET=trading-states

# PostgreSQL (alternative)
TRADING_STATE_POSTGRES_HOST=localhost
TRADING_STATE_POSTGRES_PORT=5432
TRADING_STATE_POSTGRES_DB=trading_states
TRADING_STATE_POSTGRES_USER=postgres
TRADING_STATE_POSTGRES_PASSWORD=your-password

# API Configuration (optional)
TRADING_STATE_API_ENABLED=true
TRADING_STATE_API_HOST=0.0.0.0
TRADING_STATE_API_PORT=8000

# General Settings
TRADING_STATE_ENABLE_VALIDATION=true
TRADING_STATE_ENABLE_FILE_FALLBACK=true
TRADING_STATE_LOG_LEVEL=INFO
```

### Configuration File

Create a `config.json` file:

```json
{
  "database": {
    "database_type": "influxdb",
    "influxdb_url": "http://localhost:8086",
    "influxdb_token": "your-token",
    "influxdb_org": "trading-org",
    "influxdb_bucket": "trading-states"
  },
  "api": {
    "enabled": true,
    "host": "0.0.0.0",
    "port": 8000
  },
  "cache": {
    "enabled": true,
    "ttl_seconds": 300,
    "max_size": 1000
  },
  "enable_validation": true,
  "enable_file_fallback": true,
  "log_level": "INFO"
}
```

## 🚀 Quick Start

### 1. Basic Usage (Drop-in Replacement)

Replace your existing StateManager with the enhanced version:

```python
# Old way
from utils.state_manager import TradingStateManager
state_manager = TradingStateManager("state.json")

# New way (backward compatible)
from utils.unified_state_manager import create_backward_compatible_manager
state_manager = create_backward_compatible_manager("state.json")

# Use exactly the same interface
positions = {"EURUSD": {"direction": "long", "quantity": 100000}}
pair_states = {"EURUSD": {"position": "long", "cooldown": 0}}

success = state_manager.save_trading_state(positions, pair_states)
loaded_state = state_manager.load_trading_state()
```

### 2. Enhanced Usage

Use the full feature set:

```python
from utils.unified_state_manager import UnifiedStateManager

# Initialize with auto-migration
with UnifiedStateManager(auto_migrate=True, legacy_state_path="old_state.json") as manager:
    
    # Save state with user tracking
    success = manager.save_trading_state(
        active_positions=positions,
        pair_states=pair_states,
        portfolio_data={"total_value": 50000},
        user_id="trader_1"
    )
    
    # Create snapshot
    manager.create_snapshot("End of day snapshot")
    
    # Get comprehensive status
    status = manager.get_system_status()
    print(f"System status: {status}")
    
    # Get state history for analysis
    history = manager.get_state_history(state_type="position", limit=50)
```

### 3. Advanced Usage with API

```python
import asyncio
from utils.unified_state_manager import UnifiedStateManager

async def main():
    manager = UnifiedStateManager()
    manager.initialize()
    
    # Start API server for external access
    await manager.start_api_server()

# Run with asyncio
asyncio.run(main())
```

## 🔄 Migration

### Automatic Migration

The system can automatically migrate your existing file-based states:

```python
from utils.unified_state_manager import migrate_legacy_system

# Migrate single file
success = migrate_legacy_system("trading_state.json")

# Migrate entire directory
success = migrate_legacy_system("./state_backups/")
```

### Manual Migration

For more control over the migration process:

```python
from utils.state_migration import StateMigrator
from utils.enhanced_state_manager import create_enhanced_trading_state_manager

# Create target manager
target_manager = create_enhanced_trading_state_manager(
    database_type='influxdb',
    database_config={
        'url': 'http://localhost:8086',
        'token': 'your-token',
        'org': 'trading-org',
        'bucket': 'trading-states'
    }
)

# Create migrator
migrator = StateMigrator("old_state.json", target_manager)

# Perform migration
success = migrator.migrate_trading_state(backup_original=True)

# Get detailed report
report = migrator.get_migration_report()
print(f"Migrated {report['successful_items']}/{report['total_items']} items")
```

## 🔧 Integration with Existing Trading System

### Update Main Trading Script

Modify your main trading script to use the enhanced state manager:

```python
# In your main.py or trading script

# Old imports
# from utils.state_manager import TradingStateManager

# New imports
from utils.unified_state_manager import get_state_manager

class EnhancedTradingSystemV3:
    def __init__(self, data_provider: str = 'ctrader', broker: str = 'ctrader'):
        # ... existing initialization ...
        
        # Replace old state manager initialization
        # self.state_manager = TradingStateManager("state.json")
        
        # New enhanced state manager
        self.state_manager = get_state_manager(
            auto_migrate=True,
            legacy_state_path="state.json"  # Your existing state file
        )
        
        # Initialize the manager
        self.state_manager.initialize()
    
    def save_current_state(self):
        """Save current trading state with enhanced features."""
        # Existing state preparation code...
        active_positions = self.get_active_positions()
        pair_states = self.get_pair_states()
        portfolio_data = self.get_portfolio_data()
        
        # Enhanced save with user tracking
        success = self.state_manager.save_trading_state(
            active_positions=active_positions,
            pair_states=pair_states,
            portfolio_data=portfolio_data,
            user_id="trading_system"
        )
        
        if success:
            logger.info("State saved successfully to database")
        else:
            logger.error("Failed to save state")
        
        return success
    
    def load_previous_state(self):
        """Load previous state with enhanced error handling."""
        state = self.state_manager.load_trading_state()
        
        if state:
            logger.info("State loaded successfully from database")
            return state
        else:
            logger.warning("No previous state found")
            return None
    
    def create_end_of_day_snapshot(self):
        """Create end-of-day snapshot."""
        description = f"End of day snapshot - {datetime.now().strftime('%Y-%m-%d')}"
        success = self.state_manager.create_snapshot(description)
        
        if success:
            logger.info("End-of-day snapshot created")
        else:
            logger.error("Failed to create snapshot")
        
        return success
```

### Update InfluxDB Integration

The enhanced state manager integrates seamlessly with your existing InfluxDB setup:

```python
# In your InfluxDBManager class

class EnhancedInfluxDBManager(InfluxDBManager):
    def __init__(self, config: TradingConfig):
        super().__init__(config)
        
        # Add state manager integration
        self.state_manager = get_state_manager()
        
    def store_trade_data(self, trade_data: Dict[str, Any]):
        """Enhanced trade data storage with state management."""
        # Store in InfluxDB as before
        super().store_trade_data(trade_data)
        
        # Also store in state management system for versioning
        if 'pair' in trade_data:
            self.state_manager.save_position(
                pair=trade_data['pair'],
                position_data=trade_data,
                user_id="influxdb_integration",
                description="Trade executed"
            )
```

## 🌐 REST API Usage

### Start API Server

```python
from utils.unified_state_manager import UnifiedStateManager

# Start API server
manager = UnifiedStateManager()
manager.initialize()
manager.start_api_server_sync()  # Starts in background thread
```

### API Endpoints

Once running, the API provides these endpoints:

```bash
# Health check
GET /health

# Create/Update state
POST /states
{
  "state_id": "position_EURUSD",
  "state_type": "position",
  "data": {"direction": "long", "quantity": 100000},
  "description": "New position opened"
}

# Get latest state
GET /states/position_EURUSD?state_type=position

# Get state history
GET /states/position_EURUSD/history?limit=10

# Create snapshot
POST /snapshots
{
  "snapshot_id": "daily_snapshot_20250118",
  "description": "Daily trading snapshot"
}

# WebSocket for real-time updates
WS /ws
```

### WebSocket Real-time Updates

```javascript
// JavaScript client example
const ws = new WebSocket('ws://localhost:8000/ws');

// Subscribe to position updates
ws.send(JSON.stringify({
    action: 'subscribe',
    topic: 'states:position'
}));

// Receive real-time updates
ws.onmessage = function(event) {
    const update = JSON.parse(event.data);
    console.log('State update:', update);
};
```

## 📊 Monitoring and Analytics

### System Health Monitoring

```python
# Check system health
manager = get_state_manager()
health_ok = manager.health_check()
status = manager.get_system_status()

print(f"System healthy: {health_ok}")
print(f"Active positions: {status['active_positions']}")
print(f"Database type: {status['database_type']}")
```

### State Analytics

```python
# Get comprehensive state history
history = manager.get_state_history(
    state_type="position",
    limit=1000
)

# Analyze position changes over time
for entry in history:
    print(f"{entry['timestamp']}: {entry['description']}")

# Get all current positions
positions = manager.get_all_positions()
print(f"Total active positions: {len(positions)}")
```

## 🛡️ Data Validation

The system includes built-in schema validation:

```python
# Position schema validation
from utils.state_database import PositionSchema, ValidationError

try:
    position = PositionSchema(
        symbol1="EUR",
        symbol2="USD", 
        direction="long",
        entry_time=datetime.now(),
        quantity=100000,
        entry_price=1.0985
    )
    print("Position data valid")
except ValidationError as e:
    print(f"Validation error: {e}")
```

## 🔒 Security Considerations

### API Security

```python
# Enable API key authentication
manager = UnifiedStateManager()
manager.config.api.api_key = "your-secure-api-key"

# Configure CORS for production
manager.config.api.cors_origins = ["https://your-trading-dashboard.com"]
```

### Database Security

```bash
# Use environment variables for sensitive data
export TRADING_STATE_INFLUXDB_TOKEN="secure-token-here"
export TRADING_STATE_POSTGRES_PASSWORD="secure-password-here"
```

## 🧪 Testing

### Unit Tests

```python
import unittest
from utils.unified_state_manager import UnifiedStateManager

class TestStateManager(unittest.TestCase):
    def setUp(self):
        self.manager = UnifiedStateManager()
        self.manager.initialize()
    
    def test_save_and_load_state(self):
        positions = {"EURUSD": {"direction": "long"}}
        pair_states = {"EURUSD": {"position": "long"}}
        
        # Save state
        success = self.manager.save_trading_state(positions, pair_states)
        self.assertTrue(success)
        
        # Load state
        loaded = self.manager.load_trading_state()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded['active_positions'], positions)
    
    def tearDown(self):
        self.manager.shutdown()

if __name__ == '__main__':
    unittest.main()
```

## 🔧 Troubleshooting

### Common Issues

1. **Database Connection Failed**
   ```python
   # Check database configuration
   manager = get_state_manager()
   status = manager.get_system_status()
   print(status)
   
   # Verify connection settings
   print(manager.config.database.get_connection_config())
   ```

2. **Migration Issues**
   ```python
   # Validate migration
   from utils.state_migration import validate_migration
   
   result = validate_migration("old_state.json", manager.enhanced_manager)
   print(f"Migration valid: {result['valid']}")
   print(f"Issues: {result['issues']}")
   ```

3. **Performance Issues**
   ```python
   # Enable caching
   manager.config.cache.enabled = True
   manager.config.cache.ttl_seconds = 600  # 10 minutes
   
   # Check cache hit rate in logs
   ```

### Debug Logging

```python
import logging

# Enable debug logging
logging.getLogger('utils.state_database').setLevel(logging.DEBUG)
logging.getLogger('utils.enhanced_state_manager').setLevel(logging.DEBUG)
```

## 📈 Performance Optimization

### Recommended Settings

```python
# Production configuration
config = StateManagementConfig(
    database=DatabaseConfig(
        database_type='postgresql',  # Better for high-frequency updates
        connection_timeout=60,
        retry_attempts=5
    ),
    cache=CacheConfig(
        enabled=True,
        ttl_seconds=600,  # 10 minutes
        max_size=10000
    ),
    enable_file_fallback=False,  # Disable for production
    backup_retention_days=30
)
```

### Batch Operations

```python
# Use batch operations for bulk updates
positions = get_multiple_positions()
pair_states = get_multiple_pair_states()

# Single operation vs multiple individual saves
success = manager.save_trading_state(positions, pair_states)
```

## 🤝 Contributing

This state management system is designed to be extensible. To add new database adapters:

1. Inherit from `DatabaseAdapter`
2. Implement all abstract methods
3. Register in `create_database_adapter` function
4. Add configuration support in `DatabaseConfig`

## 📄 License

[Add your license information here]

## 🆘 Support

For issues or questions:
1. Check the troubleshooting section
2. Review logs for error details
3. Verify configuration settings
4. Test with minimal configuration

---

This enhanced state management system provides a robust, scalable foundation for your trading system while maintaining full backward compatibility with your existing code.
