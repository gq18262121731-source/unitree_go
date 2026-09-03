"""Fail-closed localization provider framework.

The package is intentionally not connected to the health_new runtime. Phase 6.5
only defines the contract and admission gates for a future validated source.
"""

from backend.localization.models import (
    LocalizationCandidate,
    LocalizationHealth,
    LocalizationPose,
    LocalizationSource,
    LocalizationState,
    LocalizationStatus,
    Quaternion,
    Vector3,
)
from backend.localization.provider import (
    LocalizationAdmissionController,
    LocalizationProvider,
    UnavailableLocalizationProvider,
)

__all__ = [
    "LocalizationAdmissionController",
    "LocalizationCandidate",
    "LocalizationHealth",
    "LocalizationPose",
    "LocalizationProvider",
    "LocalizationSource",
    "LocalizationState",
    "LocalizationStatus",
    "Quaternion",
    "UnavailableLocalizationProvider",
    "Vector3",
]
