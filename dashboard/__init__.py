"""
Comprehensive Trading Dashboard System

This module provides a unified, scalable dashboard for visualizing:
- Backtesting results and analytics
- Live trading data streams and positions
- Portfolio performance and risk metrics
- Real-time market data and technical indicators

Features:
- Web-based interface with real-time updates
- Interactive charts and visualizations
- Flexible data source integration
- Responsive design for desktop and mobile
- Real-time WebSocket streaming
- RESTful API for data access
"""

from .dashboard_manager import DashboardManager
from .web_server import DashboardServer
from .data_adapter import DataAdapter
from .chart_generator import ChartGenerator
from .websocket_handler import WebSocketHandler

__all__ = [
    'DashboardManager',
    'DashboardServer', 
    'DataAdapter',
    'ChartGenerator',
    'WebSocketHandler'
]

__version__ = "1.0.0"
