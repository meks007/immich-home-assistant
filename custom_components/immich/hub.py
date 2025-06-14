"""Hub for Immich integration."""
from __future__ import annotations

import logging
from urllib.parse import urljoin

import aiohttp
import aiofiles
import os
import shutil

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CACHE_MODE, DEFAULT_CACHE_MODE,
    CONF_PICTURE_TYPE, DEFAULT_PICTURE_TYPE
)

_HEADER_API_KEY = "x-api-key"
_LOGGER = logging.getLogger(__name__)

_ALLOWED_MIME_TYPES = ["image/png", "image/jpeg"]


class ImmichHub:
    """Immich API hub."""

    def __init__(self, host: str, api_key: str, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize."""
        self.host = host
        self.api_key = api_key
        self.hass = hass
        self.config_entry = config_entry

    async def authenticate(self) -> bool:
        """Test if we can authenticate with the host."""
        try:
            async with aiohttp.ClientSession() as session:
                url = urljoin(self.host, "/api/auth/validateToken")
                headers = {"Accept": "application/json", _HEADER_API_KEY: self.api_key}

                async with session.post(url=url, headers=headers) as response:
                    if response.status != 200:
                        raw_result = await response.text()
                        _LOGGER.error("Error from API: body=%s", raw_result)
                        return False

                    auth_result = await response.json()

                    if not auth_result.get("authStatus"):
                        raw_result = await response.text()
                        _LOGGER.error("Error from API: body=%s", raw_result)
                        return False

                    return True
        except aiohttp.ClientError as exception:
            _LOGGER.error("Error connecting to the API: %s", exception)
            raise CannotConnect from exception

    async def get_my_user_info(self) -> dict:
        """Get user info."""
        try:
            async with aiohttp.ClientSession() as session:
                url = urljoin(self.host, "/api/users/me")
                headers = {"Accept": "application/json", _HEADER_API_KEY: self.api_key}

                async with session.get(url=url, headers=headers) as response:
                    if response.status != 200:
                        raw_result = await response.text()
                        _LOGGER.error("Error from API: body=%s", raw_result)
                        raise ApiError()

                    user_info: dict = await response.json()

                    return user_info
        except aiohttp.ClientError as exception:
            _LOGGER.error("Error connecting to the API: %s", exception)
            raise CannotConnect from exception

    async def get_asset_info(self, asset_id: str) -> dict | None:
        """Get asset info."""
        try:
            async with aiohttp.ClientSession() as session:
                url = urljoin(self.host, f"/api/assets/{asset_id}")
                headers = {"Accept": "application/json", _HEADER_API_KEY: self.api_key}

                async with session.get(url=url, headers=headers) as response:
                    if response.status != 200:
                        raw_result = await response.text()
                        _LOGGER.error("Error from API: body=%s", raw_result)
                        raise ApiError()

                    asset_info: dict = await response.json()

                    return asset_info
        except aiohttp.ClientError as exception:
            _LOGGER.error("Error connecting to the API: %s", exception)
            raise CannotConnect from exception

    async def download_asset(self, asset_id: str) -> bytes | None:
        """Download the asset."""

        asset_bytes = await self.load_cached_asset(asset_id)
        if asset_bytes:
            return asset_bytes
        
        picture_type = self.config_entry.options.get(CONF_PICTURE_TYPE, DEFAULT_PICTURE_TYPE)

        try:
            async with aiohttp.ClientSession() as session:
                _LOGGER.info("Downloading uncached asset from Immich: %s", asset_id)
                url = urljoin(self.host, f"/api/assets/{asset_id}/thumbnail?size={picture_type}")    
                headers = {_HEADER_API_KEY: self.api_key}

                async with session.get(url=url, headers=headers) as response:
                    if response.status != 200:
                        _LOGGER.error("Error from API: status=%d", response.status)
                        return None

                    if response.content_type not in _ALLOWED_MIME_TYPES:
                        _LOGGER.error(
                            "MIME type is not supported: %s", response.content_type
                        )
                        return None

                    return await response.read()
        except aiohttp.ClientError as exception:
            _LOGGER.error("Error connecting to the API: %s", exception)
            raise CannotConnect from exception

    async def load_cached_asset(self, asset_id) -> bytes | None:
        filename = os.path.join(self.asset_cache_path, f"{asset_id}")
        if os.path.isfile(filename):
            try:
                async with aiofiles.open(filename, "rb") as f:
                    _LOGGER.info("Serving asset from cache: %s", asset_id)
                    return await f.read()
            except Exception as e:
                _LOGGER.error("Unable load cached assed: %s %s", asset_id, e)
        return None

    async def cache_album_assets(self, album_assets: list[str]) -> None:
        """Cache album assets."""

        if self.cache_assets:
            for asset_id in album_assets:
                filename = os.path.join(self.asset_cache_path, f"{asset_id}")  # Optional: content_type prüfen
                if not os.path.isfile(filename):
                    asset_bytes = await self.download_asset(asset_id)
                    if asset_bytes:
                        try:
                            async with aiofiles.open(filename, "wb") as f:
                                _LOGGER.info("Caching asset: %s", asset_id)
                                await f.write(asset_bytes)
                        except Exception as e:
                            _LOGGER.error("Unable to cache asset: %s %s", asset_id, e)

    def initialize_asset_cache(self) -> None:
        self.cache_assets = self.config_entry.options.get(CONF_CACHE_MODE, DEFAULT_CACHE_MODE)
        self.asset_cache_path = self.hass.config.path('immich_cache')
        
        if os.path.isdir(self.asset_cache_path):
            try:
                shutil.rmtree(self.asset_cache_path)
                _LOGGER.info("Cleared asset cache")
            except Exception as e:
                _LOGGER.error("Unable to clear asset cache directory: %s", e)

        if self.cache_assets:
            try:
                os.makedirs(self.asset_cache_path, exist_ok=True)
                _LOGGER.info("Created asset cache directory: ", self.asset_cache_path)
            except Exception as e:
                _LOGGER.error("Unable to create asset cache directory: %s %s", self.asset_cache_path, e)

    async def list_favorite_images(self) -> list[dict]:
        """List all favorite images."""
        try:
            async with aiohttp.ClientSession() as session:
                url = urljoin(self.host, "/api/search/metadata")
                headers = {"Accept": "application/json", _HEADER_API_KEY: self.api_key}
                data = {"isFavorite": "true"}

                async with session.post(url=url, headers=headers, data=data) as response:
                    if response.status != 200:
                        raw_result = await response.text()
                        _LOGGER.error("Error from API: body=%s", raw_result)
                        raise ApiError()

                    favorites = await response.json()
                    assets: list[dict] = favorites["assets"]["items"]

                    filtered_assets: list[dict] = [
                        asset for asset in assets if asset["type"] == "IMAGE"
                    ]

                    return filtered_assets
        except aiohttp.ClientError as exception:
            _LOGGER.error("Error connecting to the API: %s", exception)
            raise CannotConnect from exception

    async def list_all_albums(self) -> list[dict]:
        """List all albums."""
        try:
            async with aiohttp.ClientSession() as session:
                url = urljoin(self.host, "/api/albums")
                headers = {"Accept": "application/json", _HEADER_API_KEY: self.api_key}

                async with session.get(url=url, headers=headers) as response:
                    if response.status != 200:
                        raw_result = await response.text()
                        _LOGGER.error("Error from API: body=%s", raw_result)
                        raise ApiError()

                    album_list: list[dict] = await response.json()

                    return album_list
        except aiohttp.ClientError as exception:
            _LOGGER.error("Error connecting to the API: %s", exception)
            raise CannotConnect from exception

    async def list_album_images(self, album_id: str) -> list[dict]:
        """List all images in an album."""
        try:
            async with aiohttp.ClientSession() as session:
                url = urljoin(self.host, f"/api/albums/{album_id}")
                headers = {"Accept": "application/json", _HEADER_API_KEY: self.api_key}

                async with session.get(url=url, headers=headers) as response:
                    if response.status != 200:
                        raw_result = await response.text()
                        _LOGGER.error("Error from API: body=%s", raw_result)
                        raise ApiError()

                    album_info: dict = await response.json()
                    assets: list[dict] = album_info["assets"]

                    filtered_assets: list[dict] = [
                        asset for asset in assets if asset["type"] == "IMAGE"
                    ]

                    return filtered_assets
        except aiohttp.ClientError as exception:
            _LOGGER.error("Error connecting to the API: %s", exception)
            raise CannotConnect from exception


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class ApiError(HomeAssistantError):
    """Error to indicate that the API returned an error."""
