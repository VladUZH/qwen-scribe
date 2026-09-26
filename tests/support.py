"""Helpers shared by the test modules."""

import sys


def install_fake_modules(test, fakes):
    """Put fake modules in sys.modules and restore exactly those keys after.

    Deliberately not mock.patch.dict on the whole of sys.modules: that
    restores the dict wholesale on exit, which also evicts any real module
    first imported inside the patch. Re-importing a C extension then fails
    with "cannot load module more than once per process" — which has now
    happened twice, once for mlx.core and once for numpy.
    """
    previous = {name: sys.modules.get(name) for name in fakes}

    def restore():
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    test.addCleanup(restore)
    sys.modules.update(fakes)
