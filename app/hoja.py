"""Registro de pedidos en una hoja de cálculo.

Por defecto escribe en `pedidos.csv` (se abre con Excel o se importa a Google Sheets).
Si en .env están GOOGLE_SHEET_ID y GOOGLE_CREDENTIALS_FILE, escribe directo en Google Sheets.

El pedido ya está guardado en la base de datos antes de llegar aquí. Si la hoja falla
(sin internet, permisos, cuota), el evento queda pendiente y se reintenta en el siguiente
pedido o con POST /sincronizar. El cliente nunca se entera y no se pierde ningún pedido.
"""

import csv
import logging
import os
import threading
from pathlib import Path

from . import db

log = logging.getLogger("reposteria")

RUTA_CSV = Path(__file__).resolve().parent.parent / "pedidos.csv"
COLUMNAS = [
    "pedido", "evento", "cliente", "telefono", "producto", "tamaño", "sabor", "cantidad",
    "dedicatoria", "fecha entrega", "hora", "entrega", "zona", "dirección", "total", "estado",
]
_candado = threading.Lock()


class DestinoCsv:
    def __init__(self, ruta: Path | None = None):
        self.ruta = ruta or RUTA_CSV

    def agregar(self, filas: list[list]) -> None:
        nuevo = not self.ruta.exists()
        # utf-8-sig para que Excel muestre bien las tildes
        with open(self.ruta, "a", newline="", encoding="utf-8-sig") as f:
            escritor = csv.writer(f)
            if nuevo:
                escritor.writerow(COLUMNAS)
            escritor.writerows(filas)


class DestinoSheets:
    """Google Sheets con gspread. Requiere: uv sync --extra sheets, y compartir la hoja con el correo de la cuenta de servicio."""

    def __init__(self, hoja_id: str, credenciales: str):
        self.hoja_id = hoja_id
        self.credenciales = credenciales

    def agregar(self, filas: list[list]) -> None:
        import gspread

        hoja = gspread.service_account(filename=self.credenciales).open_by_key(self.hoja_id).sheet1
        if not hoja.get_all_values():
            hoja.append_row(COLUMNAS)
        hoja.append_rows(filas, value_input_option="USER_ENTERED")


def destino_configurado():
    hoja_id, credenciales = os.environ.get("GOOGLE_SHEET_ID"), os.environ.get("GOOGLE_CREDENTIALS_FILE")
    return DestinoSheets(hoja_id, credenciales) if hoja_id and credenciales else DestinoCsv()


def _fila(p) -> list:
    return [
        p["pedido_id"], p["evento"], p["cliente"], p["telefono"], p["producto"], p["tamano"], p["sabor"],
        p["cantidad"], p["dedicatoria"], p["fecha"], p["hora"], p["entrega"], p["zona"] or "",
        p["direccion"], p["total"], p["estado"],
    ]


def pendientes() -> int:
    with db.conectar() as conn:
        return conn.execute("SELECT COUNT(*) FROM hoja_pendiente WHERE enviado = 0").fetchone()[0]


def sincronizar(destino=None) -> int:
    """Envía a la hoja los eventos pendientes. Devuelve cuántos envió (0 si no había o si falló)."""
    with _candado:
        with db.conectar() as conn:
            filas = conn.execute(
                """SELECT h.id AS evento_id, h.evento, p.id AS pedido_id, p.cliente, p.telefono,
                          pr.nombre AS producto, o.detalle AS tamano, p.sabor, p.cantidad, p.dedicatoria,
                          p.fecha, p.hora, p.entrega, z.nombre AS zona, p.direccion, p.total, p.estado
                   FROM hoja_pendiente h
                   JOIN pedidos p ON p.id = h.pedido_id
                   JOIN productos pr ON pr.id = p.producto_id
                   JOIN opciones o ON o.producto_id = p.producto_id AND o.tamano = p.tamano
                   LEFT JOIN zonas z ON z.id = p.zona_id
                   WHERE h.enviado = 0
                   ORDER BY h.id"""
            ).fetchall()
        if not filas:
            return 0
        try:
            (destino or destino_configurado()).agregar([_fila(f) for f in filas])
        except Exception:
            log.exception("No se pudo escribir en la hoja de cálculo; %d eventos quedan pendientes", len(filas))
            return 0
        with db.conectar() as conn:
            conn.executemany("UPDATE hoja_pendiente SET enviado = 1 WHERE id = ?", [(f["evento_id"],) for f in filas])
        return len(filas)
