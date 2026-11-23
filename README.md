# shortvid-corpus

Build reproducible research corpora of short videos (metadata + spoken transcripts) from public pages of **Kwai** and **Snapchat Spotlight**.

```bash
pip install git+https://github.com/zaxdm/shortvid-corpus
shortvid collect kwai --search "vacina" --hashtag saude --limit 200 --out corpus/vaccines
```

You get a folder with one row per video: caption, hashtags, date, views, likes, comments, shares, author, and the transcript of what is said in the video, plus a `manifest.json` that records how the corpus was made.

## Why

Studies of short-video content (health misinformation, political messaging, advertising, education) usually analyze what creators *say*, not only their captions. Most of these studies still collect videos and transcribe them by hand, which limits sample sizes and makes the data collection hard to repeat or audit.

`shortvid-corpus` automates that step for platforms that are widely used but rarely studied. Kwai has a large audience in Latin America, Brazil and Indonesia and almost no research tooling. It also records the provenance a methods section needs: which sources, when, with which tool version and which speech-to-text model.

What it does:

- **Collects** public videos by profile, keyword search, hashtag or URL, without logging in.
- **Transcribes** speech with Whisper, either locally (no data leaves your computer) or through Groq or Deepgram. It uses the platform's own subtitles when they exist and skips videos that the platform flags as music only.
- **Writes** `videos.jsonl` (full records with timestamped segments), `videos.csv` (flat, for R, pandas or spreadsheets) and `manifest.json` (provenance per run).
- **Grows** a corpus over time: running again on the same folder adds only new videos and logs the run.
- **Pseudonymizes** authors with a salted hash and drops URLs, for datasets that must not identify individuals.

## Installation

Python 3.11 or newer.

```bash
pip install git+https://github.com/zaxdm/shortvid-corpus                    # hosted speech-to-text (Groq or Deepgram)
pip install "shortvid-corpus[local] @ git+https://github.com/zaxdm/shortvid-corpus"   # adds local Whisper
```

A release on PyPI is planned.

`ffmpeg` is bundled through `imageio-ffmpeg`; nothing else is needed.

## Usage

### Command line

```bash
# Latest 100 videos of two Kwai profiles, transcribed locally with Whisper "small"
shortvid collect kwai --profile cocinaoriental --profile receitasdavovo --limit 100 \
    --transcriber local --out corpus/recipes

# Kwai keyword search and hashtag, transcribed with Groq (free tier available)
export GROQ_API_KEY=...
shortvid collect kwai --search "remédio caseiro" --hashtag saude --out corpus/health

# Snapchat Spotlight of a public profile; Snapchat's own subtitles are used when present
shortvid collect snapchat --profile chef.ana --out corpus/snap

# Pseudonymized dataset (keep the salt private and reuse it to extend the corpus)
export SHORTVID_SALT="a-long-random-string"
shortvid collect kwai --hashtag eleicoes --pseudonymize --out corpus/elections
```

Main options:

| Option | Meaning |
|---|---|
| `--profile`, `--search`, `--hashtag`, `--url` | Sources; each can be repeated. Snapchat supports `--profile` and `--url`. |
| `--limit N` | Maximum videos per source (default 50). |
| `--transcriber` | `auto` (Groq/Deepgram if their API key is set, otherwise local), `groq`, `deepgram`, `local`, `none`. |
| `--language es` | Spoken language; detected per video when omitted. |
| `--no-native` | Ignore the platform's own subtitles and always run speech-to-text. |
| `--keep-music` | Also transcribe videos the platform flags as music only. |
| `--pseudonymize` | Salted author pseudonyms, no URLs (needs `SHORTVID_SALT`). |

### Python

```python
import asyncio
from shortvid_corpus import Options, Source, build_corpus, get_platform
from shortvid_corpus.transcribe import LocalWhisper

result = asyncio.run(build_corpus(
    get_platform('kwai'),
    [Source('search', 'vacina'), Source('hashtag', 'saude')],
    'corpus/vaccines',
    transcriber=LocalWhisper('small'),
    options=Options(limit_per_source=200, language='pt'),
))
print(result.added, result.statuses)
```

Then, for example with pandas:

```python
import pandas as pd
df = pd.read_csv('corpus/vaccines/videos.csv')
df[df.transcript.str.contains('efeito colateral', case=False, na=False)]
```

## Output

`videos.csv` columns: `platform, video_id, url, source, created_at, duration_s, views, likes, comments, shares, caption, hashtags, author_id, author_username, author_name, author_followers, transcript, transcript_language, transcript_source, transcript_status, collected_at`.

`transcript_status` is one of `ok`, `no_speech`, `too_long`, `error`, `no_transcriber` or `not_requested`. `transcript_source` is `native` (the platform's subtitles) or the speech-to-text model, for example `faster-whisper/small` or `groq/whisper-large-v3-turbo`.

`manifest.json` keeps one entry per run with its start and end time, tool version, sources, options, speech-to-text model, number of videos added and skipped, transcript status counts and errors.

## Responsible use

- The tool reads only public pages that need no login, at a modest rate. It does not bypass access controls.
- Check the platform's terms of service and your institution's ethics requirements before collecting. Store and share data according to your approved protocol.
- Prefer `--pseudonymize` when the research question does not need identities, and local transcription when audio must not leave your machine.
- Engagement counts are snapshots at `collected_at` and change over time.

## Limitations

- Platforms change their pages without notice; a change can break collection until the parser is updated. Please open an issue when that happens.
- Snapchat profile pages list only the recent Spotlight videos, and there is no keyword search for Spotlight.
- Transcription quality depends on the model and the audio. Report the model (it is in `transcript_source`) and validate a sample by hand.

## Contributing and support

Bug reports, questions and pull requests are welcome: see [CONTRIBUTING.md](CONTRIBUTING.md). For a new platform, implement `Platform.collect` (and `native_transcript` if the platform publishes subtitles) in `src/shortvid_corpus/platforms/`.

## Citation

If you use `shortvid-corpus` in research, please cite it (see [CITATION.cff](CITATION.cff)).

## License

MIT
