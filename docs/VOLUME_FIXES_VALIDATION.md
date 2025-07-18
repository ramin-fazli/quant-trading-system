# CTrader Volume Calculation Fixes

## Issues Identified and Fixed

### 1. **Volume Constraints Conversion**
**Problem**: cTrader API returns volume constraints (minVolume, maxVolume, stepVolume) in centilots but code treated them as lots.

**Fix**: Convert from centilots to lots by dividing by 100:
```python
# Convert from centilots to lots (divide by 100)
symbol_details[local_name] = value / 100.0
```

**Expected Result**: 
- Before: min_volume=100000 (incorrect)
- After: min_volume=1000.0 (correct for 1000 centilots = 10 lots)

### 2. **Lot Size (Contract Size) Conversion**
**Problem**: lotSize from cTrader API is in centilots but used directly as contract size.

**Fix**: Convert lot size from centilots:
```python
symbol_details['lot_size'] = value / 100.0
```

**Expected Result**:
- Before: contract_size=10000000 (incorrect)
- After: contract_size=100000 (correct for standard forex lot)

### 3. **Order Volume Conversion**
**Problem**: Volume sent to cTrader API was incorrectly calculated.

**Fix**: Proper conversion from lots to centilots:
```python
# Convert volume to cTrader centilots format for the API
broker_volume = int(round(volume * 100))
```

### 4. **Default Values for Missing Fields**
**Problem**: No fallback values when symbol details are missing.

**Fix**: Added reasonable defaults:
```python
if field == 'min_volume':
    details[field] = 0.01  # 0.01 lots
elif field == 'step_volume':
    details[field] = 0.01  # 0.01 lots  
elif field == 'lot_size':
    details[field] = 100000.0  # Standard forex contract size
```

## Expected Log Output After Fixes

### Volume Calculation Logs Should Show:
```
Volume calculation for NZDUSD-USDCAD:
  NZDUSD: price=0.59185, min_volume=1.0, step_volume=1.0
  USDCAD: price=1.37562, min_volume=1.0, step_volume=1.0
  Target monetary value per leg: $5000.00
  Contract sizes: NZDUSD=100000, USDCAD=100000
  Raw volumes: NZDUSD=0.084, USDCAD=0.036
  Normalized volumes: NZDUSD=1.000000, USDCAD=1.000000
  Initial monetary values: NZDUSD=$5918.50, USDCAD=$13756.25
```

### Key Improvements:
1. **Reasonable min_volume**: ~1.0 instead of 100000
2. **Correct contract_size**: 100000 instead of 10000000
3. **Sensible raw volumes**: ~0.08 instead of 0.000845
4. **Realistic monetary values**: ~$5000-15000 instead of trillions

## Validation Checklist
- [ ] Volume constraints are in reasonable ranges (0.01 to 100 lots)
- [ ] Contract sizes are realistic (around 100000 for forex)
- [ ] Raw volumes are sensible (0.01 to 10 lots typically)
- [ ] Monetary values are within expected ranges ($1000-50000)
- [ ] Orders can be placed without volume errors
- [ ] No null/missing values cause crashes

## Testing Commands
To verify the fixes work:
1. Run the trading system
2. Check logs for volume calculation
3. Verify monetary values are reasonable
4. Confirm orders can be placed successfully
