"""Speech-to-text backends.

Every backend takes an audio file and returns a `Transcript`. `model_id` is written into each
record and into the corpus manifest, so a reader of the dataset knows which model produced
each transcript.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..models import Segment

# Whisper returns the language as an English name and Deepgram as a code; both become ISO codes.
_LANG_CODES = {
    'portuguese': 'pt', 'spanish': 'es', 'english': 'en', 'indonesian': 'id', 'arabic': 'ar',
    'french': 'fr', 'italian': 'it', 'german': 'de', 'hindi': 'hi', 'turkish': 'tr', 'russian': 'ru',
    'japanese': 'ja', 'korean': 'ko', 'chinese': 'zh', 'vietnamese': 'vi', 'thai': 'th', 'bengali': 'bn',
    'urdu': 'ur', 'tagalog': 'tl', 'malay': 'ms', 'dutch': 'nl', 'polish': 'pl',
}


def lang_code(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return _LANG_CODES.get(value.lower(), value.lower())


class TranscriptionError(RuntimeError):
    """The backend could not transcribe the audio."""


class RateLimited(TranscriptionError):
    """The backend hit its quota; a fallback backend may take over."""


@dataclass
class Transcript:
    text: str
    language: str | None
    model_id: str
    segments: list[Segment] = field(default_factory=list)


class Transcriber(ABC):
    model_id: str

    @abstractmethod
    async def transcribe(self, client: httpx.AsyncClient, audio: Path, language: str | None) -> Transcript:
        """Transcribes the audio. `language` is an ISO code, or None to detect it."""


class GroqWhisper(Transcriber):
    """Whisper hosted by Groq (needs an API key, has a free tier)."""

    URL = 'https://api.groq.com/openai/v1/audio/transcriptions'

    def __init__(self, api_key: str, model: str = 'whisper-large-v3-turbo', wait_on_limit: bool = True) -> None:
        self.api_key = api_key
        self.model = model
        self.model_id = f'groq/{model}'
        self.wait_on_limit = wait_on_limit

    async def transcribe(self, client: httpx.AsyncClient, audio: Path, language: str | None) -> Transcript:
        data = {'model': self.model, 'response_format': 'verbose_json', 'temperature': '0'}
        if language:
            data['language'] = language
        for attempt in range(4):
            with audio.open('rb') as fh:
                resp = await client.post(
                    self.URL, headers={'Authorization': f'Bearer {self.api_key}'}, data=data,
                    files={'file': (audio.name, fh, 'audio/ogg')}, timeout=180,
                )
            if resp.status_code == 429:
                wait = float(resp.headers.get('retry-after') or 0) or 5 * (attempt + 1)
                if not self.wait_on_limit or wait > 60:
                    raise RateLimited(f'Groq quota reached (retry in {int(wait)} s).')
                await asyncio.sleep(wait)
                continue
            if resp.status_code >= 500:
                await asyncio.sleep(5 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                raise TranscriptionError(f'Groq answered HTTP {resp.status_code}.')
            result = resp.json()
            return Transcript(
                text=(result.get('text') or '').strip(),
                language=lang_code(result.get('language')),
                model_id=self.model_id,
                segments=[
                    Segment(s.get('start'), s.get('end'), (s.get('text') or '').strip())
                    for s in result.get('segments') or []
                ],
            )
        raise RateLimited('Groq stayed busy after several attempts.')


class Deepgram(Transcriber):
    """Deepgram Nova (needs an API key)."""

    URL = 'https://api.deepgram.com/v1/listen'

    def __init__(self, api_key: str, model: str = 'nova-3') -> None:
        self.api_key = api_key
        self.model = model
        self.model_id = f'deepgram/{model}'

    async def transcribe(self, client: httpx.AsyncClient, audio: Path, language: str | None) -> Transcript:
        params = {'model': self.model, 'smart_format': 'true', 'utterances': 'true'}
        if language:
            params['language'] = language
        else:
            params['detect_language'] = 'true'
        for attempt in range(3):
            resp = await client.post(
                self.URL, params=params, content=audio.read_bytes(), timeout=180,
                headers={'Authorization': f'Token {self.api_key}', 'Content-Type': 'audio/ogg'},
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                await asyncio.sleep(5 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                raise TranscriptionError(f'Deepgram answered HTTP {resp.status_code}.')
            result = resp.json()
            channel = ((result.get('results') or {}).get('channels') or [{}])[0]
            alt = (channel.get('alternatives') or [{}])[0]
            return Transcript(
                text=(alt.get('transcript') or '').strip(),
                language=lang_code(channel.get('detected_language') or language),
                model_id=self.model_id,
                segments=[
                    Segment(u.get('start'), u.get('end'), (u.get('transcript') or '').strip())
                    for u in (result.get('results') or {}).get('utterances') or []
                ],
            )
        raise RateLimited('Deepgram stayed busy after several attempts.')


class LocalWhisper(Transcriber):
    """Whisper running on this computer with faster-whisper (no API key, no data leaves the machine).

    Needs the `local` extra (faster-whisper); see the README. Useful when ethics approval does not allow
    sending audio to third parties, and for exact reproducibility (fixed model weights).
    """

    def __init__(self, model: str = 'small', device: str = 'auto', compute_type: str = 'default') -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise TranscriptionError('Local transcription needs faster-whisper: pip install faster-whisper') from exc
        self.model_id = f'faster-whisper/{model}'
        self._model = WhisperModel(model, device=device, compute_type=compute_type)
        self._lock = asyncio.Lock()  # One audio at a time: the model already uses every core.

    async def transcribe(self, client: httpx.AsyncClient, audio: Path, language: str | None) -> Transcript:
        async with self._lock:
            return await asyncio.to_thread(self._run, audio, language)

    def _run(self, audio: Path, language: str | None) -> Transcript:
        segments, info = self._model.transcribe(str(audio), language=language, temperature=0)
        segs = [Segment(round(s.start, 3), round(s.end, 3), s.text.strip()) for s in segments]
        return Transcript(' '.join(s.text for s in segs).strip(), lang_code(info.language), self.model_id, segs)


class Fallback(Transcriber):
    """Uses the first backend until it hits its quota, then the next one for a while."""

    def __init__(self, *backends: Transcriber, pause_s: float = 600) -> None:
        if not backends:
            raise ValueError('Fallback needs at least one backend.')
        self.backends = backends
        self.pause_s = pause_s
        self._paused_until = [0.0] * len(backends)
        self.model_id = ' > '.join(b.model_id for b in backends)

    async def transcribe(self, client: httpx.AsyncClient, audio: Path, language: str | None) -> Transcript:
        last: Exception | None = None
        for i, backend in enumerate(self.backends):
            if time.monotonic() < self._paused_until[i]:
                continue
            try:
                return await backend.transcribe(client, audio, language)
            except RateLimited as exc:
                self._paused_until[i] = time.monotonic() + self.pause_s
                last = exc
        raise last or RateLimited('Every backend is paused by its quota.')


__all__ = [
    'Deepgram', 'Fallback', 'GroqWhisper', 'LocalWhisper', 'RateLimited',
    'Transcriber', 'Transcript', 'TranscriptionError', 'lang_code',
]
