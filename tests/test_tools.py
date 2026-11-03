"""Pruebas de las reglas del negocio. No usan la IA: corren gratis y en segundos.

Correr con:  uv run pytest
"""

import csv
import threading
from datetime import datetime

import pytest

from app import db, hoja, tools
from app.agent import ejecutar_herramienta

MARTES = "2026-11-03"
MIERCOLES = "2026-11-04"
JUEVES = "2026-11-05"
SABADO = "2026-11-07"
DOMINGO = "2026-11-08"
TEL = "+1 809 111 2222"
OTRO_TEL = "+1 809 999 8888"


def pedir(*, producto="bizcocho", tamano="mediano", sabor="chocolate", cantidad=1, dedicatoria="Feliz cumple Ana",
          fecha=JUEVES, hora="15:00", entrega="domicilio", zona="piantini", direccion="Calle 5 #12, Piantini",
          cliente="Ana Pérez", telefono=TEL):
    return tools.crear_pedido(cliente, producto, tamano, sabor, cantidad, dedicatoria, fecha, hora, entrega,
                              zona, direccion, telefono=telefono)


def pedido_de(i, **kw):
    return pedir(cliente=f"Cliente {i}", telefono=f"+1 809 000 00{i:02d}", **kw)


def filas_csv():
    with open(hoja.RUTA_CSV, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# ---------- Horas y fechas como las escribe la gente ----------

@pytest.mark.parametrize("entrada,esperado", [
    ("10:00", "10:00"), ("9", "09:00"), ("3pm", "15:00"), ("3 pm", "15:00"), ("3:30 p.m.", "15:30"),
    ("12pm", "12:00"), ("15:00:00", "15:00"),
])
def test_normaliza_horas(entrada, esperado):
    assert tools.normalizar_hora(entrada) == esperado


@pytest.mark.parametrize("entrada", ["", "mañana", "25:00", "10:75", "tres de la tarde"])
def test_rechaza_horas_invalidas(entrada):
    assert tools.normalizar_hora(entrada) is None


def test_acepta_hora_en_formato_humano():
    assert pedir(hora="3pm")["hora"] == "15:00"


@pytest.mark.parametrize("hora", ["09:30", "18:30", "20:00", "10:15", "00:00"])
def test_solo_se_entrega_en_el_horario(hora):
    assert "error" in pedir(hora=hora)


@pytest.mark.parametrize("hora", ["10:00", "10:30", "18:00"])
def test_horas_limite_validas(hora):
    assert pedir(hora=hora)["ok"]


@pytest.mark.parametrize("fecha,palabra", [
    ("2026-10-30", "ya pasó"),
    (DOMINGO, "domingo"),
    ("2027-06-01", "días adelante"),
    ("el jueves", "inválida"),
])
def test_dias_no_validos(fecha, palabra):
    assert palabra in pedir(fecha=fecha)["error"]
    assert palabra in tools.ver_cupo(fecha, "bizcocho")["error"]


def test_feriado(monkeypatch):
    monkeypatch.setattr(db, "DIAS_CERRADOS", {JUEVES})
    assert "feriado" in pedir()["error"]


def test_acepta_fecha_sin_ceros():
    assert pedir(fecha="2026-11-5")["ok"]


# ---------- Menú y precios: vienen de la base, no de la IA ----------

def test_menu_tiene_precios_y_sabores():
    menu = tools.ver_menu()
    assert {p["id"] for p in menu["productos"]} == {"bizcocho", "cupcakes", "galletas"}
    bizcocho = next(p for p in menu["productos"] if p["id"] == "bizcocho")
    assert "red velvet" in bizcocho["sabores"] and bizcocho["tamanos"][0]["precio"] == 1800


def test_entregas_listan_zonas_y_horario():
    entregas = tools.ver_entregas()
    assert {z["id"] for z in entregas["domicilio"]} == {z[0] for z in db.ZONAS}
    assert "lunes a sábado" in entregas["horario"]


def test_herramientas_y_base_estan_alineadas():
    """Si se agrega un producto o tamaño al menú, la IA debe poder pedirlo."""
    esquema = next(t for t in tools.TOOLS if t["name"] == "crear_pedido")["input_schema"]["properties"]
    assert set(esquema["producto_id"]["enum"]) == {p[0] for p in db.PRODUCTOS}
    assert set(esquema["tamano"]["enum"]) == {o[1] for o in db.OPCIONES}
    assert set(esquema["zona_id"]["enum"]) == {z[0] for z in db.ZONAS} | {"ninguna"}


# ---------- Crear pedido ----------

def test_total_incluye_el_envio():
    r = pedir(tamano="mediano", zona="gazcue")  # 3200 + 350
    assert r["ok"] and r["subtotal"] == 3200 and r["envio"] == 350 and r["total"] == 3550


def test_recoger_en_tienda_no_cobra_envio():
    r = pedir(entrega="recoger", zona="ninguna", direccion="")
    assert r["ok"] and r["envio"] == 0 and r["total"] == 3200 and r["zona"] is None


def test_cantidad_multiplica_el_precio_pero_el_envio_se_cobra_una_vez():
    r = pedir(producto="cupcakes", tamano="docena", sabor="vainilla", cantidad=3, dedicatoria="", fecha=MIERCOLES)
    assert r["subtotal"] == 3600 and r["total"] == 3850


@pytest.mark.parametrize("sabor", ["limón", "limon", "LIMÓN", "  Limón "])
def test_el_sabor_se_entiende_con_o_sin_tilde(sabor):
    r = pedir(producto="cupcakes", tamano="docena", sabor=sabor, dedicatoria="", fecha=MIERCOLES)
    assert r["ok"] and r["sabor"] == "limón"


def test_sabor_que_no_existe():
    r = pedir(sabor="fresa")
    assert "error" in r and "chocolate" in r["sabores_validos"]


def test_tamano_de_otro_producto():
    assert "tamanos_validos" in pedir(tamano="media_docena")


def test_producto_inexistente():
    assert "error" in pedir(producto="pizza")


def test_dedicatoria_solo_en_bizcochos_y_con_limite():
    assert "no lleva dedicatoria" in pedir(producto="cupcakes", tamano="docena", sabor="vainilla",
                                           dedicatoria="Hola", fecha=MIERCOLES)["error"]
    assert "máximo" in pedir(dedicatoria="x" * 61)["error"]
    assert pedir(dedicatoria="")["ok"]


@pytest.mark.parametrize("cantidad", [0, -1, 11, "abc", None])
def test_cantidad_invalida(cantidad):
    assert "error" in pedir(cantidad=cantidad)


def test_pide_nombre_completo():
    assert "nombre" in pedir(cliente="  ")["error"]
    assert "nombre" in pedir(cliente="A")["error"]


def test_domicilio_necesita_zona_y_direccion():
    assert "dirección" in pedir(direccion="")["error"]
    assert "zona" in pedir(zona="ninguna")["error"]
    assert "zona" in pedir(zona="santiago")["error"]
    assert "error" in pedir(entrega="envio")


def test_anticipacion_minima():
    # Lunes 8:00. Un bizcocho necesita 48 h: el martes no se puede, el miércoles a las 10:00 sí.
    r = pedir(fecha=MARTES)
    assert "48 horas" in r["error"] and r["entrega_minima"] == "2026-11-04 08:00"
    assert pedir(fecha=MIERCOLES, hora="10:00")["ok"]
    # Los cupcakes necesitan solo 24 h: mañana a las 10:00 sí
    assert pedir(producto="cupcakes", tamano="docena", sabor="vainilla", dedicatoria="", fecha=MARTES, hora="10:00")["ok"]


def test_no_se_pueden_pedir_cosas_para_hoy_si_no_alcanza():
    assert "error" in pedir(producto="cupcakes", tamano="docena", sabor="vainilla", dedicatoria="", fecha="2026-11-02")


def test_reintento_no_duplica_el_pedido():
    primero, segundo = pedir(), pedir()
    assert segundo["ok"] and segundo["pedido_id"] == primero["pedido_id"]
    assert len(tools.buscar_pedidos(telefono=TEL)["pedidos"]) == 1
    assert len(filas_csv()) == 1  # y la hoja tampoco se duplica


# ---------- Cupo diario ----------

def test_cupo_diario_de_bizcochos():
    assert tools.ver_cupo(JUEVES, "bizcocho")["cupos_disponibles"] == 4
    for i in range(4):
        assert pedido_de(i)["ok"]
    sin_cupo = pedido_de(5)
    assert "cupos" in sin_cupo["error"] and sin_cupo["cupos_disponibles"] == 0
    assert tools.ver_cupo(JUEVES, "bizcocho")["cupos_disponibles"] == 0
    assert pedido_de(6, fecha=SABADO)["ok"]  # otro día sí


def test_cantidad_mayor_al_cupo_restante():
    assert pedido_de(1, cantidad=3)["ok"]
    r = pedido_de(2, cantidad=2)
    assert "error" in r and r["cupos_disponibles"] == 1


def test_cancelar_libera_el_cupo():
    pedidos = [pedido_de(i) for i in range(4)]
    assert tools.cancelar_pedido(pedidos[0]["pedido_id"], telefono="+1 809 000 0000")["ok"]
    assert tools.ver_cupo(JUEVES, "bizcocho")["cupos_disponibles"] == 1


def test_diez_clientes_al_mismo_tiempo_con_cupo_de_cuatro():
    """El caso que más daño hace: pasarse del cupo cuando llegan pedidos simultáneos."""
    resultados = []
    barrera = threading.Barrier(10)

    def pedir_a_la_vez(i):
        barrera.wait()
        resultados.append(pedido_de(i))

    hilos = [threading.Thread(target=pedir_a_la_vez, args=(i,)) for i in range(10)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert sum(1 for r in resultados if r.get("ok")) == 4
    with db.conectar() as conn:
        assert conn.execute("SELECT SUM(cantidad) FROM pedidos").fetchone()[0] == 4


# ---------- Buscar y cancelar: cada quien solo lo suyo ----------

def test_cada_cliente_ve_solo_sus_pedidos():
    pedir(telefono=TEL)
    pedir(telefono=OTRO_TEL, cliente="Otro Cliente")
    mios = tools.buscar_pedidos(telefono=TEL)["pedidos"]
    assert len(mios) == 1 and mios[0]["producto"] == "Bizcocho"


def test_no_cancela_pedidos_ajenos():
    ajeno = pedir(telefono=OTRO_TEL, cliente="Otro Cliente")
    assert "error" in tools.cancelar_pedido(ajeno["pedido_id"], telefono=TEL)
    assert len(tools.buscar_pedidos(telefono=OTRO_TEL)["pedidos"]) == 1


def test_cancela_el_propio_y_no_dos_veces():
    p = pedir()
    assert tools.cancelar_pedido(p["pedido_id"], telefono=TEL)["ok"]
    assert "error" in tools.cancelar_pedido(p["pedido_id"], telefono=TEL)
    assert tools.buscar_pedidos(telefono=TEL)["pedidos"] == []


def test_cancelar_acepta_numero_como_decimal_pero_no_texto():
    p = pedir()
    assert "error" in tools.cancelar_pedido("abc", telefono=TEL)
    assert tools.cancelar_pedido(float(p["pedido_id"]), telefono=TEL)["ok"]


def test_no_se_cancela_cuando_ya_esta_en_produccion(monkeypatch):
    p = pedir(fecha=JUEVES, hora="10:00")
    monkeypatch.setattr(db, "ahora", lambda: datetime(2026, 11, 4, 12, 0))  # faltan 22 h
    r = tools.cancelar_pedido(p["pedido_id"], telefono=TEL)
    assert "producción" in r["error"] and "transferir_a_humano" in r["error"]
    assert len(tools.buscar_pedidos(telefono=TEL)["pedidos"]) == 1


# ---------- Pasar a una persona ----------

def test_transferir_a_humano():
    assert not db.en_atencion_humana(TEL)
    assert tools.transferir_a_humano("quiere un bizcocho de boda", telefono=TEL)["ok"]
    tools.transferir_a_humano("sigue esperando", telefono=TEL)  # no se duplica
    assert db.en_atencion_humana(TEL) and not db.en_atencion_humana(OTRO_TEL)
    with db.conectar() as conn:
        assert conn.execute("SELECT COUNT(*) FROM transferencias").fetchone()[0] == 1
    db.resolver_transferencias(TEL)
    assert not db.en_atencion_humana(TEL)


def test_transferir_pide_motivo():
    assert "error" in tools.transferir_a_humano(" ", telefono=TEL)


# ---------- Hoja de cálculo ----------

def test_cada_pedido_queda_en_la_hoja():
    p = pedir(dedicatoria="Feliz cumple Ana")
    fila = filas_csv()[0]
    assert fila["pedido"] == str(p["pedido_id"]) and fila["evento"] == "nuevo"
    assert fila["cliente"] == "Ana Pérez" and fila["sabor"] == "chocolate" and fila["total"] == "3450"
    assert fila["tamaño"] == "20 porciones" and fila["zona"] == "Piantini" and fila["dedicatoria"] == "Feliz cumple Ana"
    assert hoja.pendientes() == 0


def test_cancelar_tambien_queda_en_la_hoja():
    p = pedir()
    tools.cancelar_pedido(p["pedido_id"], telefono=TEL)
    assert [f["evento"] for f in filas_csv()] == ["nuevo", "cancelado"]
    assert filas_csv()[1]["estado"] == "cancelado"


def test_si_la_hoja_falla_el_pedido_no_se_pierde(monkeypatch):
    class Caida:
        def agregar(self, filas):
            raise ConnectionError("Google Sheets no responde")

    normal = hoja.destino_configurado
    monkeypatch.setattr(hoja, "destino_configurado", lambda: Caida())
    p = pedir()
    assert p["ok"] and len(tools.buscar_pedidos(telefono=TEL)["pedidos"]) == 1
    assert hoja.pendientes() == 1

    # Cuando la hoja vuelve, se envía lo pendiente una sola vez
    monkeypatch.setattr(hoja, "destino_configurado", normal)
    assert hoja.sincronizar() == 1 and hoja.sincronizar() == 0 and hoja.pendientes() == 0
    assert len(filas_csv()) == 1


def test_el_destino_se_elige_por_configuracion(monkeypatch):
    assert isinstance(hoja.destino_configurado(), hoja.DestinoCsv)
    monkeypatch.setenv("GOOGLE_SHEET_ID", "abc")
    monkeypatch.setenv("GOOGLE_CREDENTIALS_FILE", "credenciales.json")
    assert isinstance(hoja.destino_configurado(), hoja.DestinoSheets)


# ---------- La IA no controla el teléfono ----------

def test_las_herramientas_no_piden_telefono_a_la_ia():
    for t in tools.TOOLS:
        assert "telefono" not in t["input_schema"]["properties"], t["name"]


def test_todos_los_parametros_son_obligatorios_para_la_ia():
    for t in tools.TOOLS:
        assert set(t["input_schema"]["required"]) == set(t["input_schema"]["properties"]), t["name"]


def test_ignora_un_telefono_inventado_por_la_ia():
    salida, error = ejecutar_herramienta(
        "crear_pedido",
        {"cliente": "Ana Pérez", "producto_id": "bizcocho", "tamano": "mediano", "sabor": "chocolate", "cantidad": 1,
         "dedicatoria": "", "fecha": JUEVES, "hora": "15:00", "entrega": "recoger", "zona_id": "ninguna",
         "direccion": "", "telefono": OTRO_TEL},
        TEL,
    )
    assert not error
    assert tools.buscar_pedidos(telefono=TEL)["pedidos"] and not tools.buscar_pedidos(telefono=OTRO_TEL)["pedidos"]


def test_herramienta_inexistente_o_parametros_raros():
    assert ejecutar_herramienta("borrar_todo", {}, TEL)[1]
    assert ejecutar_herramienta("ver_cupo", {"fecha": JUEVES}, TEL)[1]
    assert ejecutar_herramienta("ver_menu", {"extra": 1}, TEL)[1]
