"""Tiny stdlib-only JSON HTTP client (no pip installs needed)."""
import base64
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request


class HttpError(Exception):
    def __init__(self, code, body, url):
        super().__init__(f"HTTP {code} on {url}: {body[:800]}")
        self.code, self.body, self.url = code, body, url


class Client:
    def __init__(self, base, user=None, password=None, token=None, timeout=60, verify=True):
        self.base = base.rstrip("/")
        self.timeout = timeout
        self.headers = {"Accept": "application/json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        elif user:
            raw = f"{user}:{password}".encode()
            self.headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
        self.ctx = ssl.create_default_context() if verify else ssl._create_unverified_context()

    def request(self, method, path, data=None, params=None, raw=None, content_type=None, ok=(200, 201, 202, 204)):
        url = path if path.startswith("http") else f"{self.base}{path}"
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        headers = dict(self.headers)
        body = None
        if raw is not None:
            body = raw if isinstance(raw, bytes) else raw.encode()
            headers["Content-Type"] = content_type or "text/plain"
        elif data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        last = None
        for attempt in range(4):
            req = urllib.request.Request(url, data=body, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx) as r:
                    txt = r.read().decode() or "{}"
                    if r.status not in ok:
                        raise HttpError(r.status, txt, url)
                    try:
                        return json.loads(txt)
                    except ValueError:
                        return {"_raw": txt}
            except urllib.error.HTTPError as e:
                txt = e.read().decode(errors="replace")
                if e.code in ok:
                    return json.loads(txt or "{}")
                if e.code in (502, 503, 504) and attempt < 3:
                    last = HttpError(e.code, txt, url)
                    time.sleep(5)
                    continue
                raise HttpError(e.code, txt, url) from None
            except (urllib.error.URLError, TimeoutError) as e:
                last = e
                time.sleep(5)
        raise last

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, data=None, **kw):
        return self.request("POST", path, data=data, **kw)

    def patch(self, path, data=None, **kw):
        return self.request("PATCH", path, data=data, **kw)


def log(msg):
    print(f"  - {msg}", flush=True)
