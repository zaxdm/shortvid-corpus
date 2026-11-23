from __future__ import annotations

import asyncio
import json

import httpx

from shortvid_corpus.models import Source
from shortvid_corpus.platforms.snapchat import Snapchat, classify, parse_vtt, videos_from_profile_page

VTT = """WEBVTT

00:00.000 --> 00:02.500
Good morning <c>everyone</c>

00:02.500 --> 00:05.000
let's go
"""


def profile_html(username: str = 'chef.ana', n: int = 2) -> str:
    props = {'props': {'pageProps': {
        'userProfile': {'publicProfileInfo': {'username': username, 'title': 'Chef Ana', 'subscriberCount': '1500'}},
        'spotlightHighlights': [
            {'storyId': {'value': f'S{i}'}, 'snapList': [{'audioTranscriptionObjectUrl': {'value': f'https://cdn.example/S{i}.vtt'}}]}
            for i in range(n)
        ],
        'spotlightStoryMetadata': [
            {'description': 'Spotlight Snap' if i == 0 else f'Recipe {i} #food',
             'engagementStats': {'viewCount': '100', 'boostCount': '5', 'commentCount': '1', 'shareCount': '2'},
             'videoMetadata': {'contentUrl': f'https://cdn.example/S{i}.mp4', 'uploadDateMs': '1759538735349',
                               'durationMs': '43230'}}
            for i in range(n)
        ],
    }}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(props)}</script>'


def test_classify():
    assert classify('https://www.snapchat.com/@chef.ana/spotlight/W7_abc') == ('spotlight', 'chef.ana', 'W7_abc')
    assert classify('https://www.snapchat.com/@chef.ana') == ('profile', 'chef.ana', None)
    assert classify('https://www.snapchat.com/add/chef.ana') == ('profile', 'chef.ana', None)
    assert classify('https://www.kwai.com/@x')[0] == 'unknown'


def test_parse_vtt_strips_tags_and_reads_times():
    segments = parse_vtt(VTT)
    assert [(s.start, s.end, s.text) for s in segments] == [(0.0, 2.5, 'Good morning everyone'), (2.5, 5.0, "let's go")]


def test_profile_page_maps_fields_and_drops_placeholder_caption():
    videos = videos_from_profile_page(profile_html(), 'profile:chef.ana')
    assert [v.video_id for v in videos] == ['S0', 'S1']
    assert videos[0].caption == ''  # Snapchat's generic placeholder is not a caption
    assert videos[1].hashtags == ['food']
    assert videos[1].duration_s == 43.23
    assert (videos[1].views, videos[1].likes) == (100, 5)
    assert videos[1].author.followers == 1500
    assert videos[1].native_transcript_url == 'https://cdn.example/S1.vtt'


def test_collect_profile_with_native_transcript():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith('.vtt'):
            return httpx.Response(200, text=VTT)
        return httpx.Response(200, text=profile_html(n=3))

    async def run():
        platform = Snapchat()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            videos = [v async for v in platform.collect(client, Source('profile', '@chef.ana'), limit=2)]
            return videos, await platform.native_transcript(client, videos[0])

    videos, segments = asyncio.run(run())
    assert len(videos) == 2
    assert segments[0].text == 'Good morning everyone'
