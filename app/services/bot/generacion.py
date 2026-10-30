"""Generación de la respuesta del bot: arma el prompt del paso actual con los datos del cliente y del proyecto, llama a Claude y
devuelve la respuesta, el siguiente paso y los datos extraídos.

Movido sin cambios desde app/services/bot (Fase 8). Los parámetros de la llamada al modelo (modelo, max_tokens, presupuesto de
tiempo, cliente con sus timeouts y reintentos) no cambian: el cliente vive en app/core/llm.py.
"""
import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

try:
    import anthropic
except ImportError:
    anthropic = None

from app.clients.supabase import _supabase_request
from app.core.config import get_settings
from app.core.llm import PRESUPUESTO_TOTAL_S, cliente_anthropic
from app.services.bot.contexto import _construir_mind, _resumir_proyecto
from app.services.bot.prompts import PASOS_CONFIG
from app.services.bot.respuesta import _interpretar_respuesta
from app.services.documentos import _CAMPOS_CALIFICACION, _lista_documentos_requeridos_texto, resumen_documentos

logger = logging.getLogger("wtsp_pita")

ANTHROPIC_API_KEY  = get_settings().anthropic_api_key


async def generar_respuesta_ia(
    *,
    prospecto: Dict,
    proyecto: Optional[Dict],
    historial: List[Dict],
    mensaje_actual: str,
    docs_recibidos: Optional[List[Dict]] = None,
    otros_proyectos: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    if not ANTHROPIC_API_KEY or anthropic is None:
        logger.warning("ANTHROPIC_API_KEY no configurada — usando eco")
        return {"respuesta": f"Hola 👋 Recibí: {mensaje_actual}", "siguiente_paso": None, "datos_extraidos": {}}

    nombre              = (prospecto.get("nombre") or "").strip() or "amigo/a"
    telefono            = prospecto.get("telefono_e164") or ""
    rut                 = prospecto.get("rut") or "no registrado"
    rango_sueldo        = prospecto.get("rango_sueldo") or "no registrado"
    numero_integrantes  = prospecto.get("numero_integrantes") or "no registrado"
    paso_actual         = prospecto.get("paso") or "INICIO"
    # Construir datos de calificación desde columnas boolean dedicadas
    datos = {campo: prospecto.get(campo) for campo in _CAMPOS_CALIFICACION}

    mind = _construir_mind(proyecto)

    p                  = proyecto or {}
    proyecto_nombre    = p.get("nombre") or "nuestro proyecto"
    proyecto_ubicacion = p.get("ubicacion") or ""
    proyecto_inmobiliaria      = mind["inmobiliaria"]

    # Datos de Etapa y Tipologia (ya no viven en Proyecto)
    _etapa_bot = None
    _tip_monto_bot = None
    if p.get("id"):
        _etapas_bot = await _supabase_request("GET", "/Etapa",
            params={"proyecto_id": f"eq.{p['id']}", "select": "fecha_entrega,estado,num_cuotas", "limit": "1", "order": "id.asc"}) or []
        _etapa_bot = _etapas_bot[0] if _etapas_bot else None
        _tips_bot = await _supabase_request("GET", "/Tipologia",
            params={"proyecto_id": f"eq.{p['id']}", "select": "id,nombre,valor_uf,dormitorios,banos,superficie_util_m2,tipo_subsidio,monto_subsidio"}) or []
        _raw_monto = _tips_bot[0].get("monto_subsidio") if _tips_bot else None
        try:
            _tip_monto_bot = float(_raw_monto) if _raw_monto is not None else None
        except (TypeError, ValueError):
            _tip_monto_bot = None

    proyecto_fecha_entrega     = (_etapa_bot.get("fecha_entrega") if _etapa_bot else None) or "por confirmar"
    proyecto_ahorro_minimo     = p.get("ahorro_minimo_uf") or 50
    proyecto_reserva_clp       = p.get("valor_reserva_clp") or ""
    proyecto_reserva_uf        = p.get("valor_reserva_uf") or ""
    proyecto_tiene_piloto      = p.get("tiene_piloto")
    proyecto_estac_uf          = p.get("valor_estacionamiento_uf") or ""
    proyecto_estac_obligatorio = p.get("estacionamiento_obligatorio")
    proyecto_notas             = p.get("notas") or ""
    proyecto_acepta_ds19       = p.get("acepta_ds19") or False
    proyecto_monto_subsidio    = _tip_monto_bot or 700
    proyecto_acepta_ds1t23     = p.get("acepta_ds1_t23") or False
    proyecto_subsidio_ds1t23   = p.get("subsidio_ds1_t23_uf") or ""
    proyecto_tipologias        = _tips_bot if p.get("id") else []

    # Construir bloque de subsidio según lo que acepta el proyecto
    subsidios_lineas = []
    if proyecto_acepta_ds19:
        subsidios_lineas.append(f"DS19: {proyecto_monto_subsidio} UF")
    if proyecto_acepta_ds1t23:
        monto_ds1t23 = proyecto_subsidio_ds1t23 or _tip_monto_bot or ""
        subsidios_lineas.append(f"DS1 T2/T3 (adjudicado){f': {monto_ds1t23} UF' if monto_ds1t23 else ''}")
    subsidios_texto = " | ".join(subsidios_lineas) if subsidios_lineas else "Este proyecto NO acepta subsidios habitacionales"

    # Estacionamiento
    if proyecto_estac_uf:
        estac_texto = f"{proyecto_estac_uf} UF {'(obligatorio)' if proyecto_estac_obligatorio else '(opcional)'}"
    else:
        estac_texto = "no disponible"

    # Lista estática de documentos requeridos (mismos nombres/cantidades que valida el backend)
    lista_documentos_requeridos = _lista_documentos_requeridos_texto(datos)

    # Estado de documentos para el paso ESPERA_DOCS (condicional según calificación)
    estado_documentos = resumen_documentos(docs_recibidos or [], datos)
    _estado_lineas = estado_documentos.split("\n")
    docs_recibidos_txt = _estado_lineas[0].replace("Recibidos: ", "") if _estado_lineas else "(ninguno)"
    docs_pendientes_txt = _estado_lineas[1].replace("Pendientes: ", "") if len(_estado_lineas) > 1 else "(ninguno)"

    # Variables adicionales para nuevo PASOS_CONFIG
    tipo_subsidio_datos   = datos.get("tipo_subsidio") or "no_determinado"
    tipo_entrega_proyecto = (_etapa_bot.get("estado") if _etapa_bot else None) or "entrega_futura"
    grupo_ds19_proyecto   = p.get("grupo_ds19") or "A"

    # Condición de pago del ahorro: calculada en código a partir de Etapa.estado/num_cuotas
    # (única fuente de verdad) — se inyecta como frase fija en DATOS DEL PROYECTO para que el
    # bot no tenga que inferir/inventar si es "al contado" o "en cuotas durante la construcción".
    if tipo_entrega_proyecto == "entrega_inmediata":
        condicion_pago_ahorro = (
            f"El ahorro de {proyecto_ahorro_minimo} UF se paga DE UNA SOLA VEZ "
            f"(proyecto de entrega inmediata — NO se paga en cuotas ni durante construcción)."
        )
    else:
        _num_cuotas_bot = _etapa_bot.get("num_cuotas") if _etapa_bot else None
        _cuotas_txt = f"en {_num_cuotas_bot} cuotas mensuales" if _num_cuotas_bot else "en cuotas mensuales"
        condicion_pago_ahorro = (
            f"El ahorro de {proyecto_ahorro_minimo} UF se paga {_cuotas_txt} SIN INTERÉS "
            f"durante la construcción, hasta la fecha de entrega estimada "
            f"({(_etapa_bot.get('fecha_entrega') if _etapa_bot else None) or 'por confirmar'})."
        )
    _credito_uf_est: Any = "consultar"
    if proyecto_tipologias:
        _precios = [t.get("valor_uf") for t in proyecto_tipologias if t.get("valor_uf")]
        if _precios:
            _precio_min = min(_precios)
            _credito_calc = round(_precio_min - proyecto_monto_subsidio - proyecto_ahorro_minimo)
            _credito_uf_est = max(_credito_calc, 0)
    _dividendo_est: Any = round(_credito_uf_est * 213) if isinstance(_credito_uf_est, (int, float)) else "consultar"
    _renta_min_est: Any = round(_dividendo_est * 4) if isinstance(_dividendo_est, (int, float)) else "consultar"

    _reserva_str = (f"{proyecto_reserva_uf} UF / ${proyecto_reserva_clp:,.0f}"
                    if proyecto_reserva_clp else proyecto_reserva_uf or "consultar")
    datos_proyecto_texto = (
        f"Nombre: {proyecto_nombre}\n"
        f"Ubicación: {proyecto_ubicacion}\n"
        f"Fecha entrega: {proyecto_fecha_entrega}\n"
        f"Tipo de entrega: {tipo_entrega_proyecto}\n"
        f"Monto subsidio: {proyecto_monto_subsidio} UF\n"
        f"Ahorro mínimo: {proyecto_ahorro_minimo} UF\n"
        f"Condición de pago del ahorro: {condicion_pago_ahorro}\n"
        f"Crédito estimado: {_credito_uf_est} UF\n"
        f"Sala piloto: {'Sí' if proyecto_tiene_piloto else 'No disponible'}\n"
        f"Estacionamiento: {estac_texto}\n"
        f"Valor reserva: {_reserva_str}\n"
        f"Tipologías: {json.dumps(proyecto_tipologias, ensure_ascii=False)}\n"
        f"Notas: {proyecto_notas}"
    )

    instrucciones = PASOS_CONFIG.get(paso_actual, PASOS_CONFIG["BIENVENIDA"]).format(
        nombre=nombre,
        rango_sueldo=rango_sueldo,
        datos=json.dumps(datos, ensure_ascii=False, indent=2),
        estado_documentos=estado_documentos,
        ahorro_minimo=proyecto_ahorro_minimo,
        monto_subsidio=proyecto_monto_subsidio,
        monto_subsidio_ds1t23=proyecto_subsidio_ds1t23 or "consultar",
        proyecto=proyecto_nombre,
        datos_proyecto=datos_proyecto_texto,
        tipo_subsidio=tipo_subsidio_datos,
        tipo_entrega=tipo_entrega_proyecto,
        credito_uf=_credito_uf_est,
        dividendo_estimado=f"{_dividendo_est:,}" if isinstance(_dividendo_est, (int, float)) else _dividendo_est,
        renta_minima_estimada=f"{_renta_min_est:,}" if isinstance(_renta_min_est, (int, float)) else _renta_min_est,
        docs_recibidos=docs_recibidos_txt,
        docs_pendientes=docs_pendientes_txt,
        grupo_ds19=grupo_ds19_proyecto,
        numero_integrantes=numero_integrantes,
        lista_documentos_requeridos=lista_documentos_requeridos,
        condicion_pago_ahorro=condicion_pago_ahorro,
    )

    sistema_identidad = f"Eres un asistente de ventas de la industria {mind['industria']}"
    if mind["empresa"]:
        sistema_identidad += f", trabajas para la empresa {mind['empresa']}"
    if mind["inmobiliaria"]:
        sistema_identidad += f", representando a la inmobiliaria {mind['inmobiliaria']}"
    if mind["proyecto"]:
        sistema_identidad += f", y tu foco actual es el proyecto {mind['proyecto']}"
    sistema_identidad += ". Eres profesional, empático y experto en el área."

    if otros_proyectos:
        _bloque_otros_proyectos = "\n".join(_resumir_proyecto(p) for p in otros_proyectos)
    else:
        _bloque_otros_proyectos = "No hay otros proyectos disponibles en este momento."

    system_prompt = f"""{sistema_identidad}

═══ DATOS DEL CLIENTE ═══
Nombre:              {nombre}
Teléfono:            {telefono}
RUT:                 {rut}
Rango sueldo:        {rango_sueldo}
Integrantes hogar:   {numero_integrantes}
Paso actual:         {paso_actual}

═══ DATOS DEL PROYECTO ═══
Nombre:              {proyecto_nombre}
Inmobiliaria:        {proyecto_inmobiliaria}
Ubicación:           {proyecto_ubicacion}
Fecha entrega:       {proyecto_fecha_entrega}
Subsidios:           {subsidios_texto}
Ahorro mínimo:       {proyecto_ahorro_minimo} UF
Cómo se paga el ahorro: {condicion_pago_ahorro}
Estacionamiento:     {estac_texto}
Sala piloto:         {'Sí' if proyecto_tiene_piloto else 'No disponible'}
Valor reserva:       {f'{proyecto_reserva_uf} UF / ${proyecto_reserva_clp:,.0f}' if proyecto_reserva_clp else proyecto_reserva_uf or 'consultar'}
Tipologías:          {json.dumps(proyecto_tipologias, ensure_ascii=False)}
Notas del proyecto:  {proyecto_notas}

═══ INSTRUCCIONES DE ESTE PASO ═══
{instrucciones}

═══ OTROS PROYECTOS DISPONIBLES ═══
{_bloque_otros_proyectos}

═══ REGLAS GENERALES ═══
- Responde en español, de forma cálida y profesional.
- Mensajes cortos (máximo 3-4 párrafos). NUNCA más de 1 pregunta a la vez.
- Usa emojis con moderación.
- Usa los datos del proyecto para responder preguntas específicas del cliente
  (precio, fecha, estacionamiento, etc.) sin inventar información.
- Sobre cómo se paga el ahorro (al contado vs en cuotas durante construcción):
  usa EXCLUSIVAMENTE lo que dice "Cómo se paga el ahorro" arriba. NUNCA asumas
  que es "en cuotas durante la construcción" por defecto — varios proyectos son
  de entrega inmediata y el ahorro se paga de una sola vez. No contradigas ese dato.
- NUNCA menciones nombres de empresas, constructoras o inmobiliarias que no estén
  explícitamente en los datos del sistema. Si no tienes la información, di que la
  coordinarás con el equipo a cargo, sin inventar nombres.
- Si el proyecto NO acepta subsidios, NO hables de subsidios ni consultes si el
  cliente tiene uno. Ignora completamente ese tema.
- Si el cliente pregunta algo fuera del tema del proyecto o subsidio,
  redirígelo amablemente sin ser brusco, recordándole en qué punto del proceso está.
- Si el cliente pide ver otros proyectos o mostrar alternativas, preséntale
  los proyectos de la sección "OTROS PROYECTOS DISPONIBLES" de forma breve.
- Cuando el cliente confirme explícitamente su interés en un proyecto distinto
  al actual, incluye "nuevo_proyecto_id" en datos_extraidos con el id exacto
  del proyecto confirmado. La calificación y documentos ya recopilados se
  conservan; NO reinicies el flujo salvo que el nuevo proyecto exija subsidio
  distinto y el cliente no haya calificado aún.

RESPONDE ÚNICAMENTE con JSON válido (sin markdown, sin texto extra):
{{
  "respuesta": "texto para enviar por WhatsApp",
  "siguiente_paso": null,
  "datos_extraidos": {{}}
}}
Valores válidos de siguiente_paso: null | "BIENVENIDA" | "INICIO" | "DOCUMENTACION" | "ESPERA_DOCS" | "DOCS_RECIBIDOS" | "NO_INTERESADO" | "NO_CALIFICA"
En datos_extraidos puedes incluir además: "nuevo_proyecto_id": "<uuid>" cuando el cliente confirme cambio de proyecto.
"""

    messages: List[Dict[str, str]] = []
    for msg in historial:
        texto = (msg.get("texto") or "").strip()
        if not texto:
            continue
        role = "user" if msg["direccion"] == "entrante" else "assistant"
        if messages and messages[-1]["role"] == role:
            messages[-1]["content"] += f"\n{texto}"
        else:
            messages.append({"role": role, "content": texto})
    messages.append({"role": "user", "content": mensaje_actual})
    # Prefill: fuerza a Claude a comenzar directamente con JSON
    messages.append({"role": "assistant", "content": "{"})

    client = cliente_anthropic(ANTHROPIC_API_KEY)
    # El presupuesto total incluye los reintentos del SDK y cualquier Retry-After (ver app/core/llm.py)
    _modelo = "claude-haiku-4-5-20251001"
    _t0 = time.perf_counter()
    try:
        response = await asyncio.wait_for(client.messages.create(
            model=_modelo,
            max_tokens=700,
            system=system_prompt,
            messages=messages,
        ), timeout=PRESUPUESTO_TOTAL_S)
    except (asyncio.TimeoutError, anthropic.APITimeoutError) as _e:
        # request_id lo añade el formateador JSON (contextvar de la petición de origen)
        logger.warning(
            "Tiempo agotado llamando al modelo (presupuesto %.0f s, reintentos incluidos): "
            "el mensaje queda pendiente para _recovery_loop",
            PRESUPUESTO_TOTAL_S,
            extra={"modelo": _modelo, "motivo": type(_e).__name__,
                   "duration_ms": round((time.perf_counter() - _t0) * 1000, 1)},
        )
        raise
    logger.info("Respuesta del modelo", extra={"modelo": _modelo,
                                               "duration_ms": round((time.perf_counter() - _t0) * 1000, 1)})

    return _interpretar_respuesta(response.content[0].text, lista_documentos_requeridos)
