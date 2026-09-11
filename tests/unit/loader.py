# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Import the collection's module_utils without an ansible_collections layout.

``ansible-test units`` requires the repo to be checked out at a path ending in
``ansible_collections/<ns>/<name>/``, which is fine in CI but a nuisance
locally. The module_utils in this collection deliberately depend on nothing but
the standard library and carry relative-import fallbacks, so they can be loaded
straight off disk and tested with plain ``unittest``:

    python -m unittest discover -s tests/unit -v
"""
import os
import sys

MODULE_UTILS = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "..", "plugins", "module_utils"))

if MODULE_UTILS not in sys.path:
    sys.path.insert(0, MODULE_UTILS)

import logs  # noqa: E402,F401
import sapv2  # noqa: E402,F401
import subtrees  # noqa: E402,F401
import survey  # noqa: E402,F401

__all__ = ["logs", "sapv2", "subtrees", "survey"]
