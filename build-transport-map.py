#!/usr/bin/env python3
"""Build the Moscow transport reachability site."""

import sys

sys.dont_write_bytecode = True

from transport_map.cli import main

if __name__ == "__main__":
    main()
