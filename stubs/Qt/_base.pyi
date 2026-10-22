from typing import Any

class _Meta(type):
    def __getattr__(cls, name: str) -> Any: ...
