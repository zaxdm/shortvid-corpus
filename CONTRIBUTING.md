# Contributing

Thanks for helping. This project is maintained by one person, so issues and pull requests may take a few days to get an answer.

## Reporting a problem

Open an issue at https://github.com/zaxdm/shortvid-corpus/issues with:

- the command or code you ran (remove API keys and salts),
- the shortvid-corpus version (`shortvid --version`) and your Python version and OS,
- what you expected and what happened (the error message, or the `errors` list in `manifest.json`).

If a platform stopped working, include the URL of a public page that fails.

## Asking for help

Use an issue with the `question` label. Questions about research design are welcome too.

## Changing the code

```bash
git clone https://github.com/zaxdm/shortvid-corpus
cd shortvid-corpus
python -m venv .venv && source .venv/bin/activate   # on Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pytest
ruff check .
```

- Tests must not call the real platforms: use `httpx.MockTransport` with synthetic pages, as in `tests/test_kwai.py`. Never commit real personal data as fixtures.
- Add a line to `CHANGELOG.md` under "Unreleased".
- Keep pull requests focused on one change.

## Adding a platform

Create `src/shortvid_corpus/platforms/<name>.py` with a `Platform` subclass that implements `collect` (and `native_transcript` if the platform publishes subtitles), register it in `platforms/__init__.py`, and add tests. Only public pages that need no login are in scope.

## Code of conduct

Be respectful and assume good faith. Harassment is not tolerated; the maintainer may remove comments or block accounts that break this rule.
