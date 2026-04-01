"""
Python 3.10+ compatibility shim.

collections.Iterable (and friends) were removed in Python 3.10.
This sitecustomize.py re-adds them so legacy code that imports from
``collections`` directly continues to work.

Usage:
    Place this file's parent directory at the FRONT of PYTHONPATH so that
    it runs before any application imports.
"""
import sys
import collections
import collections.abc

_COMPAT_NAMES = [
    "Awaitable", "Coroutine", "AsyncIterable", "AsyncIterator",
    "AsyncGenerator", "Hashable", "Iterable", "Iterator",
    "Generator", "Reversible", "Container", "Collection",
    "Callable", "Set", "MutableSet", "Mapping", "MutableMapping",
    "MappingView", "KeysView", "ItemsView", "ValuesView",
    "Sequence", "MutableSequence", "ByteString",
]

if sys.version_info >= (3, 10):
    for _name in _COMPAT_NAMES:
        if not hasattr(collections, _name) and hasattr(collections.abc, _name):
            setattr(collections, _name, getattr(collections.abc, _name))
