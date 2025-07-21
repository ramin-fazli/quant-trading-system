# Trading State Restoration Guide

## Overview

The Enhanced Trading System V3 automatically saves and restores trading state to ensure continuity across restarts. This document explains how the state restoration works and how to manage it.

## Automatic State Management

### When State is Saved
1. **Trade Execution**: Every time a trade is executed
2. **Periodic Saves**: Every 5 minutes during live trading
3. **Graceful Shutdown**: When the system is properly stopped
4. **Manual Saves**: Using the state manager API

### What State is Saved
- **Open Positions**: All current trading positions with entry prices, quantities, timestamps
- **Pair States**: Strategy-specific state for each trading pair (cooldowns, z-scores, etc.)
- **Portfolio Summary**: Total value, P&L, position counts
- **System Configuration**: Data provider, broker, strategy, trading pairs
- **Session Metadata**: Last save time, system version, trading mode

### When State is Restored
- **System Startup**: Automatically during initialization
- **After Database Connection**: Once state manager connects to InfluxDB
- **Manual Restoration**: Using utility scripts

## State Restoration Process

### 1. Automatic Restoration (Default)

The system automatically restores state during initialization:

```python
# This happens automatically in main.py
system = EnhancedTradingSystemV3(data_provider='ctrader', broker='ctrader')
system.initialize()  # State restoration happens here
```

During restoration, you'll see logs like:
```
============================================================
RESTORING PREVIOUS TRADING STATE
============================================================
Found 3 stored positions
📊 Restored Positions:
  📈 BTCUSD-ETHUSD: long | Entry: 50000.0 | Qty: 0.1 | Time: 2025-07-20T08:00:00
  📈 EURUSD-GBPUSD: short | Entry: 1.2500 | Qty: 0.05 | Time: 2025-07-20T08:15:00
📊 Restored Pair States: 15 pairs
✅ Previous trading state successfully restored
```

### 2. Manual State Inspection

Check current state in the database:

```bash
cd /d/trading_system/pair_trading_system
python scripts/state_manager.py check
```

This shows:
- All stored positions
- Current trading state
- Pair states and cooldowns
- Portfolio summary

### 3. Manual Backup Creation

Create a backup of current state:

```bash
python scripts/state_manager.py backup
```

Creates a timestamped backup file in `state_fallback/` directory.

### 4. Manual State Restoration

Restore from a specific backup:

```bash
python scripts/state_manager.py restore --file state_fallback/trading_state_backup_20250720_080000.json
```

## State Storage Locations

### Primary Storage: InfluxDB
- **Database**: Enhanced state management system
- **Connection**: Configured via `.env` file
- **Tables**: `positions`, `trading_states`, `pair_states`

### Backup Storage: File System
- **Location**: `state_fallback/` directory
- **Format**: JSON files with timestamps
- **Auto-created**: During backup operations

## Configuration

### Environment Variables (.env)
```bash
# State Management Database
TRADING_STATE_DB_TYPE=influxdb
TRADING_STATE_INFLUXDB_URL=http://localhost:8086
TRADING_STATE_INFLUXDB_TOKEN=your-token
TRADING_STATE_INFLUXDB_ORG=quant-system
TRADING_STATE_INFLUXDB_BUCKET=quant-data

# Options
TRADING_STATE_ENABLE_VALIDATION=true
TRADING_STATE_ENABLE_FILE_FALLBACK=true
```

### State Manager Options
```python
# In main.py - these are set automatically
state_manager = UnifiedStateManager(
    auto_migrate=True,  # Migrate from legacy states
    enable_validation=True,  # Validate state schemas
    file_fallback=True  # Use file backup if DB fails
)
```

## Best Practices

### 1. Regular Monitoring
- Check state restoration logs during startup
- Monitor periodic save confirmations during live trading
- Use `scripts/state_manager.py check` for health checks

### 2. Backup Management
- Create manual backups before major changes
- Keep backup files in version control (optional)
- Test restoration process periodically

### 3. Troubleshooting

**State Not Restoring:**
1. Check InfluxDB connection in logs
2. Verify database credentials in `.env`
3. Run `python scripts/state_manager.py check`

**Schema Validation Errors:**
1. Check position data format in logs
2. Ensure all required fields are present
3. Validation errors don't stop trading (by design)

**Database Connection Issues:**
1. Verify InfluxDB is running: `docker ps` or service status
2. Test connection: `curl http://localhost:8086/health`
3. Check token permissions in InfluxDB admin panel

## Integration with Trading Strategies

### Strategy State Interface

Strategies can implement optional methods for state management:

```python
class CustomStrategy(BaseStrategy):
    def get_current_state(self):
        """Return strategy-specific state for saving"""
        return {
            'pair_states': self.pair_states,
            'indicators': self.current_indicators,
            'custom_data': self.strategy_data
        }
    
    def restore_state(self, metadata):
        """Restore strategy from saved metadata"""
        if 'strategy_metadata' in metadata:
            self.restore_indicators(metadata['strategy_metadata'])
    
    def get_state_metadata(self):
        """Return metadata for state saving"""
        return {
            'strategy_version': '1.0',
            'last_rebalance': self.last_rebalance_time,
            'risk_metrics': self.current_risk_metrics
        }
```

## Signal Handling

The system handles shutdown signals gracefully:

- **SIGINT (Ctrl+C)**: Saves state and shuts down cleanly
- **SIGTERM**: Graceful shutdown with state saving
- **Exception Handling**: State saved even on unexpected errors

## Advanced Usage

### Custom State Restoration
```python
# Custom restoration in your code
system = EnhancedTradingSystemV3(data_provider='ctrader', broker='ctrader')
system.initialize()

# Force state save
system.save_current_trading_state("Custom checkpoint")

# Manual state restoration (if needed)
system._restore_trading_state()
```

### State Validation
```python
# Check if restoration was successful
if system.state_manager:
    status = system.state_manager.get_system_status()
    positions = system.state_manager.get_all_positions()
    print(f"Restored {len(positions)} positions")
```

## Summary

The state restoration system ensures your trading operations continue seamlessly across restarts by:

1. **Automatically saving** state during trading operations
2. **Automatically restoring** state on system startup  
3. **Providing tools** for manual state management
4. **Handling errors gracefully** without stopping trading
5. **Supporting multiple storage backends** (InfluxDB primary, file backup)

This ensures you never lose track of your positions or trading state, even after unexpected shutdowns or system restarts.
