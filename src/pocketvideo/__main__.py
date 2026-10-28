"""Allow running PocketVideo via `python -m pocketvideo`."""

import sys
from pocketvideo.main import main

if __name__ == "__main__":
    sys.exit(main())
