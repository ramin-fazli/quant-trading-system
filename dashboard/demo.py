"""
Quick Start Example for Trading Dashboard

This script demonstrates how to quickly set up and run the trading dashboard
with sample data for testing and development purposes.
"""

import sys
import os

# Add the project root to the path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(project_root)

try:
    from dashboard.dashboard_integration import (
        quick_start_dashboard,
        start_dashboard_with_backtest,
        example_backtest_dashboard,
        example_live_dashboard
    )
    import json
    import time
    import threading
    from datetime import datetime, timedelta
    import pandas as pd
    import numpy as np
    
    def generate_sample_backtest_data():
        """Generate realistic sample backtest data for demonstration"""
        
        # Generate sample portfolio equity curve
        dates = pd.date_range(start='2024-01-01', end='2024-12-31', freq='D')
        returns = np.random.normal(0.0008, 0.015, len(dates))  # Daily returns
        equity_curve = pd.Series((1 + returns).cumprod())  # Convert to pandas Series
        
        portfolio_data = pd.DataFrame({
            'timestamp': dates,
            'equity': equity_curve * 100000,  # Starting with $100k
            'returns': returns,
            'trades': np.random.poisson(2, len(dates)),  # Average 2 trades per day
            'drawdown': equity_curve / equity_curve.expanding().max() - 1
        })
        
        # Generate sample pair results
        pairs = ['AAPL.US-MSFT.US', 'NVDA.US-AMD.US', 'JPM.US-BAC.US', 'TSLA.US-GM.US']
        pair_results = []
        
        for pair in pairs:
            # Generate realistic pair performance
            pair_returns = np.random.normal(0.001, 0.02, 100)
            sharpe = np.mean(pair_returns) / np.std(pair_returns) * np.sqrt(252)
            max_dd = np.min(np.cumsum(pair_returns))
            
            pair_data = {
                'pair': pair,
                'return': np.sum(pair_returns) * 100,
                'sharpe_ratio': sharpe,
                'max_drawdown': max_dd * 100,
                'total_trades': np.random.randint(50, 200),
                'win_rate': np.random.uniform(0.45, 0.65),
                'profit_factor': np.random.uniform(1.1, 2.5),
                'avg_trade_duration': np.random.uniform(2, 10),  # days
                'correlation': np.random.uniform(0.3, 0.8),
                'z_score_mean': np.random.uniform(-0.1, 0.1),
                'z_score_std': np.random.uniform(0.8, 1.5)
            }
            pair_results.append(pair_data)
        
        # Calculate portfolio metrics
        portfolio_metrics = {
            'portfolio_return': float((equity_curve.iloc[-1] - 1) * 100),
            'portfolio_sharpe': float(np.mean(returns) / np.std(returns) * np.sqrt(252)),
            'portfolio_max_drawdown': float(portfolio_data['drawdown'].min() * 100),
            'total_trades': int(portfolio_data['trades'].sum()),
            'portfolio_win_rate': np.random.uniform(0.52, 0.68),
            'portfolio_volatility': float(np.std(returns) * np.sqrt(252) * 100),
            'portfolio_calmar': float((equity_curve.iloc[-1] - 1) / abs(portfolio_data['drawdown'].min())),
            'avg_monthly_return': float(np.mean(returns) * 30 * 100),
            'best_month': float(portfolio_data.set_index('timestamp')['returns'].resample('ME').sum().max() * 100),
            'worst_month': float(portfolio_data.set_index('timestamp')['returns'].resample('ME').sum().min() * 100)
        }
        
        return {
            'portfolio_data': portfolio_data,
            'portfolio_metrics': portfolio_metrics,
            'pair_results': pair_results,
            'backtest_config': {
                'start_date': '2024-01-01',
                'end_date': '2024-12-31',
                'initial_capital': 100000,
                'pairs_count': len(pairs),
                'strategy': 'Statistical Arbitrage'
            }
        }
    
    def demo_basic_dashboard():
        """Demonstrate basic dashboard functionality"""
        print("🚀 Starting Basic Dashboard Demo...")
        print("=" * 50)
        
        # Start basic dashboard
        dashboard = quick_start_dashboard(
            port=8050,
            theme='dark',
            open_browser=False
        )
        
        if dashboard:
            print("✅ Basic dashboard started successfully!")
            print(f"🌐 Dashboard URL: http://127.0.0.1:8050")
            print("📊 Features: Basic overview, system status")
            print("\nPress Ctrl+C to stop the dashboard")
            
            try:
                dashboard.keep_alive()
            except KeyboardInterrupt:
                print("\n🛑 Stopping dashboard...")
                dashboard.stop()
        else:
            print("❌ Failed to start basic dashboard")
    
    def demo_backtest_dashboard():
        """Demonstrate dashboard with backtest data"""
        print("📊 Starting Backtest Dashboard Demo...")
        print("=" * 50)
        
        # Generate sample data
        print("📈 Generating sample backtest data...")
        backtest_data = generate_sample_backtest_data()
        
        print(f"✅ Generated data for {len(backtest_data['pair_results'])} trading pairs")
        print(f"📊 Portfolio Return: {backtest_data['portfolio_metrics']['portfolio_return']:.2f}%")
        print(f"📈 Sharpe Ratio: {backtest_data['portfolio_metrics']['portfolio_sharpe']:.2f}")
        print(f"📉 Max Drawdown: {backtest_data['portfolio_metrics']['portfolio_max_drawdown']:.2f}%")
        
        # Start dashboard with backtest data
        dashboard = start_dashboard_with_backtest(
            backtest_results=backtest_data,
            config={'port': 8051}
        )
        
        if dashboard:
            print("\n✅ Backtest dashboard started successfully!")
            print(f"🌐 Dashboard URL: http://127.0.0.1:8051")
            print("📊 Features: Portfolio analysis, pair performance, risk metrics")
            print("\nPress Ctrl+C to stop the dashboard")
            
            try:
                dashboard.keep_alive()
            except KeyboardInterrupt:
                print("\n🛑 Stopping dashboard...")
                dashboard.stop()
        else:
            print("❌ Failed to start backtest dashboard")
    
    def demo_live_dashboard():
        """Demonstrate dashboard with live data simulation"""
        print("📡 Starting Live Dashboard Demo...")
        print("=" * 50)
        
        # Start dashboard with simulated live data
        symbols = ['AAPL.US', 'MSFT.US', 'NVDA.US', 'TSLA.US']
        dashboard = example_live_dashboard()
        
        if dashboard:
            print("✅ Live dashboard started successfully!")
            print(f"🌐 Dashboard URL: http://127.0.0.1:8050")
            print(f"📡 Simulating live data for: {', '.join(symbols)}")
            print("📊 Features: Real-time updates, live charts, WebSocket streaming")
            print("\nPress Ctrl+C to stop the dashboard")
            
            try:
                dashboard.keep_alive()
            except KeyboardInterrupt:
                print("\n🛑 Stopping dashboard...")
                dashboard.stop()
        else:
            print("❌ Failed to start live dashboard")
    
    def interactive_demo():
        """Interactive demo menu"""
        print("🎯 Trading Dashboard Interactive Demo")
        print("=" * 50)
        
        while True:
            print("\nChoose a demo option:")
            print("1. 🚀 Basic Dashboard (minimal setup)")
            print("2. 📊 Backtest Dashboard (with sample data)")
            print("3. 📡 Live Dashboard (with simulated data)")
            print("4. 📖 View Documentation")
            print("5. 🚪 Exit")
            
            choice = input("\nEnter your choice (1-5): ").strip()
            
            if choice == '1':
                demo_basic_dashboard()
            elif choice == '2':
                demo_backtest_dashboard()
            elif choice == '3':
                demo_live_dashboard()
            elif choice == '4':
                readme_path = os.path.join(os.path.dirname(__file__), 'README.md')
                if os.path.exists(readme_path):
                    print(f"\n📖 Documentation available at: {readme_path}")
                    print("🌐 Or visit the dashboard and check the help section")
                else:
                    print("\n📖 Documentation not found. Check the README.md file.")
            elif choice == '5':
                print("\n👋 Thanks for trying the Trading Dashboard!")
                break
            else:
                print("❌ Invalid choice. Please select 1-5.")
    
    if __name__ == "__main__":
        print("🎯 Trading Dashboard Demo")
        print("=" * 50)
        print("This demo showcases the trading dashboard capabilities")
        print("with sample data and real-time simulations.")
        print("")
        
        # Check if running interactively
        if len(sys.argv) > 1:
            demo_type = sys.argv[1].lower()
            if demo_type == 'basic':
                demo_basic_dashboard()
            elif demo_type == 'backtest':
                demo_backtest_dashboard()
            elif demo_type == 'live':
                demo_live_dashboard()
            else:
                print(f"❌ Unknown demo type: {demo_type}")
                print("Available options: basic, backtest, live")
        else:
            interactive_demo()

except ImportError as e:
    print(f"❌ Import Error: {e}")
    print("Please ensure the dashboard module is properly installed.")
    print("Run: pip install flask flask-socketio pandas numpy plotly")
except Exception as e:
    print(f"❌ Error: {e}")
    print("Please check the dashboard installation and configuration.")
