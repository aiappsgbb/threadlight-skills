"""Package marker for the threadlight-governed-actions test suite.

Without this marker pytest's default ``prepend`` import mode names every file
here by bare basename, so ``tests/test_scaffold.py`` claims the top-level module
``test_scaffold`` -- which ``skills/threadlight-consumption-iq/tests/test_scaffold.py``
also claims. Whichever suite is imported second dies with "import file
mismatch". The marker moves this suite behind a package boundary
(``tests.test_scaffold``), which is the same convention
``skills/threadlight-local-test/references/quickstart/tests/__init__.py``
already uses.

That convention has one repo-wide catch: a package boundary built from
directory names can only ever be called ``tests``, so two marked suites collide
on the package itself instead of on the module. ``__path__`` is therefore kept
sibling-aware -- it re-reads ``sys.path`` on every submodule lookup, so the
quickstart suite (whose root pytest inserts later in the run) still resolves its
own ``tests.*`` modules through this package.
"""
import os
import sys


class _SiblingAwarePath(list):
    """``__path__`` that also exposes ``tests`` packages added to sys.path later."""

    def __iter__(self):
        entries = list(list.__iter__(self))
        for entry in sys.path:
            candidate = os.path.join(entry, "tests")
            if candidate not in entries and os.path.isdir(candidate):
                entries.append(candidate)
        return iter(entries)


__path__ = _SiblingAwarePath(__path__)
