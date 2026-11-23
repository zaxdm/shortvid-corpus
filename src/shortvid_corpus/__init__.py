"""Build reproducible research corpora of short videos (metadata + transcripts) from public pages."""

__version__ = '0.1.0'

from .corpus import Options, RunResult, build_corpus
from .models import Author, Segment, Source, Video
from .platforms import PLATFORMS, get_platform

__all__ = [
    'PLATFORMS', 'Author', 'Options', 'RunResult', 'Segment', 'Source', 'Video',
    '__version__', 'build_corpus', 'get_platform',
]
