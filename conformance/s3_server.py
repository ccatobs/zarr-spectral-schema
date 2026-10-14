"""An in-process S3 endpoint (moto) for the object-storage tests of both bindings.

The server records every request it serves, so a test can prove which objects a
validator read: :meth:`S3Server.offending` names each request that lists or
opens anything but a ``zarr.json`` object, or lists below an array node (the
prefix that holds its chunk keys).

Python tests import :class:`S3Server`. The Rust tests run this file:

    python conformance/s3_server.py <store_dir>...

It uploads each store under its directory name into bucket ``zss``, prints the
endpoint URL and its AWS environment (:func:`env`) as one JSON line on stdout
and serves until stdin closes. Each
stdin line ``requests`` prints one JSON line: the requests served since the
previous line, and the offending ones among them.

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
        backend = DomainDispatcherApplication(create_backend_app)

        def app(environ, start_response):
            self.requests.append(
                [
                    environ["REQUEST_METHOD"],
                    environ["PATH_INFO"],
                    environ["QUERY_STRING"],
                ]
            )
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

    def take_requests(self) -> list[list[str]]:
        """The requests served since the previous call."""
        taken, self.requests = self.requests, []
        return taken

    def offending(self, requests: list[list[str]]) -> list[list[str]]:
        """Requests that read anything but a zarr.json object or list below an array node."""
        bad = []
        for method, path, query in requests:
            key = path.removeprefix(f"/{BUCKET}").removeprefix("/")
            prefix = parse_qs(query).get("prefix", [""])[0]
            if key and not (key == "zarr.json" or key.endswith("/zarr.json")):
                bad.append([method, path, query])  # an object other than zarr.json
            elif any((prefix + "/").startswith(a) for a in self.arrays):
                bad.append([method, path, query])  # a listing of chunk keys
        return bad

    def stop(self) -> None:
        self._server.shutdown()


def main(stores: list[str]) -> None:
    server = S3Server()
    for store in stores:
        server.upload(Path(store))
    print(
        json.dumps({"endpoint": server.endpoint, "env": env(server.endpoint)}),
        flush=True,
    )
    for line in sys.stdin:
        if line.strip() == "requests":
            served = server.take_requests()
            print(
                json.dumps({"served": served, "offending": server.offending(served)}),
                flush=True,
            )
    server.stop()


if __name__ == "__main__":
    main(sys.argv[1:])
