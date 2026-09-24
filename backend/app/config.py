from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOTOR_", case_sensitive=False)

    qb_url: str = "http://qbittorrent:9090"
    qb_username: str = "admin"
    qb_password: str = ""
    trusted_networks: str = "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
    fetch_timeout_seconds: float = 10.0
    fetch_max_bytes: int = 2_000_000
    monitor_interval_seconds: float = 10.0
    series_path: str = "/storage/series"
    movies_path: str = "/storage/movies"
    series_qb_path: str = "/downloads/series"
    movies_qb_path: str = "/downloads/movies"
    series_volume_id: str = "plex-media"
    movies_volume_id: str = "plex-media"


@lru_cache
def get_settings() -> Settings:
    return Settings()

