@app.route("/", methods=["GET"])
def home():
    return "¡Flask está vivo y responde!"


from flask import Flask, request, jsonify
import requests
import os
import datetime
import re

# (Opcional: para Drive/RAG)
# from googleapiclient.discovery import build
# from google.oauth2 import service_account
# import pdfplumber
# import docx
# import io
# import openai
# import faiss
# import numpy as np

app = Flask(__name__)

# ====== CONFIGURACIÓN ======
# Airtable
AIRTABLE_TOKEN = os.environ.get("AIRTABLE_TOKEN") or "Bearer patZO88B42WhnVmCl.c09adf5b589ce3ae34cbf769d1d2b412cb9ba0da6ccede1cc91ccd2a5842495c"
BASE_ID = "appe2SiWhVOuEZJEt"
TABLE_FECHAS = "tblFtf5eMJaDoEykE"
TABLE_ORACULO = "tbl3XcK3LRqYEetIO"
AIRTABLE_API = f"https://api.airtable.com/v0/{BASE_ID}"

# CAMPOS DE FECHAS_GREGORIANAS
FIELD_FECHA = "fechafldHzpCRrHNc6EYq5"
FIELD_KIN_CENTRAL = "Kin Centralfld6z8Dipfe2t6rVJ"

# CAMPOS DE ORACULO_TZOLKIN
FIELD_IDKIN = "idkinfldTBvI5SXibJHk3L"
FIELD_KIN_ORACULO = "Kin CentralfldGGijwtgKdX1kNf"
FIELD_SELLO = "Sello Centralfld2hwTEDuot1z01o"
FIELD_NUM_SELLO = "numero sellofldf4lEQV00vX8QGp"
FIELD_TONO = "Tono CentralfldYfpMc0Gj2oNlbz"
FIELD_GUIA = "GuíafldXSdBjPXL61bf9v"
FIELD_ANALOGO = "AnálogofldbFsqbkQqCsSiyY"
FIELD_ANTIPODA = "AntípodafldDRTvvc75s5DIA1"
FIELD_OCULTO = "OcultofldRO16Xf91ouVIsv"

# (Opcional para Google Drive/RAG)
# SERVICE_ACCOUNT_FILE = 'credentials.json'
# SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
# FOLDER_ID = 'TU_FOLDER_ID'
# openai.api_key = "TU_OPENAI_API_KEY"

# ========== UTILIDAD: NORMALIZAR FECHA ==========
def normalizar_fecha(fecha_input):
    fecha_input = fecha_input.strip().replace("-", "/")
    for fmt in ["%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"]:
        try:
            d = datetime.datetime.strptime(fecha_input, fmt)
            return f"{d.day}/{d.month:02d}/{d.year}"
        except Exception:
            pass
    # Si no pudo parsear, regresa la entrada original
    return fecha_input

# ========== ENDPOINT: BUSCAR KIN CENTRAL POR FECHA ==========
@app.route("/fechas_gregorianas", methods=["GET"])
def obtener_kin_por_fecha():
    # Permite ?fecha= o ?date=
    user_fecha = request.args.get("fecha") or request.args.get("date")
    if not user_fecha:
        return jsonify({
            "error": "Debes enviar la fecha como parámetro (?fecha=). "
                     "Formatos aceptados: 2/05/2002, 02/05/2002, 2002-05-02, etc."
        }), 400
    fecha = normalizar_fecha(user_fecha)
    params = {
        "filterByFormula": f"{{{FIELD_FECHA}}}='{fecha}'",
        "fields[]": [FIELD_FECHA, FIELD_KIN_CENTRAL]
    }
    headers = {"Authorization": AIRTABLE_TOKEN}
    r = requests.get(f"{AIRTABLE_API}/{TABLE_FECHAS}", params=params, headers=headers)
    return r.json(), r.status_code

# ========== ENDPOINT: BUSCAR ORÁCULO POR KIN ==========
@app.route("/oraculo_tzolkin", methods=["GET"])
def obtener_oraculo_por_kin():
    kin = request.args.get("kin")
    if not kin:
        return jsonify({"error": "Debes enviar el kin como parámetro (?kin=)"}), 400
    params = {
        "filterByFormula": f"{{{FIELD_KIN_ORACULO}}}={kin}",
        "fields[]": [
            FIELD_IDKIN, FIELD_KIN_ORACULO, FIELD_SELLO, FIELD_NUM_SELLO, FIELD_TONO,
            FIELD_GUIA, FIELD_ANALOGO, FIELD_ANTIPODA, FIELD_OCULTO
        ]
    }
    headers = {"Authorization": AIRTABLE_TOKEN}
    r = requests.get(f"{AIRTABLE_API}/{TABLE_ORACULO}", params=params, headers=headers)
    return r.json(), r.status_code

# =======================
# AQUÍ VAN LOS ENDPOINTS PARA GOOGLE DRIVE Y RAG
# (Déjalos listos para agregar el código cuando quieras)
# =======================

@app.route('/drive/index', methods=['GET'])
def listar_documentos():
    # --- Código de Google Drive aquí ---
    return jsonify({"error": "Función no implementada todavía"}), 501

@app.route('/drive/read', methods=['POST'])
def leer_documento():
    # --- Código de Google Drive aquí ---
    return jsonify({"error": "Función no implementada todavía"}), 501

@app.route('/drive/search', methods=['POST'])
def buscar_contenido():
    # --- Código de búsqueda semántica aquí ---
    return jsonify({"error": "Función no implementada todavía"}), 501

# =======================

if __name__ == '__main__':
    app.run(debug=True)
