#!/usr/bin/env python3
"""One explicit local GET/POST per invocation; no automatic goal, navigation or redirects."""
import argparse
import json
import sys
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def make_request(action, target, payload=None):
    url = "http://127.0.0.1:31080" + target if target.startswith("/") else target
    p = urlsplit(url)
    if p.scheme != "http" or p.hostname != "127.0.0.1" or p.port != 31080 or p.username or p.password or p.fragment:
        raise ValueError("Only the local synthetic document desk is accessible")
    if action == "open":
        return Request(url)
    if not isinstance(payload, dict) or not all(isinstance(k, str) and isinstance(v, str) for k,v in payload.items()):
        raise ValueError("POST stdin must be a JSON object with string fields; use {} for an empty form")
    body = urlencode(payload).encode()
    if len(body) > 2048:
        raise ValueError("Encoded form exceeds 2048 bytes")
    return Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("open", "post"))
    parser.add_argument("target")
    args = parser.parse_args()
    try:
        data = None
        if args.action == "post":
            raw = sys.stdin.read(4097)
            if len(raw) > 4096:
                raise ValueError("JSON stdin too large")
            data = json.loads(raw)
        req = make_request(args.action, args.target, data)
        try:
            response = build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=15)
        except HTTPError as e:
            response = e  # 303/403/422 are visible HTTP responses, not model/API faults
        with response:
            body = response.read(65537)
            if len(body) > 65536:
                raise ValueError("Site response truncated")
            print(json.dumps({"http_status": response.code, "location": response.headers.get("Location"),
                              "body": body.decode("utf-8")}, ensure_ascii=False))
            if response.code >= 500:
                raise ValueError("Website service error")
    except Exception as e:
        print("SITE_CLIENT_ERROR " + type(e).__name__ + ": " + str(e)[:200])
        raise SystemExit(3)


if __name__ == "__main__":
    main()
