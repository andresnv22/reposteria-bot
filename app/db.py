"""Base de datos de la repostería (SQLite). Guarda el menú, las zonas de entrega, los pedidos y la configuración."""

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DB_PATH = Path(__file__).resolve().parent.parent / "reposteria.db"

# Datos de ejemplo: un negocio ficticio para la demo. En un cliente real se cambian aquí
# (o se cargan desde un archivo JSON) y el resto del código no se toca.
NEGOCIO = "Dulce Capricho Repostería"
MONEDA = "RD$"
DIRECCION_TIENDA = "Piantini, Santo Domingo (dirección de ejemplo para la demo)"

# id, nombre, horas de anticipación, cupo diario (unidades por día), sabores, admite dedicatoria
PRODUCTOS = [
    ("bizcocho", "Bizcocho", 48, 4, ["vainilla", "chocolate", "red velvet", "zanahoria", "tres leches"], 1),
    ("cupcakes", "Cupcakes", 24, 6, ["vainilla", "chocolate", "red velvet", "limón"], 0),
    ("galletas", "Galletas decoradas", 48, 6, ["mantequilla", "chocolate"], 0),
]

# producto, tamaño, detalle, precio
OPCIONES = [
    ("bizcocho", "pequeno", "10 porciones", 1800),
    ("bizcocho", "mediano", "20 porciones", 3200),
    ("bizcocho", "grande", "35 porciones", 5000),
    ("cupcakes", "media_docena", "6 cupcakes", 650),
    ("cupcakes", "docena", "12 cupcakes", 1200),
    ("galletas", "docena", "12 galletas", 1500),
]

# id, nombre, costo del envío
ZONAS = [
    ("piantini", "Piantini", 250),
    ("naco", "Naco", 250),
    ("evaristo_morales", "Evaristo Morales", 250),
    ("bella_vista", "Bella Vista", 300),
    ("gazcue", "Gazcue", 350),
    ("zona_colonial", "Zona Colonial", 350),
    ("arroyo_hondo", "Arroyo Hondo", 400),
]

# Entregas de lunes a sábado, de 10:00 a 18:00 (el domingo no se entrega)
HORA_ENTREGA_INICIO = (10, 0)
HORA_ENTREGA_FIN = (18, 0)
DIAS_ENTREGA = {0, 1, 2, 3, 4, 5}  # 0 = lunes
# Festivos o días en que no se trabaja, en formato YYYY-MM-DD
DIAS_CERRADOS: set[str] = set()
MAX_DIAS_ADELANTE = 60
HORAS_PARA_CANCELAR = 24  # después de esto el pedido ya está en producción

# La hora del negocio, no la del servidor.
ZONA_HORARIA = ZoneInfo(os.environ.get("ZONA_HORARIA", "America/Santo_Domingo"))


def ahora() -> datetime:
    """Fecha y hora actual en la zona del negocio (sin tzinfo, para comparar con la BD)."""
    return datetime.now(ZONA_HORARIA).replace(tzinfo=None)


def conectar() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def inicializar() -> None:
    with conectar() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS productos (
                id TEXT PRIMARY KEY,
                nombre TEXT NOT NULL,
                anticipacion_horas INTEGER NOT NULL,
                cupo_diario INTEGER NOT NULL,
                sabores TEXT NOT NULL,  -- lista en JSON
                admite_dedicatoria INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS opciones (
                producto_id TEXT NOT NULL REFERENCES productos(id),
                tamano TEXT NOT NULL,
                detalle TEXT NOT NULL,
                precio INTEGER NOT NULL,
                PRIMARY KEY (producto_id, tamano)
            );
            CREATE TABLE IF NOT EXISTS zonas (
                id TEXT PRIMARY KEY,
                nombre TEXT NOT NULL,
                costo INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pedidos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente TEXT NOT NULL,
                telefono TEXT NOT NULL,
                producto_id TEXT NOT NULL REFERENCES productos(id),
                tamano TEXT NOT NULL,
                sabor TEXT NOT NULL,
                cantidad INTEGER NOT NULL,
                dedicatoria TEXT NOT NULL DEFAULT '',
                fecha TEXT NOT NULL,   -- 'YYYY-MM-DD'
                hora TEXT NOT NULL,    -- 'HH:MM'
                entrega TEXT NOT NULL, -- domicilio | recoger
                zona_id TEXT REFERENCES zonas(id),
                direccion TEXT NOT NULL DEFAULT '',
                subtotal INTEGER NOT NULL,
                envio INTEGER NOT NULL,
                total INTEGER NOT NULL,
                estado TEXT NOT NULL DEFAULT 'confirmado',  -- confirmado | cancelado
                creado TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            );
            -- Cola para la hoja de cálculo: se guarda en la misma transacción que el pedido,
            -- así un pedido nunca se pierde aunque Google Sheets esté caído.
            CREATE TABLE IF NOT EXISTS hoja_pendiente (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pedido_id INTEGER NOT NULL REFERENCES pedidos(id),
                evento TEXT NOT NULL,  -- nuevo | cancelado
                enviado INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS transferencias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telefono TEXT NOT NULL,
                motivo TEXT NOT NULL,
                creada TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                atendida INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        conn.executemany(
            "INSERT OR IGNORE INTO productos VALUES (?, ?, ?, ?, ?, ?)",
            [(i, n, h, c, json.dumps(s, ensure_ascii=False), d) for i, n, h, c, s, d in PRODUCTOS],
        )
        conn.executemany("INSERT OR IGNORE INTO opciones VALUES (?, ?, ?, ?)", OPCIONES)
        conn.executemany("INSERT OR IGNORE INTO zonas VALUES (?, ?, ?)", ZONAS)


def en_atencion_humana(telefono: str) -> bool:
    """True si este cliente fue pasado a una persona y todavía no lo atienden."""
    with conectar() as conn:
        fila = conn.execute(
            "SELECT 1 FROM transferencias WHERE telefono = ? AND atendida = 0", (telefono,)
        ).fetchone()
    return fila is not None


def resolver_transferencias(telefono: str) -> None:
    with conectar() as conn:
        conn.execute("UPDATE transferencias SET atendida = 1 WHERE telefono = ?", (telefono,))
