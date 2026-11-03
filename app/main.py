"""Servidor web: expone el agente por HTTP y sirve la página del demo."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import db, hoja
from .agent import conversaciones, falta_clave, responder

RAIZ = Path(__file__).resolve().parent.parent
STATIC = RAIZ / "static"

# Los errores técnicos van a reposteria.log (máximo 3 archivos de 1 MB), nunca al cliente
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(RAIZ / "reposteria.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"),
    ],
)

app = FastAPI(title="Repostería Bot")
db.inicializar()


class Mensaje(BaseModel):
    telefono: str = Field(min_length=5, max_length=30)
    texto: str = Field(max_length=5000)


@app.get("/")
def inicio():
    return FileResponse(STATIC / "index.html")


@app.post("/chat")
def chat(msg: Mensaje):
    # Esto es un error de configuración del dueño, no del cliente
    variable = falta_clave()
    if variable:
        raise HTTPException(500, f"Falta {variable} en el archivo .env")
    # responder() nunca lanza errores: si la IA falla, devuelve un mensaje amable
    return responder(msg.telefono.strip(), msg.texto)


@app.post("/reiniciar/{telefono}")
def reiniciar(telefono: str):
    conversaciones.pop(telefono, None)
    db.resolver_transferencias(telefono)  # en la demo, "reiniciar" también devuelve el chat al bot
    return {"ok": True}


@app.get("/pedidos")
def pedidos():
    """Vista del negocio: todos los pedidos. (En producción esto necesita contraseña.)"""
    with db.conectar() as conn:
        filas = conn.execute(
            """SELECT p.id, p.cliente, p.telefono, pr.nombre AS producto, o.detalle AS tamano, p.sabor, p.cantidad,
                      p.fecha, p.hora, p.entrega, p.total, p.estado,
                      NOT EXISTS (SELECT 1 FROM hoja_pendiente h WHERE h.pedido_id = p.id AND h.enviado = 0) AS en_hoja
               FROM pedidos p JOIN productos pr ON pr.id = p.producto_id
               JOIN opciones o ON o.producto_id = p.producto_id AND o.tamano = p.tamano
               ORDER BY p.id"""
        ).fetchall()
    return {"moneda": db.MONEDA, "pedidos": [dict(f) for f in filas]}


@app.get("/transferencias")
def transferencias():
    with db.conectar() as conn:
        filas = conn.execute(
            "SELECT id, telefono, motivo, creada, atendida FROM transferencias ORDER BY id DESC"
        ).fetchall()
    return [dict(f) for f in filas]


@app.post("/sincronizar")
def sincronizar():
    """Reintenta enviar a la hoja de cálculo lo que haya quedado pendiente."""
    enviados = hoja.sincronizar()
    return {"enviados": enviados, "pendientes": hoja.pendientes()}
