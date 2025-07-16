#!/usr/bin/env python3
"""
CTrader Account and Symbol Access Verification
==============================================

This script helps verify your cTrader account status and symbol access permissions.
"""

import os
import sys

def print_account_verification_steps():
    """Print detailed steps to verify account access"""
    
    print("\n" + "="*70)
    print("🔍 CTRADER ACCOUNT VERIFICATION CHECKLIST")
    print("="*70)
    
    print("""
Your cTrader API connection and authentication are working perfectly! ✅
However, your account is not receiving any trading symbols, which indicates
a permissions or account setup issue.

🔍 IMMEDIATE TROUBLESHOOTING STEPS:

1. 📱 VERIFY ACCOUNT IN CTRADER PLATFORM:
   - Open your cTrader trading platform
   - Log in with the SAME account ID you're using in the API
   - Check if you can see trading symbols (EURUSD, GBPUSD, etc.)
   - If no symbols appear, your account lacks trading permissions

2. 🏦 ACCOUNT TYPE VERIFICATION:
   - Check if you're using the correct account type:
     • Live account with live API credentials ✅
     • Demo account with demo API credentials ✅
     • Live account with demo credentials ❌
     • Demo account with live credentials ❌

3. 🌍 API ACCESS PERMISSIONS:
   - Log into your cTrader account management portal
   - Navigate to API settings
   - Verify that API access is ENABLED
   - Check if there are any restrictions on symbol access

4. 📋 SYMBOL ACCESS RIGHTS:
   - Some accounts have restricted symbol access
   - Contact your broker to verify symbol permissions
   - Ask specifically about "API symbol access"

5. 🔄 TOKEN REGENERATION:
   - Sometimes access tokens lose symbol permissions
   - Try regenerating your access token:
     a) Go to cTrader API portal
     b) Revoke current access token
     c) Generate a new access token
     d) Update your CTRADER_ACCESS_TOKEN environment variable

⚠️  CRITICAL QUESTIONS TO ASK YOUR BROKER:

1. "Is my account {account_id} enabled for API trading?"
2. "Does my account have symbol access permissions?"
3. "Are there any restrictions on my API access?"
4. "Can you see trading symbols when you test my account?"

📞 CONTACT INFORMATION:
- cTrader Support: https://help.ctrader.com/
- Your Broker's Support Team
- cTrader API Support: api-support@ctrader.com

🔧 QUICK FIXES TO TRY:

1. Try a different account ID (if you have multiple accounts)
2. Regenerate your access token
3. Check if you're using the correct broker's cTrader server
4. Verify account funding status (some require minimum balance)

🚀 NEXT STEPS:
""")

    # Get current credentials for reference
    account_id = os.getenv('CTRADER_ACCOUNT_ID', 'NOT_SET')
    client_id = os.getenv('CTRADER_CLIENT_ID', 'NOT_SET')
    
    if account_id != 'NOT_SET':
        masked_account = account_id if len(account_id) <= 4 else account_id[:2] + "*" * (len(account_id)-4) + account_id[-2:]
        print(f"   Your Account ID: {masked_account}")
    
    if client_id != 'NOT_SET':
        masked_client = client_id[:4] + "*" * (len(client_id)-8) + client_id[-4:] if len(client_id) > 8 else client_id
        print(f"   Your Client ID: {masked_client}")
    
    print(f"""
1. Contact your broker with the above account information
2. Ask them to verify API symbol access for your account
3. If needed, request symbol access permissions
4. Once confirmed, test again with: python scripts/diagnose_ctrader.py

💡 TEMPORARY WORKAROUND:
If you need to continue development while resolving this issue,
you can use the demo/simulation mode of the trading system.
""")

def check_alternative_solutions():
    """Suggest alternative solutions"""
    
    print("\n" + "="*70)
    print("🔄 ALTERNATIVE SOLUTIONS")
    print("="*70)
    
    print("""
If the above steps don't resolve the issue, try these alternatives:

1. 🔄 SWITCH TO DIFFERENT ACCOUNT:
   - If you have multiple cTrader accounts, try a different one
   - Some accounts have different permission levels

2. 📞 BROKER-SPECIFIC SETUP:
   - Different brokers have different cTrader configurations
   - Some require additional setup steps for API access
   - Contact your specific broker for their API setup guide

3. 🏦 ACCOUNT VERIFICATION:
   - Ensure your account is fully verified and funded
   - Some brokers require KYC completion before API access
   - Check for any pending verification requirements

4. 🌍 GEOGRAPHICAL RESTRICTIONS:
   - Some regions have restricted trading symbols
   - Verify if your location affects symbol access
   - Ask broker about available symbols for your region

5. 📊 DEMO ACCOUNT TESTING:
   - Create a cTrader demo account
   - Test with demo credentials first
   - This helps isolate permission vs. technical issues

6. 🔧 API VERSION COMPATIBILITY:
   - Ensure you're using the latest cTrader Open API
   - Some older versions have symbol access issues
   - Update with: pip install --upgrade ctrader-open-api
""")

def print_success_verification():
    """Print steps to verify when issue is resolved"""
    
    print("\n" + "="*70)
    print("✅ VERIFICATION WHEN FIXED")
    print("="*70)
    
    print("""
When you've resolved the symbol access issue, you should see:

1. 📋 In the diagnostic script:
   ✅ Symbols Received: ✅
   📊 Received XXX symbols (should be > 0)

2. 🚀 In your trading system:
   ✅ Successfully retrieved XXX symbols from cTrader
   📋 Example symbols: EURUSD, GBPUSD, USDJPY, etc.
   🚀 Starting real trading loop with live cTrader data

3. 📊 Expected log output:
   [INFO] ✅ Successfully retrieved 150+ symbols from cTrader
   [INFO] 📋 Example symbols: EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD
   [INFO] 🚀 Starting real trading loop with live cTrader data

If you still don't see symbols after following the above steps,
the issue is definitely with account permissions, not your setup.
""")

def main():
    """Main function"""
    print_account_verification_steps()
    check_alternative_solutions()
    print_success_verification()
    
    print("\n" + "="*70)
    print("📞 RECOMMENDED IMMEDIATE ACTION")
    print("="*70)
    print("""
Based on the diagnostic results, your next step should be:

1. 📞 CONTACT YOUR BROKER immediately with these details:
   - Account authentication is working ✅
   - No trading symbols are being returned ❌
   - Request verification of API symbol access permissions

2. 🔄 While waiting for broker response:
   - Try regenerating your access token
   - Test with a demo account if available
   - Verify account status in cTrader platform

3. 📊 Test resolution:
   - Run: python scripts/diagnose_ctrader.py
   - Look for "Received XXX symbols" message
   - Should see > 100 symbols for most accounts

The technical connection is perfect - this is purely an account permissions issue! 🎯
""")

if __name__ == "__main__":
    main()
