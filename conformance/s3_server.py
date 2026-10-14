"""An in-process S3 endpoint (moto) for the object-storage tests of both bindings.

The server records every request it serves, so a test can prove which objects a
validator read: :meth:`S3Server.offending` names each request that opens
anything but a ``zarr.json`` object, lists without the ``/`` delimiter, or lists
below an array node (the prefix that holds its chunk keys). The server answers
403 (AccessDenied) for each object key or listing prefix in ``deny``.

Python tests import :class:`S3Server`. The Rust tests run this file:

    python conformance/s3_server.py [<name>=]<store_dir>...

It uploads each store under ``<name>`` (default: its directory name) into bucket
``zss``, prints the endpoint URL and its AWS environment (:func:`env`) as one
JSON line on stdout and serves until stdin closes. It answers each stdin line
with one JSON line:

- ``requests``: the requests served since the previous ``requests`` line, and
  the offending ones among them;
- ``deny <key or prefix>``: answer 403 for it from now on;
- ``allow``: clear the deny list.

Clients take the endpoint from the standard AWS environment (:func:`env`).
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs

import boto3
from moto.moto_server.werkzeug_app import (
    DomainDispatcherApplication,
    create_backend_app,
)
from werkzeug.serving import make_server

BUCKET = "zss"


def env(endpoint: str) -> dict[str, str]:
    """The AWS environment that points fsspec (s3fs) and object_store at ``endpoint``."""
    return {
        "AWS_ENDPOINT_URL": endpoint,
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_REGION": "us-east-1",
        "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_ALLOW_HTTP": "true",
        # A proxy must not see requests to a local endpoint.
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }


class S3Server:
    """moto S3 on 127.0.0.1 (a free port), recording ``[method, key, query]``."""

    def __init__(self) -> None:
        self.requests: list[list[str]] = []
        self.arrays: set[str] = set()  # key prefixes of array nodes
        self.deny: set[str] = (
            set()
        )  # object keys and listing prefixes answered with 403
        backend = DomainDispatcherApplication(create_backend_app)

        def app(environ, start_response):
            request = [
                environ["REQUEST_METHOD"],
                environ["PATH_INFO"],
                environ["QUERY_STRING"],
            ]
            self.requests.append(request)
            key, params = _parse(request)
            if (key or params.get("prefix", [""])[0]) in self.deny:
                start_response("403 Forbidden", [("Content-Type", "application/xml")])
                return [
                    b"<Error><Code>AccessDenied</Code><Message>denied</Message></Error>"
                ]
            return backend(environ, start_response)

        logging.getLogger("werkzeug").setLevel(logging.ERROR)  # no line per request
        self._server = make_server("127.0.0.1", 0, app, threaded=True)
        self.endpoint = f"http://127.0.0.1:{self._server.server_port}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        self._s3 = boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            region_name="us-east-1",
            aws_access_key_id="testing",
            aws_secret_access_key="testing",
        )
        self._s3.create_bucket(Bucket=BUCKET)

    def upload(self, store: Path, name: str | None = None) -> str:
        """Upload a local store under ``name`` (default: its directory name); return its URL."""
        name = name or store.name
        for f in sorted(store.rglob("*")):
            if not f.is_file():
                continue
            key = f"{name}/{f.relative_to(store).as_posix()}"
            self._s3.upload_file(str(f), BUCKET, key)
            if (
                f.name == "zarr.json"
                and json.loads(f.read_bytes()).get("node_type") == "array"
            ):
                self.arrays.add(key.removesuffix("zarr.json"))
        self.requests.clear()
        return f"s3://{BUCKET}/{name}"

    def put(self, key: str, body: bytes) -> None:
        """Write one object (e.g. a node added after a first check)."""
        self._s3.put_object(Bucket=BUCKET, Key=key, Body=body)

    def take_requests(self) -> list[list[str]]:
        """The requests served since the previous call."""
        taken, self.requests = self.requests, []
        return taken

    def offending(self, requests: list[list[str]]) -> list[list[str]]:
        """Requests that read anything but a zarr.json object, or that list
        without the "/" delimiter or below an array node (chunk keys)."""
        bad = []
        for request in requests:
            key, params = _parse(request)
            prefix = params.get("prefix", [""])[0]
            if key and not (key == "zarr.json" or key.endswith("/zarr.json")):
                bad.append(request)  # an object other than zarr.json
            elif not key and params.get("delimiter") != ["/"]:
                bad.append(request)  # a recursive listing reaches chunk keys
            elif any((prefix + "/").startswith(a) for a in self.arrays):
                bad.append(request)  # a listing of chunk keys
        return bad

    def stop(self) -> None:
        self._server.shutdown()


def _parse(request: list[str]) -> tuple[str, dict[str, list[str]]]:
    """The object key (empty for a bucket request) and the query parameters."""
    _, path, query = request
    return path.removeprefix(f"/{BUCKET}").removeprefix("/"), parse_qs(query)


def main(stores: list[str]) -> None:
    server = S3Server()
    for arg in stores:
        name, _, store = arg.rpartition("=")
        server.upload(Path(store), name or None)
    print(
        json.dumps({"endpoint": server.endpoint, "env": env(server.endpoint)}),
        flush=True,
    )
    for line in sys.stdin:
        command, _, arg = line.strip().partition(" ")
        if command == "requests":
            served = server.take_requests()
            answer = {"served": served, "offending": server.offending(served)}
        elif command == "deny":
            server.deny.add(arg)
            answer = {"deny": sorted(server.deny)}
        else:  # allow
            server.deny.clear()
            answer = {"deny": []}
        print(json.dumps(answer), flush=True)
    server.stop()


if __name__ == "__main__":
    main(sys.argv[1:])
