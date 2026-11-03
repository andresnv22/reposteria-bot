"""Herramientas que el agente puede usar.

Cada herramienta tiene dos partes:
  1. La definición (TOOLS): lo que la IA ve, nombre, descripción y parámetros.
  2. La función de Python: lo que realmente se ejecuta cuando la IA la pide.

La IA nunca toca la base de datos directo. Solo pide "ejecuta crear_pedido con
estos datos" y nuestro código decide si se puede o no. Así el bot no puede
inventar precios, sabores ni fechas imposibles, ni pasarse del cupo del día.

El teléfono del cliente nunca lo escribe la IA: lo pone el sistema (viene de
WhatsApp). Así nadie puede ver ni cancelar pedidos ajenos.
"""

import json
import logging
import re
import unicodedata
from datetime import date, datetime, timedelta

from . import db, hoja

log = logging.getLogger("reposteria")

FORMATO = "%Y-%m-%d %H:%M"
MAX_DEDICATORIA = 60
MAX_CANTIDAD = 10


# ---------- Validaciones ----------
# Los mensajes de error están escritos para que la IA entienda qué corregir.

def _sin_acentos(texto: str) -> str:
    base = unicodedata.normalize("NFD", texto)
    return "".join(c for c in base if unicodedata.category(c) != "Mn").lower().strip()


def normalizar_hora(hora: str) -> str | None:
    """Convierte '3pm', '3:30 p.m.', '9:00', '15:00:00' en 'HH:MM'. None si no se entiende."""
    texto = hora.strip().lower().replace(".", "").replace(" ", "")
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(?::\d{2})?(am|pm)?", texto)
    if not m:
        return None
    h, minutos, sufijo = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if sufijo == "pm" and h < 12:
        h += 12
    elif sufijo == "am" and h == 12:
        h = 0
    if h > 23 or minutos > 59:
        return None
    return f"{h:02d}:{minutos:02d}"


def _validar_dia(fecha: str) -> date | dict:
    """Devuelve la fecha si se puede entregar ese día, o un dict con el error."""
    try:
        dia = datetime.strptime(str(fecha).strip(), "%Y-%m-%d").date()
    except ValueError:
        return {"error": f"Fecha '{fecha}' inválida. Usa el formato YYYY-MM-DD."}
    hoy = db.ahora().date()
    if dia < hoy:
        return {"error": f"La fecha {dia} ya pasó. Hoy es {hoy}."}
    if dia > hoy + timedelta(days=db.MAX_DIAS_ADELANTE):
        return {"error": f"Solo se toman pedidos hasta {db.MAX_DIAS_ADELANTE} días adelante."}
    if dia.weekday() not in db.DIAS_ENTREGA:
        return {"error": f"El {dia} es domingo y no hacemos entregas. Entregamos de lunes a sábado."}
    if dia.isoformat() in db.DIAS_CERRADOS:
        return {"error": f"El {dia} estamos cerrados (feriado)."}
    return dia


def _validar_hora_entrega(hora: str) -> str | dict:
    hora_ok = normalizar_hora(hora)
    if hora_ok is None:
        return {"error": f"Hora '{hora}' inválida. Usa el formato HH:MM, ej. 15:30."}
    h, m = int(hora_ok[:2]), int(hora_ok[3:])
    if m not in (0, 30) or not (db.HORA_ENTREGA_INICIO <= (h, m) <= db.HORA_ENTREGA_FIN):
        return {"error": f"Entregamos entre {db.HORA_ENTREGA_INICIO[0]}:00 y {db.HORA_ENTREGA_FIN[0]}:00, "
                         "en punto o y media. Pide otra hora."}
    return hora_ok


def _transaccion(funcion) -> dict:
    """Corre `funcion(conn)` en una transacción con escritura bloqueada (BEGIN IMMEDIATE).

    Si dos clientes piden lo último que queda al mismo tiempo, solo uno lo obtiene.
    Si la función devuelve un error, no se guarda nada.
    """
    conn = db.conectar()
    conn.isolation_level = None  # manejamos la transacción a mano
    try:
        conn.execute("BEGIN IMMEDIATE")
        resultado = funcion(conn)
        conn.execute("ROLLBACK" if "error" in resultado else "COMMIT")
        return resultado
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def _enviar_a_hoja() -> None:
    """La hoja es secundaria: si falla, el pedido ya está guardado y se reintenta después."""
    try:
        hoja.sincronizar()
    except Exception:
        log.exception("Falló la sincronización con la hoja")


def _cupo_usado(conn, producto_id: str, fecha: str) -> int:
    return conn.execute(
        "SELECT COALESCE(SUM(cantidad), 0) FROM pedidos WHERE producto_id = ? AND fecha = ? AND estado = 'confirmado'",
        (producto_id, fecha),
    ).fetchone()[0]


# ---------- Funciones ----------

def ver_menu() -> dict:
    with db.conectar() as conn:
        productos = []
        for p in conn.execute("SELECT * FROM productos ORDER BY rowid").fetchall():
            tamanos = conn.execute(
                "SELECT tamano, detalle, precio FROM opciones WHERE producto_id = ? ORDER BY precio", (p["id"],)
            ).fetchall()
            productos.append({
                "id": p["id"], "nombre": p["nombre"], "sabores": json.loads(p["sabores"]),
                "tamanos": [dict(t) for t in tamanos], "horas_de_anticipacion": p["anticipacion_horas"],
                "admite_dedicatoria": bool(p["admite_dedicatoria"]),
            })
    return {"negocio": db.NEGOCIO, "moneda": db.MONEDA, "productos": productos}


def ver_entregas() -> dict:
    with db.conectar() as conn:
        zonas = conn.execute("SELECT id, nombre, costo FROM zonas ORDER BY costo, nombre").fetchall()
    return {
        "moneda": db.MONEDA,
        "horario": f"lunes a sábado, de {db.HORA_ENTREGA_INICIO[0]}:00 a {db.HORA_ENTREGA_FIN[0]}:00 (domingo no hay entregas)",
        "domicilio": [dict(z) for z in zonas],
        "recoger_en_tienda": {"direccion": db.DIRECCION_TIENDA, "costo": 0},
        "nota": "Solo entregamos a domicilio en estas zonas; para otras, el cliente puede recoger en la tienda.",
    }


def ver_cupo(fecha: str, producto_id: str) -> dict:
    dia = _validar_dia(fecha)
    if isinstance(dia, dict):
        return dia
    with db.conectar() as conn:
        producto = conn.execute("SELECT * FROM productos WHERE id = ?", (producto_id,)).fetchone()
        if producto is None:
            return {"error": f"No existe el producto '{producto_id}'."}
        usado = _cupo_usado(conn, producto_id, dia.isoformat())
    minimo = db.ahora() + timedelta(hours=producto["anticipacion_horas"])
    respuesta = {
        "fecha": dia.isoformat(), "producto": producto["nombre"],
        "cupos_disponibles": max(0, producto["cupo_diario"] - usado),
        "entrega_minima": minimo.strftime(FORMATO),
    }
    if respuesta["cupos_disponibles"] == 0:
        respuesta["nota"] = "Ese día ya no quedan cupos. Ofrece otro día."
    return respuesta


def crear_pedido(cliente: str, producto_id: str, tamano: str, sabor: str, cantidad: int, dedicatoria: str,
                 fecha: str, hora: str, entrega: str, zona_id: str, direccion: str, *, telefono: str) -> dict:
    cliente = " ".join(str(cliente).split())
    if len(cliente) < 3 or len(cliente) > 80:
        return {"error": "Falta el nombre completo del cliente. Pídeselo."}
    try:
        cantidad = int(cantidad)
    except (TypeError, ValueError):
        return {"error": "La cantidad debe ser un número entero."}
    if not 1 <= cantidad <= MAX_CANTIDAD:
        return {"error": f"La cantidad debe estar entre 1 y {MAX_CANTIDAD}. Para pedidos más grandes, pasa con una persona."}
    dedicatoria = " ".join(str(dedicatoria or "").split())
    if len(dedicatoria) > MAX_DEDICATORIA:
        return {"error": f"La dedicatoria puede tener máximo {MAX_DEDICATORIA} caracteres."}
    hora_ok = _validar_hora_entrega(hora)
    if isinstance(hora_ok, dict):
        return hora_ok
    dia = _validar_dia(fecha)
    if isinstance(dia, dict):
        return dia
    if entrega not in ("domicilio", "recoger"):
        return {"error": "El tipo de entrega debe ser 'domicilio' o 'recoger'."}
    direccion = " ".join(str(direccion or "").split())
    if entrega == "domicilio" and len(direccion) < 5:
        return {"error": "Para domicilio falta la dirección completa. Pídesela al cliente."}

    fecha_txt = dia.isoformat()

    def crear(conn) -> dict:
        producto = conn.execute("SELECT * FROM productos WHERE id = ?", (producto_id,)).fetchone()
        if producto is None:
            return {"error": f"No existe el producto '{producto_id}'. Usa ver_menu."}
        opcion = conn.execute(
            "SELECT * FROM opciones WHERE producto_id = ? AND tamano = ?", (producto_id, tamano)
        ).fetchone()
        if opcion is None:
            validos = [t["tamano"] for t in conn.execute("SELECT tamano FROM opciones WHERE producto_id = ?", (producto_id,))]
            return {"error": f"'{tamano}' no es un tamaño de {producto['nombre']}.", "tamanos_validos": validos}
        sabores = json.loads(producto["sabores"])
        sabor_ok = next((s for s in sabores if _sin_acentos(s) == _sin_acentos(str(sabor))), None)
        if sabor_ok is None:
            return {"error": f"'{sabor}' no es un sabor de {producto['nombre']}.", "sabores_validos": sabores}
        if dedicatoria and not producto["admite_dedicatoria"]:
            return {"error": f"{producto['nombre']} no lleva dedicatoria."}

        entrega_dt = datetime.strptime(f"{fecha_txt} {hora_ok}", FORMATO)
        minimo = db.ahora() + timedelta(hours=producto["anticipacion_horas"])
        if entrega_dt < minimo:
            return {"error": f"{producto['nombre']} se pide con {producto['anticipacion_horas']} horas de anticipación.",
                    "entrega_minima": minimo.strftime(FORMATO)}

        zona = None
        if entrega == "domicilio":
            zona = conn.execute("SELECT * FROM zonas WHERE id = ?", (zona_id,)).fetchone()
            if zona is None:
                return {"error": "No entregamos en esa zona. Usa ver_entregas y ofrece recoger en la tienda."}
        envio = zona["costo"] if zona else 0
        subtotal = opcion["precio"] * cantidad

        # Si este mismo pedido ya existe (por ejemplo, el mensaje se reintentó), no lo duplicamos
        existente = conn.execute(
            """SELECT id, total FROM pedidos WHERE telefono = ? AND producto_id = ? AND tamano = ? AND sabor = ?
               AND cantidad = ? AND fecha = ? AND hora = ? AND entrega = ? AND estado = 'confirmado'""",
            (telefono, producto_id, tamano, sabor_ok, cantidad, fecha_txt, hora_ok, entrega),
        ).fetchone()
        if existente:
            return {"ok": True, "pedido_id": existente["id"], "total": existente["total"], "moneda": db.MONEDA,
                    "nota": "Ya estaba registrado."}

        libres = producto["cupo_diario"] - _cupo_usado(conn, producto_id, fecha_txt)
        if cantidad > libres:
            return {"error": f"Ese día solo quedan {max(0, libres)} cupos de {producto['nombre']}.",
                    "cupos_disponibles": max(0, libres)}

        cursor = conn.execute(
            """INSERT INTO pedidos (cliente, telefono, producto_id, tamano, sabor, cantidad, dedicatoria, fecha, hora,
                                    entrega, zona_id, direccion, subtotal, envio, total)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cliente, telefono, producto_id, tamano, sabor_ok, cantidad, dedicatoria, fecha_txt, hora_ok, entrega,
             zona["id"] if zona else None, direccion if entrega == "domicilio" else "", subtotal, envio, subtotal + envio),
        )
        conn.execute("INSERT INTO hoja_pendiente (pedido_id, evento) VALUES (?, 'nuevo')", (cursor.lastrowid,))
        return {
            "ok": True, "pedido_id": cursor.lastrowid, "cliente": cliente, "producto": producto["nombre"],
            "tamano": opcion["detalle"], "sabor": sabor_ok, "cantidad": cantidad, "dedicatoria": dedicatoria,
            "fecha": fecha_txt, "hora": hora_ok, "entrega": entrega,
            "zona": zona["nombre"] if zona else None, "subtotal": subtotal, "envio": envio,
            "total": subtotal + envio, "moneda": db.MONEDA,
        }

    resultado = _transaccion(crear)
    if resultado.get("ok"):
        _enviar_a_hoja()
    return resultado


def buscar_pedidos(*, telefono: str) -> dict:
    with db.conectar() as conn:
        filas = conn.execute(
            """SELECT p.id, pr.nombre AS producto, o.detalle AS tamano, p.sabor, p.cantidad, p.fecha, p.hora,
                      p.entrega, p.total
               FROM pedidos p JOIN productos pr ON pr.id = p.producto_id
               JOIN opciones o ON o.producto_id = p.producto_id AND o.tamano = p.tamano
               WHERE p.telefono = ? AND p.estado = 'confirmado' AND p.fecha >= ?
               ORDER BY p.fecha, p.hora""",
            (telefono, db.ahora().date().isoformat()),
        ).fetchall()
    return {"pedidos": [dict(f) for f in filas], "moneda": db.MONEDA}


def cancelar_pedido(pedido_id: int, *, telefono: str) -> dict:
    try:
        pedido_id = int(pedido_id)
    except (TypeError, ValueError):
        return {"error": "Número de pedido inválido. Usa buscar_pedidos para ver los números."}

    def cancelar(conn) -> dict:
        # Solo se cancelan pedidos de este mismo teléfono
        pedido = conn.execute(
            "SELECT * FROM pedidos WHERE id = ? AND telefono = ? AND estado = 'confirmado'", (pedido_id, telefono)
        ).fetchone()
        if pedido is None:
            return {"error": "Este cliente no tiene un pedido activo con ese número. Usa buscar_pedidos."}
        entrega_dt = datetime.strptime(f"{pedido['fecha']} {pedido['hora']}", FORMATO)
        if entrega_dt - db.ahora() < timedelta(hours=db.HORAS_PARA_CANCELAR):
            return {"error": f"Faltan menos de {db.HORAS_PARA_CANCELAR} horas para la entrega y el pedido ya está en "
                             "producción. Ofrece pasar al cliente con una persona (transferir_a_humano)."}
        conn.execute("UPDATE pedidos SET estado = 'cancelado' WHERE id = ?", (pedido_id,))
        conn.execute("INSERT INTO hoja_pendiente (pedido_id, evento) VALUES (?, 'cancelado')", (pedido_id,))
        return {"ok": True, "pedido_id": pedido_id, "estado": "cancelado"}

    resultado = _transaccion(cancelar)
    if resultado.get("ok"):
        _enviar_a_hoja()
    return resultado


def transferir_a_humano(motivo: str, *, telefono: str) -> dict:
    motivo = " ".join(str(motivo).split())
    if len(motivo) < 3:
        return {"error": "Falta el motivo de la transferencia."}
    with db.conectar() as conn:
        if not conn.execute("SELECT 1 FROM transferencias WHERE telefono = ? AND atendida = 0", (telefono,)).fetchone():
            conn.execute("INSERT INTO transferencias (telefono, motivo) VALUES (?, ?)", (telefono, motivo[:300]))
    return {"ok": True, "nota": "Una persona del equipo le escribirá a este cliente en horario de atención."}


FUNCIONES = {
    "ver_menu": ver_menu,
    "ver_entregas": ver_entregas,
    "ver_cupo": ver_cupo,
    "crear_pedido": crear_pedido,
    "buscar_pedidos": buscar_pedidos,
    "cancelar_pedido": cancelar_pedido,
    "transferir_a_humano": transferir_a_humano,
}
# Estas reciben el teléfono del cliente desde el sistema, nunca desde la IA
USAN_TELEFONO = {"crear_pedido", "buscar_pedidos", "cancelar_pedido", "transferir_a_humano"}


# ---------- Definiciones para la IA ----------

def _tool(name: str, description: str, properties: dict) -> dict:
    return {
        "name": name,
        "description": description,
        "strict": True,  # garantiza que Claude mande exactamente estos parámetros
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


_producto = {"type": "string", "enum": [p[0] for p in db.PRODUCTOS], "description": "Id del producto."}
_fecha = {"type": "string", "description": "Fecha de entrega en formato YYYY-MM-DD."}

TOOLS = [
    _tool("ver_menu", "Lista los productos con sabores, tamaños, precios y anticipación necesaria. Úsala antes de dar precios.", {}),
    _tool("ver_entregas", "Zonas de entrega a domicilio con su costo, horario de entregas y dirección para recoger en tienda.", {}),
    _tool(
        "ver_cupo",
        "Dice cuántos cupos quedan de un producto en una fecha y cuál es la primera entrega posible. Úsala antes de prometer una fecha.",
        {"fecha": _fecha, "producto_id": _producto},
    ),
    _tool(
        "crear_pedido",
        "Registra el pedido del cliente de esta conversación. Solo úsala cuando el cliente ya confirmó TODOS los datos y el total.",
        {
            "cliente": {"type": "string", "description": "Nombre completo del cliente."},
            "producto_id": _producto,
            "tamano": {
                "type": "string",
                "enum": sorted({o[1] for o in db.OPCIONES}),
                "description": "Id del tamaño, válido para ese producto (ver_menu).",
            },
            "sabor": {"type": "string", "description": "Sabor, uno de los que ofrece ese producto."},
            "cantidad": {"type": "integer", "description": "Cuántas unidades (1 si es un solo bizcocho)."},
            "dedicatoria": {"type": "string", "description": "Texto para el bizcocho. Cadena vacía si no lleva."},
            "fecha": _fecha,
            "hora": {"type": "string", "description": "Hora de entrega en formato HH:MM (24 horas), ej. 15:30."},
            "entrega": {"type": "string", "enum": ["domicilio", "recoger"], "description": "Domicilio o recoger en tienda."},
            "zona_id": {
                "type": "string",
                "enum": [z[0] for z in db.ZONAS] + ["ninguna"],
                "description": "Zona de entrega; 'ninguna' si va a recoger.",
            },
            "direccion": {"type": "string", "description": "Dirección completa para domicilio. Cadena vacía si va a recoger."},
        },
    ),
    _tool("buscar_pedidos", "Busca los próximos pedidos confirmados del cliente de esta conversación.", {}),
    _tool(
        "cancelar_pedido",
        "Cancela un pedido del cliente de esta conversación. Confirma con el cliente antes de usarla.",
        {"pedido_id": {"type": "integer", "description": "Número del pedido (de buscar_pedidos)."}},
    ),
    _tool(
        "transferir_a_humano",
        "Pasa la conversación a una persona del equipo. Úsala para: diseños personalizados o pedidos especiales, "
        "alergias, quejas, pedidos de más de 10 unidades, cancelaciones tardías o cuando no puedas resolver.",
        {"motivo": {"type": "string", "description": "Resumen corto de qué necesita el cliente."}},
    ),
]
