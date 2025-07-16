#!/usr/bin/env python3
"""
CTrader API Connection Diagnostic Tool
=====================================

This script helps diagnose cTrader API connection issues by testing each step
of the authentication and connection process in detail.

Usage: python scripts/diagnose_ctrader.py
"""

import os
import sys
import logging
import time
from datetime import datetime
from typing import Optional

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set up detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('ctrader_diagnostic.log')
    ]
)

logger = logging.getLogger(__name__)

def check_environment_variables():
    """Check if all required environment variables are set"""
    print("\n" + "="*60)
    print("🔍 CHECKING ENVIRONMENT VARIABLES")
    print("="*60)
    
    required_vars = [
        'CTRADER_CLIENT_ID',
        'CTRADER_CLIENT_SECRET', 
        'CTRADER_ACCESS_TOKEN',
        'CTRADER_ACCOUNT_ID'
    ]
    
    missing_vars = []
    masked_vars = {}
    
    for var in required_vars:
        value = os.getenv(var)
        if value:
            # Mask sensitive information
            if len(value) > 8:
                masked_value = value[:4] + "*" * (len(value) - 8) + value[-4:]
            else:
                masked_value = "*" * len(value)
            masked_vars[var] = masked_value
            print(f"✅ {var}: {masked_value}")
        else:
            missing_vars.append(var)
            print(f"❌ {var}: NOT SET")
    
    if missing_vars:
        print(f"\n⚠️  Missing environment variables: {', '.join(missing_vars)}")
        print("\nPlease set these environment variables:")
        for var in missing_vars:
            print(f"export {var}='your_value_here'")
        return False
    
    print("\n✅ All environment variables are set")
    return True

def check_ctrader_api_import():
    """Check if cTrader API library is properly installed"""
    print("\n" + "="*60)
    print("📦 CHECKING CTRADER API LIBRARY")
    print("="*60)
    
    try:
        from ctrader_open_api import Client, TcpProtocol, EndPoints
        from ctrader_open_api.messages import OpenApiMessages_pb2, OpenApiModelMessages_pb2
        print("✅ cTrader Open API library imported successfully")
        
        # Check version if available
        try:
            import ctrader_open_api
            if hasattr(ctrader_open_api, '__version__'):
                print(f"📋 Version: {ctrader_open_api.__version__}")
        except:
            pass
            
        return True
        
    except ImportError as e:
        print(f"❌ Failed to import cTrader Open API: {e}")
        print("\nTo install:")
        print("pip install ctrader-open-api")
        return False

def validate_credentials():
    """Validate credential format"""
    print("\n" + "="*60)
    print("🔐 VALIDATING CREDENTIAL FORMAT")
    print("="*60)
    
    client_id = os.getenv('CTRADER_CLIENT_ID')
    client_secret = os.getenv('CTRADER_CLIENT_SECRET')
    access_token = os.getenv('CTRADER_ACCESS_TOKEN')
    account_id = os.getenv('CTRADER_ACCOUNT_ID')
    
    issues = []
    
    # Check Client ID (should be numeric)
    if client_id:
        if not client_id.isdigit():
            issues.append(f"❌ CTRADER_CLIENT_ID should be numeric, got: {client_id[:4]}...")
        else:
            print(f"✅ CTRADER_CLIENT_ID format looks correct")
    
    # Check Client Secret (should be alphanumeric, 32+ chars)
    if client_secret:
        if len(client_secret) < 32:
            issues.append(f"❌ CTRADER_CLIENT_SECRET too short ({len(client_secret)} chars, expected 32+)")
        else:
            print(f"✅ CTRADER_CLIENT_SECRET length looks correct ({len(client_secret)} chars)")
    
    # Check Access Token (should be long alphanumeric string)
    if access_token:
        if len(access_token) < 50:
            issues.append(f"❌ CTRADER_ACCESS_TOKEN too short ({len(access_token)} chars, expected 50+)")
        else:
            print(f"✅ CTRADER_ACCESS_TOKEN length looks correct ({len(access_token)} chars)")
    
    # Check Account ID (should be numeric)
    if account_id:
        if not account_id.isdigit():
            issues.append(f"❌ CTRADER_ACCOUNT_ID should be numeric, got: {account_id}")
        else:
            print(f"✅ CTRADER_ACCOUNT_ID format looks correct")
    
    if issues:
        print("\n⚠️  Credential format issues found:")
        for issue in issues:
            print(f"   {issue}")
        return False
    
    print("\n✅ All credentials have correct format")
    return True

def test_connection():
    """Test basic connection to cTrader API"""
    print("\n" + "="*60)
    print("🌐 TESTING CONNECTION TO CTRADER API")
    print("="*60)
    
    try:
        from ctrader_open_api import Client, TcpProtocol, EndPoints
        from ctrader_open_api.messages import OpenApiMessages_pb2
        from twisted.internet import reactor
        
        # Test connection parameters
        host = EndPoints.PROTOBUF_LIVE_HOST
        port = EndPoints.PROTOBUF_PORT
        
        print(f"🔗 Attempting connection to {host}:{port}")
        
        # Connection state tracking
        connection_state = {
            'connected': False,
            'app_authenticated': False,
            'account_authenticated': False,
            'symbols_received': False,
            'error': None,
            'messages_received': []
        }
        
        def on_connected(client):
            print("✅ Connected to cTrader API server")
            connection_state['connected'] = True
            
            # Send application authentication
            print("🔐 Sending application authentication...")
            request = OpenApiMessages_pb2.ProtoOAApplicationAuthReq()
            request.clientId = os.getenv('CTRADER_CLIENT_ID')
            request.clientSecret = os.getenv('CTRADER_CLIENT_SECRET')
            
            deferred = client.send(request)
            deferred.addErrback(on_error)
        
        def on_disconnected(client, reason):
            print(f"❌ Disconnected from cTrader API: {reason}")
            reactor.stop()
        
        def on_error(failure):
            print(f"❌ Connection error: {failure}")
            connection_state['error'] = str(failure)
            reactor.stop()
        
        def on_message_received(client, message):
            message_type = message.payloadType
            connection_state['messages_received'].append(message_type)
            
            print(f"📨 Received message type: {message_type}")
            
            if message_type == OpenApiMessages_pb2.ProtoOAApplicationAuthRes().payloadType:
                print("✅ Application authentication successful")
                connection_state['app_authenticated'] = True
                
                # Send account authentication
                print("🔐 Sending account authentication...")
                request = OpenApiMessages_pb2.ProtoOAAccountAuthReq()
                request.ctidTraderAccountId = int(os.getenv('CTRADER_ACCOUNT_ID'))
                request.accessToken = os.getenv('CTRADER_ACCESS_TOKEN')
                
                deferred = client.send(request)
                deferred.addErrback(on_error)
                
            elif message_type == OpenApiMessages_pb2.ProtoOAAccountAuthRes().payloadType:
                print("✅ Account authentication successful")
                connection_state['account_authenticated'] = True
                
                # Request symbols list
                print("📋 Requesting symbols list...")
                request = OpenApiMessages_pb2.ProtoOASymbolsListReq()
                request.ctidTraderAccountId = int(os.getenv('CTRADER_ACCOUNT_ID'))
                
                deferred = client.send(request)
                deferred.addErrback(on_error)
                
            elif message_type == OpenApiMessages_pb2.ProtoOASymbolsListRes().payloadType:
                print("✅ Symbols list received successfully")
                connection_state['symbols_received'] = True
                
                # Check if we got symbols
                if hasattr(message.payload, 'symbol') and len(message.payload.symbol) > 0:
                    print(f"📊 Received {len(message.payload.symbol)} symbols")
                    print("✅ FULL CONNECTION TEST PASSED")
                else:
                    print("❌ Symbols list was empty")
                
                reactor.stop()
            
            elif message_type == 2142:  # Account auth response alternative
                print("✅ Account authentication successful (type 2142)")
                connection_state['account_authenticated'] = True
                
                # Request symbols list
                print("📋 Requesting symbols list...")
                request = OpenApiMessages_pb2.ProtoOASymbolsListReq()
                request.ctidTraderAccountId = int(os.getenv('CTRADER_ACCOUNT_ID'))
                
                deferred = client.send(request)
                deferred.addErrback(on_error)
            
            elif message_type == 2143:  # Symbols list response alternative
                print("✅ Symbols list received successfully (type 2143)")
                connection_state['symbols_received'] = True
                
                # Check if we got symbols
                if hasattr(message.payload, 'symbol') and len(message.payload.symbol) > 0:
                    print(f"📊 Received {len(message.payload.symbol)} symbols")
                    print("✅ FULL CONNECTION TEST PASSED")
                else:
                    print("❌ Symbols list was empty")
                
                reactor.stop()
        
        # Set up timeout
        def timeout():
            print("⏰ Connection test timed out")
            reactor.stop()
        
        reactor.callLater(45.0, timeout)  # 45 second timeout
        
        # Create and start client
        client = Client(host, port, TcpProtocol)
        client.setConnectedCallback(on_connected)
        client.setDisconnectedCallback(on_disconnected)
        client.setMessageReceivedCallback(on_message_received)
        
        d = client.startService()
        if d is not None:
            d.addErrback(on_error)
        
        # Run reactor
        print("🚀 Starting connection test...")
        reactor.run()
        
        # Print results
        print("\n" + "="*60)
        print("📊 CONNECTION TEST RESULTS")
        print("="*60)
        print(f"Connected:            {'✅' if connection_state['connected'] else '❌'}")
        print(f"App Authenticated:    {'✅' if connection_state['app_authenticated'] else '❌'}")
        print(f"Account Authenticated:{'✅' if connection_state['account_authenticated'] else '❌'}")
        print(f"Symbols Received:     {'✅' if connection_state['symbols_received'] else '❌'}")
        
        if connection_state['error']:
            print(f"Error:                {connection_state['error']}")
        
        print(f"Messages Received:    {connection_state['messages_received']}")
        
        return connection_state['symbols_received']
        
    except Exception as e:
        print(f"❌ Connection test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def check_network_connectivity():
    """Check basic network connectivity to cTrader servers"""
    print("\n" + "="*60)
    print("🌍 CHECKING NETWORK CONNECTIVITY")
    print("="*60)
    
    try:
        from ctrader_open_api import EndPoints
        import socket
        
        host = EndPoints.PROTOBUF_LIVE_HOST
        port = EndPoints.PROTOBUF_PORT
        
        print(f"🔍 Testing TCP connection to {host}:{port}")
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        
        try:
            result = sock.connect_ex((host, port))
            if result == 0:
                print("✅ TCP connection successful")
                return True
            else:
                print(f"❌ TCP connection failed (error code: {result})")
                return False
        finally:
            sock.close()
            
    except Exception as e:
        print(f"❌ Network connectivity check failed: {e}")
        return False

def print_troubleshooting_tips():
    """Print troubleshooting tips based on common issues"""
    print("\n" + "="*60)
    print("🛠️  TROUBLESHOOTING TIPS")
    print("="*60)
    
    print("""
Common cTrader API connection issues and solutions:

1. 🔐 CREDENTIAL ISSUES:
   - Ensure your cTrader account has API access enabled
   - Check that your access token hasn't expired
   - Verify the account ID matches your trading account
   - Make sure you're using the correct client ID and secret

2. 🌐 NETWORK ISSUES:
   - Check your firewall settings
   - Ensure no proxy is blocking the connection
   - Try from a different network if possible
   - Check if your ISP blocks trading API connections

3. 🏦 ACCOUNT ISSUES:
   - Verify your account is active and funded
   - Check if your account has trading permissions
   - Ensure you're using a live account (not demo) if trying live trading
   - Contact cTrader support if account issues persist

4. 🔧 API CONFIGURATION:
   - Double-check all environment variables
   - Ensure no extra spaces or quotes in credentials
   - Try regenerating your access token
   - Verify you're using the latest cTrader Open API library

5. ⏰ TIMING ISSUES:
   - cTrader API can be slow during market hours
   - Try connecting during off-peak hours
   - Increase timeout values if needed
   - Some responses can take 30+ seconds

To get help:
- Check cTrader API documentation
- Contact cTrader support with your client ID
- Verify your account settings in cTrader platform
""")

def main():
    """Main diagnostic function"""
    print("🔧 CTrader API Connection Diagnostic Tool")
    print("="*60)
    print(f"⏰ Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Run all checks
    checks_passed = 0
    total_checks = 5
    
    if check_environment_variables():
        checks_passed += 1
    
    if check_ctrader_api_import():
        checks_passed += 1
    
    if validate_credentials():
        checks_passed += 1
    
    if check_network_connectivity():
        checks_passed += 1
    
    if test_connection():
        checks_passed += 1
    
    # Final results
    print("\n" + "="*60)
    print("📊 DIAGNOSTIC SUMMARY")
    print("="*60)
    print(f"Checks passed: {checks_passed}/{total_checks}")
    
    if checks_passed == total_checks:
        print("✅ All checks passed! Your cTrader API should work correctly.")
    elif checks_passed >= 3:
        print("⚠️  Most checks passed. Review the failed items above.")
    else:
        print("❌ Multiple issues found. Please address the problems above.")
    
    print_troubleshooting_tips()
    
    print(f"\n📄 Detailed log saved to: ctrader_diagnostic.log")
    print(f"⏰ Completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

if __name__ == "__main__":
    main()
