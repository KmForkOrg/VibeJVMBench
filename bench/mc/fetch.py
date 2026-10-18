#!/usr/bin/env python3

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import BENCH_HOME

VERSION = "26.3"
MANIFEST_SHA1 = "11f1aefe473eefba0af62e3286c33b692807b2eb"
MANIFEST_URL = f"https://piston-meta.mojang.com/v1/packages/{MANIFEST_SHA1}/{VERSION}.json"
SERVER_SHA1 = "33680f5f2ac32864d6d7cf5e56a705fdb3e05f4c"

HOME = Path(os.environ.get("BENCH_MC_HOME") or BENCH_HOME / "mc") / VERSION
SERVER = HOME / "server.jar"


def sha1(path):
    digest = hashlib.sha1()
    with open(path, "rb") as file:
        for block in iter(lambda: file.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(url, digest, destination):
    destination = Path(destination)
    if destination.exists() and sha1(destination) == digest:
        return False
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read()
    actual = hashlib.sha1(data).hexdigest()
    if actual != digest:
        sys.exit(f"{url}: sha1 {actual}, expected {digest}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".part")
    part.write_bytes(data)
    os.replace(part, destination)
    return True


def manifest():
    path = HOME / f"{VERSION}.json"
    fetch(MANIFEST_URL, MANIFEST_SHA1, path)
    return json.loads(path.read_text())


def main(argv=None):
    argparse.ArgumentParser(
        description=f"Download and verify the Minecraft {VERSION} dedicated server into {HOME}."
    ).parse_args(argv)
    server = manifest()["downloads"]["server"]
    assert server["sha1"] == SERVER_SHA1, server["sha1"]
    downloaded = fetch(server["url"], SERVER_SHA1, SERVER)
    print(f"{SERVER} verified" + (" (downloaded)" if downloaded else ""))


if __name__ == "__main__":
    main()
