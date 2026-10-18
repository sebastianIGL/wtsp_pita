"""Evaluación por correo: arma el correo con la evaluación de un cliente (datos, proyecto, documentos adjuntos) y lo
envía a los ejecutivos.

Movido sin cambios desde main.py (Fase 6, tramo 2, lote B).
"""
import logging
from html import escape as html_escape
from typing import Dict, List

from app.clients.gmail import _gmail_send
from app.clients.supabase import _supabase_request
from app.clients.supabase_storage import _descargar_documento_storage
from app.core.config import get_settings
from app.core.errors import AppError

logger = logging.getLogger("wtsp_pita")


CONDICION_LABEL = {
    "nueva": "Nueva",
    "semi-nueva": "Semi-nueva",
}


async def _enviar_email_evaluacion(
    cliente_id: int,
    usuario: dict | None = None,
    ejecutivos_emails: list | None = None,
    nota: str | None = None,
) -> dict:
    if not get_settings().google_service_account_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON no configurada")

    clientes = await _supabase_request("GET", "/Cliente",
        params={"id": f"eq.{cliente_id}", "select": "*", "limit": "1"})
    if not clientes:
        raise AppError(f"Cliente {cliente_id} no encontrado")
    c = clientes[0]

    ahorro_uf = c.get("ahorro_uf")
    if ahorro_uf is None:
        raise AppError(
            "El campo 'Ahorro (UF)' es requerido para enviar la evaluación. "
            "Por favor agrégalo en los datos del cliente."
        )
    ahorro_f = float(ahorro_uf)

    if not c.get("etapa_tipologia_id"):
        raise AppError(
            "El cliente no tiene tipología y etapa asignada. "
            "Edítalo y selecciona la tipología y etapa antes de enviar la evaluación."
        )

    if ejecutivos_emails:
        destinatarios = ejecutivos_emails
    else:
        ejecutivos = await _supabase_request("GET", "/EjecutivoBancario",
            params={"disponible": "eq.true", "select": "email,ejecutivo"})
        destinatarios = [e["email"] for e in (ejecutivos or []) if e.get("email")]
    if not destinatarios:
        raise AppError("No hay ejecutivos disponibles con email configurado")

    nombre   = (c.get("Contacto") or "Sin nombre").strip()
    rut      = c.get("Rut") or "No registrado"
    telefono = c.get("Telefono") or "No registrado"
    correo   = (c.get("Correo") or "").strip()
    renta    = c.get("Tramo de renta") or "No registrado"

    proyecto_nombre     = "No registrado"
    proyecto_ubicacion  = "No registrada"
    inmobiliaria_nombre = "No registrada"
    condicion_propiedad = "nueva"
    valor_uf: float | None = None
    monto_subsidio: float | None = None
    entrega_label = "Futura"

    proyecto_id_raw        = c.get("proyecto_id")
    etapa_tipologia_id_raw = c.get("etapa_tipologia_id")
    if proyecto_id_raw:
        proy_rows = await _supabase_request("GET", "/Proyecto",
            params={
                "id": f"eq.{proyecto_id_raw}",
                "select": "nombre,ubicacion,condicion_propiedad,Inmobiliaria(nombre)",
                "limit": "1",
            })
        if proy_rows:
            p = proy_rows[0]
            proyecto_nombre    = p.get("nombre") or "No registrado"
            proyecto_ubicacion = p.get("ubicacion") or "No registrada"
            condicion_propiedad = p.get("condicion_propiedad") or "nueva"
            inm = (p.get("Inmobiliaria") or {})
            inmobiliaria_nombre = inm.get("nombre") or "No registrada"

        # Usar la tipología + etapa específica asignada al cliente
        if etapa_tipologia_id_raw:
            et_rows = await _supabase_request("GET", "/EtapaTipologia",
                params={
                    "id": f"eq.{etapa_tipologia_id_raw}",
                    "select": "Tipologia(valor_uf,monto_subsidio),Etapa(estado)",
                    "limit": "1",
                })
            if et_rows:
                tip   = et_rows[0].get("Tipologia") or {}
                etapa = et_rows[0].get("Etapa") or {}
                if tip.get("valor_uf") is not None:
                    valor_uf = float(tip["valor_uf"])
                if tip.get("monto_subsidio") is not None:
                    monto_subsidio = float(tip["monto_subsidio"])
                if etapa.get("estado"):
                    entrega_label = "Inmediata" if etapa["estado"] == "entrega_inmediata" else "Futura"

    monto_credito = (
        round(valor_uf - (monto_subsidio or 0) - ahorro_f, 2)
        if valor_uf is not None else None
    )

    def _fmt_uf(v):
        return f"UF {float(v):,.2f}".replace(",", ".") if v is not None else "No registrado"

    prospectos = await _supabase_request("GET", "/Prospecto",
        params={"cliente_id": f"eq.{cliente_id}", "select": "id", "limit": "1"})
    docs = []
    if prospectos:
        docs = await _supabase_request("GET", "/Documento",
            params={
                "prospecto_id": f"eq.{prospectos[0]['id']}",
                "select": "tipo,nombre_archivo,url_storage,mime_type",
            }) or []

    PLURAL_TIPO = {
        "liquidacion_sueldo": ("liquidación de sueldo", "liquidaciones de sueldo"),
        "certificado_afp": ("certificado AFP", "certificados AFP"),
        "carnet_identidad": ("cédula de identidad", "cédulas de identidad"),
        "libreta_ahorro": ("libreta de ahorro", "libretas de ahorro"),
        "informe_deudas": ("informe de deudas", "informes de deudas"),
        "antiguedad_laboral": ("certificado de antigüedad laboral", "certificados de antigüedad laboral"),
        "otro": ("documento adicional", "documentos adicionales"),
    }
    conteo_docs: Dict[str, int] = {}
    for d in docs:
        tipo = d.get("tipo") or "otro"
        conteo_docs[tipo] = conteo_docs.get(tipo, 0) + 1
    partes_docs = []
    for tipo, cant in conteo_docs.items():
        sing, plur = PLURAL_TIPO.get(tipo, (tipo, tipo))
        partes_docs.append(f"{cant} {sing if cant == 1 else plur}")
    resumen_docs = (
        "Se adjuntan: " + ", ".join(partes_docs) + "."
        if partes_docs else "No se adjuntan documentos."
    )

    fila_correo = ""
    bg_tramo = ""
    if correo:
        fila_correo = f"""
        <tr>
          <td style="padding:8px 12px;font-weight:bold;color:#555;">Correo</td>
          <td style="padding:8px 12px;">{html_escape(correo)}</td>
        </tr>"""
        bg_tramo = ' style="background:#f5f7fa;"'

    bloque_nota = ""
    if nota:
        bloque_nota = f"""
      <div style="margin-top:20px;padding:12px 16px;background:#fff8e1;border-left:4px solid #f59e0b;font-size:14px;color:#374151;">
        <strong style="color:#92400e;">Nota:</strong> {html_escape(nota)}
      </div>"""

    body_html = f"""
    <div style="font-family:Arial,sans-serif;max-width:600px;">
      <h2 style="color:#1e3a5f;border-bottom:2px solid #1e3a5f;padding-bottom:8px;">
        Solicitud de Evaluación de Crédito
      </h2>
      <p style="font-size:14px;color:#374151;margin:16px 0;">
        Junto con saludar cordialmente, solicito tu amable colaboración para realizar la evaluación del siguiente cliente, conforme a las condiciones que se adjuntan a este correo.
      </p>
      <table style="border-collapse:collapse;font-size:14px;width:100%;">
        <tr style="background:#f5f7fa;">
          <td style="padding:8px 12px;font-weight:bold;color:#555;width:210px;">Nombre</td>
          <td style="padding:8px 12px;">{nombre}</td>
        </tr>
        <tr>
          <td style="padding:8px 12px;font-weight:bold;color:#555;">RUT</td>
          <td style="padding:8px 12px;">{rut}</td>
        </tr>
        <tr style="background:#f5f7fa;">
          <td style="padding:8px 12px;font-weight:bold;color:#555;">Teléfono</td>
          <td style="padding:8px 12px;">{telefono}</td>
        </tr>{fila_correo}
        <tr{bg_tramo}>
          <td style="padding:8px 12px;font-weight:bold;color:#555;">Tramo de renta</td>
          <td style="padding:8px 12px;">{renta}</td>
        </tr>
        <tr style="background:#e8eef7;border-top:2px solid #1e3a5f;">
          <td style="padding:10px 12px;font-weight:bold;color:#1e3a5f;">Proyecto</td>
          <td style="padding:10px 12px;font-weight:bold;color:#1e3a5f;">{proyecto_nombre}</td>
        </tr>
        <tr>
          <td style="padding:8px 12px;font-weight:bold;color:#555;">Condición</td>
          <td style="padding:8px 12px;">{CONDICION_LABEL.get(condicion_propiedad, condicion_propiedad)}</td>
        </tr>
        <tr style="background:#f5f7fa;">
          <td style="padding:8px 12px;font-weight:bold;color:#555;">Dirección</td>
          <td style="padding:8px 12px;">{proyecto_ubicacion}</td>
        </tr>
        <tr>
          <td style="padding:8px 12px;font-weight:bold;color:#555;">Inmobiliaria</td>
          <td style="padding:8px 12px;">{inmobiliaria_nombre}</td>
        </tr>
        <tr style="background:#f5f7fa;">
          <td style="padding:8px 12px;font-weight:bold;color:#555;">Entrega</td>
          <td style="padding:8px 12px;">{entrega_label}</td>
        </tr>
        <tr>
          <td style="padding:8px 12px;font-weight:bold;color:#555;">Valor propiedad</td>
          <td style="padding:8px 12px;">{_fmt_uf(valor_uf)}</td>
        </tr>
        <tr style="background:#f5f7fa;">
          <td style="padding:8px 12px;font-weight:bold;color:#555;">Subsidio</td>
          <td style="padding:8px 12px;">{_fmt_uf(monto_subsidio)}</td>
        </tr>
        <tr>
          <td style="padding:8px 12px;font-weight:bold;color:#555;">Ahorro cliente</td>
          <td style="padding:8px 12px;">{_fmt_uf(ahorro_f)}</td>
        </tr>
        <tr style="background:#e8f0fe;border-top:1px solid #c7d7f7;">
          <td style="padding:10px 12px;font-weight:bold;color:#1e3a5f;">Monto crédito solicitado</td>
          <td style="padding:10px 12px;font-weight:bold;color:#1e3a5f;">{_fmt_uf(monto_credito)}</td>
        </tr>
      </table>{bloque_nota}
      <h3 style="color:#1e3a5f;margin-top:24px;">Documentos adjuntos ({len(docs)})</h3>
      <p style="font-size:13px;color:#444;margin:4px 0 0;">{resumen_docs}</p>
    </div>
    """

    alias = (usuario or {}).get("email_alias") or (usuario or {}).get("correo") or (get_settings().email_admin or "")
    nombre_usuario = (usuario or {}).get("nombre") or "QueSubsidio"
    email_from = f"{nombre_usuario} <{alias}>" if "@" in alias and "<" not in alias else alias

    adjuntos: List[Dict] = []
    for doc in docs:
        url            = doc.get("url_storage")
        nombre_archivo = doc.get("nombre_archivo") or doc.get("tipo") or "documento"
        if not url:
            continue
        try:
            file_bytes = await _descargar_documento_storage(url)
            mime_type  = doc.get("mime_type") or "application/octet-stream"
            adjuntos.append({"filename": nombre_archivo, "content": file_bytes, "content_type": mime_type})
        except Exception as e:
            logger.warning("No se pudo adjuntar '%s': %s", nombre_archivo, e)

    asunto = f"Evaluación Hipotecaria {rut} – Proyecto {proyecto_nombre} Entrega {entrega_label}"

    logger.info("Gmail: enviando evaluación de %s a %d destinatarios (%d adjuntos)", nombre, len(destinatarios), len(adjuntos))
    for destinatario in destinatarios:
        await _gmail_send(
            from_addr=email_from,
            to=[destinatario],
            subject=asunto,
            html=body_html,
            attachments=adjuntos,
        )
        logger.info("Gmail: evaluación enviada a %s", destinatario)
    logger.info("Gmail: evaluación enviada correctamente a todos los destinatarios")

    return {"enviado_a": destinatarios, "documentos_adjuntos": len(adjuntos)}
