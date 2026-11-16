"""Entry point: ``python -m nightexpress`` or the ``nightexpress`` command."""

import sys

from nightexpress.app import run


def main() -> int:
    return run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
