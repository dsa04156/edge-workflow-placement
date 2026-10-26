"""Pilot-local wrapper for real-topology-live http_runtime.request, shipped only in this run's owned ConfigMap.

run_pilot.VqaSession.config_map() inserts two lines before http_runtime's __main__ guard, so the operator
(__main__) and edge_client/load_trace (which import `request` from http_runtime) use the wrapped function;
the legacy files on disk stay unchanged. On an HTTP error status the wrapper reads a bounded body and raises
HTTPChainError, whose repr carries the outer status and the nested status chain. The existing wrappers that
answer 500 with repr(error) (edge_client handler, http_runtime Handler) therefore forward the chain, and
load_trace's terminal row keeps it in `error`. Only status codes and bounded text are kept; nothing is
inferred from elapsed time.
"""
import json
import re
import urllib.error

BODY_LIMIT = 65536
EXCERPT = 300
PREFIX = 'HTTPChainError('
LEGACY = re.compile(r"<HTTPError (\d{3}): ")


class HTTPChainError(urllib.error.HTTPError):
    def __init__(self, url, code, msg, evidence):
        super().__init__(url, code, msg, None, None)
        self.evidence = evidence

    def __repr__(self):
        return PREFIX+json.dumps(self.evidence, separators=(',', ':'), sort_keys=True)+')'

    __str__ = __repr__


def parse(text):
    """Evidence dict from an HTTPChainError repr, else None."""
    if isinstance(text, str) and text.startswith(PREFIX) and text.endswith(')'):
        try:
            return json.loads(text[len(PREFIX):-1])
        except ValueError:
            return None
    return None


def chain_error(url, error):
    raw = error.read(BODY_LIMIT) if error.fp is not None else b''
    text = raw.decode(errors='replace')
    try:
        inner = json.loads(text).get('error', '')
    except (ValueError, AttributeError):
        inner = text
    nested = parse(inner)
    legacy = LEGACY.match(inner) if isinstance(inner, str) else None
    if nested:
        codes, message = [error.code, *nested['http_codes']], nested['message']
    elif legacy:
        codes, message = [error.code, int(legacy.group(1))], inner[:EXCERPT]
    else:
        codes, message = [error.code], str(inner)[:EXCERPT]
    evidence = dict(http_code=error.code, http_codes=codes, message=message, url=url[:200],
                    body_excerpt=text[:EXCERPT] if not nested else None)
    return HTTPChainError(url, error.code, error.msg, evidence)


def wrap(request):
    def wrapped(url, data, timeout=60):
        try:
            return request(url, data, timeout=timeout)
        except HTTPChainError:
            raise
        except urllib.error.HTTPError as error:
            raise chain_error(url, error) from None
    wrapped.vqa_adapter = True
    return wrapped
