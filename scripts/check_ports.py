#!/usr/bin/env python3
"""Fail with a readable message when requested localhost ports are occupied."""

from __future__ import annotations

import argparse
import socket


def is_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("ports", nargs="+", type=int)
    args = parser.parse_args()
    occupied = [port for port in args.ports if not is_available(port)]
    if occupied:
        print("Host port(s) already in use: " + ", ".join(map(str, occupied)))
        print("Stop the conflicting process or change the corresponding value in .env.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
