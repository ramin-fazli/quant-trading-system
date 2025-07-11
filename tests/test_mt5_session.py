import MetaTrader5 as mt5
import pandas as pd
import sys
import datetime

# def check_symbol_session(symbol):
if not mt5.initialize():
    print("MT5 initialization failed")
    sys.exit(1)
info = mt5.symbol_info_tick("EURUSD")
print(f"Info: {info}")

# if __name__ == "__main__":
#     # Example: EURUSD or any symbol available in your MT5
#     symbol = "JPM.US"
#     check_symbol_session(symbol)

