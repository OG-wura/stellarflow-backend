import threading
from typing import Dict, Any, Optional

class AssetRegistry:
    """
    Thread-safe global registry for asset mapping configurations.
    Protects reads and mutations to prevent data race conditions in concurrent environments.
    """
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._assets: Dict[str, Any] = {
            "USD": "US Dollar",
            "EUR": "Euro",
            "GBP": "British Pound",
            "NGN": "Nigerian Naira",
            "GHS": "Ghanaian Cedi",
            "KES": "Kenyan Shilling",
            "XLM": "Stellar Lumens",
        }

    def get_asset_name(self, asset_code: str) -> Optional[str]:
        """
        Thread-safe lookup of an asset's name by its code.
        """
        with self._lock:
            return self._assets.get(asset_code)

    def register_asset(self, asset_code: str, name: str) -> None:
        """
        Thread-safe registration of a new asset or update of an existing one.
        """
        with self._lock:
            self._assets[asset_code] = name

    def remove_asset(self, asset_code: str) -> None:
        """
        Thread-safe removal of an asset.
        """
        with self._lock:
            if asset_code in self._assets:
                del self._assets[asset_code]

    def get_all_assets(self) -> Dict[str, str]:
        """
        Thread-safe retrieval of a copy of all global asset configurations.
        """
        with self._lock:
            return self._assets.copy()

# Global config instance to be used across the application.
global_assets: AssetRegistry = AssetRegistry()
