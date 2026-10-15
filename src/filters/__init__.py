"""Registry of selectable native-output pose filters."""

from src.config.config import DEFAULT_FILTER_ENABLED

from .accela_style import (
    ACCELA_STYLE_FILTER,
    ACCELA_STYLE_FILTER_ID,
    AccelaStylePoseFilter,
)
from .base import FilterDefinition, FilterParameter, PoseFilter
from .critically_damped import (
    CRITICALLY_DAMPED_FILTER,
    CRITICALLY_DAMPED_FILTER_ID,
    CriticallyDampedPoseFilter,
)
from .ema import EMA_FILTER, EMA_FILTER_ID, EmaPoseFilter
from .gyro_adaptive import (
    GYRO_ADAPTIVE_FILTER,
    GYRO_ADAPTIVE_FILTER_ID,
    GyroAdaptivePoseFilter,
)
from .hysteresis import (
    HYSTERESIS_FILTER,
    HYSTERESIS_FILTER_ID,
    HysteresisPoseFilter,
)
from .one_euro import (
    ONE_EURO_FILTER,
    ONE_EURO_FILTER_ID,
    OneEuroPoseFilter,
)


FILTER_REGISTRY = {
    ONE_EURO_FILTER.identifier: ONE_EURO_FILTER,
    GYRO_ADAPTIVE_FILTER.identifier: GYRO_ADAPTIVE_FILTER,
    ACCELA_STYLE_FILTER.identifier: ACCELA_STYLE_FILTER,
    CRITICALLY_DAMPED_FILTER.identifier: CRITICALLY_DAMPED_FILTER,
    EMA_FILTER.identifier: EMA_FILTER,
    HYSTERESIS_FILTER.identifier: HYSTERESIS_FILTER,
}
DEFAULT_FILTER_ID = GYRO_ADAPTIVE_FILTER_ID


def available_filters() -> tuple[FilterDefinition, ...]:
    """Return filter definitions in UI selection order."""
    return tuple(FILTER_REGISTRY.values())


def get_filter_definition(identifier: str) -> FilterDefinition:
    """Return a registered filter definition or raise a clear error."""
    try:
        return FILTER_REGISTRY[identifier]
    except KeyError as error:
        raise ValueError(f"Unsupported native output filter: {identifier}") from error


def default_filter_settings() -> dict:
    """Return default ID, enable state, and settings for the configured default."""
    definition = get_filter_definition(DEFAULT_FILTER_ID)
    return {
        "filter_type": definition.identifier,
        "filter_enabled": DEFAULT_FILTER_ENABLED,
        "filter_settings": {
            definition.identifier: definition.default_settings(),
        },
    }


__all__ = [
    "FILTER_REGISTRY",
    "DEFAULT_FILTER_ID",
    "FilterDefinition",
    "FilterParameter",
    "PoseFilter",
    "EMA_FILTER_ID",
    "EmaPoseFilter",
    "GYRO_ADAPTIVE_FILTER_ID",
    "GyroAdaptivePoseFilter",
    "ACCELA_STYLE_FILTER_ID",
    "AccelaStylePoseFilter",
    "CRITICALLY_DAMPED_FILTER_ID",
    "CriticallyDampedPoseFilter",
    "HYSTERESIS_FILTER_ID",
    "HysteresisPoseFilter",
    "ONE_EURO_FILTER_ID",
    "OneEuroPoseFilter",
    "available_filters",
    "default_filter_settings",
    "get_filter_definition",
]
