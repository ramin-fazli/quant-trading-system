# CTrader API Implementation Fixes

## Summary
Fixed price and volume calculations in cTrader modules to comply with the official cTrader Open API documentation.

## Issues Fixed

### 1. Historical Price Data Processing (data/ctrader.py)

**Problem**: Historical trendbar data was not properly processed according to cTrader's relative format.

**Fix**: 
- Added proper implementation of cTrader's relative price format
- Historical prices now calculated as: `close = (low + deltaClose) / 100000` rounded to symbol digits
- Added fallback mechanisms for different trendbar attribute formats
- Implemented proper symbol details collection for accurate digit information

**Key Changes**:
```python
def _process_trendbar_prices(self, bar, symbol_details):
    # According to cTrader documentation:
    # - low is the base price
    # - high = low + deltaHigh  
    # - open = low + deltaOpen
    # - close = low + deltaClose
    # All prices must be divided by 100000 and rounded to symbol digits
```

### 2. Real-Time Price Processing (brokers/ctrader.py)

**Problem**: Spot price events were not using proper symbol digits for rounding.

**Fix**:
- Implemented proper price conversion function `_get_price_from_relative()`
- Bid/ask prices now properly converted: `price = relative_price / 100000` rounded to symbol digits
- Added proper error handling for missing symbol details

**Key Changes**:
```python
def _get_price_from_relative(self, symbol_details, relative_price):
    # Divide by 100000 and round to symbol digits as per cTrader documentation
    digits = symbol_details.get('digits', 5)
    actual_price = relative_price / 100000.0
    return round(actual_price, digits)
```

### 3. Volume Constraints Handling

**Problem**: Volume constraints were incorrectly divided by 100, treating them as "cents".

**Fix**:
- Corrected volume normalization to use raw values from cTrader API
- `minVolume`, `maxVolume`, `stepVolume` are already in correct units (lots)
- Removed incorrect division by 100 for volume constraints

**Key Changes**:
```python
def _normalize_ctrader_volume(self, symbol, volume_raw, symbol_details):
    # cTrader volume constraints are already in standard units (not cents)
    min_vol_standard = min_vol  # No division needed
    step_standard = step        # No division needed
```

### 4. Contract Size Handling

**Problem**: Lot sizes were incorrectly divided by 100.

**Fix**:
- Contract sizes now use lot size values directly from cTrader API
- `lotSize` represents actual contract size (e.g., 100000 for standard forex lot)
- Removed incorrect conversion from "cents"

**Key Changes**:
```python
# Use lot sizes directly - they represent the actual contract size
contract_size1 = lot_size1  # No division by 100
contract_size2 = lot_size2  # No division by 100
```

### 5. Symbol Details Collection

**Problem**: Data manager wasn't collecting symbol details needed for proper price conversion.

**Fix**:
- Added symbol details collection during symbols list processing
- Stores `digits`, `pipPosition`, and other essential symbol information
- Provides fallback defaults when symbol details are unavailable

## Compliance with cTrader Documentation

The fixes ensure compliance with the official cTrader documentation patterns:

1. **Price Conversion**: `actual_price = relative_price / 100000` rounded to symbol digits
2. **Trendbar Processing**: Use relative format with low + delta calculations  
3. **Volume Handling**: Use API values directly without additional conversions
4. **Symbol Details**: Collect and use proper symbol metadata for accurate calculations

## Files Modified

1. `data/ctrader.py` - Historical data processing
2. `brokers/ctrader.py` - Real-time trading and volume calculations

## Testing Recommendations

1. Verify historical price data matches expected values from cTrader charts
2. Confirm real-time prices are properly rounded to symbol-specific decimal places
3. Test volume calculations produce valid lot sizes within min/max constraints
4. Validate monetary exposure calculations use correct contract sizes

## Impact

These fixes ensure:
- Accurate price representation matching cTrader platform
- Proper volume calculations for risk management
- Compliance with cTrader API specifications
- Reliable historical and real-time data processing
