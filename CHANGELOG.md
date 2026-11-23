# Changelog

## Unreleased

## 0.1.0 - 2026-11-23

- First public version.
- Kwai: profiles (with paging), keyword search, hashtags and single videos.
- Snapchat Spotlight: public profiles and single videos, with Snapchat's own subtitles.
- Speech-to-text with local Whisper (faster-whisper), Groq or Deepgram, with fallback when a quota is reached.
- Corpus folder with `videos.jsonl`, `videos.csv` and a provenance `manifest.json`; reruns add only new videos.
- Optional salted pseudonymization of authors.
- `shortvid` command line.
