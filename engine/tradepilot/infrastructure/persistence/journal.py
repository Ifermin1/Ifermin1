"""Diario forense: cada mensaje que entra del bróker y cada orden que sale, en JSONL por día.
Con esto un incidente se reproduce exactamente y se convierte en prueba automática."""
import json
from datetime import datetime
from pathlib import Path

from loguru import logger


class Journal:
    def __init__(self, directory: str | None) -> None:
        self.dir = Path(directory) if directory else None
        self._fh = None
        self._day = None
        if self.dir:
            self.dir.mkdir(parents=True, exist_ok=True)

    def _file(self):
        if not self.dir:
            return None
        day = datetime.now().strftime("%Y-%m-%d")
        if self._fh is None or day != self._day:
            if self._fh:
                self._fh.close()
            self._day = day
            self._fh = open(self.dir / f"{day}.jsonl", "a", encoding="utf-8")
        return self._fh

    def write(self, direction: str, payload) -> None:
        """direction: 'in' (del bróker), 'out' (orden enviada), 'cmd' (comando al bróker), 'note'."""
        fh = self._file()
        if fh is None:
            return
        try:
            fh.write(json.dumps({"t": datetime.now().isoformat(timespec="milliseconds"), "dir": direction, "msg": payload},
                                default=str, ensure_ascii=False) + "\n")
            fh.flush()
        except Exception as exc:
            logger.warning(f"journal: {exc}")

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None
