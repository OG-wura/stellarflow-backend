import logging
from src.config.assets import global_assets

logger = logging.getLogger(__name__)

def validate_asset_pair(asset_code: str):
    try:
        result = global_assets.get_asset_name(asset_code)
        if result is not None:
            return result
        raise KeyError(asset_code)
    except Exception:
        logger.warning(f"Unmapped asset code encountered: {asset_code}")