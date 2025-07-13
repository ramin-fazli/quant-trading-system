"""
Improved CTrader Data Manager with Better Connection Handling
============================================================

This module provides an improved CTrader data manager that:
- Uses a single connection for all data requests
- Implements proper rate limiting and batch processing
- Provides better error handling and fallback mechanisms
- Reduces API timeouts through sequential processing

Author: Trading System v2.0
Date: July 2025
"""

import numpy as np
import pandas as pd
import warnings
import os
import datetime
import time
import logging
import threading
from collections import deque, defaultdict
from config import TradingConfig, get_config, force_config_update

# cTrader Open API imports
try:
    from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
    from ctrader_open_api.messages import (
        OpenApiMessages_pb2,
        OpenApiModelMessages_pb2
    )
    from twisted.internet import reactor, defer
    import calendar
    
    # Import specific message types
    ProtoOAApplicationAuthReq = OpenApiMessages_pb2.ProtoOAApplicationAuthReq
    ProtoOAApplicationAuthRes = OpenApiMessages_pb2.ProtoOAApplicationAuthRes
    ProtoOAAccountAuthReq = OpenApiMessages_pb2.ProtoOAAccountAuthReq
    ProtoOAAccountAuthRes = OpenApiMessages_pb2.ProtoOAAccountAuthRes
    ProtoOASymbolsListReq = OpenApiMessages_pb2.ProtoOASymbolsListReq
    ProtoOASymbolsListRes = OpenApiMessages_pb2.ProtoOASymbolsListRes
    ProtoOAGetTrendbarsReq = OpenApiMessages_pb2.ProtoOAGetTrendbarsReq
    ProtoOAGetTrendbarsRes = OpenApiMessages_pb2.ProtoOAGetTrendbarsRes
    ProtoOATrendbarPeriod = OpenApiModelMessages_pb2.ProtoOATrendbarPeriod
    
    CTRADER_API_AVAILABLE = True
except ImportError as e:
    print(f"cTrader Open API not available: {e}")
    CTRADER_API_AVAILABLE = False

CONFIG = get_config()
logger = logging.getLogger(__name__)

# Load environment variables
def load_ctrader_env(env_path=None):
    """Load .env file for cTrader API credentials."""
    from dotenv import load_dotenv
    if env_path is None:
        env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    load_dotenv(env_path, override=False)

def get_ctrader_credentials():
    """Get cTrader API credentials from environment variables."""
    creds = {
        'CTRADER_CLIENT_ID': os.getenv('CTRADER_CLIENT_ID'),
        'CTRADER_CLIENT_SECRET': os.getenv('CTRADER_CLIENT_SECRET'),
        'CTRADER_ACCESS_TOKEN': os.getenv('CTRADER_ACCESS_TOKEN'),
        'CTRADER_ACCOUNT_ID': os.getenv('CTRADER_ACCOUNT_ID'),
    }
    return creds

# Load credentials
if not os.getenv('CTRADER_CLIENT_ID'):
    load_ctrader_env()

creds = get_ctrader_credentials()
CTRADER_CLIENT_ID = creds['CTRADER_CLIENT_ID']
CTRADER_CLIENT_SECRET = creds['CTRADER_CLIENT_SECRET']
CTRADER_ACCESS_TOKEN = creds['CTRADER_ACCESS_TOKEN']
CTRADER_ACCOUNT_ID = creds['CTRADER_ACCOUNT_ID']


class ImprovedCTraderDataManager:
    """
    Improved CTrader data manager with better connection handling
    """
    
    def __init__(self, config: TradingConfig):
        self.config = config
        self.client = None
        self.account_id = None
        self.access_token = CTRADER_ACCESS_TOKEN
        self.symbols_map = {}
        self.symbol_id_to_name_map = {}
        self.symbol_details = {}
        
        # Connection state
        self.connection_established = False
        self.symbols_loaded = False
        self.authenticated = False
        
        # Data processing
        self.retrieved_data = {}
        self.pending_requests = 0
        self.request_queue = []
        self.current_request = None
        
        # Rate limiting
        self.last_request_time = 0
        self.min_request_interval = 2.0  # 2 seconds between requests
        self.max_concurrent_requests = 1  # Only 1 request at a time
        
        # Timeout and retry handling
        self.default_timeout = 30
        self.max_retries = 3
        self.retry_delay = 5
        
        # Parse account ID
        if CTRADER_ACCOUNT_ID and CTRADER_ACCOUNT_ID.strip():
            try:
                self.account_id = int(CTRADER_ACCOUNT_ID.strip())
                logger.info(f"Using account ID: {self.account_id}")
            except ValueError as e:
                logger.error(f"Invalid account ID format: '{CTRADER_ACCOUNT_ID}' - {e}")
                self.account_id = None
        else:
            logger.warning("No account ID provided")
            self.account_id = None
    
    def _validate_credentials(self) -> bool:
        """Validate that all required credentials are available"""
        missing_creds = []
        if not CTRADER_CLIENT_ID:
            missing_creds.append("CTRADER_CLIENT_ID")
        if not CTRADER_CLIENT_SECRET:
            missing_creds.append("CTRADER_CLIENT_SECRET")
        if not CTRADER_ACCESS_TOKEN:
            missing_creds.append("CTRADER_ACCESS_TOKEN")
        
        if missing_creds:
            logger.error("Missing cTrader API credentials:")
            for cred in missing_creds:
                logger.error(f"  - {cred}")
            return False
        
        return True
    
    def connect(self) -> bool:
        """Establish connection to CTrader API"""
        if not CTRADER_API_AVAILABLE:
            logger.error("CTrader API not available")
            return False
        
        if not self._validate_credentials():
            return False
        
        if self.connection_established:
            logger.info("CTrader connection already established")
            return True
        
        try:
            logger.info("Connecting to CTrader API...")
            
            # Setup client
            self.client = Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)
            self.client.setConnectedCallback(self._on_connected)
            self.client.setDisconnectedCallback(self._on_disconnected)
            self.client.setMessageReceivedCallback(self._on_message_received)
            
            # Start connection in a controlled way
            self.client.startService()
            
            # Wait for connection with timeout and better event processing
            timeout = 30
            start_time = time.time()
            
            while not self.connection_established and (time.time() - start_time) < timeout:
                time.sleep(0.1)
                try:
                    # Process reactor events more carefully
                    if not reactor.running:
                        # Start reactor for a very short time to process events
                        reactor.callLater(0.05, reactor.stop)
                        try:
                            reactor.run(installSignalHandlers=False)
                        except:
                            pass  # Reactor might already be stopped
                    else:
                        # Just process pending events
                        reactor.runUntilCurrent()
                        
                except Exception as e:
                    logger.debug(f"Reactor processing error: {e}")
                    # Don't break on reactor errors, continue trying
                    continue
            
            if not self.connection_established:
                logger.error("Failed to establish CTrader connection within timeout")
                self._cleanup_connection()
                return False
            
            # Additional wait for authentication to complete
            auth_timeout = 10
            start_time = time.time()
            while not self.authenticated and (time.time() - start_time) < auth_timeout:
                time.sleep(0.1)
                try:
                    if reactor.running:
                        reactor.runUntilCurrent()
                except:
                    pass
            
            if not self.authenticated:
                logger.error("Failed to authenticate with CTrader within timeout")
                self._cleanup_connection()
                return False
            
            logger.info("CTrader connection established successfully")
            return True
            
        except Exception as e:
            logger.error(f"Error connecting to CTrader: {e}")
            self._cleanup_connection()
            return False
    
    def _cleanup_connection(self):
        """Clean up connection resources"""
        try:
            if self.client:
                if hasattr(self.client, 'stopService'):
                    self.client.stopService()
                elif hasattr(self.client, 'disconnect'):
                    self.client.disconnect()
            self.connection_established = False
            self.authenticated = False
            self.symbols_loaded = False
        except Exception as e:
            logger.debug(f"Error during connection cleanup: {e}")
    
    def _on_connected(self, client):
        """Handle successful connection"""
        logger.info("Connected to cTrader API")
        # Don't set connection_established yet, wait for authentication
        
        # Authenticate application
        request = ProtoOAApplicationAuthReq()
        request.clientId = CTRADER_CLIENT_ID
        request.clientSecret = CTRADER_CLIENT_SECRET
        
        deferred = client.send(request)
        deferred.addErrback(self._on_error)
        deferred.addTimeout(self.default_timeout, reactor)
    
    def _on_disconnected(self, client, reason):
        """Handle disconnection"""
        logger.warning(f"Disconnected from cTrader API: {reason}")
        self.connection_established = False
        self.authenticated = False
        self.symbols_loaded = False
    
    def _on_error(self, failure):
        """Handle API errors"""
        logger.error(f"CTrader API Error: {failure}")
        
        if 'TimeoutError' in str(failure):
            logger.warning("Request timeout occurred")
        
        # Don't retry automatically, let calling code handle it
        return failure
    
    def _on_message_received(self, client, message):
        """Handle incoming messages from CTrader API"""
        try:
            if message.payloadType == ProtoOAApplicationAuthRes:
                logger.info("API Application authorized")
                self._authenticate_account()
            
            elif message.payloadType == ProtoOAAccountAuthRes:
                logger.info(f"Account {self.account_id} authorized")
                self.authenticated = True
                self.connection_established = True  # Set this after authentication
                self._load_symbols()
            
            elif message.payloadType == ProtoOASymbolsListRes:
                self._process_symbols_list(message)
            
            elif message.payloadType == ProtoOAGetTrendbarsRes:
                self._process_trendbar_data(message)
                
        except Exception as e:
            logger.error(f"Error processing message: {e}")
    
    def _authenticate_account(self):
        """Authenticate trading account"""
        if not self.account_id:
            logger.error("No account ID available for authentication")
            return
        
        request = ProtoOAAccountAuthReq()
        request.ctidTraderAccountId = self.account_id
        request.accessToken = self.access_token
        
        deferred = self.client.send(request)
        deferred.addErrback(self._on_error)
        deferred.addTimeout(self.default_timeout, reactor)
    
    def _load_symbols(self):
        """Load available symbols"""
        if self.symbols_loaded:
            return
        
        request = ProtoOASymbolsListReq()
        request.ctidTraderAccountId = self.account_id
        
        deferred = self.client.send(request)
        deferred.addErrback(self._on_error)
        deferred.addTimeout(self.default_timeout, reactor)
    
    def _process_symbols_list(self, message):
        """Process symbols list response"""
        response = Protobuf.extract(message)
        
        self.symbols_map = {}
        self.symbol_id_to_name_map = {}
        
        for symbol in response.symbol:
            symbol_name = symbol.symbolName
            symbol_id = symbol.symbolId
            self.symbols_map[symbol_name] = symbol_id
            self.symbol_id_to_name_map[symbol_id] = symbol_name
            self.symbol_details[symbol_name] = symbol
        
        self.symbols_loaded = True
        logger.info(f"Loaded {len(self.symbols_map)} symbols")
    
    def _process_trendbar_data(self, message):
        """Process trendbar data response"""
        response = Protobuf.extract(message)
        
        # Find symbol name from ID
        symbol_name = self.symbol_id_to_name_map.get(response.symbolId)
        
        if not symbol_name:
            logger.error(f"Unknown symbol ID: {response.symbolId}")
            return
        
        # Extract price data
        closes = []
        timestamps = []
        
        if hasattr(response, 'trendbar') and len(response.trendbar) > 0:
            for bar in response.trendbar:
                try:
                    # Get close price
                    close_price = getattr(bar, 'close', getattr(bar, 'closePrice', getattr(bar, 'c', None)))
                    
                    if close_price is not None:
                        # Adjust for pip factor if available
                        pip_factor = getattr(response, 'pipFactor', 0)
                        adjusted_price = close_price / (10 ** pip_factor) if pip_factor > 0 else close_price
                        closes.append(adjusted_price)
                        
                        # Get timestamp
                        timestamp = None
                        if hasattr(bar, 'utcTimestampInMinutes'):
                            timestamp = pd.to_datetime(bar.utcTimestampInMinutes * 60, unit='s')
                        elif hasattr(bar, 'timestamp'):
                            timestamp = pd.to_datetime(bar.timestamp, unit='ms')
                        
                        if timestamp:
                            timestamps.append(timestamp)
                
                except Exception as e:
                    logger.error(f"Error processing bar data: {e}")
                    continue
        
        # Create pandas Series
        if closes and timestamps and len(closes) == len(timestamps):
            series = pd.Series(closes, index=timestamps)
            series.name = symbol_name
            self.retrieved_data[symbol_name] = series
            logger.info(f"Retrieved {len(series)} bars for {symbol_name}")
        else:
            logger.warning(f"No valid data retrieved for {symbol_name}")
            self.retrieved_data[symbol_name] = pd.Series(dtype=float)
        
        # Mark request as complete
        self.pending_requests -= 1
        if self.pending_requests <= 0:
            logger.info("All data requests completed")
    
    def get_historical_data(self, symbols, interval, start, end=None):
        """
        Get historical data for symbols with improved connection handling
        
        Args:
            symbols: str or list of str - symbol(s) to retrieve data for
            interval: str - time interval
            start: str - start date
            end: str - end date (optional)
            
        Returns:
            pandas.Series if single symbol, dict of Series if multiple symbols
        """
        # Handle both single symbol and list of symbols
        if isinstance(symbols, str):
            symbol_list = [symbols]
            return_single = True
        else:
            symbol_list = symbols
            return_single = False
        
        logger.info(f"CTrader: Requesting data for {symbol_list}")
        
        # Reset retrieved data
        self.retrieved_data = {}
        
        # Check if connected
        if not self.connection_established or not self.authenticated:
            if not self.connect():
                logger.error("Failed to establish CTrader connection")
                if return_single:
                    return pd.Series(dtype=float)
                else:
                    return {symbol: pd.Series(dtype=float) for symbol in symbol_list}
        
        # Wait for symbols to load
        if not self.symbols_loaded:
            timeout = 30
            start_time = time.time()
            while not self.symbols_loaded and (time.time() - start_time) < timeout:
                time.sleep(0.1)
                try:
                    if reactor.running:
                        reactor.runUntilCurrent()
                    else:
                        # Process a small reactor cycle
                        reactor.callLater(0.1, reactor.stop)
                        reactor.run(installSignalHandlers=False)
                except Exception as e:
                    logger.debug(f"Reactor processing error during symbol loading: {e}")
                    break
        
        if not self.symbols_loaded:
            logger.error("Failed to load symbols from CTrader")
            if return_single:
                return pd.Series(dtype=float)
            else:
                return {symbol: pd.Series(dtype=float) for symbol in symbol_list}
        
        # Filter available symbols
        available_symbols = [s for s in symbol_list if s in self.symbols_map]
        if not available_symbols:
            logger.warning(f"No symbols found in CTrader: {symbol_list}")
            if return_single:
                return pd.Series(dtype=float)
            else:
                return {symbol: pd.Series(dtype=float) for symbol in symbol_list}
        
        logger.info(f"Found {len(available_symbols)} available symbols: {available_symbols}")
        
        # Convert dates to timestamps
        start_dt = pd.to_datetime(start)
        end_dt = pd.to_datetime(end) if end else pd.Timestamp.now()
        
        from_timestamp = int(start_dt.timestamp() * 1000)
        to_timestamp = int(end_dt.timestamp() * 1000)
        
        # Map interval to CTrader period
        period_map = {
            '1D': 'D1', '1d': 'D1',
            '1H': 'H1', '1h': 'H1',
            '4H': 'H4', '4h': 'H4',
            '1M': 'M1', '1m': 'M1',
            '5M': 'M5', '5m': 'M5',
            '15M': 'M15', '15m': 'M15',
            '30M': 'M30', '30m': 'M30',
        }
        period = period_map.get(interval, 'D1')
        
        # Request data sequentially to avoid timeouts
        self.pending_requests = len(available_symbols)
        
        try:
            for i, symbol in enumerate(available_symbols):
                # Add delay between requests to avoid rate limiting
                if i > 0:
                    time.sleep(self.min_request_interval)
                
                self._request_symbol_data_sequential(symbol, from_timestamp, to_timestamp, period)
            
            # Wait for all requests to complete
            timeout = 60 * len(available_symbols)  # 1 minute per symbol
            start_time = time.time()
            
            while self.pending_requests > 0 and (time.time() - start_time) < timeout:
                time.sleep(0.1)
                try:
                    if reactor.running:
                        reactor.runUntilCurrent()
                    else:
                        # Process reactor events in small cycles
                        reactor.callLater(0.1, reactor.stop)
                        reactor.run(installSignalHandlers=False)
                except Exception as e:
                    logger.debug(f"Reactor processing error during data wait: {e}")
                    break
            
            if self.pending_requests > 0:
                logger.warning(f"Timeout waiting for {self.pending_requests} requests to complete")
            
        except Exception as e:
            logger.error(f"Error requesting data: {e}")
        
        # Return results
        if return_single:
            symbol = symbol_list[0]
            return self.retrieved_data.get(symbol, pd.Series(dtype=float))
        else:
            result = {}
            for symbol in symbol_list:
                result[symbol] = self.retrieved_data.get(symbol, pd.Series(dtype=float))
            return result
    
    def _request_symbol_data_sequential(self, symbol, from_timestamp, to_timestamp, period):
        """Request data for a single symbol with proper error handling"""
        if symbol not in self.symbols_map:
            logger.warning(f"Symbol {symbol} not found in available symbols")
            self.retrieved_data[symbol] = pd.Series(dtype=float)
            self.pending_requests -= 1
            return
        
        symbol_id = self.symbols_map[symbol]
        logger.info(f"Requesting data for {symbol} (ID: {symbol_id})")
        
        try:
            request = ProtoOAGetTrendbarsReq()
            request.ctidTraderAccountId = self.account_id
            request.symbolId = symbol_id
            request.period = ProtoOATrendbarPeriod.Value(period)
            request.fromTimestamp = from_timestamp
            request.toTimestamp = to_timestamp
            
            deferred = self.client.send(request)
            deferred.addErrback(self._on_symbol_error)
            deferred.addTimeout(self.default_timeout, reactor)
            
        except Exception as e:
            logger.error(f"Error requesting data for {symbol}: {e}")
            self.retrieved_data[symbol] = pd.Series(dtype=float)
            self.pending_requests -= 1
    
    def _on_symbol_error(self, failure):
        """Handle errors for individual symbol requests"""
        logger.error(f"Symbol data request error: {failure}")
        self.pending_requests -= 1
        return failure
    
    def disconnect(self):
        """Disconnect from CTrader API"""
        try:
            if self.client:
                # The CTrader client uses stopService() instead of disconnect()
                if hasattr(self.client, 'stopService'):
                    self.client.stopService()
                elif hasattr(self.client, 'disconnect'):
                    self.client.disconnect()
                logger.info("Disconnected from CTrader API")
            
            self.connection_established = False
            self.authenticated = False
            self.symbols_loaded = False
            
        except Exception as e:
            logger.error(f"Error disconnecting from CTrader: {e}")
    
    @staticmethod
    def test_basic_connectivity() -> bool:
        """
        Test basic CTrader API connectivity without full initialization
        """
        if not CTRADER_API_AVAILABLE:
            logger.debug("CTrader API not available")
            return False
        
        # Check credentials
        if not CTRADER_CLIENT_ID or not CTRADER_CLIENT_SECRET or not CTRADER_ACCESS_TOKEN:
            logger.debug("CTrader credentials not available")
            return False
        
        try:
            # Try to create a client without connecting
            test_client = Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)
            if test_client:
                logger.debug("CTrader basic connectivity test passed")
                return True
        except Exception as e:
            logger.debug(f"CTrader basic connectivity test failed: {e}")
            return False
        
        return False


# Wrapper class for compatibility
class CTraderDataManager(ImprovedCTraderDataManager):
    """Compatibility wrapper for the improved CTrader data manager"""
    pass
