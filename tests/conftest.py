"""Qt runs headless in every test, whether or not the shell sets a platform."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
