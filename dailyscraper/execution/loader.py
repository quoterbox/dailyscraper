from __future__ import annotations

import importlib
import importlib.util
import sys
from types import ModuleType

from dailyscraper.schema import ScraperPackage

PACKAGES_NAMESPACE = "dailyscraper_packages"


class PackageCodeError(ImportError):
    pass


def load_package_class[T](
    package: ScraperPackage, module: str, name: str, base: type[T]
) -> type[T]:
    """``base`` unless the package's ``<module>.py`` defines ``<name>`` deriving from it."""

    if not (package.root / f"{module}.py").is_file():
        return base
    loaded = _import(package, module)
    cls = getattr(loaded, name, None)
    if not isinstance(cls, type) or not issubclass(cls, base):
        raise PackageCodeError(
            f"{package.root / module}.py must define class {name}({base.__name__})"
        )
    return cls


def _import(package: ScraperPackage, module: str) -> ModuleType:
    parent = f"{PACKAGES_NAMESPACE}.{package.name}"
    _register_package(PACKAGES_NAMESPACE, None)
    _register_package(parent, package.root)
    try:
        return importlib.import_module(f"{parent}.{module}")
    except Exception as error:
        raise PackageCodeError(f"cannot import {package.root / module}.py: {error}") from error


def _register_package(name: str, path) -> None:
    if name in sys.modules:
        return
    spec = importlib.util.spec_from_loader(name, loader=None, is_package=True)
    module = importlib.util.module_from_spec(spec)
    module.__path__ = [str(path)] if path is not None else []
    sys.modules[name] = module
