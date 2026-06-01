from __future__ import annotations

import sys

from app.services.control_room import core as _core


sys.modules[__name__] = _core
