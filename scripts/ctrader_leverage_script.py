#!/usr/bin/env python3
"""
REAL CTrader Leverage Extraction Script
=======================================

THIS SCRIPT ACTUALLY CONNECTS TO CTRADER API AND GETS REAL LEVERAGE DATA!
No mock data, no bullshit - just real API calls to cTrader.

Requirements:
- ctrader-open-api package
- Valid cTrader credentials in .env file

Usage:
    python get_real_leverage.py --symbol EURUSD

Author: Trading System v3.0
Date: July 2025
"""

import argparse
import logging
import sys
import os
import time
import threading
from typing import Dict, Optional, Any
from datetime import datetime
from queue import Queue, Empty

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Load environment variables
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    logger.warning("python-dotenv not available, using system environment variables")

# cTrader Open API imports
try:
    from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
    from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import *
    from ctrader_open_api.messages.OpenApiMessages_pb2 import (
        ProtoOAApplicationAuthReq, ProtoOAAccountAuthReq, ProtoOASymbolsListReq, 
        ProtoOAGetDynamicLeverageByIDReq, ProtoOASymbolByIdReq
    )
    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import *
    from twisted.internet import reactor, defer
    
    # Message type constants
    MESSAGE_TYPES = {
        'APPLICATION_AUTH_REQ': 2100,
        'APPLICATION_AUTH_RES': 2101,
        'ACCOUNT_AUTH_REQ': 2102,
        'ACCOUNT_AUTH_RES': 2103,
        'SYMBOLS_LIST_REQ': 2114,
        'SYMBOLS_LIST_RES': 2115,
        'SYMBOL_BY_ID_REQ': 2121,
        'SYMBOL_BY_ID_RES': 2117,
        'GET_DYNAMIC_LEVERAGE_BY_ID_REQ': 2148,
        'GET_DYNAMIC_LEVERAGE_BY_ID_RES': 2178,  # Updated based on actual API response
        'ERROR_RES': 2142
    }
    
    CTRADER_API_AVAILABLE = True
    logger.info("✅ cTrader Open API package loaded successfully")
except ImportError as e:
    logger.error(f"❌ cTrader Open API not available: {e}")
    logger.error("Install with: pip install ctrader-open-api")
    sys.exit(1)


class RealCTraderLeverageExtractor:
    """REAL cTrader leverage extractor that actually calls the API"""
    
    def __init__(self):
        """Initialize with credentials from environment"""
        # Get credentials from environment
        self.client_id = os.getenv('CTRADER_CLIENT_ID')
        self.client_secret = os.getenv('CTRADER_CLIENT_SECRET')
        self.account_id = int(os.getenv('CTRADER_ACCOUNT_ID', '0'))
        
        if not all([self.client_id, self.client_secret, self.account_id]):
            logger.error("❌ Missing cTrader credentials in environment variables")
            logger.error("Required: CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET, CTRADER_ACCOUNT_ID")
            sys.exit(1)
        
        logger.info(f"🔑 Using Client ID: {self.client_id}")
        logger.info(f"🆔 Using Account ID: {self.account_id}")
        
        # Connection state
        self.client = None
        self.connected = False
        self.app_authenticated = False
        self.account_authenticated = False
        
        # Data storage
        self.symbols_data = {}
        self.leverage_data = {}
        
        # Synchronization
        self.result_queue = Queue()
        self.operation_complete = threading.Event()
        self.target_symbol = None
        
        # Results
        self.final_result = None
    
    def start_real_extraction(self, symbol_name: str) -> Dict[str, Any]:
        """Start REAL leverage extraction from cTrader API"""
        self.target_symbol = symbol_name.upper()
        
        logger.info("🚀 Starting REAL cTrader API connection...")
        logger.info(f"🎯 Target symbol: {self.target_symbol}")
        
        try:
            # Setup client
            if not self._setup_client():
                raise Exception("Failed to setup cTrader client")
            
            # Run reactor in a separate thread to avoid blocking
            reactor_thread = threading.Thread(target=self._run_reactor, daemon=True)
            reactor_thread.start()
            
            # Wait for operation to complete (max 30 seconds)
            if not self.operation_complete.wait(30):
                logger.error("❌ Operation timed out after 30 seconds")
                return self._create_error_result("Operation timed out")
            
            # Stop reactor
            if reactor.running:
                reactor.callFromThread(reactor.stop)
            
            return self.final_result
            
        except Exception as e:
            logger.error(f"❌ Extraction failed: {e}")
            return self._create_error_result(str(e))
    
    def _setup_client(self) -> bool:
        """Setup cTrader API client"""
        try:
            # Check host type from environment
            host_type = os.getenv('CTRADER_HOST_TYPE', 'demo').lower()
            if host_type == 'demo':
                host = EndPoints.PROTOBUF_DEMO_HOST
                logger.info("🔧 Using DEMO environment")
            else:
                host = EndPoints.PROTOBUF_LIVE_HOST
                logger.info("🔧 Using LIVE environment")
            
            port = EndPoints.PROTOBUF_PORT
            
            logger.info(f"🌐 Connecting to {host}:{port}")
            
            self.client = Client(host, port, TcpProtocol)
            self.client.setConnectedCallback(self._on_connected)
            self.client.setDisconnectedCallback(self._on_disconnected)
            self.client.setMessageReceivedCallback(self._on_message_received)
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to setup client: {e}")
            return False
    
    def _run_reactor(self):
        """Run the Twisted reactor"""
        try:
            # Start the client service
            d = self.client.startService()
            if d is not None:
                d.addErrback(self._on_error)
            
            # Run reactor
            reactor.run(installSignalHandlers=False)
            
        except Exception as e:
            logger.error(f"❌ Reactor error: {e}")
            self._complete_operation(self._create_error_result(f"Reactor error: {e}"))
    
    def _on_connected(self, client):
        """Called when connected to cTrader"""
        logger.info("✅ Connected to cTrader API")
        self.connected = True
        self._authenticate_application()
    
    def _on_disconnected(self, client, reason):
        """Called when disconnected"""
        logger.warning(f"📡 Disconnected from cTrader: {reason}")
        self.connected = False
    
    def _on_error(self, failure):
        """Handle connection errors"""
        logger.error(f"❌ Connection error: {failure}")
        self._complete_operation(self._create_error_result(f"Connection error: {failure}"))
    
    def _on_message_received(self, client, message):
        """Handle received messages"""
        try:
            logger.debug(f"📩 Received message type: {message.payloadType}")
            
            if message.payloadType == MESSAGE_TYPES['APPLICATION_AUTH_RES']:
                self._handle_app_auth_response(message)
            elif message.payloadType == MESSAGE_TYPES['ACCOUNT_AUTH_RES']:
                self._handle_account_auth_response(message)
            elif message.payloadType == MESSAGE_TYPES['SYMBOLS_LIST_RES']:
                self._handle_symbols_response(message)
            elif message.payloadType == MESSAGE_TYPES['SYMBOL_BY_ID_RES']:
                self._handle_symbol_details_response(message)
            elif message.payloadType == MESSAGE_TYPES['GET_DYNAMIC_LEVERAGE_BY_ID_RES']:
                self._handle_leverage_response(message)
            elif message.payloadType == MESSAGE_TYPES['ERROR_RES']:
                self._handle_error_response(message)
            else:
                logger.debug(f"📩 Unhandled message type: {message.payloadType}")
                
        except Exception as e:
            logger.error(f"❌ Error handling message: {e}")
            self._complete_operation(self._create_error_result(f"Message handling error: {e}"))
    
    def _authenticate_application(self):
        """Authenticate application with cTrader"""
        try:
            logger.info("🔐 Authenticating application...")
            logger.debug(f"Client ID: {self.client_id}")
            logger.debug(f"Client Secret: {self.client_secret[:10]}...")
            
            auth_request = ProtoOAApplicationAuthReq()
            auth_request.clientId = str(self.client_id)  # Ensure it's a string
            auth_request.clientSecret = str(self.client_secret)  # Ensure it's a string
            
            logger.debug(f"Auth request clientId set: {auth_request.clientId}")
            logger.debug(f"Auth request clientSecret set: {auth_request.clientSecret[:10]}...")
            
            # Send the request object directly, not the serialized string
            deferred = self.client.send(auth_request)
            logger.info("📤 Application auth request sent")
            
        except Exception as e:
            logger.error(f"❌ App authentication failed: {e}")
            self._complete_operation(self._create_error_result(f"App auth error: {e}"))
    
    def _handle_app_auth_response(self, message):
        """Handle application authentication response"""
        try:
            logger.info("✅ Application authenticated successfully")
            self.app_authenticated = True
            self._authenticate_account()
            
        except Exception as e:
            logger.error(f"❌ App auth response error: {e}")
            self._complete_operation(self._create_error_result(f"App auth response error: {e}"))
    
    def _authenticate_account(self):
        """Authenticate account with cTrader"""
        try:
            logger.info("🔐 Authenticating account...")
            
            account_auth_request = ProtoOAAccountAuthReq()
            account_auth_request.ctidTraderAccountId = self.account_id
            account_auth_request.accessToken = os.getenv('CTRADER_ACCESS_TOKEN', 'dummy_token')
            
            deferred = self.client.send(account_auth_request)
            logger.info("📤 Account auth request sent")
            
        except Exception as e:
            logger.error(f"❌ Account authentication failed: {e}")
            self._complete_operation(self._create_error_result(f"Account auth error: {e}"))
    
    def _handle_account_auth_response(self, message):
        """Handle account authentication response"""
        try:
            logger.info("✅ Account authenticated successfully")
            self.account_authenticated = True
            self._request_symbols()
            
        except Exception as e:
            logger.error(f"❌ Account auth response error: {e}")
            self._complete_operation(self._create_error_result(f"Account auth response error: {e}"))
    
    def _request_symbols(self):
        """Request symbols list from cTrader"""
        try:
            logger.info("📋 Requesting symbols list...")
            
            symbols_request = ProtoOASymbolsListReq()
            symbols_request.ctidTraderAccountId = self.account_id
            
            deferred = self.client.send(symbols_request)
            logger.info("📤 Symbols request sent")
            
        except Exception as e:
            logger.error(f"❌ Symbols request failed: {e}")
            self._complete_operation(self._create_error_result(f"Symbols request error: {e}"))
    
    def _handle_symbols_response(self, message):
        """Handle symbols list response"""
        try:
            symbols_list = Protobuf.extract(message)
            logger.info(f"📋 Received {len(symbols_list.symbol)} symbols from cTrader")
            
            # Store symbols data and find target symbol ID
            target_symbol_found = False
            target_symbol_id = None
            
            for symbol in symbols_list.symbol:
                symbol_name = symbol.symbolName
                symbol_id = symbol.symbolId
                
                self.symbols_data[symbol_name] = {
                    'symbolId': symbol_id,
                    'symbolName': symbol_name,
                    # Note: leverageId is only available in detailed symbol response
                }
                
                # Check if this is our target symbol
                if symbol_name == self.target_symbol:
                    target_symbol_found = True
                    target_symbol_id = symbol_id
                    logger.info(f"🎯 Found target symbol {self.target_symbol} with ID: {symbol_id}")
            
            if target_symbol_found and target_symbol_id:
                # Request detailed symbol information to get leverage data
                self._request_symbol_details(target_symbol_id)
            else:
                logger.error(f"❌ Target symbol {self.target_symbol} not found in symbols list")
                self._complete_operation(self._create_error_result(f"Symbol {self.target_symbol} not found"))
                
        except Exception as e:
            logger.error(f"❌ Symbols response error: {e}")
            self._complete_operation(self._create_error_result(f"Symbols response error: {e}"))
    
    def _request_symbol_details(self, symbol_id: int):
        """Request detailed symbol information to get leverage data"""
        try:
            logger.info(f"📋 Requesting detailed info for symbol ID: {symbol_id}")
            
            symbol_details_request = ProtoOASymbolByIdReq()
            symbol_details_request.ctidTraderAccountId = self.account_id
            symbol_details_request.symbolId.append(symbol_id)  # Add to repeated field
            
            deferred = self.client.send(symbol_details_request)
            logger.info("📤 Symbol details request sent")
            
        except Exception as e:
            logger.error(f"❌ Symbol details request failed: {e}")
            self._complete_operation(self._create_error_result(f"Symbol details request error: {e}"))
    
    def _handle_symbol_details_response(self, message):
        """Handle detailed symbol information response"""
        try:
            response = Protobuf.extract(message)
            logger.info(f"📋 Received detailed info for {len(response.symbol)} symbols")
            
            for symbol in response.symbol:
                # Get symbol details using the ID
                symbol_id = symbol.symbolId
                
                # Find the symbol name from our stored mapping
                symbol_name = None
                for stored_name, stored_data in self.symbols_data.items():
                    if stored_data['symbolId'] == symbol_id:
                        symbol_name = stored_name
                        break
                
                if not symbol_name:
                    logger.warning(f"Symbol ID {symbol_id} not found in stored mapping")
                    continue
                
                # Extract leverage information
                leverage_id = getattr(symbol, 'leverageId', None)
                max_leverage = getattr(symbol, 'maxLeverage', None)
                leverage_in_cents = getattr(symbol, 'leverageInCents', None)
                
                # Update our symbol data
                self.symbols_data[symbol_name].update({
                    'leverageId': leverage_id,
                    'maxLeverage': max_leverage,
                    'leverageInCents': leverage_in_cents
                })
                
                logger.info(f"🎯 Symbol {symbol_name} leverage info: leverageId={leverage_id}, maxLeverage={max_leverage}")
                
                # If this is our target symbol, proceed with leverage request
                if symbol_name == self.target_symbol:
                    if leverage_id is not None:
                        logger.info(f"✅ Found leverage ID {leverage_id} for {self.target_symbol}")
                        self._request_leverage(leverage_id)
                    else:
                        logger.warning(f"⚠️ No leverage ID for symbol {self.target_symbol}, using static leverage data")
                        # Create result with available leverage info
                        result = {
                            'success': True,
                            'symbol': self.target_symbol,
                            'leverage_data': {
                                'leverageId': None,
                                'maxLeverage': max_leverage,
                                'leverageInCents': leverage_in_cents,
                                'dynamicLeverage': None,
                                'source': 'cTrader API - Symbol Details',
                                'note': 'No dynamic leverage ID available - using static leverage info'
                            }
                        }
                        self._complete_operation(result)
                    return
            
            # If we get here, target symbol wasn't in the detailed response
            logger.error(f"❌ Target symbol {self.target_symbol} not found in detailed response")
            self._complete_operation(self._create_error_result(f"Target symbol {self.target_symbol} not in detailed response"))
                
        except Exception as e:
            logger.error(f"❌ Symbol details response error: {e}")
            self._complete_operation(self._create_error_result(f"Symbol details response error: {e}"))
    
    def _request_leverage(self, leverage_id: int):
        """Request leverage data for specific leverage ID"""
        try:
            logger.info(f"💪 Requesting leverage data for ID: {leverage_id}")
            
            leverage_request = ProtoOAGetDynamicLeverageByIDReq()
            leverage_request.ctidTraderAccountId = self.account_id
            leverage_request.leverageId = leverage_id
            
            deferred = self.client.send(leverage_request)
            logger.info("📤 Leverage request sent")
            
        except Exception as e:
            logger.error(f"❌ Leverage request failed: {e}")
            self._complete_operation(self._create_error_result(f"Leverage request error: {e}"))
    
    def _handle_leverage_response(self, message):
        """Handle leverage response - THE REAL DATA!"""
        try:
            leverage_response = Protobuf.extract(message)
            logger.info("🎉 Received REAL leverage data from cTrader API!")
            
            # Debug: Log the structure of the response
            logger.debug(f"Leverage response type: {type(leverage_response)}")
            logger.debug(f"Leverage response attributes: {dir(leverage_response)}")
            
            # Extract the ProtoOADynamicLeverage object from the response
            if hasattr(leverage_response, 'leverage'):
                dynamic_leverage = leverage_response.leverage
                logger.info("📊 Processing dynamic leverage data...")
                logger.debug(f"Dynamic leverage type: {type(dynamic_leverage)}")
                logger.debug(f"Dynamic leverage attributes: {dir(dynamic_leverage)}")
                
                # Extract leverage information from ProtoOADynamicLeverage
                leverage_id = getattr(dynamic_leverage, 'leverageId', 'unknown')
                tiers = []
                max_leverage = 0
                
                # Process leverage tiers from the dynamic leverage object
                if hasattr(dynamic_leverage, 'tiers'):
                    logger.info(f"📊 Processing {len(dynamic_leverage.tiers)} leverage tiers")
                    for tier in dynamic_leverage.tiers:
                        logger.debug(f"Tier type: {type(tier)}, attributes: {dir(tier)}")
                        raw_leverage = getattr(tier, 'leverage', 0)
                        actual_leverage = raw_leverage / 100  # Convert from cents to actual leverage
                        tier_data = {
                            'leverage': actual_leverage,
                            'raw_leverage': raw_leverage,  # Keep original value for reference
                            'volume': getattr(tier, 'volume', 0),  # Single volume field
                            'minVolume': getattr(tier, 'minVolume', 0),  # Fallback
                            'maxVolume': getattr(tier, 'maxVolume', 0)   # Fallback
                        }
                        tiers.append(tier_data)
                        max_leverage = max(max_leverage, actual_leverage)
                        logger.info(f"   Tier: {actual_leverage}:1 leverage (raw: {raw_leverage}), "
                                  f"volume: {tier_data['volume']} (min: {tier_data['minVolume']}, max: {tier_data['maxVolume']})")
                elif hasattr(dynamic_leverage, 'tier'):
                    logger.info(f"📊 Processing {len(dynamic_leverage.tier)} leverage tiers")
                    for tier in dynamic_leverage.tier:
                        logger.debug(f"Tier type: {type(tier)}, attributes: {dir(tier)}")
                        tier_data = {
                            'leverage': getattr(tier, 'leverage', 0),
                            'minVolume': getattr(tier, 'minVolume', 0),
                            'maxVolume': getattr(tier, 'maxVolume', 0)
                        }
                        tiers.append(tier_data)
                        max_leverage = max(max_leverage, tier_data['leverage'])
                        logger.info(f"   Tier: {tier_data['leverage']}:1 leverage, "
                                  f"volume: {tier_data['minVolume']} - {tier_data['maxVolume']}")
                elif hasattr(dynamic_leverage, 'leverageTier'):
                    # Try alternative field name
                    logger.info(f"📊 Processing {len(dynamic_leverage.leverageTier)} leverage tiers (leverageTier field)")
                    for tier in dynamic_leverage.leverageTier:
                        logger.debug(f"Tier type: {type(tier)}, attributes: {dir(tier)}")
                        tier_data = {
                            'leverage': getattr(tier, 'leverage', 0),
                            'minVolume': getattr(tier, 'minVolume', 0),
                            'maxVolume': getattr(tier, 'maxVolume', 0)
                        }
                        tiers.append(tier_data)
                        max_leverage = max(max_leverage, tier_data['leverage'])
                        logger.info(f"   Tier: {tier_data['leverage']}:1 leverage, "
                                  f"volume: {tier_data['minVolume']} - {tier_data['maxVolume']}")
                else:
                    logger.warning("⚠️ No tier or leverageTier field found in dynamic leverage")
                    logger.debug(f"Available fields: {[attr for attr in dir(dynamic_leverage) if not attr.startswith('_')]}")
                
                # If no tiers, check for direct leverage value
                if not tiers and hasattr(dynamic_leverage, 'leverage'):
                    raw_leverage = getattr(dynamic_leverage, 'leverage', 0)
                    max_leverage = raw_leverage / 100  # Convert from cents to actual leverage
                    logger.info(f"📊 Single leverage value: {max_leverage}:1 (raw: {raw_leverage})")
                
                # Create success result with REAL data
                result = {
                    'symbol': self.target_symbol,
                    'timestamp': datetime.now().isoformat(),
                    'success': True,
                    'source': 'REAL_CTRADER_API',
                    'leverage_id': leverage_id,
                    'max_leverage': max_leverage,
                    'tiers': tiers,
                    'tier_count': len(tiers),
                    'margin_requirement': round(100 / max_leverage, 4) if max_leverage > 0 else None,
                    'api_response': True
                }
                
                logger.info("✅ REAL leverage data extracted successfully!")
                self._complete_operation(result)
                
            else:
                logger.error("❌ No leverage object found in response")
                logger.debug(f"Available response fields: {[attr for attr in dir(leverage_response) if not attr.startswith('_')]}")
                self._complete_operation(self._create_error_result("No leverage data in response"))
            
        except Exception as e:
            logger.error(f"❌ Error processing leverage response: {e}")
            logger.exception("Full error details:")
            self._complete_operation(self._create_error_result(f"Leverage processing error: {e}"))
    
    def _handle_error_response(self, message):
        """Handle API error response"""
        try:
            error_response = Protobuf.extract(message)
            error_code = getattr(error_response, 'errorCode', 'unknown')
            error_desc = getattr(error_response, 'description', 'No description')
            
            logger.error(f"🚨 cTrader API Error {error_code}: {error_desc}")
            self._complete_operation(self._create_error_result(f"API Error {error_code}: {error_desc}"))
            
        except Exception as e:
            logger.error(f"❌ Error processing error response: {e}")
            self._complete_operation(self._create_error_result(f"Error processing error: {e}"))
    
    def _create_error_result(self, error_message: str) -> Dict[str, Any]:
        """Create error result"""
        return {
            'symbol': self.target_symbol or 'UNKNOWN',
            'timestamp': datetime.now().isoformat(),
            'success': False,
            'source': 'REAL_CTRADER_API',
            'error': error_message,
            'leverage_id': None,
            'max_leverage': None,
            'tiers': [],
            'api_response': True
        }
    
    def _complete_operation(self, result: Dict[str, Any]):
        """Complete the operation with result"""
        self.final_result = result
        self.operation_complete.set()


def main():
    """Main function to run REAL leverage extraction"""
    parser = argparse.ArgumentParser(description='REAL cTrader leverage extraction from API')
    parser.add_argument('--symbol', required=True, help='Symbol to extract leverage for (e.g., EURUSD)')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose logging')
    
    args = parser.parse_args()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    try:
        logger.info("🚀 REAL CTrader Leverage Extraction")
        logger.info("=" * 50)
        logger.info("THIS SCRIPT CONNECTS TO REAL CTRADER API!")
        logger.info("=" * 50)
        logger.info(f"🎯 Target Symbol: {args.symbol}")
        logger.info("")
        
        # Create extractor
        extractor = RealCTraderLeverageExtractor()
        
        # Start REAL extraction
        result = extractor.start_real_extraction(args.symbol)
        
        # Display results
        logger.info("\n" + "=" * 50)
        logger.info("📊 REAL API RESULTS")
        logger.info("=" * 50)
        
        if result['success']:
            logger.info(f"✅ SUCCESS: REAL leverage data for {result['symbol']}")
            logger.info(f"   📈 Max Leverage: {result['max_leverage']}:1")
            logger.info(f"   🔢 Leverage ID: {result['leverage_id']}")
            logger.info(f"   🏷️ Number of Tiers: {result['tier_count']}")
            logger.info(f"   💰 Margin Required: {result['margin_requirement']}%")
            logger.info(f"   🌐 Data Source: {result['source']}")
            logger.info(f"   📅 Retrieved: {result['timestamp']}")
            
            if result['tiers']:
                logger.info("\n📊 REAL LEVERAGE TIERS:")
                for i, tier in enumerate(result['tiers'], 1):
                    logger.info(f"   Tier {i}: {tier['leverage']}:1 leverage")
                    logger.info(f"      Volume: {tier['minVolume']:,} - {tier['maxVolume']:,}")
            
            logger.info("\n🎉 REAL leverage data extracted successfully from cTrader API!")
        else:
            logger.error(f"❌ FAILED: {result['error']}")
            logger.error("Make sure your cTrader credentials are correct in .env file")
            sys.exit(1)
        
    except KeyboardInterrupt:
        logger.info("\n⏹️ Extraction interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"\n💥 Unexpected error: {e}")
        import traceback
        if args.verbose:
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
