"""Snapchat Spotlight: public profiles and single Spotlight videos (snapchat.com, no login).

Profile pages carry their data in `__NEXT_DATA__` (Next.js pages router). A single Spotlight
page uses the app router: the video comes in the RSC stream (`self.__next_f`) and in `ld+json`.
Everything is read as JSON; the page's JavaScript is never executed.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, ClassVar
from urllib.parse import urlparse

import httpx

from ..models import Author, Segment, Source, Video
from .base import USER_AGENT, Platform, PlatformError, fetch_text

BASE = 'https://www.snapchat.com'
# Placeholder text Snapchat shows when a Spotlight has no description.
_GENERIC_DESCRIPTIONS = {'Another Spotlight Snap brought to you by Snapchat', 'Spotlight Snap'}
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL)
_LD_JSON = re.compile(r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL)
_FLIGHT = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')
_USERNAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{1,30}$')
_CUE_TIME = re.compile(r'(\d+):(\d{2}):(\d{2})\.(\d{3})|(\d{2}):(\d{2})\.(\d{3})')


def classify(url: str) -> tuple[str, str | None, str | None]:
    """Returns (kind, username, spotlight_id); kind is 'spotlight', 'profile' or 'unknown'."""
    parsed = urlparse(url)
    if not (parsed.hostname or '').lower().endswith('snapchat.com'):
        return 'unknown', None, None
    parts = [p for p in parsed.path.split('/') if p]
    if 'spotlight' in parts:
        i = parts.index('spotlight')
        if i + 1 < len(parts):
            user = parts[0][1:] if parts[0].startswith('@') else None
            return 'spotlight', user, parts[i + 1]
    if parts and parts[0].startswith('@') and len(parts[0]) > 1:
        return 'profile', parts[0][1:], None
    if len(parts) >= 2 and parts[0] == 'add':
        return 'profile', parts[1], None
    return 'unknown', None, None


def clean_username(value: str) -> str | None:
    name = value.strip().lstrip('@')
    return name if _USERNAME.match(name) else None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _iso_from_ms(value: Any) -> str | None:
    ms = _int(value)
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat() if ms else None


def _value(obj: Any) -> Any:
    """Snapchat wraps many fields in {"value": ...}."""
    return obj.get('value') if isinstance(obj, dict) else obj


def _caption(*candidates: Any) -> str:
    for text in candidates:
        if isinstance(text, str) and text.strip() and text.strip() not in _GENERIC_DESCRIPTIONS:
            return text.strip()
    return ''


def _hashtags(caption: str, listed: Any) -> list[str]:
    tags = [str(t).lstrip('#') for t in listed or [] if isinstance(t, str) and t.strip('#').strip()]
    tags += re.findall(r'#(\w+)', caption)
    return list(dict.fromkeys(tags))


def _next_data(html: str) -> dict[str, Any]:
    m = _NEXT_DATA.search(html)
    if not m:
        raise PlatformError('The page has no __NEXT_DATA__ block.')
    try:
        return json.loads(m.group(1))['props']['pageProps']
    except (ValueError, KeyError) as exc:
        raise PlatformError('Unexpected __NEXT_DATA__ format.') from exc


def videos_from_profile_page(html: str, source: str) -> list[Video]:
    """Recent Spotlight videos of a public profile."""
    pp = _next_data(html)
    info = (pp.get('userProfile') or {}).get('publicProfileInfo')
    if not isinstance(info, dict):
        return []
    username = info.get('username') or ''
    author = Author(
        username=username or None,
        name=info.get('title') or None,
        followers=_int(info.get('subscriberCount')),
        profile_url=f'{BASE}/@{username}' if username else None,
    )
    videos: list[Video] = []
    for story, meta in zip(pp.get('spotlightHighlights') or [], pp.get('spotlightStoryMetadata') or []):
        snaps = story.get('snapList') or []
        video = (meta or {}).get('videoMetadata') or {}
        snap_id = _value(story.get('storyId'))
        if not snaps or not snap_id or not video.get('contentUrl'):
            continue
        stats = meta.get('engagementStats') or {}
        caption = _caption(meta.get('description'), video.get('description'))
        videos.append(Video(
            platform='snapchat',
            video_id=snap_id,
            url=f'{BASE}/@{username}/spotlight/{snap_id}',
            source=source,
            caption=caption,
            hashtags=_hashtags(caption, meta.get('hashtags')),
            created_at=_iso_from_ms(video.get('uploadDateMs')),
            duration_s=(_int(video.get('durationMs')) or 0) / 1000 or None,
            views=_int(stats.get('viewCount') or video.get('viewCount')),
            likes=_int(stats.get('boostCount')),
            comments=_int(stats.get('commentCount')),
            shares=_int(stats.get('shareCount')),
            author=author,
            media_urls=[video['contentUrl']],
            media_headers={'Referer': BASE + '/'},
            native_transcript_url=_value(snaps[0].get('audioTranscriptionObjectUrl')),
        ))
    return videos


def _flight(html: str) -> str:
    return ''.join(json.loads('"' + m.group(1) + '"') for m in _FLIGHT.finditer(html))


def _video_object(html: str) -> dict[str, Any]:
    for m in _LD_JSON.finditer(html):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(data, dict) and data.get('@type') == 'VideoObject':
            return data
    return {}


def _iso_duration(value: Any) -> float | None:
    m = re.fullmatch(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?', str(value or ''))
    if not m or not any(m.groups()):
        return None
    h, mnt, s = (float(g) if g else 0.0 for g in m.groups())
    return h * 3600 + mnt * 60 + s


def videos_from_spotlight_page(html: str, video_id: str, source: str) -> list[Video]:
    """The Spotlight on the page, or [] if it is no longer available."""
    flight = _flight(html)
    start = flight.find(f'"video":{{"id":"{video_id}"')
    if start < 0:
        return []
    try:
        video, _ = json.JSONDecoder().raw_decode(flight, start + len('"video":'))
    except ValueError as exc:
        raise PlatformError('Could not read the Spotlight video.') from exc
    ld = _video_object(html)
    creator = ld.get('creator') if isinstance(ld.get('creator'), dict) else {}
    username = video.get('postingUsername') or creator.get('alternateName') or ''
    caption = _caption(video.get('caption'), ld.get('name'))
    media = video.get('videoUrl') or ld.get('contentUrl')
    return [Video(
        platform='snapchat',
        video_id=video_id,
        url=f'{BASE}/@{username}/spotlight/{video_id}' if username else f'{BASE}/spotlight/{video_id}',
        source=source,
        caption=caption,
        hashtags=_hashtags(caption, None),
        created_at=_iso_from_ms(video.get('uploadTimestampMs')) or ld.get('uploadDate'),
        duration_s=_iso_duration(ld.get('duration')),
        views=_int(video.get('viewCount')),
        likes=_int(video.get('likeCount')),
        comments=_int(video.get('commentCount')),
        shares=_int(video.get('shareCount')),
        author=Author(
            username=username or None,
            name=video.get('creatorName') or creator.get('name'),
            profile_url=f'{BASE}/@{username}' if username else None,
        ),
        media_urls=[media] if media else [],
        media_headers={'Referer': BASE + '/'},
        native_transcript_url=video.get('transcriptUrl'),
    )]


def _seconds(stamp: str) -> float:
    m = _CUE_TIME.fullmatch(stamp.strip())
    if not m:
        return 0.0
    if m.group(1):
        h, mnt, s, ms = (int(m.group(i)) for i in (1, 2, 3, 4))
    else:
        h, (mnt, s, ms) = 0, (int(m.group(i)) for i in (5, 6, 7))
    return round(h * 3600 + mnt * 60 + s + ms / 1000, 3)


def parse_vtt(text: str) -> list[Segment]:
    """Segments of a WebVTT file."""
    segments: list[Segment] = []
    for block in re.split(r'\r?\n\r?\n', text.replace('﻿', '')):
        lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
        timing = next((i for i, ln in enumerate(lines) if '-->' in ln), None)
        if timing is None:
            continue
        start, _, end = lines[timing].partition('-->')
        body = ' '.join(re.sub(r'<[^>]+>', '', ln) for ln in lines[timing + 1:]).strip()
        if body:
            segments.append(Segment(_seconds(start), _seconds(end.split()[0] if end.split() else ''), body))
    return segments


class Snapchat(Platform):
    name = 'snapchat'
    source_kinds = ('profile', 'url')
    headers: ClassVar[dict[str, str]] = {'User-Agent': USER_AGENT, 'Accept-Language': 'en-US,en;q=0.9'}

    async def collect(self, client: httpx.AsyncClient, source: Source, limit: int) -> AsyncIterator[Video]:
        self.check(source)
        if source.kind == 'url':
            kind, username, video_id = classify(source.value)
            if kind == 'spotlight' and video_id:
                html = await fetch_text(client, source.value)
                for video in videos_from_spotlight_page(html, video_id, str(source))[:limit]:
                    yield video
                return
            if kind != 'profile' or not username:
                raise ValueError(f'Not a Snapchat profile or Spotlight URL: {source.value}')
        else:
            username = clean_username(source.value)
            if not username:
                raise ValueError(f'Not a valid Snapchat username: {source.value!r}')
        # Public profile pages list the recent Spotlight videos only; there is no paging.
        html = await fetch_text(client, f'{BASE}/@{username}')
        for video in videos_from_profile_page(html, str(source))[:limit]:
            yield video

    async def native_transcript(self, client: httpx.AsyncClient, video: Video) -> list[Segment] | None:
        if not video.native_transcript_url:
            return None
        for _ in range(2):
            try:
                resp = await client.get(video.native_transcript_url, headers={'Referer': BASE + '/'})
            except httpx.HTTPError:
                continue
            if resp.status_code == 200:
                return parse_vtt(resp.text)
            if resp.status_code in (403, 404, 410):
                return None
        return None
