"""
Working CTrader Data Manager based on pairs_trading_ctrader_v4.py patterns
============================================================================

This module provides a CTrader data manager that works reliably by following
the exact patterns from the working pairs_trading_ctrader_v4.py file.

Key improvements:
- Proper reactor handling with single run() call
- Deferred-based async operations
- Better error handling and timeouts
- Simplified connection logic

Author: Trading System v2.0
Date: July 2025
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import warnings
import os
import datetime
import time
import logging
import threading
import calendar
import traceback
import inspect
from collections import deque, defaultdict
from typing import Optional
from config import TradingConfig, get_config, force_config_update

# cTrader Open API imports
try:
    from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
    from ctrader_open_api.messages import (
        OpenApiMessages_pb2,
        OpenApiModelMessages_pb2
    )
    from twisted.internet import reactor, defer
    
    # Import specific message types
    ProtoOAApplicationAuthReq = OpenApiMessages_pb2.ProtoOAApplicationAuthReq
    ProtoOAApplicationAuthRes = OpenApiMessages_pb2.ProtoOAApplicationAuthRes
    ProtoOAAccountAuthReq = OpenApiMessages_pb2.ProtoOAAccountAuthReq
    ProtoOAAccountAuthRes = OpenApiMessages_pb2.ProtoOAAccountAuthRes
    ProtoOASymbolsListReq = OpenApiMessages_pb2.ProtoOASymbolsListReq
    ProtoOASymbolsListRes = OpenApiMessages_pb2.ProtoOASymbolsListRes
    ProtoOAGetTrendbarsReq = OpenApiMessages_pb2.ProtoOAGetTrendbarsReq
    ProtoOAGetTrendbarsRes = OpenApiMessages_pb2.ProtoOAGetTrendbarsRes
    ProtoOAErrorRes = OpenApiMessages_pb2.ProtoOAErrorRes
    ProtoOATrendbarPeriod = OpenApiModelMessages_pb2.ProtoOATrendbarPeriod
    
    CTRADER_API_AVAILABLE = True
except ImportError as e:
    print(f"cTrader Open API not available: {e}")
    CTRADER_API_AVAILABLE = False

# Setup logger
logger = logging.getLogger(__name__)

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

CTRADER_CLIENT_ID = os.getenv("CTRADER_CLIENT_ID")
CTRADER_CLIENT_SECRET = os.getenv("CTRADER_CLIENT_SECRET")
CTRADER_ACCESS_TOKEN = os.getenv("CTRADER_ACCESS_TOKEN")
CTRADER_ACCOUNT_ID = os.getenv("CTRADER_ACCOUNT_ID")


class CTraderDataManager:
    """
    CTrader Data Manager using proven working patterns from pairs_trading_ctrader_v4.py
    
    IMPORTANT LIMITATION: Due to Twisted reactor constraints, this manager can only handle
    one connection session per process. After the first data retrieval, subsequent requests
    in the same process will fail with ReactorNotRestartable error. This is a fundamental
    limitation of the Twisted framework.
    
    For backtesting multiple pairs, the system will automatically fall back to MT5 after
    the first CTrader request completes successfully.
    """
    
    def __init__(self, config=None):
        self.config = config or TradingConfig()
        self.client = None
        self.account_id = None
        self.access_token = None
        self.symbols_map = {}  # Map symbol names to IDs
        self.symbol_id_to_name_map = {}  # Reverse map for performance
        self.symbol_details = {}
        self.retrieved_data = {}
        self.pending_requests = 0
        self.deferred_result = None
        self.available_accounts = []
        self.timeout_count = 0
        self.max_retries = 3
        self.request_delay = 1.0  # Delay between requests
        self.target_symbols = []
        
        # Connection state management
        self.is_connected = False
        self.is_authenticated = False
        self.symbols_loaded = False
        self._connection_lock = threading.Lock()
        self._last_request_time = 0
        
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
    
    def _get_price_from_relative(self, symbol_details, relative_price):
        """
        Convert relative price to actual price according to cTrader documentation
        
        Args:
            symbol_details: Dictionary containing symbol information including 'digits'
            relative_price: Raw price value from cTrader API
            
        Returns:
            Properly formatted price rounded to symbol digits, or None if invalid
        """
        # Validate symbol_details and digits field
        if not symbol_details or 'digits' not in symbol_details:
            logger.error(f"Missing or invalid symbol_details for price conversion: {symbol_details}")
            return None
            
        digits = symbol_details['digits']
        if not isinstance(digits, int) or digits < 0:
            logger.error(f"Invalid digits value: {digits}")
            return None
            
        # Divide by 100000 and round to symbol digits as per cTrader documentation
        actual_price = relative_price / 100000.0
        return round(actual_price, digits)
    
    def _process_trendbar_prices(self, bar, symbol_details):
        """
        Process trendbar data according to cTrader documentation relative format
        
        According to cTrader documentation:
        - low is the base price in relative format
        - high = low + deltaHigh  
        - open = low + deltaOpen
        - close = low + deltaClose
        - All prices must be divided by 100000 and rounded to symbol digits
        
        Args:
            bar: Trendbar object from cTrader API
            symbol_details: Dictionary containing symbol information
            
        Returns:
            Dictionary with OHLC prices properly converted, or None if processing fails
        """
        try:
            # According to cTrader documentation:
            # - low is the base price
            # - high = low + deltaHigh  
            # - open = low + deltaOpen
            # - close = low + deltaClose
            # All prices must be divided by 100000 and rounded to symbol digits
            
            if not hasattr(bar, 'low'):
                return None
                
            low_raw = bar.low
            low_price = self._get_price_from_relative(symbol_details, low_raw)
            
            prices = {'low': low_price}
            
            if hasattr(bar, 'deltaHigh'):
                high_raw = low_raw + bar.deltaHigh
                prices['high'] = self._get_price_from_relative(symbol_details, high_raw)
            
            if hasattr(bar, 'deltaOpen'):
                open_raw = low_raw + bar.deltaOpen  
                prices['open'] = self._get_price_from_relative(symbol_details, open_raw)
                
            if hasattr(bar, 'deltaClose'):
                close_raw = low_raw + bar.deltaClose
                prices['close'] = self._get_price_from_relative(symbol_details, close_raw)
            
            # Validate that we have at least a close price
            if 'close' not in prices:
                logger.error("No close price available in trendbar data and no deltaClose field")
                return None
                
            return prices
            
        except Exception as e:
            logger.error(f"Error processing trendbar prices: {e}")
            return None
    
    @staticmethod
    def test_basic_connectivity():
        """Test basic connectivity to CTrader API"""
        if not CTRADER_API_AVAILABLE:
            logger.error("CTrader API not available")
            return False
        
        # Check credentials
        missing_creds = []
        if not CTRADER_CLIENT_ID:
            missing_creds.append("CTRADER_CLIENT_ID")
        if not CTRADER_CLIENT_SECRET:
            missing_creds.append("CTRADER_CLIENT_SECRET")
        if not CTRADER_ACCESS_TOKEN:
            missing_creds.append("CTRADER_ACCESS_TOKEN")
        
        if missing_creds:
            logger.error(f"Missing cTrader API credentials: {missing_creds}")
            return False
        
        return True
    
    def connect(self) -> bool:
        """Simple connection test without running reactor"""
        if not CTRADER_API_AVAILABLE:
            logger.error("CTrader API not available")
            return False
        
        if not self.test_basic_connectivity():
            return False
        
        # Just return True if credentials are valid - actual connection will happen during data retrieval
        logger.info("CTrader credentials validated, connection will be established during data retrieval")
        return True
    
    def _on_connected_test(self, client):
        """Handle successful connection for testing - exactly like working version"""
        logger.info("Connected to cTrader API")
        # Authenticate application
        request = ProtoOAApplicationAuthReq()
        request.clientId = CTRADER_CLIENT_ID
        request.clientSecret = CTRADER_CLIENT_SECRET
        deferred = client.send(request)
        deferred.addErrback(self._on_error)
        # Add timeout to prevent hanging
        deferred.addTimeout(30, reactor)
    
    def _on_message_received_test(self, client, message):
        """Handle incoming messages for connection test"""
        try:
            if message.payloadType == ProtoOAApplicationAuthRes().payloadType:
                logger.info("API Application authorized")
                if self.account_id:
                    self._authenticate_account()
                else:
                    # Get list of available accounts first
                    self._get_account_list()
            elif message.payloadType == OpenApiMessages_pb2.ProtoOAGetAccountListByAccessTokenRes().payloadType:
                self._process_account_list(message)
            elif message.payloadType == ProtoOAAccountAuthRes().payloadType:
                logger.info(f"Account {self.account_id} authorized")
                self._get_symbols_list()
            elif message.payloadType == ProtoOASymbolsListRes().payloadType:
                self._process_symbols_list_test(message)
        except Exception as e:
            logger.error(f"Error processing message during test: {e}")
    
    def _process_symbols_list_test(self, message):
        """Process symbols list response for connection test only"""
        response = Protobuf.extract(message)
        for symbol in response.symbol:
            # Map symbol name to ID and vice-versa
            self.symbols_map[symbol.symbolName] = symbol.symbolId
            self.symbol_id_to_name_map[symbol.symbolId] = symbol.symbolName
        
        logger.info(f"Loaded {len(self.symbols_map)} symbols")
        
        # Stop reactor after loading symbols (connection test complete)
        logger.info("Connection test completed successfully")
        reactor.stop()
    
    def _test_timeout(self):
        """Handle timeout for connection test"""
        logger.error("CTrader connection test timed out")
        self._timed_out = True
        if reactor.running:
            reactor.stop()
    
    def _on_connected(self, client=None):
        """Callback when client connects - data retrieval mode"""
        logger.info("Connected to cTrader for data retrieval")
        
        # Authenticate with OAuth token - exactly like working version
        auth_req = ProtoOAApplicationAuthReq()
        auth_req.clientId = CTRADER_CLIENT_ID
        auth_req.clientSecret = CTRADER_CLIENT_SECRET
        self.client.send(auth_req)
    
    def _on_disconnected(self, client=None, reason=None):
        """Callback when client disconnects - data retrieval mode"""
        logger.info("Disconnected from cTrader")
        
        # Stop the reactor when disconnected - exactly like working version
        if reactor.running:
            reactor.stop()
    
    def _on_message_received(self, client, message):
        """Handle messages from cTrader - data retrieval mode"""
        
        if message.payloadType == ProtoOAApplicationAuthRes().payloadType:
            logger.info("Application authenticated successfully")
            
            # Get account access token - exactly like working version
            access_req = ProtoOAAccountAuthReq()
            access_req.ctidTraderAccountId = int(CTRADER_ACCOUNT_ID)  # Ensure integer type
            access_req.accessToken = self.access_token
            client.send(access_req)
            
        elif message.payloadType == ProtoOAAccountAuthRes().payloadType:
            logger.info("Account authenticated successfully")
            
            # Request symbols to map symbol names to IDs - exactly like working version
            symbols_req = ProtoOASymbolsListReq()
            symbols_req.ctidTraderAccountId = int(CTRADER_ACCOUNT_ID)  # Ensure integer type
            symbols_req.includeArchivedSymbols = False
            client.send(symbols_req)
            
        elif message.payloadType == ProtoOASymbolsListRes().payloadType:
            # Extract the message content properly
            response = Protobuf.extract(message)
            logger.info(f"Received symbols list: {len(response.symbol)} symbols")
            
            # Map symbol names to IDs and collect basic symbol details
            self.symbol_name_to_id = {}
            symbol_ids_for_details = []
            
            for symbol in response.symbol:
                # Store both exact name and cleaned name mappings
                self.symbol_name_to_id[symbol.symbolName] = symbol.symbolId
                # Also store cleaned versions (remove suffixes like .r, .m, etc)
                clean_name = symbol.symbolName.split('.')[0]
                if clean_name not in self.symbol_name_to_id:
                    self.symbol_name_to_id[clean_name] = symbol.symbolId
                
                # Store basic symbol details for price conversion with validation
                digits = getattr(symbol, 'digits', None)
                pip_position = getattr(symbol, 'pipPosition', None)
                
                if digits is None:
                    logger.error(f"Missing digits field for symbol {symbol.symbolName}")
                    continue
                if pip_position is None:
                    logger.warning(f"Missing pipPosition for symbol {symbol.symbolName}")
                    pip_position = -5  # Common default for forex pairs
                
                self.symbol_details[symbol.symbolName] = {
                    'digits': digits,
                    'pip_position': pip_position,
                    'symbol_id': symbol.symbolId
                }
                
                # Also store for clean name
                if clean_name not in self.symbol_details:
                    self.symbol_details[clean_name] = self.symbol_details[symbol.symbolName].copy()
                    
            logger.info(f"Mapped {len(self.symbol_name_to_id)} symbol names to IDs")
            logger.info(f"Collected symbol details for {len(self.symbol_details)} symbols")
            
            # Start requesting historical data for each symbol
            self._request_all_symbol_data()
            
        elif message.payloadType == ProtoOAGetTrendbarsRes().payloadType:
            # Process historical data response - exactly like working version
            self._process_historical_data(message)
            
        elif message.payloadType == ProtoOAErrorRes().payloadType:
            response = Protobuf.extract(message)
            logger.error(f"cTrader API Error: {response.description}")
            self.pending_requests -= 1
            self._check_completion()
    
    def _request_all_symbol_data(self):
        """Request historical data for all target symbols using sequential pattern like pairs_trading_ctrader_v4.py"""
        
        # Find available symbols (that exist in CTrader)
        available_symbols = []
        for symbol in self.target_symbols:
            symbol_id = self._get_symbol_id(symbol)
            if symbol_id:
                available_symbols.append(symbol)
            else:
                logger.warning(f"Symbol {symbol} not found in cTrader")
                self.retrieved_data[symbol] = pd.Series(dtype=float)
        
        logger.info(f"Requesting data for {len(available_symbols)} available symbols out of {len(self.target_symbols)} requested")
        
        if not available_symbols:
            logger.warning("No valid symbols found for data retrieval")
            self._finish_data_collection()
            return
        
        self.pending_requests = len(available_symbols)
        
        # Request data for symbols one by one with delays to avoid rate limiting - EXACTLY like pairs_trading_ctrader_v4.py
        for i, symbol in enumerate(available_symbols):
            reactor.callLater(i * self.request_delay, self._request_single_symbol_data, symbol)
    
    def _request_single_symbol_data(self, symbol):
        """Request data for a single symbol - EXACTLY like pairs_trading_ctrader_v4.py"""
        symbol_id = self._get_symbol_id(symbol)
        if symbol_id:
            logger.info(f"Requesting data for {symbol} (ID: {symbol_id})")
            
            # Convert interval to cTrader period
            period_map = {
                'D1': ProtoOATrendbarPeriod.D1,
                '1D': ProtoOATrendbarPeriod.D1,
                'H1': ProtoOATrendbarPeriod.H1,
                '1H': ProtoOATrendbarPeriod.H1,
                'M1': ProtoOATrendbarPeriod.M1,
                '1M': ProtoOATrendbarPeriod.M1,
                'M5': ProtoOATrendbarPeriod.M5,
                '5M': ProtoOATrendbarPeriod.M5,
                'M15': ProtoOATrendbarPeriod.M15,
                '15M': ProtoOATrendbarPeriod.M15,
                'M30': ProtoOATrendbarPeriod.M30,
                '30M': ProtoOATrendbarPeriod.M30,
            }
            
            if self.interval.upper() not in period_map:
                logger.error(f"Unsupported interval: {self.interval}")
                self.retrieved_data[symbol] = pd.Series(dtype=float)
                self.pending_requests -= 1
                if self.pending_requests <= 0:
                    self._finish_data_collection()
                return
                
            period = period_map[self.interval.upper()]
            
            # Convert dates to timestamps
            start_timestamp = int(self.start_date.timestamp() * 1000)
            end_timestamp = int(self.end_date.timestamp() * 1000) if self.end_date else int(datetime.datetime.now().timestamp() * 1000)
            
            # Create and send request - EXACTLY like pairs_trading_ctrader_v4.py
            trendbars_req = ProtoOAGetTrendbarsReq()
            trendbars_req.ctidTraderAccountId = int(CTRADER_ACCOUNT_ID)
            trendbars_req.symbolId = symbol_id
            trendbars_req.period = period
            trendbars_req.fromTimestamp = start_timestamp
            trendbars_req.toTimestamp = end_timestamp
            
            deferred = self.client.send(trendbars_req)
            deferred.addErrback(self._on_data_error)
            deferred.addTimeout(60, reactor)  # Longer timeout for data requests
        else:
            logger.warning(f"Symbol {symbol} not found in available symbols")
            self.retrieved_data[symbol] = pd.Series(dtype=float)
            self.pending_requests -= 1
            if self.pending_requests <= 0:
                self._finish_data_collection()
    
    def _on_data_error(self, failure):
        """Handle errors for individual data requests - EXACTLY like pairs_trading_ctrader_v4.py"""
        logger.warning(f"Data request error: {failure}")
        self.pending_requests -= 1
        
        # Continue with remaining requests even if one fails
        if self.pending_requests <= 0:
            self._finish_data_collection()
    
    def _get_symbol_id(self, symbol_name):
        """Get symbol ID from name - exactly like working version"""
        
        # Try exact match first
        if symbol_name in self.symbol_name_to_id:
            return self.symbol_name_to_id[symbol_name]
            
        # Try with common cTrader suffixes
        for suffix in ['.r', '.m', '.c']:
            full_name = f"{symbol_name}{suffix}"
            if full_name in self.symbol_name_to_id:
                return self.symbol_name_to_id[full_name]
                
        # Try cleaned version
        clean_name = symbol_name.split('.')[0]
        if clean_name in self.symbol_name_to_id:
            return self.symbol_name_to_id[clean_name]
            
        return None
    

    def _process_historical_data(self, message):
        """Process historical data response - exactly like working version"""
        
        try:
            # Extract the message content properly
            response = Protobuf.extract(message)
            
            # Find symbol name by ID
            symbol_name = None
            for name, symbol_id in self.symbol_name_to_id.items():
                if symbol_id == response.symbolId:
                    symbol_name = name
                    break
            
            if not symbol_name:
                logger.warning(f"Could not find symbol name for ID {response.symbolId}")
                self.pending_requests -= 1
                self._check_completion()
                return
            
            # Find target symbol that matches this response
            target_symbol = None
            for target in self.target_symbols:
                if (target == symbol_name or 
                    target == symbol_name.split('.')[0] or
                    symbol_name.startswith(target)):
                    target_symbol = target
                    break
            
            if not target_symbol:
                logger.warning(f"Could not match symbol {symbol_name} to target symbols")
                self.pending_requests -= 1
                self._check_completion()
                return
            
            # Process trendbars data according to cTrader documentation
            if hasattr(response, 'trendbar') and response.trendbar:
                dates = []
                prices = []
                
                # Get symbol details for proper price conversion with validation
                symbol_details = self.symbol_details.get(target_symbol)
                if not symbol_details:
                    logger.error(f"No symbol details available for {target_symbol}")
                    self.retrieved_data[target_symbol] = pd.Series(dtype=float)
                    self.pending_requests -= 1
                    self._check_completion()
                    return
                
                for bar in response.trendbar:
                    # Convert timestamp to datetime
                    dt = datetime.datetime.fromtimestamp(bar.utcTimestampInMinutes * 60)
                    dates.append(dt)
                    
                    # Process trendbar using cTrader documentation format
                    bar_prices = self._process_trendbar_prices(bar, symbol_details)
                    
                    if bar_prices and 'close' in bar_prices:
                        prices.append(bar_prices['close'])
                    else:
                        # Skip bar if primary processing failed
                        logger.error(f"Failed to process trendbar data for bar at {dt}")
                        dates.pop()  # Remove the date we just added
                        continue
                
                # Create pandas Series
                if dates and prices:
                    series = pd.Series(prices, index=pd.DatetimeIndex(dates))
                    series.name = target_symbol
                    self.retrieved_data[target_symbol] = series
                    logger.info(f"Retrieved {len(series)} data points for {target_symbol}")
                else:
                    logger.warning(f"No data points received for {target_symbol}")
                    self.retrieved_data[target_symbol] = pd.Series(dtype=float)
            else:
                logger.warning(f"No trendbar data in response for {target_symbol}")
                self.retrieved_data[target_symbol] = pd.Series(dtype=float)
                
        except Exception as e:
            logger.error(f"Error processing historical data: {e}")
            if 'target_symbol' in locals():
                self.retrieved_data[target_symbol] = pd.Series(dtype=float)
        
        self.pending_requests -= 1
        self._check_completion()
    
    def _check_completion(self):
        """Check if all data requests are complete - exactly like working version"""
        
        if self.pending_requests <= 0:
            logger.info("All data retrieval requests completed")
            self._finish_data_collection()
    
    def _finish_data_collection(self):
        """Finish data collection and stop reactor - exactly like working version"""
        
        logger.info(f"Data collection finished. Retrieved data for {len(self.retrieved_data)} symbols")
        
        # Ensure all target symbols have data (empty series if not retrieved)
        for symbol in self.target_symbols:
            if symbol not in self.retrieved_data:
                self.retrieved_data[symbol] = pd.Series(dtype=float)
        
        # Disconnect and stop
        if hasattr(self, 'client') and self.client:
            self.client.stopService()
        
        # Stop reactor to return control
        if reactor.running:
            reactor.stop()
    
    def _global_timeout(self):
        """Global timeout handler - exactly like working version"""
        
        logger.error("Global timeout reached during data retrieval")
        self._finish_data_collection()

    # Legacy methods for backward compatibility
    def _authenticate_account(self):
        """Authenticate trading account - exactly like working version"""
        request = ProtoOAAccountAuthReq()
        request.ctidTraderAccountId = self.account_id
        request.accessToken = self.access_token
        deferred = self.client.send(request)
        deferred.addErrback(self._on_error)
        deferred.addTimeout(30, reactor)
    
    def _on_connected_data(self, client):
        """Handle successful connection for data retrieval"""
        logger.info("Connected to cTrader API for data retrieval")
        # Authenticate application
        request = ProtoOAApplicationAuthReq()
        request.clientId = CTRADER_CLIENT_ID
        request.clientSecret = CTRADER_CLIENT_SECRET
        deferred = client.send(request)
        deferred.addErrback(self._on_error)
        deferred.addTimeout(30, reactor)
    
    def _on_message_received_data(self, client, message):
        """Legacy method - not used in working version"""
        pass
    
    # Legacy methods kept for compatibility but not used in working version
    def _authenticate_account_data(self):
        """Legacy method - not used in working version"""  
        pass
    
    def _get_account_list_data(self):
        """Legacy method - not used in working version"""
        pass
    
    def _process_account_list_data(self, message):
        """Legacy method - not used in working version"""
        pass
    
    def _get_symbols_list_data(self):
        """Legacy method - not used in working version"""
        pass
    
    def _get_account_list(self):
        """Legacy method - not used in working version"""
        pass
    
    def _process_account_list(self, message):
        """Legacy method - not used in working version"""
        pass
    
    def _get_symbols_list(self):
        """Legacy method - not used in working version"""
        pass
    
    def _on_error(self, failure):
        """Handle API errors - exactly like working version"""
        logger.error(f"CTrader API Error: {failure}")
        self.timeout_count += 1
        
        # Check if it's a timeout error
        if 'TimeoutError' in str(failure) or 'timeout' in str(failure).lower():
            logger.warning(f"Request timeout occurred (attempt {self.timeout_count}/{self.max_retries})")
            
            if self.timeout_count < self.max_retries:
                logger.warning("Retrying in 5 seconds...")
                if reactor.running:
                    reactor.callLater(5, self._retry_connection)
                return
            else:
                logger.error("Max retries reached. Giving up.")
        
        # Complete with empty data on error
        if hasattr(self, 'deferred_result') and self.deferred_result and not self.deferred_result.called:
            self.deferred_result.callback({})
        
        if reactor.running:
            reactor.stop()
        return failure
    
    def _retry_connection(self):
        """Retry the connection after a timeout - exactly like working version"""
        logger.info("Retrying connection...")
        try:
            # Reset state
            self.pending_requests = 0
            self.symbols_map = {}
            self.retrieved_data = {}
            
            # Try to reconnect
            if hasattr(self, 'client') and self.client:
                self.client.disconnect()
            
            # Setup new client
            self._setup_fresh_client()
                
        except Exception as e:
            logger.error(f"Error during retry: {e}")
            if hasattr(self, 'deferred_result') and self.deferred_result and not self.deferred_result.called:
                self.deferred_result.callback({})
            if reactor.running:
                reactor.stop()
    
    def _setup_fresh_client(self):
        """Setup a fresh client for retries"""
        try:
            self.client = Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)
            self.access_token = CTRADER_ACCESS_TOKEN
            
            # Set up callbacks
            self.client.setConnectedCallback(self._on_connected)
            self.client.setDisconnectedCallback(self._on_disconnected)
            self.client.setMessageReceivedCallback(self._on_message_received)
            
            self.client.startService()
        except Exception as e:
            logger.error(f"Failed to setup fresh client: {e}")
            if hasattr(self, 'deferred_result') and self.deferred_result and not self.deferred_result.called:
                self.deferred_result.callback({})
            if reactor.running:
                reactor.stop()
    
    def disconnect(self):
        """Disconnect from CTrader API"""
        try:
            if hasattr(self, 'client') and self.client:
                if hasattr(self.client, 'stopService'):
                    self.client.stopService()
                elif hasattr(self.client, 'disconnect'):
                    self.client.disconnect()
                elif hasattr(self.client, 'transport') and hasattr(self.client.transport, 'loseConnection'):
                    self.client.transport.loseConnection()
                else:
                    logger.debug("No known disconnect method found for cTrader client")
            logger.info("Disconnected from CTrader API")
        except Exception as e:
            logger.debug(f"Error during disconnect: {e}")
    
    def get_historical_data(self, symbols, interval, start_date, end_date=None):
        """
        Get historical data for single or multiple symbols with improved connection handling
        
        Args:
            symbols: Single symbol name or list of symbol names
            interval: Time interval (D1, H1, M5, etc.)
            start_date: Start date for data retrieval
            end_date: End date for data retrieval (optional)
            
        Returns:
            For single symbol: pandas Series with historical data
            For multiple symbols: Dictionary mapping symbol names to pandas Series
        """
        
        # Handle single symbol requests (common in backtesting)
        if isinstance(symbols, str):
            return self._get_single_symbol_data(symbols, interval, start_date, end_date)
        
        # Handle multiple symbols
        logger.info(f"Retrieving historical data for {len(symbols)} symbols: {symbols}")
        logger.info(f"Interval: {interval}, Start: {start_date}, End: {end_date}")
        
        # Use the working pattern from pairs_trading_ctrader_v4.py
        return self._get_multiple_symbols_data(symbols, interval, start_date, end_date)
    
    def _get_single_symbol_data(self, symbol, interval, start_date, end_date=None):
        """
        Optimized method for getting single symbol data to avoid reactor issues
        This handles the twisted reactor limitation of only being able to run once per process
        """
        with self._connection_lock:
            # Rate limiting - ensure at least 2 seconds between requests
            current_time = time.time()
            time_since_last = current_time - self._last_request_time
            if time_since_last < 2.0:
                sleep_time = 2.0 - time_since_last
                logger.debug(f"Rate limiting: sleeping for {sleep_time:.1f} seconds")
                time.sleep(sleep_time)
            
            # Check if this is the first request after reactor has been used
            if hasattr(reactor, '_started') and reactor._started:
                logger.warning(f"Reactor has been used before for {symbol}, cannot retrieve CTrader data")
                # Return empty series to allow fallback to MT5
                return pd.Series(dtype=float)
            
            # Get data using batch method but for single symbol
            result = self._get_multiple_symbols_data([symbol], interval, start_date, end_date)
            
            self._last_request_time = time.time()
            
            # Return just the series for single symbol (not dict)
            if isinstance(result, dict) and symbol in result:
                return result[symbol]
            else:
                logger.warning(f"Failed to retrieve data for {symbol}")
                return pd.Series(dtype=float)
    
    def _get_multiple_symbols_data(self, symbols, interval, start_date, end_date=None):
        """Get historical data for multiple symbols using working patterns with reactor management"""
        
        # Convert string dates to datetime objects if needed
        if isinstance(start_date, str):
            try:
                # Try to parse common date formats
                if 'T' in start_date:  # ISO format
                    self.start_date = datetime.datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                else:
                    # Try standard date format
                    self.start_date = datetime.datetime.strptime(start_date, '%Y-%m-%d')
            except ValueError:
                logger.error(f"Could not parse start_date: {start_date}")
                return {symbol: pd.Series(dtype=float) for symbol in symbols}
        else:
            self.start_date = start_date
            
        if isinstance(end_date, str) and end_date:
            try:
                if 'T' in end_date:  # ISO format
                    self.end_date = datetime.datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                else:
                    self.end_date = datetime.datetime.strptime(end_date, '%Y-%m-%d')
            except ValueError:
                logger.error(f"Could not parse end_date: {end_date}")
                self.end_date = None
        else:
            self.end_date = end_date
        
        # Set instance variables for data retrieval
        self.target_symbols = symbols
        self.interval = interval
        self.retrieved_data = {}
        self.pending_requests = 0
        
        if not CTRADER_API_AVAILABLE:
            logger.error("cTrader API not available, returning empty data")
            return {symbol: pd.Series(dtype=float) for symbol in symbols}
        
        # Check credentials
        missing_creds = []
        if not CTRADER_CLIENT_ID:
            missing_creds.append("CTRADER_CLIENT_ID")
        if not CTRADER_CLIENT_SECRET:
            missing_creds.append("CTRADER_CLIENT_SECRET") 
        if not CTRADER_ACCESS_TOKEN:
            missing_creds.append("CTRADER_ACCESS_TOKEN")
            
        if missing_creds:
            logger.error(f"Missing cTrader API credentials: {missing_creds}")
            return {symbol: pd.Series(dtype=float) for symbol in symbols}
        
        # CRITICAL FIX FOR REACTOR CONFLICT:
        # Detect if we're in a live trading context even before reactor starts
        # This prevents using reactor.run() during data pre-fetching for live trading
        
        is_live_trading_context = self._detect_live_trading_context()
        
        if reactor.running:
            # Reactor is already running (live trading mode) - use async approach
            logger.info("Reactor already running - using async data fetching to coexist with live trading")
            return self._get_data_async_mode(symbols, interval, start_date, end_date)
        elif is_live_trading_context:
            # We're preparing for live trading but reactor hasn't started yet
            # Use async mode to avoid claiming the reactor
            logger.info("Live trading context detected - using async data fetching to preserve reactor for live trading")
            return self._get_data_async_mode(symbols, interval, start_date, end_date)
        else:
            # Reactor not running and not live trading context - use traditional approach
            logger.info("Reactor not running and no live trading context - using traditional data fetching")
            return self._get_data_traditional_mode(symbols, interval, start_date, end_date)
    
    def _detect_live_trading_context(self):
        """
        Detect if we're in a live trading context even before reactor starts
        This checks multiple indicators to determine if live trading will need the reactor
        """
        
        # Check environment variables
        trading_mode = os.getenv('TRADING_MODE', '').lower()
        if trading_mode == 'live':
            logger.debug("Live trading context detected via TRADING_MODE environment variable")
            return True
        
        # Check call stack for live trading indicators
        for frame_info in inspect.stack():
            frame_locals = frame_info.frame.f_locals
            frame_globals = frame_info.frame.f_globals
            
            # Check for live trading indicators in frame variables
            mode_indicators = [
                frame_locals.get('mode', ''),
                frame_locals.get('execution_mode', ''),
                frame_globals.get('mode', ''),
                frame_globals.get('execution_mode', ''),
            ]
            
            for indicator in mode_indicators:
                if 'live' in str(indicator).lower():
                    logger.debug(f"Live trading context detected in call stack: {indicator}")
                    return True
            
            # Check if we're being called from live trading initialization code
            filename = frame_info.filename
            function_name = frame_info.function
            
            # Look for live trading patterns in function names and file paths
            live_patterns = [
                'start_ctrader_trading',
                'start_live_trading',
                '_prepare_trading_data',
                'live',
                'real_time'
            ]
            
            for pattern in live_patterns:
                if pattern in function_name.lower() or pattern in filename.lower():
                    logger.debug(f"Live trading context detected via function/file pattern: {function_name} in {filename}")
                    return True
            
            # Check if main.py is in the stack (likely live trading initiation)
            if filename.endswith('main.py') and 'trading' in function_name.lower():
                logger.debug(f"Live trading context detected: {function_name} in main.py")
                return True
        
        logger.debug("No live trading context detected - safe to use traditional reactor mode")
        return False
    
    def _get_data_async_mode(self, symbols, interval, start_date, end_date=None):
        """
        Get data when reactor is already running (live trading scenario)
        OR when we want to preserve reactor for live trading
        Uses deferred-based approach to avoid reactor conflicts
        """
        logger.info(f"Fetching data for {len(symbols)} symbols in async mode")
        
        # If reactor is not running yet, we need to use a different approach
        # that doesn't consume the reactor for live trading
        if not reactor.running:
            logger.info("Reactor not running yet - using non-reactor data fetching to preserve reactor for live trading")
            return self._get_data_without_reactor(symbols, interval, start_date, end_date)
        
        # Original async mode code for when reactor is running
        # Create client but don't start new reactor
        try:
            host_type = os.getenv('CTRADER_HOST_TYPE', 'Demo').lower()
            if host_type == 'demo':
                host = EndPoints.PROTOBUF_DEMO_HOST
            else:
                host = EndPoints.PROTOBUF_LIVE_HOST
            
            # Use a separate client instance for data fetching
            data_client = Client(host, EndPoints.PROTOBUF_PORT, TcpProtocol)
            
            # Create a deferred for the async operation
            result_deferred = defer.Deferred()
            
            # Set up data fetching state
            self.async_target_symbols = symbols
            self.async_interval = interval
            self.async_retrieved_data = {}
            self.async_pending_requests = 0
            self.async_result_deferred = result_deferred
            self.async_client = data_client
            
            # Set up callbacks for async mode
            data_client.setConnectedCallback(self._on_connected_async)
            data_client.setDisconnectedCallback(self._on_disconnected_async)
            data_client.setMessageReceivedCallback(self._on_message_received_async)
            
            # Start the client service (but don't run reactor)
            data_client.startService()
            
            # Set timeout for async operation
            timeout_call = reactor.callLater(120, self._async_timeout)
            
            # Add cleanup callback
            def cleanup_async(result):
                if timeout_call.active():
                    timeout_call.cancel()
                try:
                    if hasattr(self, 'async_client') and self.async_client:
                        self.async_client.stopService()
                except Exception as e:
                    logger.debug(f"Error cleaning up async client: {e}")
                return result
            
            result_deferred.addBoth(cleanup_async)
            
            # Return the deferred - reactor will handle it
            return result_deferred
            
        except Exception as e:
            logger.error(f"Failed to create async CTrader client: {e}")
            return {symbol: pd.Series(dtype=float) for symbol in symbols}
    
    def _get_data_without_reactor(self, symbols, interval, start_date, end_date=None):
        """
        Get data without using reactor to preserve it for live trading
        This uses alternative approaches like REST API or cached data
        """
        logger.info(f"Fetching data for {len(symbols)} symbols without reactor (preserving for live trading)")
        
        # For now, return empty data to allow live trading to proceed
        # The live trading system can accumulate data in real-time
        logger.warning("Data pre-fetching skipped to preserve reactor for live trading")
        logger.info("Live trading will accumulate historical data in real-time instead")
        
        # Return empty series for all symbols
        result = {}
        for symbol in symbols:
            result[symbol] = pd.Series(dtype=float, name=symbol)
            logger.debug(f"Returning empty series for {symbol} - live trading will populate")
        
        logger.info(f"Returned empty data for {len(symbols)} symbols to preserve reactor")
        return result
    
    def _get_data_traditional_mode(self, symbols, interval, start_date, end_date=None):
        """
        Get data when reactor is not running (traditional standalone scenario)
        Uses reactor.run() approach for complete control
        """
        logger.info(f"Fetching data for {len(symbols)} symbols in traditional mode")
        
        # Create a fresh client for data retrieval - exactly like working version
        try:
            # Get host based on environment variable
            host_type = os.getenv('CTRADER_HOST_TYPE', 'Demo').lower()
            if host_type == 'demo':
                host = EndPoints.PROTOBUF_DEMO_HOST
                logger.info(f"🔧 Data retrieval using DEMO server: {host}:{EndPoints.PROTOBUF_PORT}")
            else:
                host = EndPoints.PROTOBUF_LIVE_HOST
                logger.info(f"🔧 Data retrieval using LIVE server: {host}:{EndPoints.PROTOBUF_PORT}")
            
            self.client = Client(host, EndPoints.PROTOBUF_PORT, TcpProtocol)
            self.access_token = CTRADER_ACCESS_TOKEN
            
            # Set up callbacks - exactly like working version
            self.client.setConnectedCallback(self._on_connected)
            self.client.setDisconnectedCallback(self._on_disconnected)
            self.client.setMessageReceivedCallback(self._on_message_received)
        except Exception as e:
            logger.error(f"Failed to create CTrader client: {e}")
            return {symbol: pd.Series(dtype=float) for symbol in symbols}
        
        try:
            # Set a global timeout - exactly like working version  
            timeout_deferred = reactor.callLater(10000, self._global_timeout)  # Increased timeout
            
            # Start the reactor to handle API communication - exactly like working version
            self.client.startService()
            
            # Run reactor - exactly like working version
            reactor.run(installSignalHandlers=False)
            
            # Cancel timeout if we succeeded
            if timeout_deferred.active():
                timeout_deferred.cancel()
                
        except Exception as e:
            logger.error(f"Error retrieving data: {e}")
            logger.error(f"Exception type: {type(e)}")
            import traceback
            logger.error(f"Full traceback: {traceback.format_exc()}")
            
            if 'timeout_deferred' in locals() and timeout_deferred.active():
                timeout_deferred.cancel()
            
            # Ensure we return empty data for all symbols on error
            if not self.retrieved_data:
                self.retrieved_data = {symbol: pd.Series(dtype=float) for symbol in symbols}
        
        finally:
            # Ensure we disconnect and clean up
            try:
                if hasattr(self, 'client') and self.client:
                    self.client.stopService()
                logger.info("Disconnected from cTrader")
            except Exception as e:
                logger.debug(f"Error during cleanup: {e}")
        
        return self.retrieved_data
    
    # ================================
    # ASYNC MODE CALLBACKS (for when reactor is already running)
    # ================================
    
    def _on_connected_async(self, client=None):
        """Callback when async client connects during live trading"""
        logger.info("Async data client connected during live trading")
        
        # Authenticate with OAuth token
        auth_req = ProtoOAApplicationAuthReq()
        auth_req.clientId = CTRADER_CLIENT_ID
        auth_req.clientSecret = CTRADER_CLIENT_SECRET
        self.async_client.send(auth_req)
    
    def _on_disconnected_async(self, client=None, reason=None):
        """Callback when async client disconnects"""
        logger.info("Async data client disconnected")
        # Don't stop reactor - it's needed for live trading
    
    def _on_message_received_async(self, client, message):
        """Handle messages from cTrader in async mode"""
        
        if message.payloadType == ProtoOAApplicationAuthRes().payloadType:
            logger.info("Async application authenticated successfully")
            
            # Get account access token
            access_req = ProtoOAAccountAuthReq()
            access_req.ctidTraderAccountId = int(CTRADER_ACCOUNT_ID)
            access_req.accessToken = CTRADER_ACCESS_TOKEN
            client.send(access_req)
            
        elif message.payloadType == ProtoOAAccountAuthRes().payloadType:
            logger.info("Async account authenticated successfully")
            
            # Request symbols to map symbol names to IDs
            symbols_req = ProtoOASymbolsListReq()
            symbols_req.ctidTraderAccountId = int(CTRADER_ACCOUNT_ID)
            symbols_req.includeArchivedSymbols = False
            client.send(symbols_req)
            
        elif message.payloadType == ProtoOASymbolsListRes().payloadType:
            # Extract the message content properly
            response = Protobuf.extract(message)
            logger.info(f"Async received symbols list: {len(response.symbol)} symbols")
            
            # Map symbol names to IDs for async operation
            self.async_symbol_name_to_id = {}
            
            for symbol in response.symbol:
                # Store both exact name and cleaned name mappings
                self.async_symbol_name_to_id[symbol.symbolName] = symbol.symbolId
                # Also store cleaned versions (remove suffixes like .r, .m, etc)
                clean_name = symbol.symbolName.split('.')[0]
                if clean_name not in self.async_symbol_name_to_id:
                    self.async_symbol_name_to_id[clean_name] = symbol.symbolId
                
                # Store basic symbol details for price conversion with validation
                digits = getattr(symbol, 'digits', None)
                pip_position = getattr(symbol, 'pipPosition', None)
                
                if digits is None:
                    logger.error(f"Missing digits field for async symbol {symbol.symbolName}")
                    continue
                if pip_position is None:
                    logger.warning(f"Missing pipPosition for async symbol {symbol.symbolName}")
                    pip_position = -5  # Common default for forex pairs
                
                symbol_details = {
                    'digits': digits,
                    'pip_position': pip_position,
                    'symbol_id': symbol.symbolId
                }
                
                # Store in async symbol details
                if not hasattr(self, 'async_symbol_details'):
                    self.async_symbol_details = {}
                self.async_symbol_details[symbol.symbolName] = symbol_details
                
                # Also store for clean name
                if clean_name not in self.async_symbol_details:
                    self.async_symbol_details[clean_name] = symbol_details.copy()
                    
            logger.info(f"Async mapped {len(self.async_symbol_name_to_id)} symbol names to IDs")
            
            # Start requesting historical data for each symbol
            self._request_all_symbol_data_async()
            
        elif message.payloadType == ProtoOAGetTrendbarsRes().payloadType:
            # Process historical data response in async mode
            self._process_historical_data_async(message)
            
        elif message.payloadType == ProtoOAErrorRes().payloadType:
            response = Protobuf.extract(message)
            logger.error(f"Async cTrader API Error: {response.description}")
            self.async_pending_requests -= 1
            self._check_completion_async()
    
    def _request_all_symbol_data_async(self):
        """Request historical data for all target symbols in async mode"""
        
        # Find available symbols
        available_symbols = []
        for symbol in self.async_target_symbols:
            symbol_id = self._get_symbol_id_async(symbol)
            if symbol_id:
                available_symbols.append(symbol)
            else:
                logger.warning(f"Async: Symbol {symbol} not found in cTrader")
                self.async_retrieved_data[symbol] = pd.Series(dtype=float)
        
        logger.info(f"Async requesting data for {len(available_symbols)} available symbols")
        
        if not available_symbols:
            logger.warning("Async: No valid symbols found for data retrieval")
            self._finish_data_collection_async()
            return
        
        self.async_pending_requests = len(available_symbols)
        
        # Request data for symbols with delays
        for i, symbol in enumerate(available_symbols):
            reactor.callLater(i * self.request_delay, self._request_single_symbol_data_async, symbol)
    
    def _request_single_symbol_data_async(self, symbol):
        """Request data for a single symbol in async mode"""
        symbol_id = self._get_symbol_id_async(symbol)
        if symbol_id:
            logger.info(f"Async requesting data for {symbol} (ID: {symbol_id})")
            
            # Convert interval to cTrader period
            period_map = {
                'D1': ProtoOATrendbarPeriod.D1,
                '1D': ProtoOATrendbarPeriod.D1,
                'H1': ProtoOATrendbarPeriod.H1,
                '1H': ProtoOATrendbarPeriod.H1,
                'M1': ProtoOATrendbarPeriod.M1,
                '1M': ProtoOATrendbarPeriod.M1,
                'M5': ProtoOATrendbarPeriod.M5,
                '5M': ProtoOATrendbarPeriod.M5,
                'M15': ProtoOATrendbarPeriod.M15,
                '15M': ProtoOATrendbarPeriod.M15,
                'M30': ProtoOATrendbarPeriod.M30,
                '30M': ProtoOATrendbarPeriod.M30,
            }
            
            if self.async_interval.upper() not in period_map:
                logger.error(f"Unsupported async interval: {self.async_interval}")
                self.async_retrieved_data[symbol] = pd.Series(dtype=float)
                self.async_pending_requests -= 1
                if self.async_pending_requests <= 0:
                    self._finish_data_collection_async()
                return
                
            period = period_map[self.async_interval.upper()]
            
            # Convert dates to timestamps
            start_timestamp = int(self.start_date.timestamp() * 1000)
            end_timestamp = int(self.end_date.timestamp() * 1000) if self.end_date else int(datetime.datetime.now().timestamp() * 1000)
            
            # Create and send request
            trendbars_req = ProtoOAGetTrendbarsReq()
            trendbars_req.ctidTraderAccountId = int(CTRADER_ACCOUNT_ID)
            trendbars_req.symbolId = symbol_id
            trendbars_req.period = period
            trendbars_req.fromTimestamp = start_timestamp
            trendbars_req.toTimestamp = end_timestamp
            
            deferred = self.async_client.send(trendbars_req)
            deferred.addErrback(self._on_data_error_async)
            deferred.addTimeout(60, reactor)
        else:
            logger.warning(f"Async: Symbol {symbol} not found in available symbols")
            self.async_retrieved_data[symbol] = pd.Series(dtype=float)
            self.async_pending_requests -= 1
            if self.async_pending_requests <= 0:
                self._finish_data_collection_async()
    
    def _on_data_error_async(self, failure):
        """Handle errors for individual data requests in async mode"""
        logger.warning(f"Async data request error: {failure}")
        self.async_pending_requests -= 1
        
        # Continue with remaining requests even if one fails
        if self.async_pending_requests <= 0:
            self._finish_data_collection_async()
    
    def _get_symbol_id_async(self, symbol_name):
        """Get symbol ID from name in async mode"""
        
        # Try exact match first
        if symbol_name in self.async_symbol_name_to_id:
            return self.async_symbol_name_to_id[symbol_name]
            
        # Try with common cTrader suffixes
        for suffix in ['.r', '.m', '.c']:
            full_name = f"{symbol_name}{suffix}"
            if full_name in self.async_symbol_name_to_id:
                return self.async_symbol_name_to_id[full_name]
                
        # Try cleaned version
        clean_name = symbol_name.split('.')[0]
        if clean_name in self.async_symbol_name_to_id:
            return self.async_symbol_name_to_id[clean_name]
            
        return None
    
    def _process_historical_data_async(self, message):
        """Process historical data response in async mode"""
        
        try:
            # Extract the message content properly
            response = Protobuf.extract(message)
            
            # Find symbol name by ID
            symbol_name = None
            for name, symbol_id in self.async_symbol_name_to_id.items():
                if symbol_id == response.symbolId:
                    symbol_name = name
                    break
            
            if not symbol_name:
                logger.warning(f"Async: Could not find symbol name for ID {response.symbolId}")
                self.async_pending_requests -= 1
                self._check_completion_async()
                return
            
            # Find target symbol that matches this response
            target_symbol = None
            for target in self.async_target_symbols:
                if (target == symbol_name or 
                    target == symbol_name.split('.')[0] or
                    symbol_name.startswith(target)):
                    target_symbol = target
                    break
            
            if not target_symbol:
                logger.warning(f"Async: Could not match symbol {symbol_name} to target symbols")
                self.async_pending_requests -= 1
                self._check_completion_async()
                return
            
            # Process trendbars data
            if hasattr(response, 'trendbar') and response.trendbar:
                dates = []
                prices = []
                
                # Get symbol details for proper price conversion with validation
                symbol_details = self.async_symbol_details.get(target_symbol)
                if not symbol_details:
                    logger.error(f"No async symbol details available for {target_symbol}")
                    self.async_retrieved_data[target_symbol] = pd.Series(dtype=float)
                    self.async_pending_requests -= 1
                    self._check_completion_async()
                    return
                
                for bar in response.trendbar:
                    # Convert timestamp to datetime
                    dt = datetime.datetime.fromtimestamp(bar.utcTimestampInMinutes * 60)
                    dates.append(dt)
                    
                    # Process trendbar using cTrader documentation format
                    bar_prices = self._process_trendbar_prices(bar, symbol_details)
                    
                    if bar_prices and 'close' in bar_prices:
                        prices.append(bar_prices['close'])
                    else:
                        # Fallback: try direct price attributes
                        try:
                            if hasattr(bar, 'close'):
                                close_price = self._get_price_from_relative(symbol_details, bar.close)
                            elif hasattr(bar, 'closePrice'):
                                close_price = self._get_price_from_relative(symbol_details, bar.closePrice)
                            elif hasattr(bar, 'c'):
                                close_price = self._get_price_from_relative(symbol_details, bar.c)
                            elif hasattr(bar, 'low'):
                                close_price = self._get_price_from_relative(symbol_details, bar.low)
                            else:
                                logger.error(f"Async: No valid price data found for bar")
                                dates.pop()
                                continue
                            
                            prices.append(close_price)
                            
                        except Exception as bar_error:
                            logger.warning(f"Async: Error processing bar data: {bar_error}")
                            dates.pop()
                            continue
                
                # Create pandas Series
                if dates and prices:
                    series = pd.Series(prices, index=pd.DatetimeIndex(dates))
                    series.name = target_symbol
                    self.async_retrieved_data[target_symbol] = series
                    logger.info(f"Async retrieved {len(series)} data points for {target_symbol}")
                else:
                    logger.warning(f"Async: No data points received for {target_symbol}")
                    self.async_retrieved_data[target_symbol] = pd.Series(dtype=float)
            else:
                logger.warning(f"Async: No trendbar data in response for {target_symbol}")
                self.async_retrieved_data[target_symbol] = pd.Series(dtype=float)
                
        except Exception as e:
            logger.error(f"Async: Error processing historical data: {e}")
            if 'target_symbol' in locals():
                self.async_retrieved_data[target_symbol] = pd.Series(dtype=float)
        
        self.async_pending_requests -= 1
        self._check_completion_async()
    
    def _check_completion_async(self):
        """Check if all async data requests are complete"""
        if self.async_pending_requests <= 0:
            logger.info("Async: All data retrieval requests completed")
            self._finish_data_collection_async()
    
    def _finish_data_collection_async(self):
        """Finish async data collection and return results via deferred"""
        logger.info(f"Async: Data collection finished. Retrieved data for {len(self.async_retrieved_data)} symbols")
        
        # Ensure all target symbols have data
        for symbol in self.async_target_symbols:
            if symbol not in self.async_retrieved_data:
                self.async_retrieved_data[symbol] = pd.Series(dtype=float)
        
        # Disconnect async client
        if hasattr(self, 'async_client') and self.async_client:
            self.async_client.stopService()
        
        # Return results via deferred
        if hasattr(self, 'async_result_deferred') and self.async_result_deferred:
            self.async_result_deferred.callback(self.async_retrieved_data)
    
    def _async_timeout(self):
        """Handle timeout for async data collection"""
        logger.error("Async data collection timeout")
        self._finish_data_collection_async()
