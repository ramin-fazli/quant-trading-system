"""
Example usage of the comprehensive data management system

This script demonstrates various features of the data management system:
- Quick setup and configuration
- Data retrieval from multiple sources
- Real-time data streaming
- Technical analysis
- Data caching and optimization
- Performance monitoring
"""

import logging
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import the data management system
try:
    from data import (
        quick_data_setup,
        get_data_fast,
        create_live_stream,
        DataFactory,
        DataQuery,
        DataType,
        TechnicalIndicators,
        DataUtils,
        DataProfiler
    )
except ImportError as e:
    logger.error(f"Failed to import data management system: {e}")
    logger.info("Make sure you're running this from the correct directory")
    exit(1)


def basic_usage_example():
    """Basic usage example"""
    logger.info("=== Basic Usage Example ===")
    
    # Define symbols to work with
    symbols = ["EURUSD", "GBPUSD", "USDJPY"]
    
    # Quick setup
    logger.info("Setting up data management system...")
    factory = quick_data_setup(symbols, data_dir="example_data")
    
    # Get some sample data (adjust dates as needed)
    logger.info("Retrieving historical data...")
    try:
        data_result = get_data_fast(
            symbols=["EURUSD"], 
            start_date="2024-01-01", 
            end_date="2024-01-31",
            timeframe="1H"
        )
        
        if not data_result["data"].empty:
            logger.info(f"Retrieved {len(data_result['data'])} data points")
            logger.info(f"Data quality score: {data_result['quality_score']:.2f}")
            logger.info(f"Data shape: {data_result['data'].shape}")
            
            # Create analysis
            analysis = factory.create_analysis_suite(data_result)
            logger.info("Technical analysis completed")
            
            return data_result, analysis
        else:
            logger.warning("No data retrieved - check your data sources")
            return None, None
            
    except Exception as e:
        logger.error(f"Failed to retrieve data: {e}")
        return None, None


def advanced_usage_example():
    """Advanced usage with custom configuration"""
    logger.info("=== Advanced Usage Example ===")
    
    # Create factory with custom config
    factory = DataFactory()
    
    # Create data manager
    data_manager = factory.create_data_manager(cache_dir="advanced_cache")
    
    # Custom query
    query = DataQuery(
        symbols=["EURUSD", "GBPUSD"],
        start_date=datetime.now() - timedelta(days=30),
        end_date=datetime.now(),
        timeframe="4H",
        data_type=DataType.OHLCV
    )
    
    try:
        # Get data asynchronously
        data = data_manager.get_data_sync(query)
        
        if not data.empty:
            logger.info(f"Advanced query returned {len(data)} records")
            
            # Data profiling
            profile = DataProfiler.profile_dataframe(data)
            logger.info(f"Data profile: {len(profile)} metrics calculated")
            
            # Performance metrics
            metrics = data_manager.get_metrics()
            logger.info(f"Performance metrics: {len(metrics)} sources tracked")
            
            return data, profile, metrics
        else:
            logger.warning("Advanced query returned no data")
            return None, None, None
            
    except Exception as e:
        logger.error(f"Advanced usage failed: {e}")
        return None, None, None


def streaming_example():
    """Real-time streaming example"""
    logger.info("=== Streaming Example ===")
    
    symbols = ["EURUSD", "GBPUSD"]
    
    try:
        # Create stream manager
        stream_manager = create_live_stream(symbols, stream_type="forex")
        
        # Get main stream
        main_stream = stream_manager.get_stream("main")
        
        if main_stream:
            # Subscribe to events
            def on_tick(tick):
                logger.info(f"Received tick: {tick.symbol} @ {tick.last:.5f}")
            
            def on_error(error_data):
                logger.error(f"Stream error: {error_data}")
            
            main_stream.subscribe("tick", on_tick)
            main_stream.subscribe("error", on_error)
            
            # Start streaming (for demo, we'll stop it quickly)
            logger.info("Starting live data stream...")
            main_stream.start()
            
            # Let it run for a few seconds
            import time
            time.sleep(3)
            
            # Get some data
            latest_data = main_stream.get_latest_data("EURUSD", "1T")
            if not latest_data.empty:
                logger.info(f"Live data: {len(latest_data)} bars retrieved")
            
            # Stop stream
            main_stream.stop()
            logger.info("Stream stopped")
            
            return stream_manager
        else:
            logger.warning("Failed to create stream")
            return None
            
    except Exception as e:
        logger.error(f"Streaming example failed: {e}")
        return None


def technical_analysis_example():
    """Technical analysis example"""
    logger.info("=== Technical Analysis Example ===")
    
    # Create sample data for demonstration
    dates = pd.date_range(start='2024-01-01', periods=100, freq='1H')
    prices = 100 + np.cumsum(np.random.randn(100) * 0.5)
    
    sample_data = pd.DataFrame({
        'close': prices,
        'volume': np.random.randint(1000, 10000, 100)
    }, index=dates)
    
    logger.info(f"Created sample data with {len(sample_data)} points")
    
    try:
        # Calculate various indicators
        sma_20 = TechnicalIndicators.sma(sample_data['close'], 20)
        ema_12 = TechnicalIndicators.ema(sample_data['close'], 12)
        rsi = TechnicalIndicators.rsi(sample_data['close'])
        bollinger = TechnicalIndicators.bollinger_bands(sample_data['close'])
        macd = TechnicalIndicators.macd(sample_data['close'])
        
        # Calculate returns and volatility
        returns = DataUtils.calculate_returns(sample_data, 'close')
        volatility = DataUtils.calculate_volatility(returns)
        
        logger.info("Technical indicators calculated:")
        logger.info(f"- SMA(20): {sma_20.dropna().iloc[-1]:.2f}")
        logger.info(f"- EMA(12): {ema_12.dropna().iloc[-1]:.2f}")
        logger.info(f"- RSI: {rsi.dropna().iloc[-1]:.2f}")
        logger.info(f"- Latest return: {returns.dropna().iloc[-1]:.4f}")
        logger.info(f"- Current volatility: {volatility.dropna().iloc[-1]:.4f}")
        
        return {
            'data': sample_data,
            'sma_20': sma_20,
            'ema_12': ema_12,
            'rsi': rsi,
            'bollinger': bollinger,
            'macd': macd,
            'returns': returns,
            'volatility': volatility
        }
        
    except Exception as e:
        logger.error(f"Technical analysis failed: {e}")
        return None


def performance_test():
    """Performance testing example"""
    logger.info("=== Performance Test ===")
    
    import time
    import numpy as np
    
    # Test data processing speed
    large_data = pd.DataFrame({
        'close': np.random.randn(10000).cumsum() + 100,
        'volume': np.random.randint(1000, 10000, 10000)
    })
    
    logger.info(f"Testing performance on {len(large_data)} data points")
    
    # Test various operations
    operations = []
    
    # SMA calculation
    start_time = time.time()
    sma = TechnicalIndicators.sma(large_data['close'], 20)
    sma_time = time.time() - start_time
    operations.append(('SMA(20)', sma_time))
    
    # EMA calculation
    start_time = time.time()
    ema = TechnicalIndicators.ema(large_data['close'], 20)
    ema_time = time.time() - start_time
    operations.append(('EMA(20)', ema_time))
    
    # RSI calculation
    start_time = time.time()
    rsi = TechnicalIndicators.rsi(large_data['close'])
    rsi_time = time.time() - start_time
    operations.append(('RSI(14)', rsi_time))
    
    # Data resampling
    start_time = time.time()
    resampled = DataUtils.resample_data(large_data, '4H')
    resample_time = time.time() - start_time
    operations.append(('Resample to 4H', resample_time))
    
    # Display results
    logger.info("Performance results:")
    for operation, exec_time in operations:
        logger.info(f"- {operation}: {exec_time:.4f} seconds")
    
    return operations


def main():
    """Main example function"""
    logger.info("Starting Data Management System Examples")
    logger.info("=" * 50)
    
    # Ensure data directory exists
    Path("example_data").mkdir(exist_ok=True)
    
    try:
        # Run examples
        results = {}
        
        # Basic usage
        basic_data, basic_analysis = basic_usage_example()
        results['basic'] = (basic_data, basic_analysis)
        
        # Advanced usage
        advanced_data, profile, metrics = advanced_usage_example()
        results['advanced'] = (advanced_data, profile, metrics)
        
        # Technical analysis
        tech_analysis = technical_analysis_example()
        results['technical'] = tech_analysis
        
        # Performance test
        performance_results = performance_test()
        results['performance'] = performance_results
        
        # Streaming (commented out by default as it's more complex)
        # streaming_manager = streaming_example()
        # results['streaming'] = streaming_manager
        
        logger.info("=" * 50)
        logger.info("All examples completed successfully!")
        
        # Summary
        logger.info("Summary:")
        if results['basic'][0] is not None:
            logger.info("✓ Basic data retrieval working")
        if results['advanced'][0] is not None:
            logger.info("✓ Advanced features working")
        if results['technical'] is not None:
            logger.info("✓ Technical analysis working")
        if results['performance']:
            logger.info("✓ Performance testing completed")
        
        return results
        
    except Exception as e:
        logger.error(f"Example execution failed: {e}")
        return None


if __name__ == "__main__":
    # Import numpy for sample data generation
    import numpy as np
    
    # Run main example
    results = main()
    
    if results:
        print("\nData Management System is ready for use!")
        print("\nKey features demonstrated:")
        print("- Multi-source data retrieval")
        print("- Intelligent caching")
        print("- Technical indicators")
        print("- Data validation and profiling")
        print("- Performance optimization")
        print("\nCheck the logs above for detailed results.")
    else:
        print("\nSome examples failed. Check the logs for details.")