import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_BASE = "https://api.bamboodeploy.com"
DIRECT_MAX = 95 * 1024 * 1024
USER_AGENT = "bamboodeploy-python/1.0.0"


class BambooError(Exception):
    def __init__(self, message, code="error", status=0, body=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.body = body or {}


def resolve_key(explicit=None):
    if explicit:
        return explicit
    env = os.environ.get("BAMBOO_API_KEY")
    if env:
        return env
    for d in (Path.cwd(), Path.home()):
        f = d / ".bamboorc"
        try:
            raw = f.read_bytes()
        except OSError:
            continue
        text = raw.decode("utf-16") if raw[:2] == b"\xff\xfe" else raw.decode("utf-8", "replace")
        m = re.search(r"(bd_live_[0-9a-f]+)", text, re.I)
        if m:
            return m.group(1)
    return None


def _fmt(n):
    if n >= 1048576:
        return "%.1f MB" % (n / 1048576)
    if n >= 1024:
        return "%d KB" % (n // 1024)
    return "%d B" % n


class BambooClient:
    def __init__(self, api_key=None, api_base=None, log=None):
        self.api_key = resolve_key(api_key)
        if not self.api_key:
            raise BambooError("No API key. Set BAMBOO_API_KEY, pass --api-key, or put the key in .bamboorc", "no_key")
        self.base = (api_base or os.environ.get("BAMBOO_API_BASE") or DEFAULT_BASE).rstrip("/")
        self.log = log or (lambda m: None)

    def _req(self, method, path, data=None, headers=None, raw=False, auth=True):
        url = path if path.startswith("http") else self.base + path
        h = dict(headers or {})
        h.setdefault("User-Agent", USER_AGENT)
        if auth:
            h["Authorization"] = "Bearer " + self.api_key
        req = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            res = urllib.request.urlopen(req, timeout=1800)
        except urllib.error.HTTPError as e:
            res = e
        if raw:
            return res
        text = res.read().decode("utf-8", "replace")
        try:
            body = json.loads(text)
        except ValueError:
            body = {"error": text[:300]}
        if res.status >= 400:
            err = body.get("error")
            code = err if err in ("premium_required", "connections_required") else ("unauthorized" if res.status == 401 else "http_error")
            raise BambooError(body.get("message") or err or "HTTP %d" % res.status, code, res.status, body)
        return body

    def upload(self, file):
        p = Path(file)
        if not p.is_file():
            raise BambooError("File not found: %s" % p, "not_found")
        size = p.stat().st_size
        name = urllib.parse.quote(p.name)
        self.log("Uploading %s (%s)" % (p.name, _fmt(size)))
        with p.open("rb") as fh:
            if size > DIRECT_MAX:
                init = self._req("POST", "/uploads/init?name=%s&size=%d" % (name, size))
                res = self._req("PUT", init["upload_url"], data=fh, headers={"Content-Length": str(size)}, raw=True, auth=False)
                if res.status >= 400:
                    raise BambooError("Upload failed: HTTP %d" % res.status, "http_error", res.status)
                return self._req("POST", "/uploads/%s/finalize?name=%s" % (init["id"], name))
            return self._req("POST", "/uploads?name=%s" % name, data=fh,
                             headers={"Content-Type": "application/octet-stream", "Content-Length": str(size)})

    def status(self, app_id):
        return self._req("GET", "/uploads/%s" % app_id)

    def list(self, limit=20):
        return self._req("GET", "/uploads?limit=%d" % limit)

    def request_sign(self, app_id):
        return self._req("POST", "/uploads/%s/sign" % app_id)

    def delete(self, app_id):
        return self._req("DELETE", "/uploads/%s" % app_id)

    def report(self, app_id, timeout=600, interval=5):
        deadline = time.time() + timeout
        while True:
            res = self._req("GET", "/uploads/%s/report" % app_id, raw=True)
            if res.status < 300:
                return res.read().decode("utf-8", "replace")
            if res.status != 409:
                raise BambooError("HTTP %d" % res.status, "http_error", res.status)
            if time.time() > deadline:
                raise BambooError("Scan did not finish in time", "timeout")
            time.sleep(interval)

    def wait_for_sign(self, app_id, timeout=900, interval=10):
        deadline = time.time() + timeout
        last = None
        while True:
            s = self.status(app_id)
            sign = s.get("sign") or {}
            st = sign.get("status", "none")
            if st != last:
                last = st
                why = ""
                if st == "pending":
                    why = " (awaiting operator review)" if sign.get("pending_reason") == "operator_review" else " (awaiting scan results)"
                self.log("Sign status: %s%s" % (st, why))
            if st == "done":
                return s
            if st in ("failed", "denied", "canceled"):
                err = sign.get("error")
                raise BambooError("Sign %s%s" % (st, ": " + err if err else ""), "sign_" + st, 200, s)
            if time.time() > deadline:
                raise BambooError("Sign did not complete within %ds (last status: %s)" % (timeout, st), "timeout", 200, s)
            time.sleep(interval)

    def download(self, app_id, dest, variant="signed"):
        res = self._req("GET", "/uploads/%s/download?variant=%s" % (app_id, variant), raw=True)
        if res.status >= 400:
            raise BambooError("Download failed: HTTP %d" % res.status, "http_error", res.status)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".bamboo-tmp")
        with tmp.open("wb") as out:
            while True:
                chunk = res.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
        os.replace(tmp, dest)
        return dest.stat().st_size


def sign_file(file, out=None, api_key=None, api_base=None, timeout=900, interval=10, log=None):
    c = BambooClient(api_key, api_base, log)
    up = c.upload(file)
    c.log("App id: %s%s" % (up["id"], " (already uploaded)" if up.get("duplicate") else ""))
    sign = c.request_sign(up["id"])
    c.log("Sign job %s: %s" % (sign.get("job_id"), sign.get("status")))
    c.wait_for_sign(up["id"], timeout, interval)
    dest = out or file
    size = c.download(up["id"], dest)
    c.log("Wrote %s (%s)" % (dest, _fmt(size)))
    return {"app_id": up["id"], "job_id": sign.get("job_id"), "out": str(dest), "size": size}


def scan_file(file, api_key=None, api_base=None, timeout=600, log=None):
    c = BambooClient(api_key, api_base, log)
    up = c.upload(file)
    text = c.report(up["id"], timeout)
    s = c.status(up["id"])
    return {"app_id": up["id"], "report": text, "summary": s.get("scan") or {}}
