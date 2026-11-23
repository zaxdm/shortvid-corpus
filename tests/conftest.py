from __future__ import annotations

import json


def kwai_photo(video_id: str = '111', kwai_id: str = 'cooking_br', **extra) -> dict:
    """A Kwai `photo` object with the fields the parser reads (synthetic data)."""
    photo = {
        'photo_id_str': video_id,
        'kwai_id': kwai_id,
        'user_id_str': '900',
        'user_name': 'Cooking BR',
        'caption': 'Bolo de cenoura #receita #bolo.',
        'timestamp': 1763050166632,
        'view_count': 610,
        'like_count': 18,
        'comment_count': 0,
        'forward_count': 8,
        'ext_params': {'video': 168800},
        'main_mv_urls': [{'url': f'https://cdn.example/{video_id}/main.mp4'}],
        'transcode_manifest_info': {
            'audioFeature': {'dialogProbability': 0.99},
            'adaptationSet': [{'representation': [
                {'url': f'https://cdn.example/{video_id}/hd.mp4', 'fileSize': 9000},
                {'url': f'https://cdn.example/{video_id}/sd.mp4', 'fileSize': 3000,
                 'backupUrl': [f'https://backup.example/{video_id}/sd.mp4']},
            ]}],
        },
    }
    photo.update(extra)
    return photo


def nuxt_page(state: dict) -> str:
    """Wraps a state in the `__NUXT__=(function(...){...}(...))` form Kwai uses."""
    body = json.dumps(state).replace('"__A__"', 'a')
    return (
        '<html><body><script>window.__NUXT__=(function(a,b){'
        f'return {body}}}("shared",void 0));</script></body></html>'
    )
