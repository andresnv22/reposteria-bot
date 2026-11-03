"""Cada prueba usa una base de datos nueva, un 'ahora' fijo (lunes 2 de nov, 8:00 a. m.) y un CSV temporal."""

from datetime import datetime

import pytest

from app import db, hoja


@pytest.fixture(autouse=True)
def entorno_de_prueba(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(db, "ahora", lambda: datetime(2026, 11, 2, 8, 0))
    monkeypatch.setattr(db, "DIAS_CERRADOS", set())
    monkeypatch.setattr(hoja, "RUTA_CSV", tmp_path / "pedidos.csv")
    monkeypatch.delenv("GOOGLE_SHEET_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CREDENTIALS_FILE", raising=False)
    db.inicializar()
