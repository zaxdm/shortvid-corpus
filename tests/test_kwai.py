from __future__ import annotations

import asyncio

import httpx
from conftest import kwai_photo, nuxt_page

from shortvid_corpus.models import Source
from shortvid_corpus.platforms._nuxt import parse_nuxt
from shortvid_corpus.platforms.kwai import (
    Kwai,
    classify,
    media_candidates,
    photos_from_profile_page,
    photos_from_video_page,
    to_video,
)


def test_nuxt_parser_resolves_variables_and_never_runs_code():
    html = (
        '<script>window.__NUXT__=(function(a,b,c){c.x=a;return {data:[c],flag:!0,off:!1,'
        'none:void 0,arr:Array(2),neg:-1.5,txt:"caf\\u00e9 \\ud83c\\udf5c",call:alert(1)}}'
        '("shared",null,{}));</script>'
    )
    state = parse_nuxt(html)
    assert state['data'] == [{'x': 'shared'}]
    assert state['flag'] is True and state['off'] is False and state['none'] is None
    assert state['arr'] == [None, None]
    assert state['neg'] == -1.5
    assert state['txt'] == 'café 🍜'
    assert state['call'] is None  # function calls other than Array() are ignored


def test_classify():
    assert classify('https://www.kwai.com/@cooking_br/video/5236992052920543570') == (
        'video', 'cooking_br', '5236992052920543570')
    assert classify('https://www.kwai.com/@cooking_br') == ('profile', 'cooking_br', None)
    assert classify('https://example.com/@x')[0] == 'unknown'


def test_media_candidates_lightest_first_without_duplicates():
    urls = media_candidates(kwai_photo())
    assert urls == [
        'https://cdn.example/111/sd.mp4',
        'https://backup.example/111/sd.mp4',
        'https://cdn.example/111/hd.mp4',
        'https://cdn.example/111/main.mp4',
    ]


def test_to_video_maps_fields():
    v = to_video(kwai_photo(), 'profile:cooking_br', followers=1200)
    assert v.platform == 'kwai' and v.video_id == '111'
    assert v.url == 'https://www.kwai.com/@cooking_br/video/111'
    assert v.hashtags == ['receita', 'bolo']
    assert v.created_at.startswith('2025-11-13')
    assert v.duration_s == 168.8
    assert (v.views, v.likes, v.comments, v.shares) == (610, 18, 0, 8)
    assert v.author.username == 'cooking_br' and v.author.followers == 1200
    assert v.speech_probability == 0.99


def test_profile_page_and_video_page():
    profile = nuxt_page({'data': [{'feedsData': {'feeds': [kwai_photo('1'), kwai_photo('2')], 'cursor': 'abc'},
                                   'userProfile': {'ownerCount': {'fan': 55}}}]})
    photos, followers, cursor = photos_from_profile_page(profile)
    assert [p['photo_id_str'] for p in photos] == ['1', '2']
    assert followers == 55 and cursor == 'abc'

    video = nuxt_page({'data': [{'photoList': [kwai_photo('9'), kwai_photo('10')]}]})
    assert [p['photo_id_str'] for p in photos_from_video_page(video, '10')] == ['10']


def test_collect_profile_follows_cursor_and_respects_limit():
    profile_html = nuxt_page({'data': [{'feedsData': {'feeds': [kwai_photo('1'), kwai_photo('2')], 'cursor': 'p2'},
                                        'userProfile': {'ownerCount': {'fan': 7}}}]})
    api_calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == 'GET':
            assert request.url.path == '/@cooking_br'
            return httpx.Response(200, text=profile_html)
        api_calls.append(request.url.path)
        return httpx.Response(200, json={'result': 1, 'feeds': [kwai_photo('2'), kwai_photo('3'), kwai_photo('4')],
                                         'cursor': 'no_more'})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return [v async for v in Kwai().collect(client, Source('profile', '@cooking_br'), limit=3)]

    videos = asyncio.run(run())
    assert [v.video_id for v in videos] == ['1', '2', '3']  # '2' repeated by the API is skipped
    assert all(v.author.followers == 7 for v in videos)
    assert api_calls == ['/rest/o/w/pc/feed/profile']


def test_collect_search_stops_when_no_more_results():
    pages = [
        {'result': 1, 'feeds': [kwai_photo('1')], 'cursor': '20'},
        {'result': 1, 'feeds': [], 'cursor': 'no_more'},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pages.pop(0))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return [v async for v in Kwai().collect(client, Source('search', 'bolo de cenoura'), limit=50)]

    assert [v.video_id for v in asyncio.run(run())] == ['1']
    assert pages == []
