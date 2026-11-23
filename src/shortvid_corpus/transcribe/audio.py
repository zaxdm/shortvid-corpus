"""Downloads a video and extracts a small mono audio track for speech-to-text."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

MAX_VIDEO_BYTES = 400 * 1024 * 1024
MAX_AUDIO_BYTES = 24 * 1024 * 1024  # Upload limit of the hosted Whisper APIs.


class AudioError(RuntimeError):
    """The video could not be downloaded or converted."""


async def download(client: httpx.AsyncClient, urls: list[str], dest: Path, headers: dict[str, str] | None = None) -> None:
    """Tries each URL in turn until one downloads."""
    last = 'no media URL'
    for url in urls:
        try:
            async with client.stream('GET', url, headers=headers or {}, follow_redirects=True) as resp:
                if resp.status_code >= 400:
                    last = f'HTTP {resp.status_code}'
                    continue
                size = 0
                with dest.open('wb') as fh:
                    async for chunk in resp.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_VIDEO_BYTES:
                            raise AudioError('The video is too large.')
                        fh.write(chunk)
                return
        except httpx.HTTPError as exc:
            last = type(exc).__name__
    raise AudioError(f'Could not download the video ({last}).')


def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


async def extract_audio(video: Path, audio: Path) -> None:
    # Mono, 16 kHz, Opus at 24 kbps: about 0.2 MB per minute, plenty for Whisper.
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_exe(),
        '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
        '-i', str(video),
        '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'libopus', '-b:a', '24k',
        str(audio),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await proc.communicate()
    if proc.returncode != 0 or not audio.exists() or audio.stat().st_size == 0:
        detail = err.decode(errors='replace').strip().splitlines()[-1:] or ['no detail']
        raise AudioError(f'Could not extract the audio: {detail[0][:200]}')
    if audio.stat().st_size > MAX_AUDIO_BYTES:
        raise AudioError('The audio is too long to transcribe in one request.')
