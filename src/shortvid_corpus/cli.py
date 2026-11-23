"""Command line: `shortvid collect kwai --search "vacuna" --limit 100 --out corpus/`."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from . import __version__
from .corpus import Options, build_corpus
from .models import Source
from .platforms import PLATFORMS, get_platform
from .transcribe import Deepgram, Fallback, GroqWhisper, LocalWhisper, Transcriber


def make_transcriber(name: str, local_model: str) -> Transcriber | None:
    groq, deepgram = os.environ.get('GROQ_API_KEY'), os.environ.get('DEEPGRAM_API_KEY')
    if name == 'none':
        return None
    if name == 'local':
        return LocalWhisper(local_model)
    if name == 'groq':
        if not groq:
            sys.exit('Set the GROQ_API_KEY environment variable, or use --transcriber local.')
        return GroqWhisper(groq)
    if name == 'deepgram':
        if not deepgram:
            sys.exit('Set the DEEPGRAM_API_KEY environment variable, or use --transcriber local.')
        return Deepgram(deepgram)
    # auto: whatever keys are available, Groq first; otherwise local.
    backends: list[Transcriber] = []
    if groq:
        backends.append(GroqWhisper(groq, wait_on_limit=not deepgram))
    if deepgram:
        backends.append(Deepgram(deepgram))
    if not backends:
        return LocalWhisper(local_model)
    return backends[0] if len(backends) == 1 else Fallback(*backends)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog='shortvid', description=__doc__)
    parser.add_argument('--version', action='version', version=f'shortvid-corpus {__version__}')
    sub = parser.add_subparsers(dest='command', required=True)
    c = sub.add_parser('collect', help='collect videos and transcripts into a corpus folder')
    c.add_argument('platform', choices=sorted(PLATFORMS))
    c.add_argument('--profile', action='append', default=[], help='username (repeatable)')
    c.add_argument('--search', action='append', default=[], help='keyword search (repeatable, Kwai only)')
    c.add_argument('--hashtag', action='append', default=[], help='hashtag (repeatable, Kwai only)')
    c.add_argument('--url', action='append', default=[], help='video or profile URL (repeatable)')
    c.add_argument('--out', required=True, help='corpus folder; reruns add only new videos')
    c.add_argument('--limit', type=int, default=50, help='maximum videos per source (default 50)')
    c.add_argument('--transcriber', choices=['auto', 'groq', 'deepgram', 'local', 'none'], default='auto',
                   help='auto: Groq/Deepgram if their API keys are set, otherwise local Whisper')
    c.add_argument('--local-model', default='small', help='faster-whisper model for --transcriber local')
    c.add_argument('--language', help='ISO code of the spoken language; detected when omitted')
    c.add_argument('--no-native', action='store_true', help="ignore the platform's own subtitles")
    c.add_argument('--keep-music', action='store_true', help='also transcribe videos flagged as music only')
    c.add_argument('--max-minutes', type=float, default=10, help='skip longer videos (default 10)')
    c.add_argument('--no-segments', action='store_true', help='do not keep timestamped segments')
    c.add_argument('--pseudonymize', action='store_true',
                   help='replace authors with salted pseudonyms and drop URLs (salt from SHORTVID_SALT)')
    c.add_argument('--concurrency', type=int, default=3)
    c.add_argument('-v', '--verbose', action='store_true')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format='%(levelname)s %(message)s')
    sources = (
        [Source('profile', v) for v in args.profile] + [Source('search', v) for v in args.search]
        + [Source('hashtag', v) for v in args.hashtag] + [Source('url', v) for v in args.url]
    )
    if not sources:
        sys.exit('Give at least one --profile, --search, --hashtag or --url.')
    salt = os.environ.get('SHORTVID_SALT', '')
    if args.pseudonymize and not salt:
        sys.exit('Set SHORTVID_SALT to a private random string to pseudonymize (reuse it across runs).')
    platform = get_platform(args.platform)
    try:
        for source in sources:
            platform.check(source)
    except ValueError as exc:
        sys.exit(str(exc))
    options = Options(
        limit_per_source=args.limit,
        transcribe=args.transcriber != 'none',
        prefer_native=not args.no_native,
        skip_no_speech=not args.keep_music,
        max_minutes=args.max_minutes,
        language=args.language,
        include_segments=not args.no_segments,
        pseudonymize=args.pseudonymize,
        salt=salt,
        concurrency=args.concurrency,
    )
    transcriber = make_transcriber(args.transcriber, args.local_model)
    result = asyncio.run(build_corpus(platform, sources, args.out, transcriber, options))
    status = ', '.join(f'{k}: {v}' for k, v in sorted(result.statuses.items())) or 'none'
    print(f'Added {result.added} videos ({result.skipped_existing} already in the corpus). Transcripts: {status}.')
    for error in result.errors:
        print(f'Error: {error}', file=sys.stderr)
    return 1 if result.errors and not result.added else 0


if __name__ == '__main__':
    raise SystemExit(main())
