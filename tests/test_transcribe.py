from __future__ import annotations

import asyncio

import httpx
import pytest

from shortvid_corpus.transcribe import Fallback, GroqWhisper, RateLimited, Transcriber, Transcript, lang_code


def test_lang_code():
    assert lang_code('Portuguese') == 'pt'
    assert lang_code('es') == 'es'
    assert lang_code(None) is None


def test_groq_parses_verbose_json(tmp_path):
    audio = tmp_path / 'a.ogg'
    audio.write_bytes(b'x')

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers['Authorization'] == 'Bearer key'
        return httpx.Response(200, json={'text': ' Olá ', 'language': 'portuguese', 'duration': 3.0,
                                         'segments': [{'start': 0, 'end': 3, 'text': ' Olá '}]})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await GroqWhisper('key').transcribe(client, audio, None)

    result = asyncio.run(go())
    assert (result.text, result.language, result.model_id) == ('Olá', 'pt', 'groq/whisper-large-v3-turbo')
    assert result.segments[0].text == 'Olá'


def test_groq_raises_rate_limited_without_waiting(tmp_path):
    audio = tmp_path / 'a.ogg'
    audio.write_bytes(b'x')

    async def go():
        transport = httpx.MockTransport(lambda r: httpx.Response(429, headers={'retry-after': '120'}))
        async with httpx.AsyncClient(transport=transport) as client:
            await GroqWhisper('key').transcribe(client, audio, None)

    with pytest.raises(RateLimited):
        asyncio.run(go())


def test_fallback_switches_backend_on_quota(tmp_path):
    class Limited(Transcriber):
        model_id = 'a'
        calls = 0

        async def transcribe(self, client, audio, language):
            self.calls += 1
            raise RateLimited('quota')

    class Works(Transcriber):
        model_id = 'b'

        async def transcribe(self, client, audio, language):
            return Transcript('ok', None, self.model_id)

    first = Limited()
    chain = Fallback(first, Works())

    async def go():
        return [await chain.transcribe(None, tmp_path, None) for _ in range(3)]

    assert [t.model_id for t in asyncio.run(go())] == ['b', 'b', 'b']
    assert first.calls == 1  # paused after hitting its quota
