"""Entry point for the packaged Windows build."""

import multiprocessing
import sys

from opent5.gui.__main__ import main

if __name__ == "__main__":
    # A frozen build that spawns a process pool relaunches this exe as the worker; without
    # this the worker would start the GUI instead of running the pool task.
    multiprocessing.freeze_support()
    sys.exit(main())
