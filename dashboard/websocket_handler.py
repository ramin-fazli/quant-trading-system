"""
WebSocket Handler - Real-time data streaming via WebSocket

Handles WebSocket connections for real-time dashboard updates including
live market data, portfolio updates, and trading signals.
"""

import logging
import json
import threading
import time
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Set, Any, Optional

logger = logging.getLogger(__name__)




# WebSocket libraries (will be imported when available)
try:
    from flask_socketio import SocketIO, emit, join_room, leave_room
    SOCKETIO_AVAILABLE = True
except ImportError:
    SOCKETIO_AVAILABLE = False
    logger.warning("Flask-SocketIO not available - WebSocket functionality disabled")


class WebSocketHandler:
    """
    Handler for WebSocket connections and real-time data streaming
    
    Manages client connections and broadcasts updates for various
    data types including market data, portfolio updates, and alerts.
    """
    
    def __init__(self, socketio, config):
        self.socketio = socketio if SOCKETIO_AVAILABLE else None
        self.config = config
        
        # Connection management
        self.connected_clients = set()
        self.client_subscriptions = {}  # client_id -> set of subscription types
        
        # Data caches for new connections
        self.latest_backtest_data = None
        self.latest_live_data = None
        self.latest_portfolio_data = None
        
        # Thread safety
        self.lock = threading.Lock()
        
        logger.info(f"WebSocketHandler initialized (SocketIO available: {SOCKETIO_AVAILABLE})")
    
    def handle_connect(self):
        """Handle new client connection"""
        if not SOCKETIO_AVAILABLE:
            return
        
        try:
            client_id = self._get_client_id()
            
            with self.lock:
                self.connected_clients.add(client_id)
                self.client_subscriptions[client_id] = set()
            
            logger.info(f"Client {client_id} connected")
            
            # Send initial data if available
            self._send_initial_data(client_id)
            
            # Emit connection confirmation
            emit('connected', {
                'status': 'success',
                'client_id': client_id,
                'timestamp': datetime.now().isoformat()
            })
            
        except Exception as e:
            logger.error(f"Error handling client connection: {e}")
    
    def handle_disconnect(self):
        """Handle client disconnection"""
        if not SOCKETIO_AVAILABLE:
            return
        
        try:
            client_id = self._get_client_id()
            
            with self.lock:
                self.connected_clients.discard(client_id)
                self.client_subscriptions.pop(client_id, None)
            
            logger.info(f"Client {client_id} disconnected")
            
        except Exception as e:
            logger.error(f"Error handling client disconnection: {e}")
    
    def handle_subscribe(self, data: Dict):
        """Handle subscription request"""
        if not SOCKETIO_AVAILABLE:
            return
        
        try:
            client_id = self._get_client_id()
            subscription_type = data.get('type')
            
            if not subscription_type:
                emit('error', {'message': 'Subscription type required'})
                return
            
            with self.lock:
                if client_id in self.client_subscriptions:
                    self.client_subscriptions[client_id].add(subscription_type)
            
            # Join appropriate room
            join_room(subscription_type)
            
            logger.debug(f"Client {client_id} subscribed to {subscription_type}")
            
            emit('subscribed', {
                'type': subscription_type,
                'status': 'success',
                'timestamp': datetime.now().isoformat()
            })
            
        except Exception as e:
            logger.error(f"Error handling subscription: {e}")
            emit('error', {'message': 'Subscription failed'})
    
    def handle_unsubscribe(self, data: Dict):
        """Handle unsubscription request"""
        if not SOCKETIO_AVAILABLE:
            return
        
        try:
            client_id = self._get_client_id()
            subscription_type = data.get('type')
            
            if not subscription_type:
                emit('error', {'message': 'Subscription type required'})
                return
            
            with self.lock:
                if client_id in self.client_subscriptions:
                    self.client_subscriptions[client_id].discard(subscription_type)
            
            # Leave room
            leave_room(subscription_type)
            
            logger.debug(f"Client {client_id} unsubscribed from {subscription_type}")
            
            emit('unsubscribed', {
                'type': subscription_type,
                'status': 'success',
                'timestamp': datetime.now().isoformat()
            })
            
        except Exception as e:
            logger.error(f"Error handling unsubscription: {e}")
            emit('error', {'message': 'Unsubscription failed'})
    
    def broadcast_backtest_update(self, backtest_data: Dict):
        """Broadcast backtest data update"""
        if not SOCKETIO_AVAILABLE:
            return
        
        try:
            with self.lock:
                self.latest_backtest_data = backtest_data
            
            update_data = {
                'type': 'backtest_update',
                'data': self.serialize_for_json(backtest_data),
                'timestamp': datetime.now().isoformat()
            }
            
            self.socketio.emit('backtest_update', update_data, 
                             room='backtest', 
                             namespace=self.config.websocket_namespace)
            
            logger.debug("Broadcasted backtest update")
            
        except Exception as e:
            logger.error(f"Error broadcasting backtest update: {e}")
    
    def broadcast_live_update(self, live_data: Dict):
        """Broadcast live data update"""
        if not SOCKETIO_AVAILABLE:
            return
        
        try:
            with self.lock:
                self.latest_live_data = live_data
            
            update_data = {
                'type': 'live_update',
                'data': self.serialize_for_json(live_data),
                'timestamp': datetime.now().isoformat()
            }
            
            self.socketio.emit('live_update', update_data, 
                             room='live', 
                             namespace=self.config.websocket_namespace)
            
            logger.debug("Broadcasted live data update")
            
        except Exception as e:
            logger.error(f"Error broadcasting live update: {e}")
    
    def broadcast_portfolio_update(self, portfolio_data: Dict):
        """Broadcast portfolio update"""
        if not SOCKETIO_AVAILABLE:
            return
        
        try:
            with self.lock:
                self.latest_portfolio_data = portfolio_data
            
            update_data = {
                'type': 'portfolio_update',
                'data': self.serialize_for_json(portfolio_data),
                'timestamp': datetime.now().isoformat()
            }
            
            self.socketio.emit('portfolio_update', update_data, 
                             room='portfolio', 
                             namespace=self.config.websocket_namespace)
            
            logger.debug("Broadcasted portfolio update")
            
        except Exception as e:
            logger.error(f"Error broadcasting portfolio update: {e}")
    
    def broadcast_custom_update(self, data_key: str, custom_data: Dict):
        """Broadcast custom data update"""
        if not SOCKETIO_AVAILABLE:
            return
        
        try:
            update_data = {
                'type': 'custom_update',
                'key': data_key,
                'data': self.serialize_for_json(custom_data),
                'timestamp': datetime.now().isoformat()
            }
            
            self.socketio.emit('custom_update', update_data, 
                             room='custom', 
                             namespace=self.config.websocket_namespace)
            
            logger.debug(f"Broadcasted custom update: {data_key}")
            
        except Exception as e:
            logger.error(f"Error broadcasting custom update: {e}")
    
    def broadcast_alert(self, alert_data: Dict):
        """Broadcast alert/notification"""
        if not SOCKETIO_AVAILABLE:
            return
        
        try:
            alert = {
                'type': 'alert',
                'level': alert_data.get('level', 'info'),  # info, warning, error
                'title': alert_data.get('title', 'Alert'),
                'message': alert_data.get('message', ''),
                'data': self.serialize_for_json(alert_data.get('data', {})),
                'timestamp': datetime.now().isoformat()
            }
            
            self.socketio.emit('alert', alert, 
                             room='alerts', 
                             namespace=self.config.websocket_namespace)
            
            logger.info(f"Broadcasted alert: {alert['title']}")
            
        except Exception as e:
            logger.error(f"Error broadcasting alert: {e}")
    
    def broadcast_trade_signal(self, signal_data: Dict):
        """Broadcast trading signal"""
        if not SOCKETIO_AVAILABLE:
            return
        
        try:
            signal = {
                'type': 'trade_signal',
                'pair': signal_data.get('pair', ''),
                'action': signal_data.get('action', ''),  # buy, sell, close
                'signal_type': signal_data.get('signal_type', ''),  # entry, exit
                'confidence': signal_data.get('confidence', 0),
                'price_1': signal_data.get('price_1', 0),
                'price_2': signal_data.get('price_2', 0),
                'z_score': signal_data.get('z_score', 0),
                'timestamp': datetime.now().isoformat()
            }
            
            self.socketio.emit('trade_signal', signal, 
                             room='signals', 
                             namespace=self.config.websocket_namespace)
            
            logger.info(f"Broadcasted trade signal: {signal['pair']} - {signal['action']}")
            
        except Exception as e:
            logger.error(f"Error broadcasting trade signal: {e}")
    
    def broadcast_system_status(self, status_data: Dict):
        """Broadcast system status update"""
        if not SOCKETIO_AVAILABLE:
            return
        
        try:
            status = {
                'type': 'system_status',
                'trading_active': status_data.get('trading_active', False),
                'data_connected': status_data.get('data_connected', False),
                'broker_connected': status_data.get('broker_connected', False),
                'active_pairs': status_data.get('active_pairs', 0),
                'open_positions': status_data.get('open_positions', 0),
                'errors': status_data.get('errors', []),
                'timestamp': datetime.now().isoformat()
            }
            
            self.socketio.emit('system_status', status, 
                             room='status', 
                             namespace=self.config.websocket_namespace)
            
            logger.debug("Broadcasted system status")
            
        except Exception as e:
            logger.error(f"Error broadcasting system status: {e}")
    
    def _send_initial_data(self, client_id: str):
        """Send initial data to newly connected client"""
        try:
            # Send latest backtest data if available
            if self.latest_backtest_data:
                serialized_data = self.serialize_for_json(self.latest_backtest_data)
                emit('backtest_update', {
                    'type': 'backtest_update',
                    'data': serialized_data,
                    'timestamp': datetime.now().isoformat(),
                    'initial': True
                })
            
            # Send latest live data if available
            if self.latest_live_data:
                serialized_data = self.serialize_for_json(self.latest_live_data)
                emit('live_update', {
                    'type': 'live_update',
                    'data': serialized_data,
                    'timestamp': datetime.now().isoformat(),
                    'initial': True
                })
            
            # Send latest portfolio data if available
            if self.latest_portfolio_data:
                serialized_data = self.serialize_for_json(self.latest_portfolio_data)
                emit('portfolio_update', {
                    'type': 'portfolio_update',
                    'data': serialized_data,
                    'timestamp': datetime.now().isoformat(),
                    'initial': True
                })
            
        except Exception as e:
            logger.error(f"Error sending initial data to client {client_id}: {e}")
    
    def _get_client_id(self) -> str:
        """Get client ID from request context"""
        if not SOCKETIO_AVAILABLE:
            return "unknown"
        
        try:
            from flask import request
            return request.sid
        except Exception:
            return f"client_{int(time.time())}"
    
    def get_connection_stats(self) -> Dict:
        """Get connection statistics"""
        with self.lock:
            stats = {
                'total_connections': len(self.connected_clients),
                'subscriptions': {},
                'websocket_enabled': SOCKETIO_AVAILABLE
            }
            
            # Count subscriptions by type
            for client_subs in self.client_subscriptions.values():
                for sub_type in client_subs:
                    stats['subscriptions'][sub_type] = stats['subscriptions'].get(sub_type, 0) + 1
            
            return stats
    
    def disconnect_client(self, client_id: str):
        """Manually disconnect a client"""
        if not SOCKETIO_AVAILABLE:
            return
        
        try:
            with self.lock:
                self.connected_clients.discard(client_id)
                self.client_subscriptions.pop(client_id, None)
            
            # TODO: Implement actual client disconnection if needed
            logger.info(f"Manually disconnected client {client_id}")
            
        except Exception as e:
            logger.error(f"Error manually disconnecting client {client_id}: {e}")
    
    def cleanup(self):
        """Cleanup WebSocket handler"""
        try:
            with self.lock:
                self.connected_clients.clear()
                self.client_subscriptions.clear()
                
            self.latest_backtest_data = None
            self.latest_live_data = None
            self.latest_portfolio_data = None
            
            logger.info("WebSocketHandler cleaned up")
            
        except Exception as e:
            logger.error(f"Error cleaning up WebSocketHandler: {e}")
    
    def serialize_for_json(self, obj):
        """Convert pandas/numpy objects to JSON-serializable format"""
        if isinstance(obj, (pd.Timestamp, pd.DatetimeIndex)):
            return obj.isoformat() if hasattr(obj, 'isoformat') else str(obj)
        elif isinstance(obj, (np.int64, np.int32, np.int8, np.int16)):
            return int(obj)
        elif isinstance(obj, (np.float64, np.float32, np.float16)):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        elif isinstance(obj, pd.Series):
            return obj.to_dict()
        elif isinstance(obj, pd.DataFrame):
            return obj.to_dict('records')
        elif isinstance(obj, dict):
            return {k: self.serialize_for_json(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [self.serialize_for_json(item) for item in obj]
        else:
            return obj
