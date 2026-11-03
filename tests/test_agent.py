"""Pruebas del agente con una IA falsa: qué ve el cliente cuando algo sale mal."""

import threading
import time

import pytest

from app import agent, db, tools

TEL = "+1 809 111 2222"


@pytest.fixture(autouse=True)
def limpio(monkeypatch):
    agent.conversaciones.clear()
    monkeypatch.setattr(agent, "PROVEEDOR", "gemini")


def ia_falsa(monkeypatch, funcion):
    monkeypatch.setattr(agent, "_responder_gemini", funcion)


def test_si_la_ia_falla_el_cliente_ve_un_mensaje_amable(monkeypatch):
    def falla(texto, mensajes, acciones, telefono):
        mensajes.append("mensaje a medias")
        raise RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand")

    ia_falsa(monkeypatch, falla)
    r = agent.responder(TEL, "hola")
    assert r["respuesta"] == agent.MENSAJE_FALLA
    assert "503" not in r["respuesta"] and "model" not in r["respuesta"]
    # La conversación queda como estaba: el siguiente mensaje empieza limpio
    assert agent.conversaciones[TEL]["mensajes"] == []


def test_respuesta_vacia_no_deja_al_cliente_sin_respuesta(monkeypatch):
    ia_falsa(monkeypatch, lambda texto, mensajes, acciones, telefono: "")
    assert agent.responder(TEL, "hola")["respuesta"] == agent.MENSAJE_ENREDO


def test_respuesta_vacia_despues_de_agendar(monkeypatch):
    def agenda_sin_texto(texto, mensajes, acciones, telefono):
        acciones.append({"herramienta": "crear_pedido", "entrada": {}, "error": False})
        return "   "

    ia_falsa(monkeypatch, agenda_sin_texto)
    assert "Listo" in agent.responder(TEL, "listo, confírmalo")["respuesta"]


def test_formato_whatsapp_aunque_la_ia_se_equivoque(monkeypatch):
    ia_falsa(monkeypatch, lambda *_: "## Tu cita\nTu cita es la **#2** del *lunes*")
    assert agent.responder(TEL, "hola")["respuesta"] == "Tu cita\nTu cita es la *#2* del *lunes*"


def test_rechazo_del_filtro(monkeypatch):
    def rechaza(texto, mensajes, acciones, telefono):
        mensajes.append("x")
        raise agent.Rechazado()

    ia_falsa(monkeypatch, rechaza)
    assert agent.responder(TEL, "algo raro")["respuesta"] == agent.MENSAJE_RECHAZO
    assert agent.conversaciones[TEL]["mensajes"] == []


def test_mensaje_vacio_no_llama_a_la_ia(monkeypatch):
    def no_deberia(*_):
        raise AssertionError("no debía llamar a la IA")

    ia_falsa(monkeypatch, no_deberia)
    assert agent.responder(TEL, "   ")["respuesta"]


def test_mensajes_muy_largos_se_recortan(monkeypatch):
    recibido = {}

    def guarda(texto, mensajes, acciones, telefono):
        recibido["texto"] = texto
        return "ok"

    ia_falsa(monkeypatch, guarda)
    agent.responder(TEL, "a" * 50_000)
    assert len(recibido["texto"]) == agent.MAX_LARGO_MENSAJE


def test_conversacion_nueva_despues_de_inactividad(monkeypatch):
    def guarda(texto, mensajes, acciones, telefono):
        mensajes.append(texto)
        return "ok"

    ia_falsa(monkeypatch, guarda)
    agent.responder(TEL, "primero")
    agent.conversaciones[TEL]["ultimo"] -= agent.INACTIVIDAD + 1
    agent.responder(TEL, "segundo")
    assert agent.conversaciones[TEL]["mensajes"] == ["segundo"]


def test_dos_mensajes_seguidos_del_mismo_cliente_no_se_mezclan(monkeypatch):
    def lenta(texto, mensajes, acciones, telefono):
        mensajes.append(("inicio", texto))
        time.sleep(0.2)
        mensajes.append(("fin", texto))
        return "ok"

    ia_falsa(monkeypatch, lenta)
    hilos = [threading.Thread(target=agent.responder, args=(TEL, t)) for t in ("uno", "dos")]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    m = agent.conversaciones[TEL]["mensajes"]
    # Cada turno completo antes del siguiente: inicio/fin del mismo mensaje juntos
    assert m[0][1] == m[1][1] and m[2][1] == m[3][1]


def test_si_ya_lo_atiende_una_persona_el_bot_no_contesta(monkeypatch):
    def no_deberia(*_):
        raise AssertionError("no debía llamar a la IA")

    ia_falsa(monkeypatch, no_deberia)
    tools.transferir_a_humano("quiere un bizcocho de boda", telefono=TEL)
    assert agent.responder(TEL, "hola?")["respuesta"] == agent.MENSAJE_HUMANO
    # Otro cliente sigue hablando con el bot
    ia_falsa(monkeypatch, lambda *_: "hola")
    assert agent.responder("+1 809 999 0000", "hola")["respuesta"] == "hola"
    # Al resolver el caso, el cliente vuelve al bot
    db.resolver_transferencias(TEL)
    ia_falsa(monkeypatch, lambda *_: "de vuelta")
    assert agent.responder(TEL, "hola")["respuesta"] == "de vuelta"
