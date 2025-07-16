#!/usr/bin/env python3
"""
CTrader Server Permission Test
=============================

This script tests whether your account has different permissions on demo vs live servers.
"""

import os
import sys
import logging
import time
from datetime import datetime

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def test_both_servers():
    """Test both demo and live servers to compare permissions"""
    
    try:
        from ctrader_open_api import Client, TcpProtocol, EndPoints
        from ctrader_open_api.messages import OpenApiMessages_pb2
        from twisted.internet import reactor
        
        # Test results
        test_results = {
            'demo_server': {
                'connected': False,
                'app_authenticated': False,
                'account_authenticated': False,
                'symbols_received': False,
                'symbol_count': 0,
                'error': None
            },
            'live_server': {
                'connected': False,
                'app_authenticated': False,
                'account_authenticated': False,
                'symbols_received': False,
                'symbol_count': 0,
                'error': None
            }
        }
        
        current_test = {'server': None}
        
        def test_server(server_type, host, port):
            """Test a specific server"""
            print(f"\n{'='*60}")
            print(f"🔍 TESTING {server_type.upper()} SERVER")
            print(f"{'='*60}")
            print(f"Host: {host}:{port}")
            
            current_test['server'] = server_type
            
            def on_connected(client):
                print(f"✅ Connected to {server_type} server")
                test_results[server_type]['connected'] = True
                
                # Send application authentication
                request = OpenApiMessages_pb2.ProtoOAApplicationAuthReq()
                request.clientId = os.getenv('CTRADER_CLIENT_ID')
                request.clientSecret = os.getenv('CTRADER_CLIENT_SECRET')
                
                deferred = client.send(request)
                deferred.addErrback(on_error)
            
            def on_disconnected(client, reason):
                print(f"❌ Disconnected from {server_type} server: {reason}")
                # Continue to next test or finish
                if server_type == 'demo':
                    # Test live server next
                    print(f"\n⏳ Waiting 2 seconds before testing live server...")
                    reactor.callLater(2, lambda: test_server('live_server', EndPoints.PROTOBUF_LIVE_HOST, EndPoints.PROTOBUF_PORT))
                else:
                    # All tests complete
                    print_results()
                    reactor.stop()
            
            def on_error(failure):
                print(f"❌ {server_type} server error: {failure}")
                test_results[server_type]['error'] = str(failure)
                # Continue to next test
                if server_type == 'demo':
                    reactor.callLater(2, lambda: test_server('live_server', EndPoints.PROTOBUF_LIVE_HOST, EndPoints.PROTOBUF_PORT))
                else:
                    print_results()
                    reactor.stop()
            
            def on_message_received(client, message):
                message_type = message.payloadType
                print(f"📨 {server_type}: Received message type {message_type}")
                
                if message_type == OpenApiMessages_pb2.ProtoOAApplicationAuthRes().payloadType:
                    print(f"✅ {server_type}: Application authentication successful")
                    test_results[server_type]['app_authenticated'] = True
                    
                    # Send account authentication
                    request = OpenApiMessages_pb2.ProtoOAAccountAuthReq()
                    request.ctidTraderAccountId = int(os.getenv('CTRADER_ACCOUNT_ID'))
                    request.accessToken = os.getenv('CTRADER_ACCESS_TOKEN')
                    
                    deferred = client.send(request)
                    deferred.addErrback(on_error)
                    
                elif message_type == OpenApiMessages_pb2.ProtoOAAccountAuthRes().payloadType:
                    print(f"✅ {server_type}: Account authentication successful")
                    test_results[server_type]['account_authenticated'] = True
                    
                    # Request symbols list
                    request = OpenApiMessages_pb2.ProtoOASymbolsListReq()
                    request.ctidTraderAccountId = int(os.getenv('CTRADER_ACCOUNT_ID'))
                    
                    deferred = client.send(request)
                    deferred.addErrback(on_error)
                    
                elif message_type == OpenApiMessages_pb2.ProtoOASymbolsListRes().payloadType:
                    print(f"✅ {server_type}: Symbols list received")
                    test_results[server_type]['symbols_received'] = True
                    
                    # Count symbols
                    if hasattr(message.payload, 'symbol'):
                        symbol_count = len(message.payload.symbol)
                        test_results[server_type]['symbol_count'] = symbol_count
                        print(f"📊 {server_type}: Received {symbol_count} symbols")
                        
                        # Show some example symbols
                        if symbol_count > 0:
                            examples = [s.symbolName for s in message.payload.symbol[:5]]
                            print(f"📋 {server_type}: Example symbols: {', '.join(examples)}")
                    else:
                        print(f"❌ {server_type}: No symbols in response")
                    
                    # Test complete for this server
                    print(f"✅ {server_type} server test completed")
                    # Use proper disconnect method
                    try:
                        if hasattr(client, 'stopService'):
                            client.stopService()
                        elif hasattr(client, 'transport') and hasattr(client.transport, 'loseConnection'):
                            client.transport.loseConnection()
                        else:
                            # Fallback - just continue to next test
                            pass
                    except Exception as e:
                        print(f"Warning: Error disconnecting {server_type}: {e}")
                    
                    # Continue to next test or finish
                    if server_type == 'demo_server':
                        reactor.callLater(2, lambda: test_server('live_server', EndPoints.PROTOBUF_LIVE_HOST, EndPoints.PROTOBUF_PORT))
                    else:
                        print_results()
                        reactor.stop()
                
                elif message_type == 2142:  # Alternative account auth response
                    print(f"✅ {server_type}: Account authentication successful (type 2142)")
                    test_results[server_type]['account_authenticated'] = True
                    
                    # Request symbols list
                    request = OpenApiMessages_pb2.ProtoOASymbolsListReq()
                    request.ctidTraderAccountId = int(os.getenv('CTRADER_ACCOUNT_ID'))
                    
                    deferred = client.send(request)
                    deferred.addErrback(on_error)
                    
                elif message_type == 2143:  # Alternative symbols response
                    print(f"✅ {server_type}: Symbols list received (type 2143)")
                    test_results[server_type]['symbols_received'] = True
                    
                    # Count symbols
                    if hasattr(message.payload, 'symbol'):
                        symbol_count = len(message.payload.symbol)
                        test_results[server_type]['symbol_count'] = symbol_count
                        print(f"📊 {server_type}: Received {symbol_count} symbols")
                    else:
                        print(f"❌ {server_type}: No symbols in response")
                    
                    # Disconnect and continue
                    try:
                        if hasattr(client, 'stopService'):
                            client.stopService()
                        elif hasattr(client, 'transport') and hasattr(client.transport, 'loseConnection'):
                            client.transport.loseConnection()
                    except Exception as e:
                        print(f"Warning: Error disconnecting {server_type}: {e}")
                    
                    # Continue to next test or finish
                    if server_type == 'demo_server':
                        reactor.callLater(2, lambda: test_server('live_server', EndPoints.PROTOBUF_LIVE_HOST, EndPoints.PROTOBUF_PORT))
                    else:
                        print_results()
                        reactor.stop()
            
            # Set timeout for this test
            def timeout():
                print(f"⏰ {server_type} server test timed out")
                test_results[server_type]['error'] = 'Timeout'
                if server_type == 'demo':
                    test_server('live_server', EndPoints.PROTOBUF_LIVE_HOST, EndPoints.PROTOBUF_PORT)
                else:
                    print_results()
                    reactor.stop()
            
            reactor.callLater(30.0, timeout)
            
            # Create and start client
            client = Client(host, port, TcpProtocol)
            client.setConnectedCallback(on_connected)
            client.setDisconnectedCallback(on_disconnected)
            client.setMessageReceivedCallback(on_message_received)
            
            d = client.startService()
            if d is not None:
                d.addErrback(on_error)
        
        def print_results():
            """Print comparison results"""
            print(f"\n{'='*80}")
            print("📊 SERVER PERMISSION COMPARISON RESULTS")
            print(f"{'='*80}")
            
            demo = test_results['demo_server']
            live = test_results['live_server']
            
            print(f"\n{'Server':<20} {'Demo':<15} {'Live':<15} {'Status'}")
            print(f"{'-'*60}")
            print(f"{'Connected':<20} {'✅' if demo['connected'] else '❌':<15} {'✅' if live['connected'] else '❌':<15} {'✅ Both work' if demo['connected'] and live['connected'] else '❌ Issue detected'}")
            print(f"{'App Auth':<20} {'✅' if demo['app_authenticated'] else '❌':<15} {'✅' if live['app_authenticated'] else '❌':<15} {'✅ Both work' if demo['app_authenticated'] and live['app_authenticated'] else '❌ Issue detected'}")
            print(f"{'Account Auth':<20} {'✅' if demo['account_authenticated'] else '❌':<15} {'✅' if live['account_authenticated'] else '❌':<15} {'✅ Both work' if demo['account_authenticated'] and live['account_authenticated'] else '❌ Issue detected'}")
            print(f"{'Symbols Received':<20} {'✅' if demo['symbols_received'] else '❌':<15} {'✅' if live['symbols_received'] else '❌':<15} {'✅ Both work' if demo['symbols_received'] and live['symbols_received'] else '❌ PERMISSION ISSUE'}")
            print(f"{'Symbol Count':<20} {demo['symbol_count']:<15} {live['symbol_count']:<15} {'✅ Both have data' if demo['symbol_count'] > 0 and live['symbol_count'] > 0 else '❌ Different access'}")
            
            print(f"\n{'='*80}")
            print("🔍 ANALYSIS")
            print(f"{'='*80}")
            
            if demo['symbols_received'] and not live['symbols_received']:
                print("🎯 ROOT CAUSE CONFIRMED:")
                print("   Your account has symbol access on DEMO server but NOT on LIVE server")
                print("   This explains why backtest works but live trading fails")
                print("")
                print("📞 SOLUTION:")
                print("   Contact your broker and request:")
                print("   1. 'Enable API symbol access on LIVE server'")
                print("   2. 'Transfer symbol permissions from demo to live'")
                print(f"   3. 'Account ID {os.getenv('CTRADER_ACCOUNT_ID')} needs live trading symbols'")
                
            elif not demo['symbols_received'] and not live['symbols_received']:
                print("❌ ACCOUNT ISSUE:")
                print("   Your account lacks symbol access on BOTH servers")
                print("   Contact broker for full API access setup")
                
            elif demo['symbols_received'] and live['symbols_received']:
                if demo['symbol_count'] != live['symbol_count']:
                    print("⚠️ PARTIAL ACCESS:")
                    print(f"   Demo server: {demo['symbol_count']} symbols")
                    print(f"   Live server: {live['symbol_count']} symbols")
                    print("   Different symbol sets - may affect specific pairs")
                else:
                    print("✅ FULL ACCESS:")
                    print("   Both servers provide equal symbol access")
                    print("   Your issue might be elsewhere - check error logs")
            
            if demo.get('error'):
                print(f"\n❌ Demo Server Error: {demo['error']}")
            if live.get('error'):
                print(f"❌ Live Server Error: {live['error']}")
        
        # Start with demo server test
        test_server('demo_server', EndPoints.PROTOBUF_DEMO_HOST, EndPoints.PROTOBUF_PORT)
        
        # Run reactor
        reactor.run()
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()

def main():
    """Main function"""
    print("🔍 CTrader Server Permission Comparison Test")
    print("="*60)
    print(f"⏰ Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("")
    print("This test will connect to both demo and live cTrader servers")
    print("to compare your account permissions on each.")
    print("")
    
    test_both_servers()

if __name__ == "__main__":
    main()
