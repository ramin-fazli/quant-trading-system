#!/usr/bin/env python
"""
Test script to verify cTrader API imports work correctly
"""

try:
    from ctrader_open_api import Client, Protobuf, TcpProtocol, EndPoints
    from ctrader_open_api.messages.OpenApiCommonMessages_pb2 import *
    from ctrader_open_api.messages.OpenApiMessages_pb2 import *
    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import *
    from twisted.internet import reactor, defer
    
    print("✅ Successfully imported cTrader Open API modules")
    
    # Test key classes we need
    test_classes = [
        'ProtoOAApplicationAuthReq',
        'ProtoOAApplicationAuthRes', 
        'ProtoOAAccountAuthReq',
        'ProtoOAAccountAuthRes',
        'ProtoOASymbolsListReq',
        'ProtoOASymbolsListRes',
        'ProtoOANewOrderReq',
        'ProtoOAOrderType',
        'ProtoOATradeSide',
        'ProtoOASubscribeSpotsReq',
        'ProtoOAUnsubscribeSpotsReq',
        'ProtoOASpotEvent',
        'ProtoOAExecutionEvent'
    ]
    
    for class_name in test_classes:
        try:
            cls = globals()[class_name]
            print(f"✅ {class_name}: Available")
        except KeyError:
            print(f"❌ {class_name}: NOT FOUND")
    
    # Test enum values
    try:
        buy_value = ProtoOATradeSide.Value("BUY")
        sell_value = ProtoOATradeSide.Value("SELL")
        market_value = ProtoOAOrderType.Value("MARKET")
        print(f"✅ Enum values - BUY: {buy_value}, SELL: {sell_value}, MARKET: {market_value}")
    except Exception as e:
        print(f"❌ Enum test failed: {e}")
        
except ImportError as e:
    print(f"❌ Import failed: {e}")
    print("Please install cTrader Open API: pip install ctrader-open-api")
