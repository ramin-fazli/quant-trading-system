import os
import json
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from pathlib import Path

@dataclass
class TradingConfig:
    """Unified configuration class for the entire trading system"""
    
    # Project paths
    project_root: str = field(default_factory=lambda: str(Path(__file__).parent.parent))
    config_file: str = field(default_factory=lambda: os.path.join(str(Path(__file__).parent.parent), "config", "trading_config.json"))
    state_file: str = field(default_factory=lambda: os.path.join(str(Path(__file__).parent.parent), "data", "trading_state.json"))
    reports_dir: str = field(default_factory=lambda: os.path.join(str(Path(__file__).parent.parent), "backtest_reports"))
    logs_dir: str = field(default_factory=lambda: os.path.join(str(Path(__file__).parent.parent), "logs"))
    pairs_file: str = field(default_factory=lambda: os.path.join(str(Path(__file__).parent.parent), "config", "pairs.json"))
    
    # Pairs configuration
    pairs: List[str] = field(default_factory=list)
    
    # Data parameters
    interval: str = "M15"
    start_date: str = "2025-06-01"
    data_provider: str = 'mt5'  # Options: 'mt5', 'ctrader'
    end_date: Optional[str] = None
    
    # Strategy parameters
    z_entry: float = 2
    z_exit: float = 0.5
    z_period: int = 100
    min_distance: float = 0  # Minimum distance from mean to enter trade (in percentage)
    min_volatility: float = 0 # Minimum volatility of Pair Ratio to consider pair
    enable_adf: bool = False  # disable ADF test
    max_adf_pval: float = 0.05
    adf_period: int = 100   # Also will be used for Johansen test
    enable_johansen: bool = False  # disable Johansen test
    johansen_crit_level: int = 95
    enable_correlation: bool = False  # disable correlation test  
    min_corr: float = 0.7
    corr_period: int = 100
    enable_vol_ratio: bool = False  # disable volatility ratio test
    vol_ratio_max: float = 2 # Maximum ratio of the symbol's volatility to consider pair valid
    dynamic_z: bool = False
    
    # Risk management parameters
    take_profit_perc: float = 5        # in percentage
    stop_loss_perc: float = 5          # in percentage
    trailing_stop_perc: float = 10     # in percentage
    cooldown_bars: int = 0              # after losing trade, wait for x bars before re-entering the same pair
    max_position_size: float = 10000    # Base currency, both legs combined
    max_open_positions: int = 10        # PORTFOLIO LEVEL: Maximum number of pairs to trade simultaneously. No Impact on backtesting.
    max_monetary_exposure: float = 100000  # PORTFOLIO LEVEL: Maximum total monetary exposure across all positions
    monetary_value_tolerance: float = 0.05  # Leg positions' monetary value difference tolerance. 0.05 means 5% difference is allowed 
    max_commission_perc: float = 0.2    # Maximum total cost (commission + spread) as percentage for both legs
    commission_fixed: float = 0.02      # Fixed commission USD cost per trade. No Impact on backtesting.
    slippage_points: int = 3            # Slippage in points (pips) for order execution
    max_pair_drawdown_perc: float = 5   # Maximum drawdown per pair (%) before suspending pair trading
    max_portfolio_drawdown_perc: float = 10  # Maximum portfolio drawdown (%) before suspending all trading
    initial_portfolio_value: float = 100000.0  # Initial portfolio value for drawdown calculation

    # Algo Trading parameters
    magic_number: int = 12345
    mt5_server: str = "MT5Server"
    mt5_login: Optional[int] = None
    mt5_password: Optional[str] = None
    
    # System parameters
    use_multiprocessing: bool = True
    max_workers: int = 8
    log_level: str = "INFO"
    
    def __post_init__(self):
        """Initialize paths and load configuration"""
        # Ensure directories exist
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
        os.makedirs(self.reports_dir, exist_ok=True)
        os.makedirs(self.logs_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.config_file), exist_ok=True)
        
        # Try to load pairs from JSON file
        # Load pairs from JSON file
        pairs_data = self._load_json_file(
            self.pairs_file,
            lambda data: isinstance(data, list) and all(isinstance(p, str) for p in data)
        )
        if pairs_data is not None:
            self.pairs = pairs_data
        
        # Check if we should force update from code defaults
        force_update = os.getenv('FORCE_CONFIG_UPDATE', 'false').lower() == 'true'
        
        # Load from file if exists, otherwise save defaults
        if os.path.exists(self.config_file) and not force_update:
            # Load existing config but merge with new defaults
            if not self.load_and_merge_config():
                # If load fails, save current defaults
                self.save_to_file()
        else:
            # Save current defaults to file
            self.save_to_file()
    
    def _load_json_file(self, filepath: str, validator: callable = None) -> Optional[Any]:
        """Generic JSON file loader with optional validation
        Args:
            filepath: Path to JSON file
            validator: Optional function that takes loaded data and returns bool
        Returns:
            Loaded and validated data or None if loading/validation fails
        """
        try:
            if os.path.exists(filepath):
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if validator is None or validator(data):
                        return data
                    print(f"Warning: Data validation failed for {filepath}")
            return None
        except Exception as e:
            print(f"Warning: Could not load JSON from {filepath}: {e}")
            return None

    def load_and_merge_config(self) -> bool:
        """Load configuration from file and merge with current defaults"""
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            
            # Get current defaults BEFORE modifying self
            current_defaults = self.to_dict()
            
            # Track if any parameters were added or changed
            config_updated = False
            
            # Check for new parameters that weren't in the file
            for key, default_value in current_defaults.items():
                if key not in config_data:
                    config_updated = True
                elif config_data[key] != default_value:
                    # Check if this is a parameter that should be updated from code defaults
                    if self._should_update_from_defaults(key, config_data[key], default_value):
                        config_updated = True
                        config_data[key] = default_value
            
            # Update fields from file (now including updated values)
            for key, value in config_data.items():
                if hasattr(self, key):
                    setattr(self, key, value)
            
            # Save updated config if any parameters were added or changed
            if config_updated:
                self.save_to_file()
            
            return True
        except Exception as e:
            print(f"Error loading config: {e}")
            return False
    
    def _should_update_from_defaults(self, key: str, file_value: Any, default_value: Any) -> bool:
        """Determine if a parameter should be updated from code defaults"""
        # Parameters that should always be updated from code defaults
        always_update = {
            'project_root', 'config_file', 'state_file', 'reports_dir', 'logs_dir'
        }
        
        if key in always_update:
            return True
        
        # Update pairs list if it's significantly different (size change indicates major update)
        if key == 'pairs':
            if isinstance(file_value, list) and isinstance(default_value, list):
                # If the default list is significantly larger, update it
                return len(default_value) > len(file_value) * 1.5
        
        # For other parameters, don't auto-update (preserve user customizations)
        return False
    
    def load_from_file(self) -> bool:
        """Load configuration from JSON file (original method)"""
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            
            # Update fields from file
            for key, value in config_data.items():
                if hasattr(self, key):
                    setattr(self, key, value)
            
            return True
        except Exception as e:
            print(f"Error loading config: {e}")
            return False
    
    def save_to_file(self) -> bool:
        """Save configuration to JSON file"""
        try:
            # Convert to dict, excluding non-serializable items
            config_dict = {}
            for key, value in self.__dict__.items():
                if not key.startswith('_') and isinstance(value, (str, int, float, bool, list, dict, type(None))):
                    config_dict[key] = value
            
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config_dict, f, indent=4, ensure_ascii=False)

            return True
        except Exception as e:
            print(f"Error saving config: {e}")
            return False
    
    def force_update_from_defaults(self) -> bool:
        """Force update the config file with current code defaults"""
        
        # Get the current defaults by creating a fresh instance without loading from file
        # We need to temporarily disable file loading to get pure defaults
        original_config_file = self.config_file
        temp_config_file = self.config_file + ".temp_disabled"
        
        try:
            # Temporarily move the config file so __post_init__ doesn't load it
            if os.path.exists(original_config_file):
                os.rename(original_config_file, temp_config_file)
            
            # Create a fresh instance with pure defaults
            fresh_config = TradingConfig()
            
            # Copy all default values to current instance
            for key, value in fresh_config.__dict__.items():
                if not key.startswith('_'):
                    setattr(self, key, value)
            
            # Restore the original config file path
            self.config_file = original_config_file
            
            # Save the updated configuration
            result = self.save_to_file()
                
            return result
            
        except Exception as e:
            print(f"Error during force update: {e}")
            return False
            
        finally:
            # Restore original config file if it was moved
            if os.path.exists(temp_config_file):
                if os.path.exists(original_config_file):
                    # If save was successful, remove the temp file
                    os.remove(temp_config_file)
                else:
                    # If save failed, restore the original
                    os.rename(temp_config_file, original_config_file)

    def force_update_specific_params(self, param_names: List[str]) -> bool:
        """Force update specific parameters from code defaults"""
        
        # Get current defaults
        current_defaults = self.to_dict()
        
        # Update specified parameters
        for param_name in param_names:
            if param_name in current_defaults:
                setattr(self, param_name, current_defaults[param_name])
        
        return self.save_to_file()

    def reset_to_defaults(self) -> bool:
        """Reset configuration to code defaults and save"""
        # Delete the existing file
        if os.path.exists(self.config_file):
            os.remove(self.config_file)
        
        # Reinitialize with defaults
        self.__post_init__()
        return True
    
    def update_from_dict(self, config_dict: Dict[str, Any]) -> None:
        """Update configuration from dictionary"""
        for key, value in config_dict.items():
            if hasattr(self, key):
                setattr(self, key, value)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert configuration to dictionary"""
        return {key: value for key, value in self.__dict__.items() 
                if not key.startswith('_')}
    
    @classmethod
    def from_env(cls) -> 'TradingConfig':
        """Create configuration from environment variables"""
        config = cls()
        
        # Override with environment variables if they exist
        env_mappings = {
            'TRADING_PAIRS': 'pairs',
            'TRADING_INTERVAL': 'interval',
            'TRADING_START_DATE': 'start_date',
            'TRADING_END_DATE': 'end_date',
            'MT5_LOGIN': 'mt5_login',
            'MT5_PASSWORD': 'mt5_password',
            'MT5_SERVER': 'mt5_server',
            'INITIAL_PORTFOLIO_VALUE': 'initial_portfolio_value',
            'MAX_POSITION_SIZE': 'max_position_size',
            'MAX_OPEN_POSITIONS': 'max_open_positions',
        }
        
        for env_var, config_attr in env_mappings.items():
            env_value = os.getenv(env_var)
            if env_value is not None:
                if config_attr == 'pairs':
                    setattr(config, config_attr, env_value.split(','))
                elif config_attr in ['mt5_login', 'max_open_positions']:
                    setattr(config, config_attr, int(env_value))
                elif config_attr in ['initial_portfolio_value', 'max_position_size']:
                    setattr(config, config_attr, float(env_value))
                else:
                    setattr(config, config_attr, env_value)
        
        return config

# Global configuration instance
CONFIG = TradingConfig()

# Convenience functions
def get_config() -> TradingConfig:
    """Get the global configuration instance"""
    return CONFIG

def reload_config() -> TradingConfig:
    """Reload configuration from file"""
    global CONFIG
    CONFIG = TradingConfig()
    return CONFIG

def update_config(**kwargs) -> None:
    """Update configuration with keyword arguments"""
    global CONFIG
    CONFIG.update_from_dict(kwargs)
    CONFIG.save_to_file()

def force_config_update() -> None:
    """Force update configuration file with current code defaults"""
    global CONFIG
    
    # Set environment variable to ensure we get fresh defaults
    os.environ['FORCE_CONFIG_UPDATE'] = 'true'
    
    try:
        # Force update the existing config instance
        success = CONFIG.force_update_from_defaults()
        
        if success:
            # Reload the config to ensure we have the latest values
            CONFIG = TradingConfig()
        else:
            print("Configuration force update failed")
            
    except Exception as e:
        print(f"Error during force config update: {e}")
        # Fallback: create entirely new config
        CONFIG = TradingConfig()
        
    finally:
        # Clean up environment variable
        if 'FORCE_CONFIG_UPDATE' in os.environ:
            del os.environ['FORCE_CONFIG_UPDATE']

def force_update_paths() -> None:
    """Force update only the path-related parameters"""
    global CONFIG
    CONFIG.force_update_specific_params(['project_root', 'config_file', 'state_file', 'reports_dir', 'logs_dir'])

def force_update_pairs() -> None:
    """Force update only the pairs list"""
    global CONFIG
    CONFIG.force_update_specific_params(['pairs'])

def reset_config_to_defaults() -> None:
    """Reset configuration to code defaults"""
    global CONFIG
    CONFIG.reset_to_defaults()
