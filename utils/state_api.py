"""
REST API for Database State Management System

This module provides a FastAPI-based REST API for accessing and managing
trading states with full CRUD operations, versioning, and real-time updates.
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional, Union
from datetime import datetime
from contextlib import asynccontextmanager

try:
    from fastapi import FastAPI, HTTPException, Depends, Query, WebSocket, WebSocketDisconnect
    from fastapi.responses import JSONResponse
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel, Field
    import uvicorn
    FASTAPI_AVAILABLE = True
except ImportError:
    FASTAPI_AVAILABLE = False
    FastAPI = None
    HTTPException = None
    BaseModel = object

from .state_database import (
    DatabaseStateManager, StateType, StateOperationType, StateVersion, StateQuery,
    create_state_manager
)

logger = logging.getLogger(__name__)


# API Models
if FASTAPI_AVAILABLE:
    class StateCreateRequest(BaseModel):
        state_id: str = Field(..., description="Unique identifier for the state")
        state_type: StateType = Field(..., description="Type of state")
        data: Dict[str, Any] = Field(..., description="State data")
        operation: StateOperationType = Field(StateOperationType.UPDATE, description="Operation type")
        user_id: Optional[str] = Field(None, description="User ID for audit trail")
        description: Optional[str] = Field(None, description="Description of the change")
    
    class StateUpdateRequest(BaseModel):
        data: Dict[str, Any] = Field(..., description="Updated state data")
        user_id: Optional[str] = Field(None, description="User ID for audit trail")
        description: Optional[str] = Field(None, description="Description of the change")
    
    class StateResponse(BaseModel):
        state_id: str
        state_type: StateType
        data: Dict[str, Any]
        version_id: str
        timestamp: datetime
        user_id: Optional[str]
        description: Optional[str]
    
    class StateHistoryResponse(BaseModel):
        total_count: int
        versions: List[StateResponse]
    
    class SnapshotRequest(BaseModel):
        snapshot_id: str = Field(..., description="Unique identifier for the snapshot")
        description: Optional[str] = Field(None, description="Description of the snapshot")
    
    class SnapshotResponse(BaseModel):
        snapshot_id: str
        success: bool
        message: str
        timestamp: datetime


class ConnectionManager:
    """Manages WebSocket connections for real-time state updates."""
    
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self.subscriptions: Dict[str, List[WebSocket]] = {}
    
    async def connect(self, websocket: WebSocket):
        """Accept a new WebSocket connection."""
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"WebSocket connected. Total connections: {len(self.active_connections)}")
    
    def disconnect(self, websocket: WebSocket):
        """Remove a WebSocket connection."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        
        # Remove from subscriptions
        for topic, connections in self.subscriptions.items():
            if websocket in connections:
                connections.remove(websocket)
        
        logger.info(f"WebSocket disconnected. Total connections: {len(self.active_connections)}")
    
    async def subscribe(self, websocket: WebSocket, topic: str):
        """Subscribe a WebSocket to a specific topic."""
        if topic not in self.subscriptions:
            self.subscriptions[topic] = []
        
        if websocket not in self.subscriptions[topic]:
            self.subscriptions[topic].append(websocket)
        
        logger.info(f"WebSocket subscribed to {topic}")
    
    async def unsubscribe(self, websocket: WebSocket, topic: str):
        """Unsubscribe a WebSocket from a specific topic."""
        if topic in self.subscriptions and websocket in self.subscriptions[topic]:
            self.subscriptions[topic].remove(websocket)
        
        logger.info(f"WebSocket unsubscribed from {topic}")
    
    async def broadcast_to_topic(self, topic: str, message: Dict[str, Any]):
        """Broadcast a message to all WebSockets subscribed to a topic."""
        if topic not in self.subscriptions:
            return
        
        disconnected = []
        for websocket in self.subscriptions[topic]:
            try:
                await websocket.send_json(message)
            except Exception as e:
                logger.error(f"Error sending WebSocket message: {e}")
                disconnected.append(websocket)
        
        # Remove disconnected WebSockets
        for websocket in disconnected:
            self.disconnect(websocket)
    
    async def broadcast_state_update(self, state_id: str, state_type: StateType, 
                                   operation: StateOperationType, data: Dict[str, Any]):
        """Broadcast a state update to relevant subscribers."""
        message = {
            "type": "state_update",
            "state_id": state_id,
            "state_type": state_type.value,
            "operation": operation.value,
            "data": data,
            "timestamp": datetime.now().isoformat()
        }
        
        # Broadcast to general state updates
        await self.broadcast_to_topic("states", message)
        
        # Broadcast to specific state type
        await self.broadcast_to_topic(f"states:{state_type.value}", message)
        
        # Broadcast to specific state ID
        await self.broadcast_to_topic(f"state:{state_id}", message)


class StateAPI:
    """REST API for state management."""
    
    def __init__(self, state_manager: DatabaseStateManager):
        self.state_manager = state_manager
        self.connection_manager = ConnectionManager()
        
        if not FASTAPI_AVAILABLE:
            raise ImportError("FastAPI is required for REST API functionality")
        
        self.app = FastAPI(
            title="Trading State Management API",
            description="REST API for managing trading states with versioning and real-time updates",
            version="1.0.0"
        )
        
        # Add CORS middleware
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        
        self._setup_routes()
    
    def _setup_routes(self):
        """Setup API routes."""
        
        @self.app.get("/health")
        async def health_check():
            """Health check endpoint."""
            return {"status": "healthy", "timestamp": datetime.now().isoformat()}
        
        @self.app.post("/states", response_model=StateResponse)
        async def create_state(request: StateCreateRequest):
            """Create a new state."""
            try:
                version_id = await self.state_manager.save_state(
                    state_id=request.state_id,
                    state_type=request.state_type,
                    data=request.data,
                    operation=request.operation,
                    user_id=request.user_id,
                    description=request.description
                )
                
                if not version_id:
                    raise HTTPException(status_code=400, detail="Failed to create state")
                
                # Broadcast update
                await self.connection_manager.broadcast_state_update(
                    request.state_id, request.state_type, request.operation, request.data
                )
                
                return StateResponse(
                    state_id=request.state_id,
                    state_type=request.state_type,
                    data=request.data,
                    version_id=version_id,
                    timestamp=datetime.now(),
                    user_id=request.user_id,
                    description=request.description
                )
                
            except Exception as e:
                logger.error(f"Error creating state: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.get("/states/{state_id}", response_model=StateResponse)
        async def get_state(
            state_id: str,
            state_type: StateType = Query(..., description="Type of state"),
            version_id: Optional[str] = Query(None, description="Specific version to retrieve")
        ):
            """Get a state by ID."""
            try:
                data = await self.state_manager.load_state(state_id, state_type, version_id)
                
                if data is None:
                    raise HTTPException(status_code=404, detail="State not found")
                
                # For latest version, we need to get the version info
                if not version_id:
                    latest_version = await self.state_manager.adapter.get_latest_state(state_id, state_type)
                    if latest_version:
                        version_id = latest_version.version_id
                        timestamp = latest_version.timestamp
                        user_id = latest_version.user_id
                        description = latest_version.description
                    else:
                        timestamp = datetime.now()
                        user_id = None
                        description = None
                else:
                    version = await self.state_manager.adapter.get_state_version(version_id)
                    if version:
                        timestamp = version.timestamp
                        user_id = version.user_id
                        description = version.description
                    else:
                        timestamp = datetime.now()
                        user_id = None
                        description = None
                
                return StateResponse(
                    state_id=state_id,
                    state_type=state_type,
                    data=data,
                    version_id=version_id or "unknown",
                    timestamp=timestamp,
                    user_id=user_id,
                    description=description
                )
                
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error getting state: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.put("/states/{state_id}", response_model=StateResponse)
        async def update_state(
            state_id: str,
            state_type: StateType,
            request: StateUpdateRequest
        ):
            """Update a state."""
            try:
                version_id = await self.state_manager.save_state(
                    state_id=state_id,
                    state_type=state_type,
                    data=request.data,
                    operation=StateOperationType.UPDATE,
                    user_id=request.user_id,
                    description=request.description
                )
                
                if not version_id:
                    raise HTTPException(status_code=400, detail="Failed to update state")
                
                # Broadcast update
                await self.connection_manager.broadcast_state_update(
                    state_id, state_type, StateOperationType.UPDATE, request.data
                )
                
                return StateResponse(
                    state_id=state_id,
                    state_type=state_type,
                    data=request.data,
                    version_id=version_id,
                    timestamp=datetime.now(),
                    user_id=request.user_id,
                    description=request.description
                )
                
            except Exception as e:
                logger.error(f"Error updating state: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.delete("/states/{state_id}")
        async def delete_state(
            state_id: str,
            state_type: StateType = Query(..., description="Type of state"),
            user_id: Optional[str] = Query(None, description="User ID for audit trail")
        ):
            """Delete a state."""
            try:
                # First mark as deleted in version history
                await self.state_manager.save_state(
                    state_id=state_id,
                    state_type=state_type,
                    data={"deleted": True},
                    operation=StateOperationType.DELETE,
                    user_id=user_id,
                    description="State deleted via API"
                )
                
                # Then actually delete
                success = await self.state_manager.delete_state(state_id, state_type)
                
                if not success:
                    raise HTTPException(status_code=400, detail="Failed to delete state")
                
                # Broadcast deletion
                await self.connection_manager.broadcast_state_update(
                    state_id, state_type, StateOperationType.DELETE, {"deleted": True}
                )
                
                return {"message": "State deleted successfully"}
                
            except Exception as e:
                logger.error(f"Error deleting state: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.get("/states/{state_id}/history", response_model=StateHistoryResponse)
        async def get_state_history(
            state_id: str,
            state_type: Optional[StateType] = Query(None, description="Filter by state type"),
            limit: Optional[int] = Query(50, ge=1, le=1000, description="Number of versions to return"),
            start_time: Optional[datetime] = Query(None, description="Start time filter"),
            end_time: Optional[datetime] = Query(None, description="End time filter")
        ):
            """Get state history."""
            try:
                versions = await self.state_manager.get_state_history(
                    state_id=state_id,
                    state_type=state_type,
                    limit=limit,
                    start_time=start_time,
                    end_time=end_time
                )
                
                response_versions = []
                for version in versions:
                    response_versions.append(StateResponse(
                        state_id=version.state_id,
                        state_type=version.state_type,
                        data=version.data,
                        version_id=version.version_id,
                        timestamp=version.timestamp,
                        user_id=version.user_id,
                        description=version.description
                    ))
                
                return StateHistoryResponse(
                    total_count=len(response_versions),
                    versions=response_versions
                )
                
            except Exception as e:
                logger.error(f"Error getting state history: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.post("/snapshots", response_model=SnapshotResponse)
        async def create_snapshot(request: SnapshotRequest):
            """Create a snapshot of all current states."""
            try:
                success = await self.state_manager.create_snapshot(
                    request.snapshot_id,
                    request.description
                )
                
                return SnapshotResponse(
                    snapshot_id=request.snapshot_id,
                    success=success,
                    message="Snapshot created successfully" if success else "Failed to create snapshot",
                    timestamp=datetime.now()
                )
                
            except Exception as e:
                logger.error(f"Error creating snapshot: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.post("/snapshots/{snapshot_id}/restore", response_model=SnapshotResponse)
        async def restore_snapshot(snapshot_id: str):
            """Restore states from a snapshot."""
            try:
                success = await self.state_manager.restore_snapshot(snapshot_id)
                
                return SnapshotResponse(
                    snapshot_id=snapshot_id,
                    success=success,
                    message="Snapshot restored successfully" if success else "Failed to restore snapshot",
                    timestamp=datetime.now()
                )
                
            except Exception as e:
                logger.error(f"Error restoring snapshot: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            """WebSocket endpoint for real-time state updates."""
            await self.connection_manager.connect(websocket)
            try:
                while True:
                    # Listen for subscription requests
                    data = await websocket.receive_json()
                    
                    if data.get("action") == "subscribe":
                        topic = data.get("topic")
                        if topic:
                            await self.connection_manager.subscribe(websocket, topic)
                            await websocket.send_json({
                                "type": "subscription_confirmed",
                                "topic": topic
                            })
                    
                    elif data.get("action") == "unsubscribe":
                        topic = data.get("topic")
                        if topic:
                            await self.connection_manager.unsubscribe(websocket, topic)
                            await websocket.send_json({
                                "type": "unsubscription_confirmed",
                                "topic": topic
                            })
                    
            except WebSocketDisconnect:
                self.connection_manager.disconnect(websocket)
            except Exception as e:
                logger.error(f"WebSocket error: {e}")
                self.connection_manager.disconnect(websocket)
    
    async def start_server(self, host: str = "0.0.0.0", port: int = 8000):
        """Start the API server."""
        config = uvicorn.Config(
            app=self.app,
            host=host,
            port=port,
            log_level="info"
        )
        server = uvicorn.Server(config)
        await server.serve()


# High-level factory functions
async def create_state_api(database_type: str, api_config: Dict[str, Any] = None, **db_config) -> StateAPI:
    """Create and initialize a state API with database backend."""
    # Create state manager
    state_manager = await create_state_manager(database_type, **db_config)
    
    # Create API
    api = StateAPI(state_manager)
    
    return api


# Integration with existing trading system
class TradingStateAPI:
    """Integration wrapper for trading system."""
    
    def __init__(self, api: StateAPI):
        self.api = api
        self.state_manager = api.state_manager
    
    async def save_position(self, pair: str, position_data: Dict[str, Any], 
                           user_id: str = None, description: str = None) -> Optional[str]:
        """Save a trading position."""
        return await self.state_manager.save_state(
            state_id=f"position_{pair}",
            state_type=StateType.POSITION,
            data=position_data,
            operation=StateOperationType.UPDATE,
            user_id=user_id,
            description=description or f"Position update for {pair}"
        )
    
    async def load_position(self, pair: str) -> Optional[Dict[str, Any]]:
        """Load a trading position."""
        return await self.state_manager.load_state(
            state_id=f"position_{pair}",
            state_type=StateType.POSITION
        )
    
    async def save_pair_state(self, pair: str, pair_data: Dict[str, Any],
                             user_id: str = None, description: str = None) -> Optional[str]:
        """Save pair state data."""
        return await self.state_manager.save_state(
            state_id=f"pair_{pair}",
            state_type=StateType.PAIR_STATE,
            data=pair_data,
            operation=StateOperationType.UPDATE,
            user_id=user_id,
            description=description or f"Pair state update for {pair}"
        )
    
    async def load_pair_state(self, pair: str) -> Optional[Dict[str, Any]]:
        """Load pair state data."""
        return await self.state_manager.load_state(
            state_id=f"pair_{pair}",
            state_type=StateType.PAIR_STATE
        )
    
    async def save_portfolio_state(self, portfolio_data: Dict[str, Any],
                                  user_id: str = None, description: str = None) -> Optional[str]:
        """Save portfolio state."""
        return await self.state_manager.save_state(
            state_id="portfolio_main",
            state_type=StateType.PORTFOLIO,
            data=portfolio_data,
            operation=StateOperationType.UPDATE,
            user_id=user_id,
            description=description or "Portfolio state update"
        )
    
    async def load_portfolio_state(self) -> Optional[Dict[str, Any]]:
        """Load portfolio state."""
        return await self.state_manager.load_state(
            state_id="portfolio_main",
            state_type=StateType.PORTFOLIO
        )
    
    async def get_all_positions(self) -> List[Dict[str, Any]]:
        """Get all active positions."""
        history = await self.state_manager.get_state_history(
            state_type=StateType.POSITION,
            limit=1000
        )
        
        # Group by state_id and get latest for each
        latest_positions = {}
        for version in history:
            if version.operation != StateOperationType.DELETE:
                if version.state_id not in latest_positions or version.timestamp > latest_positions[version.state_id].timestamp:
                    latest_positions[version.state_id] = version
        
        return [version.data for version in latest_positions.values()]
    
    async def get_all_pair_states(self) -> List[Dict[str, Any]]:
        """Get all pair states."""
        history = await self.state_manager.get_state_history(
            state_type=StateType.PAIR_STATE,
            limit=1000
        )
        
        # Group by state_id and get latest for each
        latest_states = {}
        for version in history:
            if version.operation != StateOperationType.DELETE:
                if version.state_id not in latest_states or version.timestamp > latest_states[version.state_id].timestamp:
                    latest_states[version.state_id] = version
        
        return [version.data for version in latest_states.values()]
    
    async def create_trading_snapshot(self, description: str = None) -> bool:
        """Create a snapshot of all trading states."""
        snapshot_id = f"trading_snapshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        return await self.state_manager.create_snapshot(snapshot_id, description)
    
    def get_websocket_manager(self):
        """Get the WebSocket connection manager for real-time updates."""
        return self.api.connection_manager


# Usage example
async def example_usage():
    """Example of how to use the state API."""
    # Create state API with InfluxDB backend
    api = await create_state_api(
        'influxdb',
        url='http://localhost:8086',
        token='your-token',
        org='trading-org',
        bucket='trading-states'
    )
    
    # Create trading wrapper
    trading_api = TradingStateAPI(api)
    
    # Save a position
    position_data = {
        "symbol1": "EUR",
        "symbol2": "USD",
        "direction": "long",
        "entry_time": datetime.now(),
        "quantity": 100000,
        "entry_price": 1.0985
    }
    
    version_id = await trading_api.save_position("EURUSD", position_data)
    print(f"Saved position with version: {version_id}")
    
    # Load the position
    loaded_position = await trading_api.load_position("EURUSD")
    print(f"Loaded position: {loaded_position}")
    
    # Create a snapshot
    success = await trading_api.create_trading_snapshot("End of day snapshot")
    print(f"Snapshot created: {success}")
    
    # Start the API server
    await api.start_server(host="0.0.0.0", port=8000)


if __name__ == "__main__":
    asyncio.run(example_usage())
