"""Estado de la paginación de la tabla del panel (spec 018). Sin Streamlit: se guarda en
`st.session_state` y se prueba aparte.

La API pagina por cursor y solo sabe ir hacia adelante, así que para volver atrás se guarda
el cursor de cada página visitada.
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass, field


@dataclass
class Pager:
    filters: Hashable | None = None
    cursors: list[str | None] = field(default_factory=lambda: [None])

    def sync(self, filters: Hashable) -> None:
        """Con filtros nuevos, la pila de cursores ya no sirve: vuelve a la página 1."""
        if filters != self.filters:
            self.filters = filters
            self.reset()

    @property
    def cursor(self) -> str | None:
        return self.cursors[-1]

    @property
    def page_number(self) -> int:
        return len(self.cursors)

    @property
    def has_previous(self) -> bool:
        return len(self.cursors) > 1

    def next(self, next_cursor: str | None) -> None:
        if next_cursor is not None:
            self.cursors.append(next_cursor)

    def previous(self) -> None:
        if self.has_previous:
            self.cursors.pop()

    def reset(self) -> None:
        self.cursors = [None]
