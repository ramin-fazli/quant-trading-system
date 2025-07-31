"""
Production-Ready Configuration System
=====================================

Environment-first configuration system optimized for deployment.
All configuration is driven by environment variables with sensible defaults.
No file system dependencies during runtime.
"""

import os
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from pathlib import Path

# Load environment variables from .env file if available
try:
    from dotenv import load_dotenv
    # Find .env file in project root
    project_root = Path(__file__).parent.parent
    env_path = project_root / '.env'
except ImportError:
    print("⚠️  python-dotenv not installed. Environment variables from .env file will not be loaded")

logger = logging.getLogger(__name__)


def _str_to_bool(value: str) -> bool:
    """Convert string to boolean"""
    return value.lower() in ('true', '1', 'yes', 'on')


def _load_pairs_from_env() -> List[str]:
    """Load trading pairs from environment variable or JSON file fallback"""
    # First priority: Environment variable
    pairs_env = os.getenv('TRADING_PAIRS')
    if pairs_env:
        pairs = [pair.strip() for pair in pairs_env.split(',') if pair.strip()]
        if pairs:
            print(f"✅ Loaded {len(pairs)} trading pairs from TRADING_PAIRS environment variable")
            return pairs
    
    # Second priority: JSON file (production-ready fallback)
    try:
        import json
        project_root = Path(__file__).parent.parent
        pairs_file = project_root / 'config' / 'pairs.json'
        
        if pairs_file.exists():
            with open(pairs_file, 'r', encoding='utf-8') as f:
                pairs = json.load(f)
                if isinstance(pairs, list) and pairs:
                    print(f"✅ Loaded {len(pairs)} trading pairs from {pairs_file}")
                    print(f"   📁 File location: {pairs_file.absolute()}")
                    print(f"   🔄 This file can be modified at runtime when using Docker volumes")
                    return pairs
                else:
                    print(f"⚠️  Invalid pairs format in {pairs_file}")
        else:
            print(f"⚠️  Pairs file not found: {pairs_file}")
            print(f"   💡 You can create this file with your trading pairs in JSON format")
    except Exception as e:
        print(f"⚠️  Error loading pairs from JSON file: {e}")


def _parse_mt5_login() -> Optional[int]:
    """Parse MT5 login from environment, handling invalid values"""
    login_str = os.getenv('MT5_LOGIN')
    if not login_str:
        return None
    
    # Check for placeholder values
    if login_str.lower() in ('your_login_number', 'none', 'null', ''):
        return None
    
    try:
        return int(login_str)
    except ValueError:
        logger.warning(f"Invalid MT5_LOGIN value: {login_str}. Using None.")
        return None


def _parse_mt5_password() -> Optional[str]:
    """Parse MT5 password from environment, handling placeholder values"""
    password = os.getenv('MT5_PASSWORD')
    if not password:
        return None
    
    # Check for placeholder values
    if password.lower() in ('your_password', 'none', 'null', ''):
        return None
    
    return password


def _parse_mt5_login() -> Optional[int]:
    """Parse MT5 login from environment variable, handling placeholder values"""
    login_str = os.getenv('MT5_LOGIN')
    if not login_str or login_str.lower() in ['your_login_number', 'none', 'null', '']:
        return None
    try:
        return int(login_str)
    except ValueError:
        logger.warning(f"Invalid MT5_LOGIN value: {login_str}. Using None.")
        return None


def _parse_mt5_password() -> Optional[str]:
    """Parse MT5 password from environment variable, handling placeholder values"""
    password = os.getenv('MT5_PASSWORD')
    if not password or password.lower() in ['your_password', 'none', 'null', '']:
        return None
    return password


@dataclass
class TradingConfig:
    """
    Production-ready configuration with environment variable priority.
    All parameters can be overridden via environment variables.
    No file system dependencies during runtime.
    
    Trading Pairs Loading Priority:
    1. TRADING_PAIRS environment variable (comma-separated)
    2. config/pairs.json file (recommended for large lists)
    3. Default hardcoded pairs (fallback)
    """
    
    # === Project Paths (computed at runtime) ===
    project_root: str = field(default_factory=lambda: str(Path(__file__).parent.parent))
    
    # === Data Parameters ===
    data_provider: str = field(default_factory=lambda: os.getenv('DATA_PROVIDER', 'ctrader'))
    broker: str = field(default_factory=lambda: os.getenv('BROKER', 'ctrader'))
    interval: str = field(default_factory=lambda: os.getenv('TRADING_INTERVAL', 'M15'))
    start_date: str = field(default_factory=lambda: os.getenv('TRADING_START_DATE', '2025-06-01'))
    end_date: Optional[str] = field(default_factory=lambda: os.getenv('TRADING_END_DATE'))
    
    # === Trading Pairs ===
    pairs: List[str] = field(default_factory=_load_pairs_from_env)
    
    # === Strategy Parameters ===
    z_entry: float = field(default_factory=lambda: float(os.getenv('Z_ENTRY', '2.0')))
    z_exit: float = field(default_factory=lambda: float(os.getenv('Z_EXIT', '0.5')))
    z_period: int = field(default_factory=lambda: int(os.getenv('Z_PERIOD', '100')))
    min_distance: float = field(default_factory=lambda: float(os.getenv('MIN_DISTANCE', '0')))
    min_volatility: float = field(default_factory=lambda: float(os.getenv('MIN_VOLATILITY', '0')))
    
    # === Statistical Tests ===
    enable_adf: bool = field(default_factory=lambda: _str_to_bool(os.getenv('ENABLE_ADF', 'false')))
    max_adf_pval: float = field(default_factory=lambda: float(os.getenv('MAX_ADF_PVAL', '0.05')))
    adf_period: int = field(default_factory=lambda: int(os.getenv('ADF_PERIOD', '100')))
    enable_johansen: bool = field(default_factory=lambda: _str_to_bool(os.getenv('ENABLE_JOHANSEN', 'false')))
    johansen_crit_level: int = field(default_factory=lambda: int(os.getenv('JOHANSEN_CRIT_LEVEL', '95')))
    enable_correlation: bool = field(default_factory=lambda: _str_to_bool(os.getenv('ENABLE_CORRELATION', 'false')))
    min_corr: float = field(default_factory=lambda: float(os.getenv('MIN_CORR', '0.7')))
    corr_period: int = field(default_factory=lambda: int(os.getenv('CORR_PERIOD', '100')))
    enable_vol_ratio: bool = field(default_factory=lambda: _str_to_bool(os.getenv('ENABLE_VOL_RATIO', 'false')))
    vol_ratio_max: float = field(default_factory=lambda: float(os.getenv('VOL_RATIO_MAX', '2')))
    dynamic_z: bool = field(default_factory=lambda: _str_to_bool(os.getenv('DYNAMIC_Z', 'false')))
    
    # === Risk Management Parameters ===
    take_profit_perc: float = field(default_factory=lambda: float(os.getenv('TAKE_PROFIT_PERC', '0.5')))
    stop_loss_perc: float = field(default_factory=lambda: float(os.getenv('STOP_LOSS_PERC', '0.5')))
    trailing_stop_perc: float = field(default_factory=lambda: float(os.getenv('TRAILING_STOP_PERC', '10')))
    cooldown_bars: int = field(default_factory=lambda: int(os.getenv('COOLDOWN_BARS', '0')))
    max_position_size: float = field(default_factory=lambda: float(os.getenv('MAX_POSITION_SIZE', '10000')))
    max_open_positions: int = field(default_factory=lambda: int(os.getenv('MAX_OPEN_POSITIONS', '10')))
    max_monetary_exposure: float = field(default_factory=lambda: float(os.getenv('MAX_MONETARY_EXPOSURE', '100000')))
    monetary_value_tolerance: float = field(default_factory=lambda: float(os.getenv('MONETARY_VALUE_TOLERANCE', '0.05')))
    max_commission_perc: float = field(default_factory=lambda: float(os.getenv('MAX_COMMISSION_PERC', '0.2')))
    commission_fixed: float = field(default_factory=lambda: float(os.getenv('COMMISSION_FIXED', '0.02')))
    slippage_points: int = field(default_factory=lambda: int(os.getenv('SLIPPAGE_POINTS', '3')))
    max_pair_drawdown_perc: float = field(default_factory=lambda: float(os.getenv('MAX_PAIR_DRAWDOWN_PERC', '5')))
    max_portfolio_drawdown_perc: float = field(default_factory=lambda: float(os.getenv('MAX_PORTFOLIO_DRAWDOWN_PERC', '10')))
    initial_portfolio_value: float = field(default_factory=lambda: float(os.getenv('INITIAL_PORTFOLIO_VALUE', '100000.0')))
    
    # === MT5 Specific Parameters ===
    magic_number: int = field(default_factory=lambda: int(os.getenv('MT5_MAGIC_NUMBER', '12345')))
    mt5_server: str = field(default_factory=lambda: os.getenv('MT5_SERVER', 'MetaQuotes-Demo'))
    mt5_login: Optional[int] = field(default_factory=lambda: _parse_mt5_login())
    mt5_password: Optional[str] = field(default_factory=lambda: _parse_mt5_password())
    
    # === CTrader Specific Parameters ===
    ctrader_trading_label: str = field(default_factory=lambda: os.getenv('CTRADER_TRADING_LABEL', 'PairsTradingBot'))
    
    # === Dashboard Parameters ===
    dashboard_host: str = field(default_factory=lambda: os.getenv('DASHBOARD_HOST', '0.0.0.0'))
    dashboard_port: int = field(default_factory=lambda: int(os.getenv('DASHBOARD_PORT', '8050')))
    execution_broker: str = field(default_factory=lambda: os.getenv('EXECUTION_BROKER', 'ctrader'))
    
    # === System Parameters ===
    use_multiprocessing: bool = field(default_factory=lambda: _str_to_bool(os.getenv('USE_MULTIPROCESSING', 'true')))
    max_workers: int = field(default_factory=lambda: int(os.getenv('MAX_WORKERS', '8')))
    log_level: str = field(default_factory=lambda: os.getenv('LOG_LEVEL', 'WARNING'))
    
    # === Computed Paths (only computed when accessed) ===
    @property
    def config_file(self) -> str:
        """Config file path (legacy compatibility)"""
        return os.path.join(self.project_root, "config", "trading_config.json")
    
    @property
    def state_file(self) -> str:
        """State file path (legacy compatibility)"""
        return os.path.join(self.project_root, "data", "trading_state.json")
    
    @property
    def reports_dir(self) -> str:
        """Reports directory path"""
        return os.path.join(self.project_root, "backtest_reports")
    
    @property
    def logs_dir(self) -> str:
        """Logs directory path"""
        return os.path.join(self.project_root, "logs")
    
    @property
    def pairs_file(self) -> str:
        """Pairs file path (legacy compatibility)"""
        return os.path.join(self.project_root, "config", "pairs.json")
    
    def __post_init__(self):
        """Minimal post-init - only essential validation"""
        # Validate critical parameters
        if not self.data_provider:
            raise ValueError("DATA_PROVIDER environment variable is required")
        
        if not self.broker:
            raise ValueError("BROKER environment variable is required")
            
        if not self.pairs:
            raise ValueError("TRADING_PAIRS environment variable is required and cannot be empty")
        
        # Validate data provider
        valid_providers = ['ctrader', 'mt5']
        if self.data_provider.lower() not in valid_providers:
            raise ValueError(f"DATA_PROVIDER must be one of: {valid_providers}")
        
        # Validate broker
        valid_brokers = ['ctrader', 'mt5']
        if self.broker.lower() not in valid_brokers:
            raise ValueError(f"BROKER must be one of: {valid_brokers}")
        
        # Log configuration summary
        logger.info(f"Configuration loaded: {self.data_provider} data, {self.broker} broker, {len(self.pairs)} pairs")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary for compatibility"""
        return {
            # Core parameters
            'data_provider': self.data_provider,
            'broker': self.broker,
            'interval': self.interval,
            'start_date': self.start_date,
            'end_date': self.end_date,
            'pairs': self.pairs,
            
            # Strategy parameters
            'z_entry': self.z_entry,
            'z_exit': self.z_exit,
            'z_period': self.z_period,
            'min_distance': self.min_distance,
            'min_volatility': self.min_volatility,
            
            # Statistical tests
            'enable_adf': self.enable_adf,
            'max_adf_pval': self.max_adf_pval,
            'adf_period': self.adf_period,
            'enable_johansen': self.enable_johansen,
            'johansen_crit_level': self.johansen_crit_level,
            'enable_correlation': self.enable_correlation,
            'min_corr': self.min_corr,
            'corr_period': self.corr_period,
            'enable_vol_ratio': self.enable_vol_ratio,
            'vol_ratio_max': self.vol_ratio_max,
            'dynamic_z': self.dynamic_z,
            
            # Risk management
            'take_profit_perc': self.take_profit_perc,
            'stop_loss_perc': self.stop_loss_perc,
            'trailing_stop_perc': self.trailing_stop_perc,
            'cooldown_bars': self.cooldown_bars,
            'max_position_size': self.max_position_size,
            'max_open_positions': self.max_open_positions,
            'max_monetary_exposure': self.max_monetary_exposure,
            'monetary_value_tolerance': self.monetary_value_tolerance,
            'max_commission_perc': self.max_commission_perc,
            'commission_fixed': self.commission_fixed,
            'slippage_points': self.slippage_points,
            'max_pair_drawdown_perc': self.max_pair_drawdown_perc,
            'max_portfolio_drawdown_perc': self.max_portfolio_drawdown_perc,
            'initial_portfolio_value': self.initial_portfolio_value,
            
            # MT5 parameters
            'magic_number': self.magic_number,
            'mt5_server': self.mt5_server,
            'mt5_login': self.mt5_login,
            'mt5_password': self.mt5_password,
            
            # System parameters
            'use_multiprocessing': self.use_multiprocessing,
            'max_workers': self.max_workers,
            'log_level': self.log_level,
        }
    
    def get_strategy_params(self) -> Dict[str, Any]:
        """Get strategy-specific parameters"""
        return {
            'z_entry': self.z_entry,
            'z_exit': self.z_exit,
            'z_period': self.z_period,
            'min_distance': self.min_distance,
            'min_volatility': self.min_volatility,
            'enable_adf': self.enable_adf,
            'max_adf_pval': self.max_adf_pval,
            'adf_period': self.adf_period,
            'enable_johansen': self.enable_johansen,
            'johansen_crit_level': self.johansen_crit_level,
            'enable_correlation': self.enable_correlation,
            'min_corr': self.min_corr,
            'corr_period': self.corr_period,
            'enable_vol_ratio': self.enable_vol_ratio,
            'vol_ratio_max': self.vol_ratio_max,
            'dynamic_z': self.dynamic_z,
        }
    
    def get_risk_params(self) -> Dict[str, Any]:
        """Get risk management parameters"""
        return {
            'take_profit_perc': self.take_profit_perc,
            'stop_loss_perc': self.stop_loss_perc,
            'trailing_stop_perc': self.trailing_stop_perc,
            'cooldown_bars': self.cooldown_bars,
            'max_position_size': self.max_position_size,
            'max_open_positions': self.max_open_positions,
            'max_monetary_exposure': self.max_monetary_exposure,
            'monetary_value_tolerance': self.monetary_value_tolerance,
            'max_commission_perc': self.max_commission_perc,
            'commission_fixed': self.commission_fixed,
            'slippage_points': self.slippage_points,
            'max_pair_drawdown_perc': self.max_pair_drawdown_perc,
            'max_portfolio_drawdown_perc': self.max_portfolio_drawdown_perc,
            'initial_portfolio_value': self.initial_portfolio_value,
        }


# Singleton instance
_config_instance = None


def get_config() -> TradingConfig:
    """
    Get configuration instance (singleton).
    Configuration is loaded from environment variables on first access.
    """
    global _config_instance
    if _config_instance is None:
        _config_instance = TradingConfig()
    return _config_instance


def force_config_update():
    """Force configuration reload from environment variables"""
    global _config_instance
    _config_instance = None
    return get_config()
