"""
Database-Agnostic State Management System for Trading System

This module provides a comprehensive, database-backed state management system
with versioning, schema validation, and API accessibility.
"""

import os
import json
import logging
import asyncio
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Union, Type, Protocol
from datetime import datetime, timezone
from dataclasses import dataclass, asdict
from enum import Enum
import threading
from contextlib import asynccontextmanager

# Schema validation
try:
    from pydantic import BaseModel, Field, ValidationError
    from pydantic.json import pydantic_encoder
    PYDANTIC_AVAILABLE = True
except ImportError:
    PYDANTIC_AVAILABLE = False
    BaseModel = object
    Field = lambda *args, **kwargs: None
    ValidationError = Exception

logger = logging.getLogger(__name__)


class StateOperationType(Enum):
    """Types of state operations for versioning."""
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    SNAPSHOT = "snapshot"
    RESTORE = "restore"


class StateType(Enum):
    """Types of trading states."""
    POSITION = "position"
    PAIR_STATE = "pair_state"
    PORTFOLIO = "portfolio"
    STRATEGY = "strategy"
    CONFIGURATION = "configuration"
    MARKET_DATA = "market_data"


@dataclass
class StateVersion:
    """Represents a version of a state."""
    version_id: str
    state_id: str
    state_type: StateType
    operation: StateOperationType
    data: Dict[str, Any]
    timestamp: datetime
    user_id: Optional[str] = None
    description: Optional[str] = None
    checksum: Optional[str] = None


@dataclass
class StateQuery:
    """Query parameters for state retrieval."""
    state_id: Optional[str] = None
    state_type: Optional[StateType] = None
    version_id: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    limit: Optional[int] = None
    user_id: Optional[str] = None


# Schema definitions for validation
if PYDANTIC_AVAILABLE:
    class PositionSchema(BaseModel):
        """Schema for position states."""
        symbol1: str = Field(..., description="First symbol in the pair")
        symbol2: str = Field(..., description="Second symbol in the pair")
        direction: str = Field(..., pattern="^(long|short)$")
        entry_time: datetime
        quantity: float = Field(..., gt=0)
        entry_price: float = Field(..., gt=0)
        stop_loss: Optional[float] = Field(None, gt=0)
        take_profit: Optional[float] = Field(None, gt=0)
        
    class PairStateSchema(BaseModel):
        """Schema for pair states."""
        symbol1: str
        symbol2: str
        position: str = Field(..., pattern="^(none|long|short)$")
        cooldown: int = Field(0, ge=0)
        last_update: datetime
        spread: Optional[float] = None
        z_score: Optional[float] = None
        
    class PortfolioSchema(BaseModel):
        """Schema for portfolio states."""
        total_value: float = Field(..., ge=0)
        available_balance: float = Field(..., ge=0)
        total_pnl: float
        open_positions: int = Field(..., ge=0)
        daily_pnl: float
        peak_value: Optional[float] = Field(None, ge=0)
        
    SCHEMA_REGISTRY = {
        StateType.POSITION: PositionSchema,
        StateType.PAIR_STATE: PairStateSchema,
        StateType.PORTFOLIO: PortfolioSchema,
    }
else:
    SCHEMA_REGISTRY = {}


class DatabaseAdapter(ABC):
    """Abstract base class for database adapters."""
    
    @abstractmethod
    async def connect(self) -> bool:
        """Connect to the database."""
        pass
    
    @abstractmethod
    async def disconnect(self):
        """Disconnect from the database."""
        pass
    
    @abstractmethod
    async def save_state_version(self, version: StateVersion) -> bool:
        """Save a state version to the database."""
        pass
    
    @abstractmethod
    async def get_latest_state(self, state_id: str, state_type: StateType) -> Optional[StateVersion]:
        """Get the latest version of a state."""
        pass
    
    @abstractmethod
    async def get_state_version(self, version_id: str) -> Optional[StateVersion]:
        """Get a specific state version."""
        pass
    
    @abstractmethod
    async def get_state_history(self, query: StateQuery) -> List[StateVersion]:
        """Get state history based on query parameters."""
        pass
    
    @abstractmethod
    async def delete_state(self, state_id: str, state_type: StateType) -> bool:
        """Delete all versions of a state."""
        pass
    
    @abstractmethod
    async def create_snapshot(self, snapshot_id: str, description: str = None) -> bool:
        """Create a snapshot of all current states."""
        pass
    
    @abstractmethod
    async def restore_snapshot(self, snapshot_id: str) -> bool:
        """Restore states from a snapshot."""
        pass


class InfluxDBAdapter(DatabaseAdapter):
    """InfluxDB adapter for state management."""
    
    def __init__(self, url: str, token: str, org: str, bucket: str):
        self.url = url
        self.token = token
        self.org = org
        self.bucket = bucket
        self.client = None
        self.write_api = None
        self.query_api = None
        
    async def connect(self) -> bool:
        """Connect to InfluxDB."""
        try:
            from influxdb_client import InfluxDBClient
            from influxdb_client.client.write_api import ASYNCHRONOUS
            
            self.client = InfluxDBClient(url=self.url, token=self.token, org=self.org)
            self.write_api = self.client.write_api(write_options=ASYNCHRONOUS)
            self.query_api = self.client.query_api()
            
            # Test connection
            await asyncio.to_thread(self.client.ping)
            logger.info("Connected to InfluxDB successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to InfluxDB: {e}")
            return False
    
    async def disconnect(self):
        """Disconnect from InfluxDB."""
        if self.client:
            self.client.close()
            logger.info("Disconnected from InfluxDB")
    
    async def save_state_version(self, version: StateVersion) -> bool:
        """Save a state version to InfluxDB."""
        try:
            from influxdb_client import Point
            
            point = (Point("trading_state")
                    .tag("state_id", version.state_id)
                    .tag("state_type", version.state_type.value)
                    .tag("operation", version.operation.value)
                    .tag("version_id", version.version_id)
                    .field("data", json.dumps(version.data, default=str))
                    .field("description", version.description or "")
                    .field("user_id", version.user_id or "")
                    .field("checksum", version.checksum or "")
                    .time(version.timestamp))
            
            await asyncio.to_thread(self.write_api.write, bucket=self.bucket, record=point)
            return True
            
        except Exception as e:
            logger.error(f"Failed to save state version to InfluxDB: {e}")
            return False
    
    async def get_latest_state(self, state_id: str, state_type: StateType) -> Optional[StateVersion]:
        """Get the latest version of a state from InfluxDB."""
        try:
            # Use pivoting to get all fields in one record
            query = f'''
            from(bucket: "{self.bucket}")
                |> range(start: -30d)
                |> filter(fn: (r) => r._measurement == "trading_state")
                |> filter(fn: (r) => r.state_id == "{state_id}")
                |> filter(fn: (r) => r.state_type == "{state_type.value}")
                |> pivot(rowKey: ["_time", "state_id", "state_type", "operation", "version_id"], 
                         columnKey: ["_field"], 
                         valueColumn: "_value")
                |> sort(columns: ["_time"], desc: true)
                |> limit(n: 1)
            '''
            
            result = await asyncio.to_thread(self.query_api.query, query)
            
            if result:
                for table in result:
                    for record in table.records:
                        return self._record_to_state_version(record)
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get latest state from InfluxDB: {e}")
            return None
    
    async def get_state_version(self, version_id: str) -> Optional[StateVersion]:
        """Get a specific state version from InfluxDB."""
        try:
            # Use pivoting to get all fields in one record
            query = f'''
            from(bucket: "{self.bucket}")
                |> range(start: -90d)
                |> filter(fn: (r) => r._measurement == "trading_state")
                |> filter(fn: (r) => r.version_id == "{version_id}")
                |> pivot(rowKey: ["_time", "state_id", "state_type", "operation", "version_id"], 
                         columnKey: ["_field"], 
                         valueColumn: "_value")
                |> limit(n: 1)
            '''
            
            result = await asyncio.to_thread(self.query_api.query, query)
            
            if result:
                for table in result:
                    for record in table.records:
                        return self._record_to_state_version(record)
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get state version from InfluxDB: {e}")
            return None
    
    async def get_state_history(self, query: StateQuery) -> List[StateVersion]:
        """Get state history from InfluxDB."""
        try:
            # Use a more comprehensive query that properly selects all fields
            flux_query = f'''
            from(bucket: "{self.bucket}")
                |> range(start: -90d)
                |> filter(fn: (r) => r._measurement == "trading_state")
            '''
            
            if query.state_id:
                flux_query += f' |> filter(fn: (r) => r.state_id == "{query.state_id}")'
            if query.state_type:
                flux_query += f' |> filter(fn: (r) => r.state_type == "{query.state_type.value}")'
            if query.user_id:
                flux_query += f' |> filter(fn: (r) => r.user_id == "{query.user_id}")'
            
            # Pivot to get all fields in one record per state version
            flux_query += '''
                |> pivot(rowKey: ["_time", "state_id", "state_type", "operation", "version_id"], 
                         columnKey: ["_field"], 
                         valueColumn: "_value")
                |> sort(columns: ["_time"], desc: true)
            '''
            
            if query.limit:
                flux_query += f' |> limit(n: {query.limit})'
            
            result = await asyncio.to_thread(self.query_api.query, flux_query)
            
            versions = []
            if result:
                for table in result:
                    for record in table.records:
                        version = self._record_to_state_version(record)
                        if version:
                            versions.append(version)
            
            return versions
            
        except Exception as e:
            logger.error(f"Failed to get state history from InfluxDB: {e}")
            return []
    
    async def delete_state(self, state_id: str, state_type: StateType) -> bool:
        """Delete all versions of a state from InfluxDB."""
        try:
            # InfluxDB doesn't support direct deletes, so we mark as deleted
            delete_version = StateVersion(
                version_id=f"{state_id}_deleted_{datetime.now().isoformat()}",
                state_id=state_id,
                state_type=state_type,
                operation=StateOperationType.DELETE,
                data={"deleted": True},
                timestamp=datetime.now(timezone.utc)
            )
            
            return await self.save_state_version(delete_version)
            
        except Exception as e:
            logger.error(f"Failed to delete state from InfluxDB: {e}")
            return False
    
    async def create_snapshot(self, snapshot_id: str, description: str = None) -> bool:
        """Create a snapshot in InfluxDB."""
        try:
            snapshot_version = StateVersion(
                version_id=snapshot_id,
                state_id="SNAPSHOT",
                state_type=StateType.CONFIGURATION,
                operation=StateOperationType.SNAPSHOT,
                data={"snapshot_id": snapshot_id, "description": description},
                timestamp=datetime.now(timezone.utc),
                description=description
            )
            
            return await self.save_state_version(snapshot_version)
            
        except Exception as e:
            logger.error(f"Failed to create snapshot in InfluxDB: {e}")
            return False
    
    async def restore_snapshot(self, snapshot_id: str) -> bool:
        """Restore from snapshot in InfluxDB."""
        try:
            # Implementation would involve querying states at snapshot time
            # and recreating them with restore operation
            logger.warning("Snapshot restore not fully implemented for InfluxDB")
            return False
            
        except Exception as e:
            logger.error(f"Failed to restore snapshot from InfluxDB: {e}")
            return False
    
    def _record_to_state_version(self, record) -> Optional[StateVersion]:
        """Convert InfluxDB record to StateVersion."""
        try:
            # After pivoting, the fields should now be accessible as direct properties
            # The pivot operation converts field names to column names
            
            # Debug the record structure first
            logger.debug(f"🔍 Raw InfluxDB record structure:")
            logger.debug(f"   Record type: {type(record)}")
            if hasattr(record, 'values') and record.values:
                logger.debug(f"   Record.values keys: {list(record.values.keys())}")
                logger.debug(f"   Record.values: {record.values}")
            
            # Extract field values - after pivot, these should be direct properties
            data_str = None
            description = None
            user_id = None
            checksum = None
            
            if hasattr(record, 'values') and record.values:
                values = record.values
                # After pivot, field data should be accessible as direct keys
                data_str = values.get('data')
                description = values.get('description', '')
                user_id = values.get('user_id', '')
                checksum = values.get('checksum', '')
                logger.debug(f"✅ Accessed pivoted fields from record.values")
            
            # If that doesn't work, try alternative access patterns
            if not data_str:
                # Method 1: Try accessing fields directly as record attributes
                data_str = getattr(record, 'data', None)
                description = getattr(record, 'description', None)
                user_id = getattr(record, 'user_id', None)
                checksum = getattr(record, 'checksum', None)
                if data_str:
                    logger.debug(f"✅ Accessed fields via direct attributes")
            
            # Extract tag values (these remain as tags and should be accessible)
            state_id = None
            state_type_str = None
            operation_str = None
            version_id = None
            
            if hasattr(record, 'values') and record.values:
                values = record.values
                # Tags should still be accessible from values
                state_id = values.get('state_id')
                state_type_str = values.get('state_type')
                operation_str = values.get('operation')
                version_id = values.get('version_id')
            
            # Fallback: try to get tags from record attributes
            if not state_id:
                state_id = getattr(record, 'state_id', None)
                state_type_str = getattr(record, 'state_type', None)
                operation_str = getattr(record, 'operation', None)
                version_id = getattr(record, 'version_id', None)
            
            # Debug logging for troubleshooting  
            logger.debug(f"🔍 InfluxDB record parsing results:")
            logger.debug(f"   state_id: {state_id}")
            logger.debug(f"   state_type: {state_type_str}")
            logger.debug(f"   operation: {operation_str}")
            logger.debug(f"   version_id: {version_id}")
            logger.debug(f"   data_str length: {len(data_str) if data_str else 0}")
            if data_str:
                logger.debug(f"   data_str sample: {data_str[:100]}...")
            
            # Validate required fields
            if not all([state_id, state_type_str, operation_str, version_id]):
                logger.warning(f"❌ Missing required tag fields in InfluxDB record:")
                logger.warning(f"   state_id: {state_id}")
                logger.warning(f"   state_type: {state_type_str}")
                logger.warning(f"   operation: {operation_str}")
                logger.warning(f"   version_id: {version_id}")
                if hasattr(record, 'values'):
                    logger.warning(f"   Available keys: {list(record.values.keys()) if record.values else 'None'}")
                return None
            
            if not data_str:
                logger.warning(f"❌ No data field found in InfluxDB record for {state_id}")
                if hasattr(record, 'values') and record.values:
                    logger.warning(f"   Available values keys: {list(record.values.keys())}")
                return None
            
            # Parse the data JSON
            try:
                parsed_data = json.loads(data_str) if data_str else {}
            except json.JSONDecodeError as e:
                logger.error(f"❌ Failed to parse JSON data for {state_id}: {e}")
                logger.error(f"   Raw data: {data_str[:100]}..." if len(data_str) > 100 else f"   Raw data: {data_str}")
                return None
            
            # Create StateVersion object
            version = StateVersion(
                version_id=version_id,
                state_id=state_id,
                state_type=StateType(state_type_str),
                operation=StateOperationType(operation_str),
                data=parsed_data,
                timestamp=record.get_time(),
                user_id=user_id,
                description=description,
                checksum=checksum
            )
            
            logger.debug(f"✅ Successfully parsed InfluxDB record for {state_id}")
            return version
            
        except Exception as e:
            logger.error(f"Failed to convert record to StateVersion: {e}")
            logger.error(f"Record type: {type(record)}")
            if hasattr(record, 'values'):
                logger.error(f"Record values: {record.values}")
            if hasattr(record, '__dict__'):
                logger.error(f"Record dict: {record.__dict__}")
            return None


class PostgreSQLAdapter(DatabaseAdapter):
    """PostgreSQL adapter for state management."""
    
    def __init__(self, connection_string: str):
        self.connection_string = connection_string
        self.pool = None
        
    async def connect(self) -> bool:
        """Connect to PostgreSQL."""
        try:
            import asyncpg
            
            self.pool = await asyncpg.create_pool(self.connection_string)
            
            # Create tables if they don't exist
            await self._create_tables()
            
            logger.info("Connected to PostgreSQL successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to connect to PostgreSQL: {e}")
            return False
    
    async def disconnect(self):
        """Disconnect from PostgreSQL."""
        if self.pool:
            await self.pool.close()
            logger.info("Disconnected from PostgreSQL")
    
    async def _create_tables(self):
        """Create necessary tables."""
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS trading_states (
            version_id VARCHAR(255) PRIMARY KEY,
            state_id VARCHAR(255) NOT NULL,
            state_type VARCHAR(50) NOT NULL,
            operation VARCHAR(50) NOT NULL,
            data JSONB NOT NULL,
            timestamp TIMESTAMPTZ NOT NULL,
            user_id VARCHAR(255),
            description TEXT,
            checksum VARCHAR(255),
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        
        CREATE INDEX IF NOT EXISTS idx_trading_states_state_id ON trading_states(state_id);
        CREATE INDEX IF NOT EXISTS idx_trading_states_state_type ON trading_states(state_type);
        CREATE INDEX IF NOT EXISTS idx_trading_states_timestamp ON trading_states(timestamp);
        """
        
        async with self.pool.acquire() as conn:
            await conn.execute(create_table_sql)
    
    async def save_state_version(self, version: StateVersion) -> bool:
        """Save a state version to PostgreSQL."""
        try:
            insert_sql = """
            INSERT INTO trading_states 
            (version_id, state_id, state_type, operation, data, timestamp, user_id, description, checksum)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """
            
            async with self.pool.acquire() as conn:
                await conn.execute(
                    insert_sql,
                    version.version_id,
                    version.state_id,
                    version.state_type.value,
                    version.operation.value,
                    json.dumps(version.data, default=str),
                    version.timestamp,
                    version.user_id,
                    version.description,
                    version.checksum
                )
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to save state version to PostgreSQL: {e}")
            return False
    
    async def get_latest_state(self, state_id: str, state_type: StateType) -> Optional[StateVersion]:
        """Get the latest version of a state from PostgreSQL."""
        try:
            query_sql = """
            SELECT * FROM trading_states 
            WHERE state_id = $1 AND state_type = $2 
            ORDER BY timestamp DESC 
            LIMIT 1
            """
            
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(query_sql, state_id, state_type.value)
                
                if row:
                    return self._row_to_state_version(row)
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get latest state from PostgreSQL: {e}")
            return None
    
    async def get_state_version(self, version_id: str) -> Optional[StateVersion]:
        """Get a specific state version from PostgreSQL."""
        try:
            query_sql = "SELECT * FROM trading_states WHERE version_id = $1"
            
            async with self.pool.acquire() as conn:
                row = await conn.fetchrow(query_sql, version_id)
                
                if row:
                    return self._row_to_state_version(row)
            
            return None
            
        except Exception as e:
            logger.error(f"Failed to get state version from PostgreSQL: {e}")
            return None
    
    async def get_state_history(self, query: StateQuery) -> List[StateVersion]:
        """Get state history from PostgreSQL."""
        try:
            conditions = []
            params = []
            param_count = 0
            
            if query.state_id:
                param_count += 1
                conditions.append(f"state_id = ${param_count}")
                params.append(query.state_id)
            
            if query.state_type:
                param_count += 1
                conditions.append(f"state_type = ${param_count}")
                params.append(query.state_type.value)
            
            if query.user_id:
                param_count += 1
                conditions.append(f"user_id = ${param_count}")
                params.append(query.user_id)
            
            if query.start_time:
                param_count += 1
                conditions.append(f"timestamp >= ${param_count}")
                params.append(query.start_time)
            
            if query.end_time:
                param_count += 1
                conditions.append(f"timestamp <= ${param_count}")
                params.append(query.end_time)
            
            where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""
            limit_clause = f"LIMIT {query.limit}" if query.limit else ""
            
            query_sql = f"""
            SELECT * FROM trading_states 
            {where_clause}
            ORDER BY timestamp DESC 
            {limit_clause}
            """
            
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(query_sql, *params)
                
                return [self._row_to_state_version(row) for row in rows]
            
        except Exception as e:
            logger.error(f"Failed to get state history from PostgreSQL: {e}")
            return []
    
    async def delete_state(self, state_id: str, state_type: StateType) -> bool:
        """Delete all versions of a state from PostgreSQL."""
        try:
            delete_sql = "DELETE FROM trading_states WHERE state_id = $1 AND state_type = $2"
            
            async with self.pool.acquire() as conn:
                await conn.execute(delete_sql, state_id, state_type.value)
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to delete state from PostgreSQL: {e}")
            return False
    
    async def create_snapshot(self, snapshot_id: str, description: str = None) -> bool:
        """Create a snapshot in PostgreSQL."""
        try:
            # Get all latest states
            query_sql = """
            SELECT DISTINCT ON (state_id, state_type) *
            FROM trading_states 
            WHERE operation != 'delete'
            ORDER BY state_id, state_type, timestamp DESC
            """
            
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(query_sql)
                
                # Create snapshot record
                snapshot_version = StateVersion(
                    version_id=snapshot_id,
                    state_id="SNAPSHOT",
                    state_type=StateType.CONFIGURATION,
                    operation=StateOperationType.SNAPSHOT,
                    data={
                        "snapshot_id": snapshot_id,
                        "description": description,
                        "state_count": len(rows)
                    },
                    timestamp=datetime.now(timezone.utc),
                    description=description
                )
                
                success = await self.save_state_version(snapshot_version)
                
                # Tag all current states with snapshot_id
                if success:
                    for row in rows:
                        data = json.loads(row['data'])
                        data['_snapshot_id'] = snapshot_id
                        
                        snapshot_state = StateVersion(
                            version_id=f"{snapshot_id}_{row['state_id']}_{row['state_type']}",
                            state_id=row['state_id'],
                            state_type=StateType(row['state_type']),
                            operation=StateOperationType.SNAPSHOT,
                            data=data,
                            timestamp=datetime.now(timezone.utc),
                            user_id=row['user_id'],
                            description=f"Snapshot: {description}"
                        )
                        
                        await self.save_state_version(snapshot_state)
                
                return success
            
        except Exception as e:
            logger.error(f"Failed to create snapshot in PostgreSQL: {e}")
            return False
    
    async def restore_snapshot(self, snapshot_id: str) -> bool:
        """Restore from snapshot in PostgreSQL."""
        try:
            # Get all states from snapshot
            query_sql = """
            SELECT * FROM trading_states 
            WHERE operation = 'snapshot' AND data::jsonb ? '_snapshot_id' 
            AND data::jsonb ->> '_snapshot_id' = $1
            """
            
            async with self.pool.acquire() as conn:
                rows = await conn.fetch(query_sql, snapshot_id)
                
                for row in rows:
                    if row['state_id'] == 'SNAPSHOT':
                        continue
                    
                    # Restore each state
                    data = json.loads(row['data'])
                    data.pop('_snapshot_id', None)
                    
                    restore_version = StateVersion(
                        version_id=f"restore_{snapshot_id}_{row['state_id']}_{datetime.now().isoformat()}",
                        state_id=row['state_id'],
                        state_type=StateType(row['state_type']),
                        operation=StateOperationType.RESTORE,
                        data=data,
                        timestamp=datetime.now(timezone.utc),
                        description=f"Restored from snapshot: {snapshot_id}"
                    )
                    
                    await self.save_state_version(restore_version)
                
                return True
            
        except Exception as e:
            logger.error(f"Failed to restore snapshot from PostgreSQL: {e}")
            return False
    
    def _row_to_state_version(self, row) -> StateVersion:
        """Convert database row to StateVersion."""
        return StateVersion(
            version_id=row['version_id'],
            state_id=row['state_id'],
            state_type=StateType(row['state_type']),
            operation=StateOperationType(row['operation']),
            data=json.loads(row['data']),
            timestamp=row['timestamp'],
            user_id=row['user_id'],
            description=row['description'],
            checksum=row['checksum']
        )


class DatabaseStateManager:
    """
    Advanced database-backed state manager with versioning, validation, and API access.
    """
    
    def __init__(self, adapter: DatabaseAdapter, enable_validation: bool = True):
        self.adapter = adapter
        self.enable_validation = enable_validation
        self._lock = threading.RLock()
        self._connected = False
        
    async def initialize(self) -> bool:
        """Initialize the state manager."""
        try:
            self._connected = await self.adapter.connect()
            if self._connected:
                logger.info("Database state manager initialized successfully")
            return self._connected
        except Exception as e:
            logger.error(f"Failed to initialize database state manager: {e}")
            return False
    
    async def cleanup(self):
        """Cleanup resources."""
        await self.adapter.disconnect()
        self._connected = False
    
    def _validate_state(self, state_type: StateType, data: Dict[str, Any]) -> bool:
        """Validate state data against schema."""
        if not self.enable_validation or not PYDANTIC_AVAILABLE:
            return True
        
        schema_class = SCHEMA_REGISTRY.get(state_type)
        if not schema_class:
            logger.warning(f"No schema found for state type: {state_type}")
            return True
        
        try:
            schema_class(**data)
            return True
        except ValidationError as e:
            logger.error(f"State validation failed for {state_type}: {e}")
            logger.debug(f"Failed data: {data}")
            
            # For position validation failures, log helpful debugging info
            if state_type == StateType.POSITION:
                logger.debug("Position validation requirements:")
                logger.debug("  - symbol1: string (required)")
                logger.debug("  - symbol2: string (required)")
                logger.debug("  - direction: 'long' or 'short' (required)")
                logger.debug("  - entry_time: datetime (required)")
                logger.debug("  - quantity: float > 0 (required)")
                logger.debug("  - entry_price: float > 0 (required)")
                logger.debug("  - stop_loss: float > 0 (optional)")
                logger.debug("  - take_profit: float > 0 (optional)")
            
            return False
    
    def _generate_version_id(self, state_id: str, operation: StateOperationType) -> str:
        """Generate a unique version ID."""
        timestamp = datetime.now().isoformat()
        return f"{state_id}_{operation.value}_{timestamp}"
    
    def _calculate_checksum(self, data: Dict[str, Any]) -> str:
        """Calculate checksum for data integrity."""
        import hashlib
        data_str = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(data_str.encode()).hexdigest()
    
    async def save_state(self, 
                        state_id: str,
                        state_type: StateType,
                        data: Dict[str, Any],
                        operation: StateOperationType = StateOperationType.UPDATE,
                        user_id: Optional[str] = None,
                        description: Optional[str] = None) -> Optional[str]:
        """
        Save a state with versioning.
        
        Returns:
            Version ID if successful, None otherwise
        """
        if not self._connected:
            logger.error("Database not connected")
            return None
        
        # Validate data
        if not self._validate_state(state_type, data):
            logger.warning(f"State validation failed for {state_id}, attempting to save anyway for trading continuity")
            # In trading scenarios, we want to continue even if validation fails
            # This ensures trading is not interrupted by schema issues
        
        with self._lock:
            try:
                version_id = self._generate_version_id(state_id, operation)
                checksum = self._calculate_checksum(data)
                
                version = StateVersion(
                    version_id=version_id,
                    state_id=state_id,
                    state_type=state_type,
                    operation=operation,
                    data=data,
                    timestamp=datetime.now(timezone.utc),
                    user_id=user_id,
                    description=description,
                    checksum=checksum
                )
                
                success = await self.adapter.save_state_version(version)
                
                if success:
                    logger.debug(f"Saved state version: {version_id}")
                    return version_id
                else:
                    logger.error(f"Failed to save state version: {version_id}")
                    return None
                
            except Exception as e:
                logger.error(f"Error saving state: {e}")
                return None
    
    async def load_state(self, 
                        state_id: str,
                        state_type: StateType,
                        version_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Load a state (latest version by default).
        
        Args:
            state_id: State identifier
            state_type: Type of state
            version_id: Specific version to load (optional)
            
        Returns:
            State data if found, None otherwise
        """
        if not self._connected:
            logger.error("Database not connected")
            return None
        
        try:
            if version_id:
                version = await self.adapter.get_state_version(version_id)
            else:
                version = await self.adapter.get_latest_state(state_id, state_type)
            
            if version and version.operation != StateOperationType.DELETE:
                # Verify checksum if available
                if version.checksum:
                    expected_checksum = self._calculate_checksum(version.data)
                    if expected_checksum != version.checksum:
                        logger.warning(f"Checksum mismatch for state {state_id}")
                
                return version.data
            
            return None
            
        except Exception as e:
            logger.error(f"Error loading state: {e}")
            return None
    
    async def get_state_history(self, 
                               state_id: Optional[str] = None,
                               state_type: Optional[StateType] = None,
                               limit: Optional[int] = None,
                               start_time: Optional[datetime] = None,
                               end_time: Optional[datetime] = None) -> List[StateVersion]:
        """Get state history with optional filtering."""
        if not self._connected:
            logger.error("Database not connected")
            return []
        
        try:
            query = StateQuery(
                state_id=state_id,
                state_type=state_type,
                limit=limit,
                start_time=start_time,
                end_time=end_time
            )
            
            return await self.adapter.get_state_history(query)
            
        except Exception as e:
            logger.error(f"Error getting state history: {e}")
            return []
    
    async def delete_state(self, state_id: str, state_type: StateType) -> bool:
        """Delete a state (marks as deleted)."""
        if not self._connected:
            logger.error("Database not connected")
            return False
        
        try:
            return await self.adapter.delete_state(state_id, state_type)
        except Exception as e:
            logger.error(f"Error deleting state: {e}")
            return False
    
    async def create_snapshot(self, snapshot_id: str, description: str = None) -> bool:
        """Create a snapshot of all current states."""
        if not self._connected:
            logger.error("Database not connected")
            return False
        
        try:
            return await self.adapter.create_snapshot(snapshot_id, description)
        except Exception as e:
            logger.error(f"Error creating snapshot: {e}")
            return False
    
    async def restore_snapshot(self, snapshot_id: str) -> bool:
        """Restore states from a snapshot."""
        if not self._connected:
            logger.error("Database not connected")
            return False
        
        try:
            return await self.adapter.restore_snapshot(snapshot_id)
        except Exception as e:
            logger.error(f"Error restoring snapshot: {e}")
            return False
    
    @asynccontextmanager
    async def transaction(self):
        """Context manager for transactional operations (if supported by adapter)."""
        # Basic implementation - adapters can override for true transactions
        yield self


def create_database_adapter(database_type: str, **config) -> DatabaseAdapter:
    """Factory function to create database adapters."""
    database_type = database_type.lower()
    
    if database_type == 'influxdb':
        return InfluxDBAdapter(
            url=config.get('url', 'http://localhost:8086'),
            token=config.get('token', ''),
            org=config.get('org', ''),
            bucket=config.get('bucket', 'trading-states')
        )
    elif database_type == 'postgresql':
        return PostgreSQLAdapter(
            connection_string=config.get('connection_string', '')
        )
    else:
        raise ValueError(f"Unsupported database type: {database_type}")


async def create_state_manager(database_type: str, **config) -> DatabaseStateManager:
    """Factory function to create and initialize a database state manager."""
    adapter = create_database_adapter(database_type, **config)
    manager = DatabaseStateManager(adapter)
    
    success = await manager.initialize()
    if not success:
        raise RuntimeError(f"Failed to initialize {database_type} state manager")
    
    return manager
