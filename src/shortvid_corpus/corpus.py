"""Builds a corpus: collects videos, transcribes them and writes the dataset with its provenance.

Output folder:
    videos.jsonl   one full record per video (with transcript segments)
    videos.csv     the same records, flat, for spreadsheets and R/pandas
    manifest.json  how the corpus was made: tool version, sources, options, models, counts per run

Running again on the same folder only adds videos that are not there yet, and appends a new
entry to the manifest. That makes it possible to grow a corpus over weeks and still report
exactly what was collected when.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import logging
import tempfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from . import __version__
from .models import CSV_COLUMNS, Author, Source, Video
from .platforms import Platform
from .transcribe import Transcriber, TranscriptionError
from .transcribe.audio import AudioError, download, extract_audio

log = logging.getLogger('shortvid_corpus')

# Kwai reports a speech probability per video; below this there is usually only music.
NO_SPEECH_THRESHOLD = 0.3


@dataclass
class Options:
    limit_per_source: int = 50
    transcribe: bool = True
    prefer_native: bool = True  # Use the platform's own subtitles when it publishes them.
    skip_no_speech: bool = True
    max_minutes: float = 10
    language: str | None = None
    include_segments: bool = True
    pseudonymize: bool = False
    salt: str = ''
    concurrency: int = 3

    def public(self) -> dict[str, Any]:
        """Options as written to the manifest (the salt is secret)."""
        data = dict(self.__dict__)
        data.pop('salt')
        return data


@dataclass
class RunResult:
    added: int = 0
    skipped_existing: int = 0
    statuses: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)


def pseudonymize(video: Video, salt: str) -> None:
    """Replaces the author's identity with a stable pseudonym and removes the URLs that reveal it.

    The same account gets the same pseudonym in every run that uses the same salt, so
    per-author analyses still work. Keep the salt private; without it the pseudonyms
    cannot be linked back to the accounts.
    """
    key = video.author.id or video.author.username or ''
    pseudonym = 'a_' + hashlib.sha256(f'{salt}|{video.platform}|{key}'.encode()).hexdigest()[:12] if key else None
    video.author = Author(id=pseudonym, username=pseudonym, followers=video.author.followers)
    video.url = None


async def _transcribe(
    client: httpx.AsyncClient, platform: Platform, transcriber: Transcriber | None, video: Video, opts: Options,
) -> None:
    native = await platform.native_transcript(client, video) if opts.prefer_native else None
    if native:
        video.transcript = ' '.join(s.text for s in native).strip()
        video.transcript_source = 'native'
        video.transcript_status = 'ok'
        video.segments = native if opts.include_segments else []
        return
    if transcriber is None:
        # Empty platform subtitles are not proof of silence: Snapchat also returns them for
        # videos with speech. Without a transcriber the record says where the answer came from.
        video.transcript_status = 'no_speech' if native == [] else 'no_transcriber'
        video.transcript_source = 'native' if native == [] else None
        return
    if opts.skip_no_speech and video.speech_probability is not None and video.speech_probability < NO_SPEECH_THRESHOLD:
        video.transcript_status = 'no_speech'
        return
    if (video.duration_s or 0) > opts.max_minutes * 60:
        video.transcript_status = 'too_long'
        return
    try:
        with tempfile.TemporaryDirectory(prefix='shortvid-') as tmp:
            media, audio = Path(tmp) / 'video', Path(tmp) / 'audio.ogg'
            await download(client, video.media_urls, media, video.media_headers)
            await extract_audio(media, audio)
            media.unlink()
            result = await transcriber.transcribe(client, audio, opts.language)
    except (AudioError, TranscriptionError, httpx.HTTPError) as exc:
        video.transcript_status = 'error'
        log.warning('%s %s: %s', video.platform, video.video_id, exc)
        return
    video.transcript = result.text
    video.transcript_language = result.language
    video.transcript_source = result.model_id
    video.transcript_status = 'ok' if result.text else 'no_speech'
    video.segments = result.segments if opts.include_segments else []


def _existing_ids(path: Path) -> set[tuple[str, str]]:
    ids: set[tuple[str, str]] = set()
    if path.exists():
        with path.open(encoding='utf-8') as fh:
            for line in fh:
                if line.strip():
                    rec = json.loads(line)
                    ids.add((rec['platform'], rec['video_id']))
    return ids


def _read_manifest(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return {'tool': 'shortvid-corpus', 'runs': []}


async def build_corpus(
    platform: Platform,
    sources: Iterable[Source],
    out_dir: str | Path,
    transcriber: Transcriber | None = None,
    options: Options | None = None,
    client: httpx.AsyncClient | None = None,
) -> RunResult:
    """Collects the sources into `out_dir`, adding to what is already there."""
    opts = options or Options()
    sources = list(sources)
    for source in sources:
        platform.check(source)
    if opts.pseudonymize and not opts.salt:
        raise ValueError('Pseudonymization needs a salt (keep it private and reuse it across runs).')
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jsonl, csv_path, manifest_path = out / 'videos.jsonl', out / 'videos.csv', out / 'manifest.json'
    existing = _existing_ids(jsonl)
    result = RunResult()
    started = datetime.now(UTC).isoformat()

    own_client = client is None
    client = client or httpx.AsyncClient(headers=platform.headers, timeout=40)
    semaphore = asyncio.Semaphore(max(1, opts.concurrency))
    new_csv = not csv_path.exists()
    try:
        with jsonl.open('a', encoding='utf-8') as jf, csv_path.open('a', encoding='utf-8', newline='') as cf:
            writer = csv.DictWriter(cf, fieldnames=CSV_COLUMNS)
            if new_csv:
                writer.writeheader()

            async def process(video: Video) -> None:
                if opts.transcribe:
                    async with semaphore:
                        await _transcribe(client, platform, transcriber, video, opts)
                video.collected_at = datetime.now(UTC).isoformat()
                if opts.pseudonymize:
                    pseudonymize(video, opts.salt)
                # Written as soon as each video is done, so an interrupted run keeps its work.
                jf.write(json.dumps(video.to_dict(), ensure_ascii=False) + '\n')
                jf.flush()
                writer.writerow(video.to_row())
                cf.flush()
                result.added += 1
                result.statuses[video.transcript_status] += 1

            for source in sources:
                tasks: list[asyncio.Task[None]] = []
                try:
                    async for video in platform.collect(client, source, opts.limit_per_source):
                        if (video.platform, video.video_id) in existing:
                            result.skipped_existing += 1
                            continue
                        existing.add((video.platform, video.video_id))
                        tasks.append(asyncio.create_task(process(video)))
                except Exception as exc:  # noqa: BLE001 - one failing source should not stop the others.
                    result.errors.append(f'{source}: {exc}')
                    log.error('%s failed: %s', source, exc)
                if tasks:
                    await asyncio.gather(*tasks)
                log.info('%s: %d new videos', source, len(tasks))
    finally:
        if own_client:
            await client.aclose()

    manifest = _read_manifest(manifest_path)
    manifest['tool_version'] = __version__
    manifest['runs'].append({
        'started_at': started,
        'finished_at': datetime.now(UTC).isoformat(),
        'tool_version': __version__,
        'platform': platform.name,
        'sources': [str(s) for s in sources],
        'options': opts.public(),
        'transcriber': transcriber.model_id if transcriber else None,
        'added': result.added,
        'skipped_existing': result.skipped_existing,
        'transcript_status': dict(result.statuses),
        'errors': result.errors,
    })
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return result
