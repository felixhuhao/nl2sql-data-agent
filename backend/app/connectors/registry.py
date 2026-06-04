import logging
from functools import lru_cache

from backend.app.config import Settings, get_settings
from backend.app.connectors.duckdb import DuckDBConnector
from backend.app.connectors.manager import DataSourceManager

logger = logging.getLogger(__name__)


def create_datasource_manager(settings: Settings) -> DataSourceManager:
    manager = DataSourceManager(default_name=settings.default_datasource)
    manager.register(DuckDBConnector(settings))

    if settings.clickhouse_enabled:
        logger.warning("ClickHouse datasource is enabled but ClickHouseConnector is not implemented until I6.2.")

    return manager


@lru_cache
def get_datasource_manager() -> DataSourceManager:
    return create_datasource_manager(get_settings())
