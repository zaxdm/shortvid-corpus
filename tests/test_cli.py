from __future__ import annotations

import pytest

from shortvid_corpus import cli
from shortvid_corpus.transcribe import Deepgram, Fallback, GroqWhisper


def test_requires_a_source(tmp_path):
    with pytest.raises(SystemExit, match='at least one'):
        cli.main(['collect', 'kwai', '--out', str(tmp_path)])


def test_rejects_source_kind_the_platform_lacks(tmp_path):
    with pytest.raises(SystemExit, match='does not support'):
        cli.main(['collect', 'snapchat', '--search', 'food', '--out', str(tmp_path), '--transcriber', 'none'])


def test_pseudonymize_needs_salt(tmp_path, monkeypatch):
    monkeypatch.delenv('SHORTVID_SALT', raising=False)
    with pytest.raises(SystemExit, match='SHORTVID_SALT'):
        cli.main(['collect', 'kwai', '--profile', 'x', '--out', str(tmp_path), '--pseudonymize'])


def test_auto_transcriber_uses_available_keys(monkeypatch):
    monkeypatch.setenv('GROQ_API_KEY', 'g')
    monkeypatch.delenv('DEEPGRAM_API_KEY', raising=False)
    assert isinstance(cli.make_transcriber('auto', 'small'), GroqWhisper)
    monkeypatch.setenv('DEEPGRAM_API_KEY', 'd')
    both = cli.make_transcriber('auto', 'small')
    assert isinstance(both, Fallback) and both.model_id == 'groq/whisper-large-v3-turbo > deepgram/nova-3'
    assert isinstance(cli.make_transcriber('deepgram', 'small'), Deepgram)
    assert cli.make_transcriber('none', 'small') is None
