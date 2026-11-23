from __future__ import annotations

import asyncio
import csv
import json
from pathlib import Path

import httpx
import pytest

from shortvid_corpus import Options, build_corpus
from shortvid_corpus.models import Author, Segment, Source, Video
from shortvid_corpus.platforms.base import Platform
from shortvid_corpus.transcribe import Transcriber, Transcript


class FakePlatform(Platform):
    """Returns fixed videos; video 'n1' has native subtitles and 'm1' is music only."""

    name = 'fake'
    source_kinds = ('profile',)

    def __init__(self, ids: list[str]) -> None:
        self.ids = ids

    async def collect(self, client, source, limit):
        for vid in self.ids[:limit]:
            yield Video(
                platform='fake', video_id=vid, url=f'https://fake/{source.value}/{vid}', source=str(source),
                author=Author(id='42', username=source.value, name='Real Name'),
                media_urls=['https://fake/media.mp4'],
                speech_probability=0.05 if vid.startswith('m') else 0.9,
                native_transcript_url='https://fake/sub.vtt' if vid.startswith('n') else None,
            )

    async def native_transcript(self, client, video):
        if video.video_id.startswith('e'):
            return []  # the platform published an empty subtitle file
        return [Segment(0, 1, 'native text')] if video.native_transcript_url else None


class FakeTranscriber(Transcriber):
    model_id = 'fake/whisper'

    def __init__(self) -> None:
        self.calls = 0

    async def transcribe(self, client, audio, language):
        self.calls += 1
        return Transcript('spoken words', 'es', self.model_id, [Segment(0, 2, 'spoken words')])


@pytest.fixture(autouse=True)
def no_media(monkeypatch):
    """Skips the real download and ffmpeg step."""
    async def fake_download(client, urls, dest, headers=None):
        dest.write_bytes(b'video')

    async def fake_extract(video, audio):
        audio.write_bytes(b'audio')

    monkeypatch.setattr('shortvid_corpus.corpus.download', fake_download)
    monkeypatch.setattr('shortvid_corpus.corpus.extract_audio', fake_extract)


def run(platform, out, transcriber=None, **opts):
    async def go():
        async with httpx.AsyncClient() as client:
            return await build_corpus(platform, [Source('profile', 'ana')], out, transcriber, Options(**opts), client)
    return asyncio.run(go())


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def test_build_writes_jsonl_csv_and_manifest(tmp_path):
    transcriber = FakeTranscriber()
    result = run(FakePlatform(['v1', 'n1', 'm1']), tmp_path, transcriber)
    assert result.added == 3
    records = {r['video_id']: r for r in read_jsonl(tmp_path / 'videos.jsonl')}
    assert records['v1']['transcript'] == 'spoken words' and records['v1']['transcript_source'] == 'fake/whisper'
    assert records['n1']['transcript'] == 'native text' and records['n1']['transcript_source'] == 'native'
    assert records['m1']['transcript_status'] == 'no_speech'
    assert transcriber.calls == 1  # native subtitles and music-only videos need no speech-to-text
    assert 'media_urls' not in records['v1']

    with (tmp_path / 'videos.csv').open(encoding='utf-8') as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 3 and rows[0]['author_username'] == 'ana'

    manifest = json.loads((tmp_path / 'manifest.json').read_text(encoding='utf-8'))
    run_info = manifest['runs'][0]
    assert run_info['sources'] == ['profile:ana'] and run_info['transcriber'] == 'fake/whisper'
    assert run_info['transcript_status'] == {'ok': 2, 'no_speech': 1}


def test_empty_native_subtitles_fall_back_to_speech_to_text(tmp_path):
    transcriber = FakeTranscriber()
    run(FakePlatform(['e1']), tmp_path / 'with', transcriber)
    run(FakePlatform(['e1']), tmp_path / 'without', None)
    with_stt = read_jsonl(tmp_path / 'with' / 'videos.jsonl')[0]
    without = read_jsonl(tmp_path / 'without' / 'videos.jsonl')[0]
    assert transcriber.calls == 1
    assert (with_stt['transcript_status'], with_stt['transcript_source']) == ('ok', 'fake/whisper')
    assert (without['transcript_status'], without['transcript_source']) == ('no_speech', 'native')


def test_rerun_only_adds_new_videos(tmp_path):
    run(FakePlatform(['v1', 'v2']), tmp_path, FakeTranscriber())
    result = run(FakePlatform(['v1', 'v2', 'v3']), tmp_path, FakeTranscriber())
    assert (result.added, result.skipped_existing) == (1, 2)
    assert [r['video_id'] for r in read_jsonl(tmp_path / 'videos.jsonl')] == ['v1', 'v2', 'v3']
    with (tmp_path / 'videos.csv').open(encoding='utf-8') as fh:
        assert len(list(csv.DictReader(fh))) == 3  # header written once
    assert len(json.loads((tmp_path / 'manifest.json').read_text(encoding='utf-8'))['runs']) == 2


def test_pseudonymize_is_stable_and_hides_identity(tmp_path):
    run(FakePlatform(['v1', 'v2']), tmp_path, None, transcribe=False, pseudonymize=True, salt='s3cret')
    records = read_jsonl(tmp_path / 'videos.jsonl')
    pseudonyms = {r['author']['username'] for r in records}
    assert len(pseudonyms) == 1 and next(iter(pseudonyms)).startswith('a_')
    assert all(r['url'] is None and r['author']['name'] is None for r in records)
    text = (tmp_path / 'videos.jsonl').read_text(encoding='utf-8') + (tmp_path / 'manifest.json').read_text(encoding='utf-8')
    assert 'Real Name' not in text and 's3cret' not in text


def test_pseudonymize_requires_salt(tmp_path):
    with pytest.raises(ValueError, match='salt'):
        run(FakePlatform(['v1']), tmp_path, None, pseudonymize=True)


def test_unsupported_source_kind_is_rejected(tmp_path):
    async def go():
        await build_corpus(FakePlatform([]), [Source('hashtag', 'x')], tmp_path)
    with pytest.raises(ValueError, match='does not support'):
        asyncio.run(go())


def test_failing_source_is_reported_and_others_continue(tmp_path):
    class Broken(FakePlatform):
        async def collect(self, client, source, limit):
            if source.value == 'bad':
                raise RuntimeError('boom')
            async for v in super().collect(client, source, limit):
                yield v

    async def go():
        async with httpx.AsyncClient() as client:
            return await build_corpus(Broken(['v1']), [Source('profile', 'bad'), Source('profile', 'ana')],
                                      tmp_path, None, Options(transcribe=False), client)

    result = asyncio.run(go())
    assert result.added == 1 and result.errors == ['profile:bad: boom']
