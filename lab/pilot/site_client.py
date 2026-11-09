#!/usr/bin/python3
"""Agent's ordinary synthetic-website client; every action makes an actual HTTP request."""
import argparse
import json
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

parser = argparse.ArgumentParser(description="Open a sandbox website page or submit an offered read-only project question")
sub = parser.add_subparsers(dest="action", required=True)
sub.add_parser("open").add_argument("url")
sub.add_parser("ask").add_argument("question", choices=("draft_plan", "permission_check", "readiness"))
args = parser.parse_args()
if args.action == "open":
    parts = urlsplit(args.url)
    if parts.scheme != "http" or parts.hostname != "127.0.0.1" or parts.port != 31080:
        parser.error("This client can access only the local synthetic website")
    request = Request(args.url)
else:
    request = Request("http://127.0.0.1:31080/question", data=urlencode({"question": args.question}).encode(),
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
try:
    with build_opener(ProxyHandler({})).open(request, timeout=15) as response:
        print(response.read(65536).decode())
except HTTPError as error:
    print(json.dumps({"http_status": error.code, "body": error.read(1000).decode(errors="replace")}))
