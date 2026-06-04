from backend.app.connectors.base import DataSourceConnector
from backend.app.connectors.schema import DataSourceInfo


class DataSourceManager:
    def __init__(self, default_name: str | None = None):
        self._registry: dict[str, DataSourceConnector] = {}
        self._configured_default = default_name
        self._fallback_default: str | None = None

    def register(self, connector: DataSourceConnector) -> None:
        if connector.name in self._registry:
            raise ValueError(f"Datasource already registered: {connector.name}")
        self._registry[connector.name] = connector
        if self._fallback_default is None:
            self._fallback_default = connector.name

    def get(self, name: str) -> DataSourceConnector:
        try:
            return self._registry[name]
        except KeyError as exc:
            raise KeyError(f"Unknown datasource: {name}") from exc

    def get_default(self) -> DataSourceConnector:
        default_name = self.default_name
        if default_name is None:
            raise LookupError("No datasource has been registered.")
        return self.get(default_name)

    def list_sources(self) -> list[DataSourceInfo]:
        return [
            DataSourceInfo(
                name=connector.name,
                dialect=connector.dialect,
                display_name=connector.display_name,
            )
            for connector in self._registry.values()
        ]

    def close(self) -> None:
        for connector in self._registry.values():
            connector.close()

    @property
    def default_name(self) -> str | None:
        if self._configured_default in self._registry:
            return self._configured_default
        return self._fallback_default
