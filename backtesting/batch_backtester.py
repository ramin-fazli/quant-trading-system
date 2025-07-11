import pandas as pd
from data.mt5 import VectorBTBacktester, MT5DataManager, TradingConfig, get_config

def run_batch_backtest(pair_list, config):
    data_manager = MT5DataManager(config)
    backtester = VectorBTBacktester(config, data_manager)
    results = []
    for batch_start in range(0, len(pair_list), config.batch_size):
        batch_pairs = pair_list[batch_start:batch_start+config.batch_size]
        config.pairs = batch_pairs
        batch_results = backtester._run_pair_backtests()
        # Save batch results to disk
        pd.DataFrame(batch_results).to_parquet(f"results_batch_{batch_start}.parquet")
        results.extend(batch_results)
    return results

if __name__ == "__main__":
    config = get_config()
    config.use_multiprocessing = True
    config.max_workers = 16  # or as many as your CPU allows
    config.batch_size = 10000  # adjust based on RAM
    # Load/generate your 1.5M pairs as a list
    all_pairs = [...]  # e.g., ["AAPL-MSFT", "GOOG-AMZN", ...]
    run_batch_backtest(all_pairs, config)