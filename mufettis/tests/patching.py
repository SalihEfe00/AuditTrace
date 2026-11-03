# -*- coding: utf-8 -*-
"""
Testler için modül düzeyi ad değiştirme (override) yardımcısı.

Proje genelinde `from x import *` kullanıldığından bir fonksiyonun (ör.
`split_legal_chunks`) birçok modülde ayrı bir referansı bulunur; tek bir modülde
`mock.patch` yapmak çağrının geçtiği modülü kaçırabilir. Eskiden bu sorun
production kodunda `config.resolve_dynamic` ile çalışma anında test modülleri
taranarak çözülüyordu. Artık production kodu testleri bilmez; aynı etki burada,
yalnızca test tarafında sağlanır.

Kullanım (eski `globals()[...]` kalıbıyla birebir uyumlu):

    original = module_overrides["split_legal_chunks"]
    try:
        module_overrides["split_legal_chunks"] = fake
        ...
    finally:
        module_overrides["split_legal_chunks"] = original

Atama, o adın orijinal nesnesini (ya da etkin sahte değeri) tutan tüm proje
modüllerinde değeri değiştirir; orijinal geri atanınca hepsi geri yüklenir.
Üçüncü taraf modüllere (ör. pypdf) dokunulmaz.
"""
from __future__ import annotations

import sys
from types import ModuleType
from typing import Any, Dict, Iterator

_PROJECT_MODULE_PREFIXES = ("mufettis", "tests", "app", "__main__")
_MISSING = object()


def _project_modules() -> Iterator[ModuleType]:
    for name, module in list(sys.modules.items()):
        if module is None:
            continue
        if name == "app" or name.split(".")[0] in _PROJECT_MODULE_PREFIXES:
            yield module


class ModuleGlobalOverrides:
    """`globals()` benzeri: okuma geçerli değeri, yazma tüm proje kopyalarını günceller."""

    def __init__(self) -> None:
        self._originals: Dict[str, Any] = {}
        self._current: Dict[str, Any] = {}

    def _find_original(self, name: str) -> Any:
        for module in _project_modules():
            value = module.__dict__.get(name, _MISSING)
            if value is not _MISSING:
                return value
        raise KeyError(name)

    def __getitem__(self, name: str) -> Any:
        if name in self._current:
            return self._current[name]
        return self._originals.get(name) if name in self._originals else self._find_original(name)

    def __setitem__(self, name: str, value: Any) -> None:
        original = self._originals.setdefault(name, self._find_original(name))
        active = self._current.get(name, _MISSING)
        for module in _project_modules():
            existing = module.__dict__.get(name, _MISSING)
            if existing is original or (active is not _MISSING and existing is active):
                setattr(module, name, value)
        if value is original:
            self._current.pop(name, None)
        else:
            self._current[name] = value

    def pop(self, name: str, default: Any = None) -> Any:
        """Etkin override'ı kaldırır (orijinali geri yükler)."""
        if name in self._current:
            current = self._current[name]
            self[name] = self._originals[name]
            return current
        return default

    def get(self, name: str, default: Any = None) -> Any:
        try:
            return self[name]
        except KeyError:
            return default


module_overrides = ModuleGlobalOverrides()

__all__ = ["ModuleGlobalOverrides", "module_overrides"]
