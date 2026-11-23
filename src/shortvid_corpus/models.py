"""Common record format shared by every platform."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# Order of the columns in the CSV export.
CSV_COLUMNS = [
    'platform', 'video_id', 'url', 'source', 'created_at', 'duration_s',
    'views', 'likes', 'comments', 'shares', 'caption', 'hashtags',
    'author_id', 'author_username', 'author_name', 'author_followers',
    'transcript', 'transcript_language', 'transcript_source', 'transcript_status',
    'collected_at',
]


@dataclass
class Source:
    """What to collect: a profile, a keyword search, a hashtag or a single URL."""

    kind: str  # 'profile', 'search', 'hashtag' or 'url'
    value: str

    def __str__(self) -> str:
        return f'{self.kind}:{self.value}'


@dataclass
class Author:
    id: str | None = None
    username: str | None = None
    name: str | None = None
    followers: int | None = None
    profile_url: str | None = None


@dataclass
class Segment:
    start: float
    end: float
    text: str


@dataclass
class Video:
    platform: str
    video_id: str
    url: str | None
    source: str
    caption: str = ''
    hashtags: list[str] = field(default_factory=list)
    created_at: str | None = None
    duration_s: float | None = None
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    author: Author = field(default_factory=Author)
    transcript: str | None = None
    transcript_language: str | None = None
    transcript_source: str | None = None  # 'native', or the name of the speech-to-text model
    transcript_status: str = 'not_requested'
    segments: list[Segment] = field(default_factory=list)
    collected_at: str | None = None
    # Not exported: where to fetch the media and the platform's own subtitles.
    media_urls: list[str] = field(default_factory=list, repr=False)
    media_headers: dict[str, str] = field(default_factory=dict, repr=False)
    native_transcript_url: str | None = field(default=None, repr=False)
    speech_probability: float | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        """Full record for the JSON Lines export."""
        data = asdict(self)
        for private in ('media_urls', 'media_headers', 'native_transcript_url'):
            data.pop(private)
        return data

    def to_row(self) -> dict[str, Any]:
        """Flat record for the CSV export."""
        row = {k: v for k, v in self.to_dict().items() if k in CSV_COLUMNS}
        row['hashtags'] = ' '.join(self.hashtags)
        row['author_id'] = self.author.id
        row['author_username'] = self.author.username
        row['author_name'] = self.author.name
        row['author_followers'] = self.author.followers
        return {col: row.get(col) for col in CSV_COLUMNS}
