import logging
from functools import lru_cache

from backend.app.config import Settings, get_settings
from backend.app.connectors.clickhouse import ClickHouseConnector
from backend.app.connectors.duckdb import DuckDBConnector
from backend.app.connectors.manager import DataSourceManager

logger = logging.getLogger(__name__)


def create_datasource_manager(settings: Settings) -> DataSourceManager:
    manager = DataSourceManager(default_name=settings.default_datasource)
    manager.register(DuckDBConnector(settings))

    if settings.clickhouse_enabled:
        try:
            clickhouse_connector = ClickHouseConnector(settings)
            clickhouse_connector.ping()
            manager.register(clickhouse_connector)
        except Exception as exc:
            logger.warning("ClickHouse connection failed, skipping registration: %s", exc)

    return manager


@lru_cache
def get_datasource_manager() -> DataSourceManager:
    return create_datasource_manager(get_settings())
