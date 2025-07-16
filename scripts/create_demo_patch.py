#!/usr/bin/env python3
"""
Temporary CTrader Live Trading Test with Demo Server
===================================================

This script temporarily modifies the CTrader broker to use the demo server
so you can test your trading logic while waiting for live server access.

WARNING: This is for testing only! Demo server trades are not real.
"""

import os
import sys

# Add the project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def create_demo_server_patch():
    """Create a patch file to temporarily use demo server for testing"""
    
    patch_content = '''
# Temporary patch to use demo server for live trading testing
# This should be removed once live server access is granted

# In brokers/ctrader.py, line ~155, change:
# host = EndPoints.PROTOBUF_LIVE_HOST
# to:
# host = EndPoints.PROTOBUF_DEMO_HOST

# This allows testing trading logic while waiting for broker to enable live server access
'''
    
    patch_file = os.path.join(os.path.dirname(__file__), '..', 'DEMO_SERVER_PATCH.txt')
    with open(patch_file, 'w') as f:
        f.write(patch_content)
    
    print(f"Created patch instructions: {patch_file}")

def main():
    """Main function"""
    print("🔧 CTrader Demo Server Testing Patch")
    print("="*50)
    print("")
    print("This creates instructions for temporarily using the demo server")
    print("for live trading tests while waiting for live server access.")
    print("")
    print("⚠️  WARNING: Demo server trades are NOT real trades!")
    print("   Use this only for testing trading logic.")
    print("")
    
    create_demo_server_patch()
    
    print("\n📋 MANUAL STEPS TO ENABLE DEMO SERVER TESTING:")
    print("")
    print("1. Edit brokers/ctrader.py")
    print("2. Find line ~155: host = EndPoints.PROTOBUF_LIVE_HOST")
    print("3. Change to: host = EndPoints.PROTOBUF_DEMO_HOST")
    print("4. Save file and test your trading system")
    print("5. Change back to PROTOBUF_LIVE_HOST when broker enables live access")
    print("")
    print("This will allow you to test all trading logic using demo server")
    print("while waiting for your broker to enable live server permissions.")

if __name__ == "__main__":
    main()
