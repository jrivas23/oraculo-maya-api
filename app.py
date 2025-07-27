from flask import Flask, request, jsonify
import requests
import os
import datetime

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "ok",
        "message": "¡Flask está vivo y responde!",
        "how_to": "Consulta /fechas_gregorianas?fecha=2/05/2002 o /oraculo_tzolkin?kin=224"
    })

# ====== CONFIGURACIÓN ======
# Airtable
AIRTABLE_TOKEN = os.environ.get("AIRTABLE_TOKEN") or "Bearer patZO88B42WhnVmCl.c09adf5b589ce3ae34cbf769d1d2b412cb9ba0da6ccede1cc91ccd2a5842495c"
BASE_ID = "appe2SiWhVOuEZJEt"
TABLE_FECHAS = "tblFtf5eMJaDoEykE"
TABLE_ORACULO = "tbl3XcK3LRqYEetIO"
AIRTABLE_API = f"https://api.airtable.com/v0/{BASE_ID}"

# CAMPOS DE FECHAS_GREGORIANAS
FIELD_FECHA = "fldHzpCRrHNc6EYq5"
FIELD_KIN_CENTRAL = "fld6z8Dipfe2t6rVJ"

# CAMPOS DE ORACULO_TZOLKIN
FIELD_IDKIN = "fldTBvI5SXibJHk3L"
FIELD_KIN_ORACULO = "fldGGijwtgKdX1kNf"
FIELD_SELLO = "fld2hwTEDuot1z01o"
FIELD_NUM_SELLO = "fldf4lEQV00vX8QGp"
FIELD_TONO = "fldYfpMc0Gj2oNlbz"
FIELD_GUIA = "fldXSdBjPXL61bf9v"
FIELD_ANALOGO = "fldbFsqbkQqCsSiyY"
FIELD_ANTIPODA = "fldDRTvvc75s5DIA1"
FIELD_OCULTO = "fldRO16Xf91ouVIsv"

# ========== UTILIDAD: NORMALIZAR FECHA ==========
def normalizar_fecha(fecha_input):
    fecha_input = fecha_input.strip().replace("-", "/")
    for fmt in ["%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"]:
        try:
            d = datetime.datetime.strptime(fecha_input, fmt)
            return f"{d.day}/{d.month:02d}/{d.year}"
        except Exception:
            pass
    return fecha_input

# ========== RESPUESTA BONITA ==========
def api_response(status, message, data=None):
    return jsonify({
        "status": status,
        "message": message,
        "data": data
    })

# ========== ENDPOINT: BUSCAR KIN CENTRAL POR FECHA ==========
@app.route("/fechas_gregorianas", methods=["GET"])
def obtener_kin_por_fecha():
    user_fecha = request.args.get("fecha") or request.args.get("date")
    if not user_fecha:
        return api_response(
            "error",
            "Debes enviar la fecha como parámetro (?fecha=). Ejemplo: 2/05/2002",
            None
        ), 400
    fecha = normalizar_fecha(user_fecha)
    params = {
        "filterByFormula": f"{{{FIELD_FECHA}}}='{fecha}'",
        "fields[]": [FIELD_FECHA, FIELD_KIN_CENTRAL]
    }
    headers = {"Authorization": AIRTABLE_TOKEN}
    r = requests.get(f"{AIRTABLE_API}/{TABLE_FECHAS}", params=params, headers=headers)
    records = r.json().get("records", [])
    if not records:
        return api_response(
            "not_found",
            f"No se encontró Kin para la fecha {fecha}.",
            None
        ), 404
    dato = records[0]["fields"]
    return api_response(
        "success",
        f"Kin encontrado para la fecha {fecha}.",
        dato
    ), 200

# ========== ENDPOINT: BUSCAR ORÁCULO POR KIN ==========
@app.route("/oraculo_tzolkin", methods=["GET"])
def obtener_oraculo_por_kin():
    kin = request.args.get("kin")
    if not kin:
        return api_response(
            "error",
            "Debes enviar el kin como parámetro (?kin=). Ejemplo: 224",
            None
        ), 400
    params = {
        "filterByFormula": f"{{{FIELD_KIN_ORACULO}}}={kin}",
        "fields[]": [
            FIELD_IDKIN, FIELD_KIN_ORACULO, FIELD_SELLO, FIELD_NUM_SELLO, FIELD_TONO,
            FIELD_GUIA, FIELD_ANALOGO, FIELD_ANTIPODA, FIELD_OCULTO
        ]
    }
    headers = {"Authorization": AIRTABLE_TOKEN}
    r = requests.get(f"{AIRTABLE_API}/{TABLE_ORACULO}", params=params, headers=headers)
    records = r.json().get("records", [])
    if not records:
        return api_response(
            "not_found",
            f"No se encontró oráculo para Kin {kin}.",
            None
        ), 404
    dato = records[0]["fields"]
    return api_response(
        "success",
        f"Oráculo encontrado para Kin {kin}.",
        dato
    ), 200

# ========== GOOGLE DRIVE & RAG: PLACEHOLDER ==========

@app.route('/drive/index', methods=['GET'])
def listar_documentos():
    return api_response("not_implemented", "Función no implementada todavía.", None), 501

@app.route('/drive/read', methods=['POST'])
def leer_documento():
    return api_response("not_implemented", "Función no implementada todavía.", None), 501

@app.route('/drive/search', methods=['POST'])
def buscar_contenido():
    return api_response("not_implemented", "Función no implementada todavía.", None), 501

# ========== ERROR HANDLER BONITO ==========
@app.errorhandler(404)
def not_found(e):
    return api_response("error", "Ruta no encontrada (404).", None), 404

@app.errorhandler(500)
def internal_error(e):
    return api_response("error", "Error interno del servidor (500).", None), 500

# =======================

if __name__ == '__main__':
    app.run(debug=True)
