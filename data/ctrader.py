import numpy as np
import pandas as pd
from statsmodels.tsa.stattools import adfuller
from statsmodels.tsa.vector_ar.vecm import coint_johansen
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
    
    # Additional imports for real-time trading
    ProtoOASubscribeSpotsReq = OpenApiMessages_pb2.ProtoOASubscribeSpotsReq
    ProtoOASubscribeSpotsRes = OpenApiMessages_pb2.ProtoOASubscribeSpotsRes
    ProtoOASpotEvent = OpenApiMessages_pb2.ProtoOASpotEvent
    ProtoOAUnsubscribeSpotsReq = OpenApiMessages_pb2.ProtoOAUnsubscribeSpotsReq
    ProtoOANewOrderReq = OpenApiMessages_pb2.ProtoOANewOrderReq
    ProtoOAExecutionEvent = OpenApiMessages_pb2.ProtoOAExecutionEvent
    ProtoOAClosePositionReq = OpenApiMessages_pb2.ProtoOAClosePositionReq
    ProtoOAAmendPositionSLTPReq = OpenApiMessages_pb2.ProtoOAAmendPositionSLTPReq
    ProtoOAPosition = OpenApiModelMessages_pb2.ProtoOAPosition
    ProtoOAOrderType = OpenApiModelMessages_pb2.ProtoOAOrderType
    ProtoOATradeSide = OpenApiModelMessages_pb2.ProtoOATradeSide
    
    CTRADER_API_AVAILABLE = True
except ImportError as e:
    print(f"cTrader Open API not available: {e}")
    print("Install with: pip install ctrader-open-api")
    CTRADER_API_AVAILABLE = False

CONFIG = get_config()
# Setup logging
log_file_path = os.path.join(CONFIG.logs_dir, "ctrader_api.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)



# === CTRADER API SETUP ===
def load_ctrader_env(env_path=None):
    """Load .env file for cTrader API credentials. Only loads once per process."""
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

# Load .env only if not already loaded (for import safety)
if not os.getenv('CTRADER_CLIENT_ID'):
    load_ctrader_env()

creds = get_ctrader_credentials()
CTRADER_CLIENT_ID = creds['CTRADER_CLIENT_ID']
CTRADER_CLIENT_SECRET = creds['CTRADER_CLIENT_SECRET']
CTRADER_ACCESS_TOKEN = creds['CTRADER_ACCESS_TOKEN']
CTRADER_ACCOUNT_ID = creds['CTRADER_ACCOUNT_ID']

# Optional: print .env contents for debugging only if run as script
if __name__ == "__main__":
    if os.path.exists('.env'):
        with open('.env', 'r') as f:
            print("Contents of .env file:")
            for line_num, line in enumerate(f.readlines(), 1):
                if 'CTRADER_ACCOUNT_ID' in line:
                    print(f"  Line {line_num}: {line.strip()}")
    print("="*40)

# Global variable to store retrieved data
retrieved_data = {}
api_client = None
current_account_id = None

# === CTRADER AUTHENTICATION AND DATA RETRIEVAL ===
class CTraderDataManager:
    # Class-level circuit breaker to prevent runaway connections
    _global_connection_attempts = 0
    _max_global_attempts = 50  # Increase limit for backtesting
    _last_reset_time = time.time()
    
    def __init__(self, config: TradingConfig):
        self.config = config
        self.client = None
        self.account_id = None
        self.access_token = None
        self.symbols_map = {}  # Map symbol names to IDs
        self.symbol_id_to_name_map = {} # Reverse map for performance
        self.symbol_details = {}  # <-- Add this line to always define symbol_details
        self.retrieved_data = {}
        self.pending_requests = 0
        self.deferred_result = None
        self.available_accounts = []
        self.timeout_count = 0
        self.max_retries = 3
        self.request_delay = 1.0  # Delay between requests
        self.connection_established = False  # Track if this instance has established a connection
        
        # Instance-level connection state to avoid multiple connections per instance
        self.connection_established = False
        self.symbols_loaded = False
        
        # Check global circuit breaker
        self._check_circuit_breaker()
    
    @classmethod
    def _check_circuit_breaker(cls):
        """Check if we should reset the global connection counter"""
        current_time = time.time()
        # Reset counter every 10 minutes
        if current_time - cls._last_reset_time > 600:
            cls._global_connection_attempts = 0
            cls._last_reset_time = current_time
            print("Circuit breaker reset - connection attempts counter reset")
    
    @classmethod
    def _increment_connection_attempts(cls):
        """Increment global connection attempts and check if we should stop"""
        cls._global_connection_attempts += 1
        if cls._global_connection_attempts > cls._max_global_attempts:
            print(f"Circuit breaker triggered: {cls._global_connection_attempts} total connection attempts exceeded limit of {cls._max_global_attempts}")
            return False
        return True
        
    def setup_client(self):
        """Setup cTrader client connection"""
        if not CTRADER_API_AVAILABLE:
            return None
        
        # Only count actual client setups, not reuse of existing connections
        if not self.connection_established:
            # Check circuit breaker only for new connections
            if not self._increment_connection_attempts():
                print("Circuit breaker: Too many connection attempts. Aborting.")
                return None
            
        # Use demo environment for testing
        self.client = Client(EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT, TcpProtocol)
        self.access_token = CTRADER_ACCESS_TOKEN
        
        # Try to parse account ID, if not available we'll get it from API
        if CTRADER_ACCOUNT_ID and CTRADER_ACCOUNT_ID.strip():
            try:
                self.account_id = int(CTRADER_ACCOUNT_ID.strip())
                print(f"Using account ID: {self.account_id}")
            except ValueError as e:
                print(f"Invalid account ID format: '{CTRADER_ACCOUNT_ID}' - {e}")
                self.account_id = None
        else:
            print("No account ID provided, will attempt to get from API")
            self.account_id = None
        
        # Set up callbacks
        self.client.setConnectedCallback(self._on_connected)
        self.client.setDisconnectedCallback(self._on_disconnected)
        self.client.setMessageReceivedCallback(self._on_message_received)
        
        return self.client
    
    def _on_connected(self, client):
        print("Connected to cTrader API")
        self.connection_established = True  # Mark connection as established
        
        # Authenticate application
        request = ProtoOAApplicationAuthReq()
        request.clientId = CTRADER_CLIENT_ID
        request.clientSecret = CTRADER_CLIENT_SECRET
        deferred = client.send(request)
        deferred.addErrback(self._on_error)
        # Add timeout to prevent hanging
        deferred.addTimeout(30, reactor)
    
    def _on_disconnected(self, client, reason):
        print(f"Disconnected from cTrader API: {reason}")

    def _on_error(self, failure):
        print(f"API Error: {failure}")
        
        # Check if it's a timeout error
        if 'TimeoutError' in str(failure) or 'timeout' in str(failure).lower():
            self.timeout_count += 1
            print(f"Request timeout occurred (attempt {self.timeout_count}/{self.max_retries})")
            
            if self.timeout_count < self.max_retries:
                print("Retrying in 5 seconds...")
                # Use callLater to prevent immediate recursion
                reactor.callLater(5, self._retry_connection)
                return
            else:
                print("Max retries reached. Giving up.")
        
        # Complete with empty data on error - always call this to stop infinite loops
        self._complete_with_empty_data()
    
    def _complete_with_empty_data(self):
        """Complete the request with empty data and stop any further retries"""
        print("Completing with empty data...")
        
        # Mark as completed to prevent further retries
        self.timeout_count = self.max_retries
        
        # Complete deferred if not already done
        if self.deferred_result and not self.deferred_result.called:
            self.deferred_result.callback({})
        
        # Try to stop reactor safely
        try:
            import signal
            if (reactor.running and 
                threading.current_thread() is threading.main_thread() and
                hasattr(signal, 'SIGINT')):
                reactor.stop()
        except Exception:
            pass
    
    def _retry_connection(self):
        """Retry the connection after a timeout"""
        print("Retrying connection...")
        try:
            # Prevent further retries if we've exceeded max attempts
            if self.timeout_count >= self.max_retries:
                print("Max retries exceeded in retry attempt. Stopping.")
                self._complete_with_empty_data()
                return
            
            # Reset state for retry
            self.pending_requests = 0
            self.symbols_map = {}
            self.retrieved_data = {}
            
            # Disconnect current client if exists
            if self.client:
                try:
                    self.client.disconnect()
                except Exception:
                    pass
                self.client = None
            
            # Setup new client
            client = self.setup_client()
            if client:
                client.startService()
            else:
                print("Failed to setup client for retry")
                self._complete_with_empty_data()
                
        except Exception as e:
            print(f"Error during retry: {e}")
            self._complete_with_empty_data()
    
    def _authenticate_account(self):
        """Authenticate trading account"""
        request = ProtoOAAccountAuthReq()
        request.ctidTraderAccountId = self.account_id
        request.accessToken = self.access_token
        deferred = self.client.send(request)
        deferred.addErrback(self._on_error)
        deferred.addTimeout(30, reactor)
    
    def _get_account_list(self):
        """Get list of available accounts"""
        print("Getting account list...")
        request = OpenApiMessages_pb2.ProtoOAGetAccountListByAccessTokenReq()
        request.accessToken = self.access_token
        deferred = self.client.send(request)
        deferred.addErrback(self._on_error)
        deferred.addTimeout(30, reactor)
    
    def _process_account_list(self, message):
        """Process account list response"""
        response = Protobuf.extract(message)
        print("Available accounts:")
        for account in response.ctidTraderAccount:
            print(f"  Account ID: {account.ctidTraderAccountId}")
            print(f"  Broker: {account.brokerName}")
            print(f"  Live: {'Yes' if account.live else 'No'}")
            print(f"  Deposit Currency: {account.depositCurrency}")
            self.available_accounts.append(account.ctidTraderAccountId)
        
        if self.available_accounts:
            # Use the first available account
            self.account_id = self.available_accounts[0]
            print(f"Using first available account: {self.account_id}")
            self._authenticate_account()
        else:
            print("No accounts available")
            self._complete_with_empty_data()
    
    def _get_symbols_list(self):
        """Get list of available symbols"""
        request = ProtoOASymbolsListReq()
        request.ctidTraderAccountId = self.account_id
        request.includeArchivedSymbols = False
        deferred = self.client.send(request)
        deferred.addErrback(self._on_error)
        deferred.addTimeout(30, reactor)
    
    def _process_symbols_list(self, message):
        """Process symbols list response"""
        response = Protobuf.extract(message)
        for symbol in response.symbol:
            # Map symbol name to ID and vice-versa
            self.symbols_map[symbol.symbolName] = symbol.symbolId
            self.symbol_id_to_name_map[symbol.symbolId] = symbol.symbolName
        
        print(f"Loaded {len(self.symbols_map)} symbols")
        
        # Check if our target symbols are available
        missing_symbols = []
        available_symbols = []
        
        for symbol in self.target_symbols:
            if symbol not in self.symbols_map:
                missing_symbols.append(symbol)
            else:
                available_symbols.append(symbol)
        
        if missing_symbols:
            print(f"Warning: These symbols were not found: {missing_symbols}")
            
            # Try to find similar symbols
            for missing in missing_symbols:
                similar = [s for s in list(self.symbols_map.keys())[:20] if missing.lower() in s.lower() or s.lower() in missing.lower()]
                if similar:
                    print(f"  Similar to '{missing}': {similar[:5]}")
        
        if available_symbols:
            print(f"Found {len(available_symbols)} available symbols: {available_symbols[:5]}{'...' if len(available_symbols) > 5 else ''}")
        
        # Start requesting data for each symbol
        self._request_historical_data()
    
    def _request_historical_data(self):
        """Request historical data for all symbols"""
        if not hasattr(self, 'target_symbols'):
            return
            
        # Calculate time range
        start_dt = pd.to_datetime(self.config.start_date)
        end_dt = pd.to_datetime(self.config.end_date) if self.config.end_date else pd.Timestamp.now()
        
        from_timestamp = int(calendar.timegm(start_dt.utctimetuple())) * 1000
        to_timestamp = int(calendar.timegm(end_dt.utctimetuple())) * 1000
        
        # Map interval to cTrader period
        period_map = {
            "M1": "M1", "M5": "M5", "M15": "M15", "M30": "M30", 
            "H1": "H1", "H4": "H4", "D1": "D1", "W1": "W1", "MN1": "MN1"
        }
        period = period_map.get(self.config.interval, "M15")
        
        # Filter symbols to only those available
        available_symbols = [s for s in self.target_symbols if s in self.symbols_map]
        print(f"Requesting data for {len(available_symbols)} available symbols out of {len(self.target_symbols)} requested")
        
        if not available_symbols:
            print("No symbols available for data request")
            self._complete_data_retrieval()
            return
        
        self.pending_requests = len(available_symbols)
        
        # Request data for symbols one by one with delays to avoid rate limiting
        for i, symbol in enumerate(available_symbols):
            reactor.callLater(i * self.request_delay, self._request_symbol_data, 
                            symbol, from_timestamp, to_timestamp, period)
    
    def _request_symbol_data(self, symbol, from_timestamp, to_timestamp, period):
        """Request data for a single symbol"""
        if symbol in self.symbols_map:
            symbol_id = self.symbols_map[symbol]
            print(f"Requesting data for {symbol} (ID: {symbol_id})")
            
            request = ProtoOAGetTrendbarsReq()
            request.ctidTraderAccountId = self.account_id
            request.symbolId = symbol_id
            request.period = ProtoOATrendbarPeriod.Value(period)
            request.fromTimestamp = from_timestamp
            request.toTimestamp = to_timestamp
            
            deferred = self.client.send(request)
            deferred.addErrback(self._on_data_error)
            deferred.addTimeout(60, reactor)  # Longer timeout for data requests
        else:
            print(f"Symbol {symbol} not found in available symbols")
            self.retrieved_data[symbol] = pd.Series(dtype=float)
            self.pending_requests -= 1
            if self.pending_requests <= 0:
                self._complete_data_retrieval()
    
    def _on_data_error(self, failure):
        """Handle errors for individual data requests"""
        print(f"Data request error: {failure}")
        
        # Increment timeout counter for data errors too
        if 'TimeoutError' in str(failure) or 'timeout' in str(failure).lower():
            self.timeout_count += 1
            if self.timeout_count >= self.max_retries:
                print("Max data request timeouts reached. Stopping.")
                self._complete_with_empty_data()
                return
        
        self.pending_requests -= 1
        
        # Continue with remaining requests even if one fails
        if self.pending_requests <= 0:
            self._complete_data_retrieval()
    
    def _process_trendbar_data(self, message):
        """Process trendbar data response"""
        response = Protobuf.extract(message)
        
        # Find symbol name from ID using the reverse map
        symbol_name = self.symbol_id_to_name_map.get(response.symbolId)
        
        if not symbol_name:
            print(f"Unknown symbol ID: {response.symbolId}")
            self.pending_requests -= 1
            return
        
        # Extract price data
        closes = []
        timestamps = []
        
        print(f"Debug: Processing trendbar data for {symbol_name}")
        print(f"Debug: Response has {len(response.trendbar) if hasattr(response, 'trendbar') else 0} bars")
        
        if hasattr(response, 'trendbar') and len(response.trendbar) > 0:
            # Debug: Print first bar structure
            first_bar = response.trendbar[0]
            
            for bar in response.trendbar:
                try:
                    # Try different possible attribute names for close price
                    close_price = None
                    if hasattr(bar, 'close'):
                        close_price = bar.close
                    elif hasattr(bar, 'closePrice'):
                        close_price = bar.closePrice
                    elif hasattr(bar, 'c'):
                        close_price = bar.c
                    elif hasattr(bar, 'low'):  # If close doesn't exist, try using low as fallback
                        close_price = bar.low
                    
                    if close_price is not None:
                        # Adjust for pip factor if available
                        pip_factor = getattr(response, 'pipFactor', 0)
                        adjusted_price = close_price / (10 ** pip_factor) if pip_factor > 0 else close_price
                        closes.append(adjusted_price)
                        
                        # Try different timestamp attribute names
                        timestamp = None
                        if hasattr(bar, 'utcTimestampInMinutes'):
                            timestamp = pd.to_datetime(bar.utcTimestampInMinutes * 60, unit='s')
                        elif hasattr(bar, 'timestamp'):
                            timestamp = pd.to_datetime(bar.timestamp, unit='ms')
                        elif hasattr(bar, 'time'):
                            timestamp = pd.to_datetime(bar.time, unit='s')
                        
                        if timestamp:
                            timestamps.append(timestamp)
                        else:
                            print(f"Debug: No timestamp found for bar")
                            break
                    else:
                        print(f"Debug: No close price found for bar. Available attributes: {[attr for attr in dir(bar) if not attr.startswith('_')]}")
                        break
                        
                except Exception as e:
                    print(f"Debug: Error processing bar: {e}")
                    break
        else:
            print(f"Debug: No trendbar data in response for {symbol_name}")
            if hasattr(response, 'trendbar'):
                print(f"Debug: trendbar length: {len(response.trendbar)}")
            else:
                print(f"Debug: Response attributes: {[attr for attr in dir(response) if not attr.startswith('_')]}")
        
        if closes and timestamps and len(closes) == len(timestamps):
            series = pd.Series(closes, index=timestamps)
            self.retrieved_data[symbol_name] = series
            print(f"Retrieved {len(series)} bars for {symbol_name}")
        else:
            print(f"No valid data for {symbol_name} - closes: {len(closes)}, timestamps: {len(timestamps)}")
            self.retrieved_data[symbol_name] = pd.Series(dtype=float)
        
        self.pending_requests -= 1
        if self.pending_requests <= 0:
            self._complete_data_retrieval()
    def _complete_data_retrieval(self):
        """Complete data retrieval process"""
        print("Data retrieval completed")
        if self.deferred_result and not self.deferred_result.called:
            self.deferred_result.callback(self.retrieved_data)
        
        # Only stop reactor safely
        try:
            import signal
            if (reactor.running and 
                threading.current_thread() is threading.main_thread() and
                hasattr(signal, 'SIGINT')):
                reactor.stop()
        except Exception:
            pass
    
    def get_historical_data(self, symbols, interval, start, end=None):
        """Get historical data for symbols
        
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
        
        print(f"CTrader: Requesting data for {symbol_list}")
        
        # Check circuit breaker first
        if not self._increment_connection_attempts():
            print("Circuit breaker: Too many global connection attempts. Returning empty data.")
            if return_single:
                return pd.Series(dtype=float)
            else:
                return {symbol: pd.Series(dtype=float) for symbol in symbol_list}
        
        # Reset state for new request
        self.target_symbols = symbol_list
        self.timeout_count = 0  # Reset timeout counter for new request
        self.retrieved_data = {}
        self.deferred_result = defer.Deferred()
        
        if not CTRADER_API_AVAILABLE:
            print("cTrader API not available, returning empty data")
            if return_single:
                return pd.Series(dtype=float)
            else:
                return {symbol: pd.Series(dtype=float) for symbol in symbol_list}
        
        # Setup client first to initialize account_id
        client = self.setup_client()
        if not client:
            print("Failed to setup client")
            if return_single:
                return pd.Series(dtype=float)
            else:
                return {symbol: pd.Series(dtype=float) for symbol in symbol_list}
        
        try:
            # Start the reactor to handle API communication
            client.startService()
            
            # Add a shorter global timeout
            timeout_deferred = reactor.callLater(60, self._global_timeout)  # 1 minute timeout
            
            def cleanup_timeout(result):
                if timeout_deferred.active():
                    timeout_deferred.cancel()
                return result
            
            self.deferred_result.addBoth(cleanup_timeout)
            
            # Simple approach: just run reactor if not running, otherwise wait
            if not reactor.running:
                reactor.run(installSignalHandlers=False)
            else:
                # Wait for result with shorter timeout
                timeout = 60  # 1 minute
                start_time = time.time()
                while not self.deferred_result.called and (time.time() - start_time) < timeout:
                    time.sleep(0.1)
                
                if not self.deferred_result.called:
                    print("Timeout waiting for data retrieval")
                    self.deferred_result.callback({})
            
        except Exception as e:
            print(f"Exception during data retrieval: {e}")
            if return_single:
                return pd.Series(dtype=float)
            else:
                return {symbol: pd.Series(dtype=float) for symbol in symbol_list}
        
        # Return the result - handle both single symbol and multiple symbols  
        if self.deferred_result.called:
            try:
                # Access the result directly from the deferred
                if hasattr(self.deferred_result, 'result'):
                    result = self.deferred_result.result
                else:
                    result = {}
            except Exception as e:
                print(f"Error accessing deferred result: {e}")
                result = {}
        else:
            result = {}
        
        if return_single:
            # Return Series for single symbol
            symbol = symbol_list[0]
            return result.get(symbol, pd.Series(dtype=float))
        else:
            # Return dict for multiple symbols
            return result
    
    def _global_timeout(self):
        """Handle global timeout for the entire operation"""
        print("Global timeout reached. Stopping data retrieval.")
        
        # Store empty data for all requested symbols
        for symbol in getattr(self, 'target_symbols', []):
            if symbol not in self.retrieved_data:
                self.retrieved_data[symbol] = pd.Series(dtype=float)
        
        # Use the helper to complete cleanly
        self._complete_with_empty_data()

    @staticmethod
    def get_data(symbols, interval, start, end=None):
        """Download data from cTrader Open API"""
        print(f"Downloading data for symbols: {symbols}")

        if not CTRADER_API_AVAILABLE:
            print("cTrader Open API not available. Please install: pip install ctrader-open-api")
            return {symbol: pd.Series(dtype=float) for symbol in symbols}

        # Check credentials with detailed feedback
        missing_creds = []
        if not CTRADER_CLIENT_ID:
            missing_creds.append("CTRADER_CLIENT_ID")
        if not CTRADER_CLIENT_SECRET:
            missing_creds.append("CTRADER_CLIENT_SECRET")
        if not CTRADER_ACCESS_TOKEN:
            missing_creds.append("CTRADER_ACCESS_TOKEN")
        # Don't require ACCOUNT_ID here since we can get it from API

        if missing_creds:
            print("Missing cTrader API credentials in .env file:")
            for cred in missing_creds:
                print(f"  - {cred}")
            print("\nPlease add these to your .env file:")
            print("CTRADER_CLIENT_ID=your_client_id")
            print("CTRADER_CLIENT_SECRET=your_client_secret")
            print("CTRADER_ACCESS_TOKEN=your_access_token")
            print("CTRADER_ACCOUNT_ID=your_account_id")
            return {symbol: pd.Series(dtype=float) for symbol in symbols}

        try:
            config = get_config()
            retriever = CTraderDataManager(config)
            return retriever.get_historical_data(symbols, interval, start, end)
        except Exception as e:
            print(f"Error retrieving data: {e}")
            import traceback
            traceback.print_exc()
            return {symbol: pd.Series(dtype=float) for symbol in symbols}

    # Update CTraderDataRetriever._on_message_received to handle real-time trading messages
    def _on_message_received(self, client, message):
        if message.payloadType == ProtoOAApplicationAuthRes().payloadType:
            print("API Application authorized")
            if self.account_id:
                self._authenticate_account()
            else:
                # Get list of available accounts first
                self._get_account_list()
        elif message.payloadType == OpenApiMessages_pb2.ProtoOAGetAccountListByAccessTokenRes().payloadType:
            self._process_account_list(message)
        elif message.payloadType == ProtoOAAccountAuthRes().payloadType:
            print(f"Account {self.account_id} authorized")
            self._get_symbols_list()
        elif message.payloadType == ProtoOASymbolsListRes().payloadType:
            self._process_symbols_list(message)
        elif message.payloadType == ProtoOAGetTrendbarsRes().payloadType:
            self._process_trendbar_data(message)
        # elif message.payloadType == ProtoOASpotEvent().payloadType:
        #     # Handle real-time price updates
        #     if hasattr(self, 'trading_engine') and self.trading_engine:
        #         self.trading_engine.process_spot_event(Protobuf.extract(message))
        elif message.payloadType == ProtoOAExecutionEvent().payloadType:
            # Handle order execution events
            if hasattr(self, 'trading_engine') and self.trading_engine:
                self.trading_engine.process_execution_event(Protobuf.extract(message))
        elif message.payloadType == ProtoOASubscribeSpotsRes().payloadType:
            # Handle subscription confirmation
            response = Protobuf.extract(message)
            logger.info("Received subscription confirmation response")
            
            # The response doesn't contain symbol details, but we can log the success
            if self.trading_engine:
                # Count how many symbols we attempted to subscribe to
                pending_symbols = set()
                for pair_str in self.config.pairs:
                    s1, s2 = pair_str.split('-')
                    if s1 in self.symbols_map:
                        pending_symbols.add(s1)
                    if s2 in self.symbols_map:
                        pending_symbols.add(s2)
                
                logger.info(f"Spot subscription confirmed - monitoring {len(pending_symbols)} symbols for price updates")
                logger.info(f"Subscribed symbols: {', '.join(sorted(pending_symbols))}")
                
                # Mark all pending symbols as subscribed since we got a success response
                for symbol in pending_symbols:
                    if symbol not in self.trading_engine.subscribed_symbols:
                        self.trading_engine.subscribed_symbols.add(symbol)
                        logger.debug(f"Confirmed subscription for {symbol}")

    def test_connection(self) -> bool:
        """Test if CTrader connection is working properly"""
        if not CTRADER_API_AVAILABLE:
            print("CTrader API not available")
            return False
        
        if not self.access_token or not self.account_id:
            print("Missing CTrader credentials")
            return False
        
        try:
            # Try a simple test with minimal timeout
            test_symbols = ['EURUSD']  # Single symbol test
            self.target_symbols = test_symbols
            self.deferred_result = defer.Deferred()
            
            client = self.setup_client()
            if not client:
                return False
            
            # Start with short timeout for connection test
            client.startService()
            timeout_deferred = reactor.callLater(30, self._test_timeout)  # 30 second timeout
            
            def cleanup_timeout(result):
                if timeout_deferred.active():
                    timeout_deferred.cancel()
                return result
            
            self.deferred_result.addBoth(cleanup_timeout)
            
            # Run test connection
            if not reactor.running:
                reactor.run()
            
            # Check if we got any data
            return len(self.retrieved_data) > 0 and any(
                isinstance(data, pd.Series) and len(data) > 0 
                for data in self.retrieved_data.values()
            )
            
        except Exception as e:
            print(f"CTrader connection test failed: {e}")
            return False
    
    def _test_timeout(self):
        """Handle timeout for connection test"""
        print("CTrader connection test timeout")
        if self.deferred_result and not self.deferred_result.called:
            self.deferred_result.callback({})
        try:
            if reactor.running:
                reactor.stop()
        except:
            pass