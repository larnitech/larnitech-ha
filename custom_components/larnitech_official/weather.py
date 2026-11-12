"""The weather element of the Larnitech weather plugin (999:9): current weather and daily forecast."""
from __future__ import annotations

from typing import Any

from homeassistant.components.weather import Forecast, WeatherEntity, WeatherEntityFeature
from homeassistant.const import UnitOfPressure, UnitOfSpeed, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import LarnitechConfigEntry
from .const import WEATHER_TYPES
from .decode import weather
from .entity import LarnitechEntity, devices_of


async def async_setup_entry(hass: HomeAssistant, entry: LarnitechConfigEntry, async_add: AddEntitiesCallback) -> None:
    async_add(LarnitechWeather(entry, d) for d in devices_of(entry, WEATHER_TYPES))


class LarnitechWeather(LarnitechEntity, WeatherEntity):
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_native_pressure_unit = UnitOfPressure.MMHG
    _attr_native_wind_speed_unit = UnitOfSpeed.KILOMETERS_PER_HOUR
    _attr_supported_features = WeatherEntityFeature.FORECAST_DAILY

    @property
    def _data(self) -> dict[str, Any] | None:
        return weather(self.status, dt_util.now().date())

    @property
    def condition(self) -> str | None:
        return (self._data or {}).get("condition")

    @property
    def native_temperature(self) -> float | None:
        return (self._data or {}).get("temperature")

    @property
    def humidity(self) -> float | None:
        return (self._data or {}).get("humidity")

    @property
    def native_pressure(self) -> float | None:
        return (self._data or {}).get("pressure")

    @property
    def native_wind_speed(self) -> float | None:
        return (self._data or {}).get("wind_speed")

    @property
    def wind_bearing(self) -> float | None:
        return (self._data or {}).get("wind_bearing")

    async def async_forecast_daily(self) -> list[Forecast] | None:
        data = self._data
        if data is None:
            return None
        tz = dt_util.get_default_time_zone()
        return [
            Forecast(
                datetime=dt_util.start_of_local_day(day["date"]).astimezone(tz).isoformat(),
                condition=day["condition"],
                native_temperature=day["temperature"],
                native_templow=day["templow"],
            )
            for day in data["forecast"]
        ]

    def _handle_status(self, status: dict[str, Any], source: str) -> None:
        self.hass.async_create_task(self.async_update_listeners(("daily",)))
