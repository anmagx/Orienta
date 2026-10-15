"""Shared contracts and metadata for native pose filters."""

from dataclasses import dataclass
import math
from typing import Callable, Protocol, Sequence


class PoseFilter(Protocol):
    """Stateful filter that smooths one yaw/pitch/roll pose sample."""

    def reset(self) -> None:
        """Clear temporal history."""

    def process(
        self,
        angles: Sequence[float],
        timestamp: float,
    ) -> tuple[float, float, float]:
        """Filter yaw, pitch, and roll in degrees."""


@dataclass(frozen=True)
class FilterParameter:
    """Numeric setting metadata used by validation and the filter UI."""

    key: str
    label: str
    tooltip: str
    minimum: float
    maximum: float
    step: float
    default: float
    decimals: int
    unit: str = ""


@dataclass(frozen=True)
class FilterDefinition:
    """Registration record for one selectable native pose filter."""

    identifier: str
    label: str
    parameters: tuple[FilterParameter, ...]
    factory: Callable[[dict[str, float]], PoseFilter]
    description: str = ""

    def default_settings(self) -> dict[str, float]:
        return {parameter.key: parameter.default for parameter in self.parameters}

    def normalize_settings(self, settings) -> dict[str, float]:
        if not isinstance(settings, dict):
            raise ValueError(f"{self.label} settings must be a mapping")

        normalized = {}
        for parameter in self.parameters:
            raw_value = settings.get(parameter.key, parameter.default)
            try:
                value = float(raw_value)
            except (TypeError, ValueError, OverflowError) as error:
                raise ValueError(
                    f"{parameter.key} must be a finite number"
                ) from error
            if not math.isfinite(value):
                raise ValueError(f"{parameter.key} must be a finite number")
            if not parameter.minimum <= value <= parameter.maximum:
                raise ValueError(
                    f"{parameter.key} must be between "
                    f"{parameter.minimum} and {parameter.maximum}"
                )
            normalized[parameter.key] = round(value, parameter.decimals)
        return normalized

    def create(self, settings) -> PoseFilter:
        return self.factory(self.normalize_settings(settings))
