"""Reads the `window.__NUXT__` state that Kwai embeds in its HTML.

Nuxt emits it as a function that receives the variables (a, b, c...) and returns
the state. Only the subset of JavaScript used by that block is interpreted
(literals, objects, arrays, assignments such as `R[0]={...}`, `Array(n)`,
`void 0`, `!0`, `!1`). Code from the page is never executed.
"""

from __future__ import annotations

import re
from typing import Any

_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<str>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')
  | (?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<id>[A-Za-z_$][\w$]*)
  | (?P<op>[{}\[\]().,:;=!+-])
    """,
    re.VERBOSE,
)

_ESCAPES = {'n': '\n', 't': '\t', 'r': '\r', 'b': '\b', 'f': '\f', 'v': '\v', '0': '\0'}


class NuxtParseError(ValueError):
    """The block does not have the expected shape."""


def _unescape(raw: str) -> str:
    body = raw[1:-1]
    out: list[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch != '\\':
            out.append(ch)
            i += 1
            continue
        nxt = body[i + 1] if i + 1 < len(body) else ''
        if nxt == 'u' and body[i + 2 : i + 3] == '{':
            end = body.index('}', i)
            out.append(chr(int(body[i + 3 : end], 16)))
            i = end + 1
        elif nxt == 'u':
            out.append(chr(int(body[i + 2 : i + 6], 16)))
            i += 6
        elif nxt == 'x':
            out.append(chr(int(body[i + 2 : i + 4], 16)))
            i += 4
        else:
            out.append(_ESCAPES.get(nxt, nxt))
            i += 2
    # Joins surrogate pairs (emojis written as \ud83c\udf5c).
    return ''.join(out).encode('utf-16', 'surrogatepass').decode('utf-16')


def _tokenize(src: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if not m:
            raise NuxtParseError(f'Unexpected character at {pos}: {src[pos:pos + 20]!r}')
        pos = m.end()
        kind = m.lastgroup
        if kind != 'ws':
            tokens.append((kind, m.group()))  # type: ignore[arg-type]
    return tokens


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.t = tokens
        self.i = 0

    # --- helpers ---
    def peek(self, offset: int = 0) -> tuple[str, str]:
        j = self.i + offset
        return self.t[j] if j < len(self.t) else ('eof', '')

    def take(self, value: str | None = None) -> tuple[str, str]:
        tok = self.peek()
        if value is not None and tok[1] != value:
            raise NuxtParseError(f'Expected {value!r}, got {tok[1]!r} (token {self.i})')
        self.i += 1
        return tok

    def accept(self, value: str) -> bool:
        if self.peek()[1] == value:
            self.i += 1
            return True
        return False

    # --- grammar ---
    def program(self) -> tuple[list[str], list[tuple], tuple, list[tuple]]:
        # __NUXT__=(function(a,b){ ...; return {...}}(x,y));
        self.take('__NUXT__')
        self.take('=')
        self.take('(')
        self.take('function')
        self.take('(')
        params: list[str] = []
        while not self.accept(')'):
            params.append(self.take()[1])
            self.accept(',')
        self.take('{')
        stmts: list[tuple] = []
        ret: tuple = ('lit', None)
        while not self.accept('}'):
            if self.accept('return'):
                ret = self.expr()
                self.accept(';')
                continue
            target = self.expr()
            self.take('=')
            stmts.append((target, self.expr()))
            self.accept(';')
        self.take('(')
        args: list[tuple] = []
        while not self.accept(')'):
            args.append(self.expr())
            self.accept(',')
        return params, stmts, ret, args

    def expr(self) -> tuple:
        node = self.primary()
        while True:
            if self.accept('.'):
                node = ('get', node, ('lit', self.take()[1]))
            elif self.accept('['):
                key = self.expr()
                self.take(']')
                node = ('get', node, key)
            elif self.peek()[1] == '(':
                self.take('(')
                call_args: list[tuple] = []
                while not self.accept(')'):
                    call_args.append(self.expr())
                    self.accept(',')
                node = ('call', node, call_args)
            else:
                return node

    def primary(self) -> tuple:
        kind, val = self.take()
        if kind == 'str':
            return ('lit', _unescape(val))
        if kind == 'num':
            num = float(val)
            return ('lit', int(num) if num.is_integer() and 'e' not in val.lower() and '.' not in val else num)
        if val == '-':
            return ('neg', self.primary())
        if val == '+':
            return self.primary()
        if val == '!':
            return ('not', self.primary())
        if val == '{':
            pairs: list[tuple[str, tuple]] = []
            while not self.accept('}'):
                kkind, key = self.take()
                key = _unescape(key) if kkind == 'str' else key
                self.take(':')
                pairs.append((key, self.expr()))
                self.accept(',')
            return ('obj', pairs)
        if val == '[':
            items: list[tuple] = []
            while not self.accept(']'):
                items.append(self.expr())
                self.accept(',')
            return ('arr', items)
        if val == '(':
            inner = self.expr()
            self.take(')')
            return inner
        if kind == 'id':
            if val == 'true':
                return ('lit', True)
            if val == 'false':
                return ('lit', False)
            if val in ('null', 'undefined'):
                return ('lit', None)
            if val == 'void':
                self.primary()
                return ('lit', None)
            return ('var', val)
        raise NuxtParseError(f'Unexpected token {val!r} (token {self.i})')


def _eval(node: tuple, env: dict[str, Any]) -> Any:
    op = node[0]
    if op == 'lit':
        return node[1]
    if op == 'var':
        return env.get(node[1])
    if op == 'neg':
        val = _eval(node[1], env)
        return -val if isinstance(val, (int, float)) else None
    if op == 'not':
        return not _eval(node[1], env)
    if op == 'obj':
        return {k: _eval(v, env) for k, v in node[1]}
    if op == 'arr':
        return [_eval(v, env) for v in node[1]]
    if op == 'get':
        base = _eval(node[1], env)
        key = _eval(node[2], env)
        if isinstance(base, dict):
            return base.get(key)
        if isinstance(base, list) and isinstance(key, int) and 0 <= key < len(base):
            return base[key]
        return None
    if op == 'call':
        fn = node[1]
        if fn == ('var', 'Array'):
            args = [_eval(a, env) for a in node[2]]
            if len(args) == 1 and isinstance(args[0], int):
                return [None] * args[0]
            return args
        return None  # Any other function call is ignored: page code is never executed.
    raise NuxtParseError(f'Unknown node {op}')


def _assign(target: tuple, value: Any, env: dict[str, Any]) -> None:
    if target[0] == 'var':
        env[target[1]] = value
        return
    if target[0] != 'get':
        raise NuxtParseError('Unsupported assignment')
    base = _eval(target[1], env)
    key = _eval(target[2], env)
    if isinstance(base, dict):
        base[key] = value
    elif isinstance(base, list) and isinstance(key, int):
        if key >= len(base):
            base.extend([None] * (key + 1 - len(base)))
        base[key] = value


def extract_nuxt_source(html: str) -> str | None:
    """Returns the `__NUXT__=(...);` source of the page, or None if it is missing."""
    start = html.find('__NUXT__=')
    if start == -1:
        return None
    end = html.find('</script>', start)
    return html[start:end].strip().rstrip(';') if end != -1 else None


def parse_nuxt(html: str) -> dict[str, Any] | None:
    """Converts the Nuxt state of the page into a Python dictionary."""
    src = extract_nuxt_source(html)
    if src is None:
        return None
    params, stmts, ret, args = _Parser(_tokenize(src)).program()
    env: dict[str, Any] = {}
    arg_values = [_eval(a, env) for a in args]
    env.update(dict(zip(params, arg_values)))
    for target, value in stmts:
        _assign(target, _eval(value, env), env)
    result = _eval(ret, env)
    return result if isinstance(result, dict) else None
