"""Only inside the disposable test interpreter: read the real model response at a capped rate."""
import json
import os
import time
import urllib.request

_open = urllib.request.urlopen
_counter = int(os.environ["K17_TEST_BPS"])


def _record(**fields):
    with open(os.environ["K17_TEST_HTTP"], "a", encoding="utf-8") as stream:
        stream.write(json.dumps(fields) + "\n")


class _MeteredResponse:
    def __init__(self, response):
        self.response = response
        self.started = time.monotonic()
        self.count = 0
        self.ended = False

    def __getattr__(self, name):
        return getattr(self.response, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if not self.ended:
            _record(body_bytes=self.count)
        return self.response.__exit__(*args)

    def read(self, *args):
        data = self.response.read(*args)
        self.count += len(data)
        delay = self.started + self.count / _counter - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        if not data and not self.ended:
            self.ended = True
            _record(body_bytes=self.count)
        return data


def _metered_open(url, *args, **kwargs):
    response = _open(url, *args, **kwargs)
    request_url = url.full_url if isinstance(url, urllib.request.Request) else url
    if request_url.startswith("https://github.com/") and request_url.endswith(".onnx"):
        _record(pid=os.getpid(), request_range=url.get_header("Range"), status=response.status,
                content_range=response.headers.get("Content-Range"),
                content_length=response.headers.get("Content-Length"))
        return _MeteredResponse(response)
    return response


urllib.request.urlopen = _metered_open
