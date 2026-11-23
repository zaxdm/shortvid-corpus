"""Supported platforms."""

from __future__ import annotations

from .base import Platform, PlatformError
from .kwai import Kwai
from .snapchat import Snapchat

PLATFORMS: dict[str, type[Platform]] = {'kwai': Kwai, 'snapchat': Snapchat}


def get_platform(name: str) -> Platform:
    try:
        return PLATFORMS[name.lower()]()
    except KeyError:
        raise ValueError(f'Unknown platform {name!r}. Available: {", ".join(PLATFORMS)}.') from None


__all__ = ['PLATFORMS', 'Kwai', 'Platform', 'PlatformError', 'Snapchat', 'get_platform']
