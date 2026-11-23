"""Kwai: public profiles, keyword search, hashtags and single videos (kwai.com, no login)."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, ClassVar
from urllib.parse import quote, urlparse

import httpx

from ..models import Author, Source, Video
from ._nuxt import parse_nuxt
from .base import USER_AGENT, Platform, fetch_text, post_json

BASE = 'https://www.kwai.com'
_API_HEADERS = {'Content-Type': 'application/json', 'Origin': BASE, 'Referer': BASE + '/'}
_VIDEO_PATH = re.compile(r'^/@([^/]+)/video/(\d+)')
_PROFILE_PATH = re.compile(r'^/@([^/?#]+)/?$')
_HASHTAG = re.compile(r'#([^\s#]+)')


def classify(url: str) -> tuple[str, str | None, str | None]:
    """Returns (kind, kwai_id, video_id); kind is 'video', 'profile' or 'unknown'."""
    parsed = urlparse(url)
    if (parsed.hostname or '').lower().endswith('kwai.com'):
        if m := _VIDEO_PATH.match(parsed.path):
            return 'video', m.group(1), m.group(2)
        if m := _PROFILE_PATH.match(parsed.path):
            return 'profile', m.group(1), None
    return 'unknown', None, None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def media_candidates(photo: dict[str, Any]) -> list[str]:
    """Video URLs from the lightest to the heaviest file, which is enough for speech-to-text."""
    reps: list[dict[str, Any]] = []
    for adaptation in (photo.get('transcode_manifest_info') or {}).get('adaptationSet') or []:
        if isinstance(adaptation, dict):
            reps.extend(r for r in adaptation.get('representation') or [] if isinstance(r, dict))
    reps.sort(key=lambda r: _int(r.get('fileSize')) or 1 << 62)
    urls: list[str] = []
    for rep in reps:
        if isinstance(rep.get('url'), str):
            urls.append(rep['url'])
        urls.extend(u for u in rep.get('backupUrl') or [] if isinstance(u, str))
    urls.extend(
        item['url'] for item in photo.get('main_mv_urls') or []
        if isinstance(item, dict) and isinstance(item.get('url'), str)
    )
    return list(dict.fromkeys(urls))


def duration_seconds(photo: dict[str, Any]) -> float | None:
    ms = _float((photo.get('ext_params') or {}).get('video'))
    if ms is None:
        for adaptation in (photo.get('transcode_manifest_info') or {}).get('adaptationSet') or []:
            if isinstance(adaptation, dict) and (ms := _float(adaptation.get('duration'))):
                break
    return round(ms / 1000, 2) if ms else None


def to_video(photo: dict[str, Any], source: str, followers: int | None = None) -> Video:
    """Converts a Kwai `photo` object into the common record."""
    video_id = str(photo.get('photo_id_str') or photo.get('photo_id') or '')
    kwai_id = photo.get('kwai_id')
    caption = photo.get('caption') or ''
    ts = _int(photo.get('timestamp'))
    audio = (photo.get('transcode_manifest_info') or {}).get('audioFeature') or {}
    return Video(
        platform='kwai',
        video_id=video_id,
        url=f'{BASE}/@{kwai_id}/video/{video_id}' if kwai_id and video_id else None,
        source=source,
        caption=caption,
        hashtags=[h.rstrip('.,;:!?') for h in _HASHTAG.findall(caption)],
        created_at=datetime.fromtimestamp(ts / 1000, tz=UTC).isoformat() if ts else None,
        duration_s=duration_seconds(photo),
        views=_int(photo.get('view_count')),
        likes=_int(photo.get('like_count')),
        comments=_int(photo.get('comment_count')),
        shares=_int(photo.get('forward_count')),
        author=Author(
            id=photo.get('user_id_str') or (str(photo['user_id']) if photo.get('user_id') else None),
            username=kwai_id,
            name=photo.get('user_name'),
            followers=followers,
            profile_url=f'{BASE}/@{kwai_id}' if kwai_id else None,
        ),
        media_urls=media_candidates(photo),
        media_headers={'Referer': BASE + '/'},
        speech_probability=_float(audio.get('dialogProbability')),
    )


def photos_from_video_page(html: str, video_id: str | None) -> list[dict[str, Any]]:
    """On a video page, returns that video only (not the recommendations around it)."""
    state = parse_nuxt(html) or {}
    photos: list[dict[str, Any]] = []
    for block in state.get('data') or []:
        if isinstance(block, dict):
            photos.extend(p for p in block.get('photoList') or [] if isinstance(p, dict))
    photos.extend(p for p in (state.get('state') or {}).get('videoList') or [] if isinstance(p, dict))
    if video_id:
        return [p for p in photos if str(p.get('photo_id_str')) == video_id][:1]
    return photos[:1]


def photos_from_profile_page(html: str) -> tuple[list[dict[str, Any]], int | None, str | None]:
    """On a profile page, returns its videos, the follower count and the cursor of the next page."""
    state = parse_nuxt(html) or {}
    for block in state.get('data') or []:
        if not isinstance(block, dict) or 'feedsData' not in block:
            continue
        feeds_data = block.get('feedsData') or {}
        feeds = [p for p in feeds_data.get('feeds') or [] if isinstance(p, dict)]
        counts = (block.get('userProfile') or {}).get('ownerCount') or {}
        return feeds, _int(counts.get('fan')), _cursor(feeds_data.get('cursor'))
    return [], None, None


def _cursor(value: Any) -> str | None:
    return None if value in (None, '', 'no_more') else str(value)


def _feeds(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get('result') != 1:
        return []
    return [f for f in payload.get('feeds') or [] if isinstance(f, dict)]


class Kwai(Platform):
    name = 'kwai'
    source_kinds = ('profile', 'search', 'hashtag', 'url')
    headers: ClassVar[dict[str, str]] = {
        'User-Agent': USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'es-419,es;q=0.9,pt-BR;q=0.8,en;q=0.7',
    }

    async def collect(self, client: httpx.AsyncClient, source: Source, limit: int) -> AsyncIterator[Video]:
        self.check(source)
        if source.kind == 'url':
            kind, kwai_id, video_id = classify(source.value)
            if kind == 'video':
                html = await fetch_text(client, source.value)
                for photo in photos_from_video_page(html, video_id)[:limit]:
                    yield to_video(photo, str(source))
                return
            if kind != 'profile' or not kwai_id:
                raise ValueError(f'Not a Kwai video or profile URL: {source.value}')
            source = Source('profile', kwai_id)
        pages = {'profile': self._profile, 'search': self._search, 'hashtag': self._hashtag}[source.kind]
        count = 0
        seen: set[str] = set()
        async for photo, followers in pages(client, source.value.strip().lstrip('@#')):
            video = to_video(photo, str(source), followers)
            if not video.video_id or video.video_id in seen:
                continue
            seen.add(video.video_id)
            yield video
            count += 1
            if count >= limit:
                return

    async def _profile(self, client: httpx.AsyncClient, kwai_id: str) -> AsyncIterator[tuple[dict[str, Any], int | None]]:
        html = await fetch_text(client, f'{BASE}/@{quote(kwai_id)}')
        photos, followers, cursor = photos_from_profile_page(html)
        for photo in photos:
            yield photo, followers
        while cursor:
            data = await post_json(
                client, f'{BASE}/rest/o/w/pc/feed/profile',
                headers={**_API_HEADERS, 'Referer': f'{BASE}/@{kwai_id}'},
                json={'userId': kwai_id, 'fromPage': 'PC_PROFILE', 'beforePage': '', 'pcursor': cursor, 'count': 20},
            )
            photos = _feeds(data)
            for photo in photos:
                yield photo, followers
            cursor = _cursor(data.get('cursor')) if photos else None

    async def _search(self, client: httpx.AsyncClient, keyword: str) -> AsyncIterator[tuple[dict[str, Any], int | None]]:
        word = quote(re.sub(r'\s+', '-', keyword))
        cursor: int | None = 0
        while cursor is not None:
            data = await post_json(
                client, f'{BASE}/rest/o/w/pc/feed/search', headers=_API_HEADERS,
                json={'ai_content': False, 'need_ld_json': False, 'need_tdk': False, 'searchWord': word,
                      'pcursor': cursor, 'count': 20, 'mobile': True, 'request_source': 1102, 'fromUser': False},
            )
            photos = _feeds(data)
            for photo in photos:
                yield photo, None
            cursor = _int(_cursor(data.get('cursor'))) if photos else None

    async def _hashtag(self, client: httpx.AsyncClient, tag: str) -> AsyncIterator[tuple[dict[str, Any], int | None]]:
        topic = re.sub(r'\s+', '-', tag)
        cursor: int | None = 0
        while cursor is not None:
            data = await post_json(
                client, f'{BASE}/rest/o/w/pic/topic', headers=_API_HEADERS,
                json={'topic': topic, 'pcursor': cursor, 'count': 25},
            )
            photos = _feeds(data)
            for photo in photos:
                yield photo, None
            cursor = _int(_cursor(data.get('pcursor'))) if photos else None
