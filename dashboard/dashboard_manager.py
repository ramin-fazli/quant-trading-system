"""
Dashboard Manager - Central coordinator for trading dashboard system

Manages the lifecycle and coordination of all dashboard components including
web server, data adapters, WebSocket handlers, and chart generators.
"""

import asyncio
import logging
import threading
import time
import signal
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Union
import json
import os

import pandas as pd
import numpy as np
from flask import Flask
from flask_socketio import SocketIO

from .web_server import DashboardServer
from .data_adapter import DataAdapter
from .websocket_handler import WebSocketHandler
from .chart_generator import ChartGenerator

logger = logging.getLogger(__name__)


class DashboardConfig:
    """Configuration for dashboard system"""
    
    def __init__(self, config_dict: Optional[Dict] = None):
        config = config_dict or {}
        
        # Server configuration with environment variable support
        self.host = os.getenv('DASHBOARD_HOST', config.get('host', '0.0.0.0'))
        self.port = int(os.getenv('DASHBOARD_PORT', config.get('port', 8050)))
        self.debug = config.get('debug', False)
        self.threaded = config.get('threaded', True)
        
        # Data configuration
        self.update_interval = config.get('update_interval', 1.0)  # seconds
        self.max_data_points = config.get('max_data_points', 10000)
        self.cache_size = config.get('cache_size', 1000)
        
        # Dashboard configuration
        self.theme = config.get('theme', 'dark')
        self.auto_refresh = config.get('auto_refresh', True)
        self.chart_height = config.get('chart_height', 400)
        
        # WebSocket configuration
        self.websocket_enabled = config.get('websocket_enabled', True)
        self.websocket_namespace = config.get('websocket_namespace', '/dashboard')
        
        # Security configuration
        self.cors_enabled = config.get('cors_enabled', True)
        self.auth_enabled = config.get('auth_enabled', False)
        self.api_key = config.get('api_key', None)


class DashboardManager:
    """
    Central manager for the trading dashboard system
    
    Coordinates all dashboard components and provides a unified interface
    for visualizing backtesting results and live trading data.
    """
    
    def __init__(self, config: Optional[Union[DashboardConfig, Dict]] = None):
        if isinstance(config, dict):
            self.config = DashboardConfig(config)
        else:
            self.config = config or DashboardConfig()
        
        self.app = None
        self.socketio = None
        self.server = None
        self.data_adapter = None
        self.websocket_handler = None
        self.chart_generator = None
        
        self.is_running = False
        self.background_tasks = []
        
        # Data storage
        self.backtest_data = {}
        self.live_data = {}
        self.portfolio_data = {}
        self.market_data = {}
        
        # Event handlers
        self.data_handlers = {}
        
        self._setup_components()
    
    def _setup_components(self):
        """Initialize all dashboard components"""
        try:
            # Initialize Flask app with SocketIO
            self.app = Flask(__name__, 
                           static_folder='static',
                           template_folder='templates')
            
            # Configure app
            self.app.config['SECRET_KEY'] = 'trading-dashboard-secret-key'
            
            # Initialize SocketIO
            if self.config.websocket_enabled:
                self.socketio = SocketIO(self.app, 
                                       cors_allowed_origins="*" if self.config.cors_enabled else None,
                                       namespace=self.config.websocket_namespace)
            
            # Initialize components
            self.server = DashboardServer(self.app, self.config)
            self.data_adapter = DataAdapter(self.config)
            self.chart_generator = ChartGenerator(self.config)
            
            if self.config.websocket_enabled:
                self.websocket_handler = WebSocketHandler(self.socketio, self.config)
                self._setup_websocket_events()
            
            self._setup_routes()
            
            logger.info("Dashboard components initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize dashboard components: {e}")
            raise
    
    def _setup_routes(self):
        """Setup Flask routes"""
        if not self.server:
            return
        
        # Register routes with server
        self.server.register_routes(
            data_adapter=self.data_adapter,
            chart_generator=self.chart_generator
        )
    
    def _setup_websocket_events(self):
        """Setup WebSocket event handlers"""
        if not self.websocket_handler:
            return
        
        @self.socketio.on('connect', namespace=self.config.websocket_namespace)
        def handle_connect():
            self.websocket_handler.handle_connect()
        
        @self.socketio.on('disconnect', namespace=self.config.websocket_namespace)
        def handle_disconnect():
            self.websocket_handler.handle_disconnect()
        
        @self.socketio.on('subscribe_data', namespace=self.config.websocket_namespace)
        def handle_subscribe(data):
            self.websocket_handler.handle_subscribe(data)
        
        @self.socketio.on('unsubscribe_data', namespace=self.config.websocket_namespace)
        def handle_unsubscribe(data):
            self.websocket_handler.handle_unsubscribe(data)
    
    def load_backtest_data(self, backtest_results: Dict, config: Any = None):
        """
        Load backtesting results into dashboard
        
        Args:
            backtest_results: Results from backtesting system
            config: Trading configuration object
        """
        try:
            logger.info("Loading backtest data into dashboard")
            
            # Process and store backtest data
            self.backtest_data = self.data_adapter.process_backtest_data(
                backtest_results, config
            )
            
            # Store reference in data adapter for API access
            self.data_adapter.backtest_data = self.backtest_data
            
            # Generate charts
            self.backtest_data['charts'] = self.chart_generator.generate_backtest_charts(
                self.backtest_data
            )
            
            # Broadcast update via WebSocket
            if self.websocket_handler:
                self.websocket_handler.broadcast_backtest_update(self.backtest_data)
            
            logger.info(f"Loaded backtest data for {len(self.backtest_data.get('pairs', []))} pairs")
            
        except Exception as e:
            logger.error(f"Failed to load backtest data: {e}")
            raise
    
    def connect_live_data_source(self, data_source: Any, symbols: List[str] = None):
        """
        Connect to live data source for real-time updates
        
        Args:
            data_source: Data source object (MT5DataManager, etc.)
            symbols: List of symbols to monitor
        """
        try:
            logger.info("Connecting to live data source")
            
            # Setup live data connection
            self.data_adapter.connect_live_source(data_source, symbols)
            
            # Start background data update task
            if self.config.auto_refresh:
                task = threading.Thread(
                    target=self._live_data_update_loop,
                    daemon=True
                )
                task.start()
                self.background_tasks.append(task)
            
            logger.info("Live data source connected successfully")
            
        except Exception as e:
            logger.error(f"Failed to connect live data source: {e}")
            raise
    
    def _live_data_update_loop(self):
        """Background loop for live data updates"""
        while self.is_running:
            try:
                # Get latest live data
                live_update = self.data_adapter.get_live_data_update()
                
                if live_update:
                    # Update stored data
                    self.live_data.update(live_update)
                    
                    # Broadcast via WebSocket
                    if self.websocket_handler:
                        self.websocket_handler.broadcast_live_update(live_update)
                
                time.sleep(self.config.update_interval)
                
            except Exception as e:
                logger.error(f"Error in live data update loop: {e}")
                time.sleep(self.config.update_interval * 2)
    
    def update_portfolio_data(self, portfolio_data: Dict):
        """
        Update portfolio data for dashboard display
        
        Args:
            portfolio_data: Current portfolio information
        """
        try:
            # Process portfolio data
            processed_data = self.data_adapter.process_portfolio_data(portfolio_data)
            
            # Update stored data
            self.portfolio_data.update(processed_data)
            
            # Generate portfolio charts
            portfolio_charts = self.chart_generator.generate_portfolio_charts(
                self.portfolio_data
            )
            
            # Broadcast update
            if self.websocket_handler:
                update_data = {
                    'portfolio': self.portfolio_data,
                    'charts': portfolio_charts
                }
                self.websocket_handler.broadcast_portfolio_update(update_data)
            
            logger.debug("Portfolio data updated")
            
        except Exception as e:
            logger.error(f"Failed to update portfolio data: {e}")
    
    def add_custom_data(self, data_key: str, data: Any, chart_config: Dict = None):
        """
        Add custom data to dashboard
        
        Args:
            data_key: Unique identifier for the data
            data: Data to display
            chart_config: Optional chart configuration
        """
        try:
            # Process custom data
            processed_data = self.data_adapter.process_custom_data(data, chart_config)
            
            # Generate chart if config provided
            if chart_config:
                chart = self.chart_generator.generate_custom_chart(
                    processed_data, chart_config
                )
                processed_data['chart'] = chart
            
            # Store data
            if 'custom' not in self.live_data:
                self.live_data['custom'] = {}
            
            self.live_data['custom'][data_key] = processed_data
            
            # Broadcast update
            if self.websocket_handler:
                self.websocket_handler.broadcast_custom_update(data_key, processed_data)
            
            logger.debug(f"Added custom data: {data_key}")
            
        except Exception as e:
            logger.error(f"Failed to add custom data {data_key}: {e}")
    
    def start(self, blocking: bool = True):
        """
        Start the dashboard server
        
        Args:
            blocking: Whether to run in blocking mode
        """
        try:
            self.is_running = True
            
            logger.info(f"Starting dashboard server on {self.config.host}:{self.config.port}")
            
            # Setup signal handlers for graceful shutdown
            signal.signal(signal.SIGINT, self._signal_handler)
            signal.signal(signal.SIGTERM, self._signal_handler)
            
            if blocking:
                if self.socketio:
                    self.socketio.run(
                        self.app,
                        host=self.config.host,
                        port=self.config.port,
                        debug=self.config.debug
                    )
                else:
                    self.app.run(
                        host=self.config.host,
                        port=self.config.port,
                        debug=self.config.debug,
                        threaded=self.config.threaded
                    )
            else:
                # Start in separate thread
                server_thread = threading.Thread(
                    target=self._run_server,
                    daemon=True
                )
                server_thread.start()
                self.background_tasks.append(server_thread)
            
            logger.info("Dashboard server started successfully")
            
        except Exception as e:
            logger.error(f"Failed to start dashboard server: {e}")
            self.stop()
            raise
    
    def _run_server(self):
        """Run server in non-blocking mode"""
        if self.socketio:
            self.socketio.run(
                self.app,
                host=self.config.host,
                port=self.config.port,
                debug=False,
                allow_unsafe_werkzeug=True  # Allow production deployment
            )
        else:
            self.app.run(
                host=self.config.host,
                port=self.config.port,
                debug=False,
                threaded=self.config.threaded
            )
    
    def stop(self):
        """Stop the dashboard server"""
        try:
            logger.info("Stopping dashboard server")
            
            self.is_running = False
            
            # Wait for background tasks to complete
            for task in self.background_tasks:
                if task.is_alive():
                    task.join(timeout=5.0)
            
            # Cleanup components
            if self.data_adapter:
                self.data_adapter.cleanup()
            
            logger.info("Dashboard server stopped")
            
        except Exception as e:
            logger.error(f"Error stopping dashboard server: {e}")
    
    def _signal_handler(self, sig, frame):
        """Handle shutdown signals"""
        logger.info(f"Received signal {sig}, shutting down dashboard")
        self.stop()
        sys.exit(0)
    
    def get_dashboard_url(self) -> str:
        """Get the dashboard URL"""
        return f"http://{self.config.host}:{self.config.port}"
    
    def get_status(self) -> Dict:
        """Get current dashboard status"""
        return {
            'running': self.is_running,
            'url': self.get_dashboard_url(),
            'config': {
                'host': self.config.host,
                'port': self.config.port,
                'websocket_enabled': self.config.websocket_enabled,
                'auto_refresh': self.config.auto_refresh
            },
            'data': {
                'backtest_loaded': bool(self.backtest_data),
                'live_connected': bool(self.live_data),
                'portfolio_data': bool(self.portfolio_data)
            },
            'components': {
                'server': self.server is not None,
                'data_adapter': self.data_adapter is not None,
                'websocket_handler': self.websocket_handler is not None,
                'chart_generator': self.chart_generator is not None
            }
        }
    
    def keep_alive(self):
        """
        Keep the dashboard running until interrupted
        
        This method blocks the main thread and keeps the dashboard server running
        until Ctrl+C is pressed or the server is stopped.
        """
        try:
            if not self.is_running:
                logger.warning("Dashboard is not running. Starting it now...")
                self.start(blocking=True)
            else:
                logger.info("Dashboard is running. Press Ctrl+C to stop.")
                # If running in non-blocking mode, we need to keep the main thread alive
                import time
                while self.is_running:
                    time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received. Stopping dashboard...")
            self.stop()
        except Exception as e:
            logger.error(f"Error in keep_alive: {e}")
            self.stop()
            raise
