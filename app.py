from flask import Flask, request, jsonify
import requests
import os
import datetime
import threading
from googleapiclient.discovery import build
from google.oauth2 import service_account
import pdfplumber
import docx
import openai
import faiss
import numpy as np
import io

app = Flask(__name__)

# ====== CONFIGURACIÓN GENERAL ======
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

# Google Drive + RAG
SERVICE_ACCOUNT_FILE = 'credentials.json'
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
FOLDER_ID = "1tsz9j9ZODDvaOzUQMLlAn2z-H3B2ozjo"  # FOLDER DE ESCUELA DE MAGIA Y LEYES UNIVERSALES
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

# FAISS (búsqueda semántica)
dimension = 1536  # openai ada-002
index = faiss.IndexFlatL2(dimension)
docs = []
doc_map = {}

# RAM para índice de Drive
drive_index = []

# ========================= UTILIDAD GENERAL =========================

def api_response(status, message, data=None):
    return jsonify({
        "status": status,
        "message": message,
        "data": data
    })

def normalizar_fecha(fecha_input):
    fecha_input = fecha_input.strip().replace("-", "/")
    for fmt in ["%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"]:
        try:
            d = datetime.datetime.strptime(fecha_input, fmt)
            return f"{d.day}/{d.month:02d}/{d.year}"
        except Exception:
            pass
    return fecha_input

# ========================= AIRTABLE ENDPOINTS =========================

@app.route("/", methods=["GET"])
def home():
    return api_response(
        "ok",
        "¡Flask está vivo y responde!",
        {"how_to": "Consulta /fechas_gregorianas?fecha=2/05/2002 o /oraculo_tzolkin?kin=224"}
    )

@app.route("/fechas_gregorianas", methods=["GET"])
def obtener_kin_por_fecha():
    user_fecha = request.args.get("fecha") or request.args.get("date")
    if not user_fecha:
        return api_response("error", "Debes enviar la fecha como parámetro (?fecha=).", None), 400
    fecha = normalizar_fecha(user_fecha)
    params = {
        "filterByFormula": f"{{{FIELD_FECHA}}}='{fecha}'",
        "fields[]": [FIELD_FECHA, FIELD_KIN_CENTRAL]
    }
    headers = {"Authorization": AIRTABLE_TOKEN}
    r = requests.get(f"{AIRTABLE_API}/{TABLE_FECHAS}", params=params, headers=headers)
    records = r.json().get("records", [])
    if not records:
        return api_response("not_found", f"No se encontró Kin para la fecha {fecha}.", None), 404
    dato = records[0]["fields"]
    return api_response("success", f"Kin encontrado para la fecha {fecha}.", dato), 200

@app.route("/oraculo_tzolkin", methods=["GET"])
def obtener_oraculo_por_kin():
    kin = request.args.get("kin")
    if not kin:
        return api_response("error", "Debes enviar el kin como parámetro (?kin=).", None), 400
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
        return api_response("not_found", f"No se encontró oráculo para Kin {kin}.", None), 404
    dato = records[0]["fields"]
    return api_response("success", f"Oráculo encontrado para Kin {kin}.", dato), 200

# ========================= GOOGLE DRIVE + RAG =========================

def cargar_indice_drive():
    global drive_index
    creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    service = build('drive', 'v3', credentials=creds)
    archivos = []
    page_token = None
    while True:
        response = service.files().list(
            q=f"'{FOLDER_ID}' in parents and trashed = false",
            fields="nextPageToken, files(id, name, mimeType)",
            pageToken=page_token
        ).execute()
        archivos.extend(response.get('files', []))
        page_token = response.get('nextPageToken', None)
        if page_token is None:
            break
    drive_index = archivos

def iniciar_indice():
    thread = threading.Thread(target=cargar_indice_drive)
    thread.start()
iniciar_indice()

@app.route('/drive/index', methods=['GET'])
def listar_documentos():
    return api_response("success", "Índice actual de Google Drive cargado.", drive_index), 200

@app.route('/drive/refresh', methods=['POST'])
def refrescar_indice():
    cargar_indice_drive()
    return api_response("success", "Índice recargado.", {"total": len(drive_index)}), 200

@app.route('/drive/read', methods=['POST'])
def leer_documento():
    data = request.get_json()
    file_id = data.get("documentId")
    page_range = data.get("range")
    if not file_id:
        return api_response("error", "Debes indicar documentId (ID de Google Drive).", None), 400

    try:
        creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        service = build('drive', 'v3', credentials=creds)
        file = service.files().get(fileId=file_id, fields='name, mimeType').execute()
        name = file['name']
        mime_type = file['mimeType']
        request_file = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        from googleapiclient.http import MediaIoBaseDownload
        downloader = MediaIoBaseDownload(fh, request_file)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        fh.seek(0)
        text = ""
        if 'pdf' in mime_type:
            with pdfplumber.open(fh) as pdf:
                total_pages = len(pdf.pages)
                if page_range:
                    start, end = [int(x)-1 for x in page_range.split('-')]
                    for i in range(start, min(end+1, total_pages)):
                        text += pdf.pages[i].extract_text() or ""
                else:
                    for page in pdf.pages:
                        text += page.extract_text() or ""
        elif 'wordprocessingml' in mime_type:  # DOCX
            doc = docx.Document(fh)
            for para in doc.paragraphs:
                text += para.text + "\n"
        else:
            text = fh.read().decode('utf-8')

        doc_map[file_id] = text
        chunks = [text[i:i+1000] for i in range(0, len(text), 1000)]
        for chunk in chunks:
            emb = openai.Embedding.create(input=chunk, model="text-embedding-ada-002")['data'][0]['embedding']
            index.add(np.array([emb], dtype=np.float32))
            docs.append(chunk)

        return api_response("success", f"Contenido leído de '{name}'.", {
            "name": name,
            "content": text[:10000] + ("... (truncado)" if len(text) > 10000 else "")
        }), 200
    except Exception as e:
        return api_response("error", f"Error al leer documento: {str(e)}", None), 500

@app.route('/drive/search', methods=['POST'])
def buscar_contenido():
    data = request.get_json()
    query = data.get("query")
    if not query:
        return api_response("error", "Debes indicar 'query' a buscar.", None), 400
    try:
        emb = openai.Embedding.create(input=query, model="text-embedding-ada-002")['data'][0]['embedding']
        D, I = index.search(np.array([emb], dtype=np.float32), k=3)
        resultados = [docs[i] for i in I[0] if i < len(docs)]
        return api_response("success", "Resultados de búsqueda semántica.", {
            "query": query,
            "results": resultados
        }), 200
    except Exception as e:
        return api_response("error", f"Error en la búsqueda semántica: {str(e)}", None), 500

# ========================= INTELIGENCIA ANALÍTICA Y ADAPTATIVA =========================

def perfilar_pregunta(texto):
    texto = texto.lower()
    if any(x in texto for x in ["dinero", "negocio", "éxito", "prosperidad", "ventas", "abundancia"]):
        return "pragmatico"
    if any(x in texto for x in ["sentido de la vida", "existencia", "vacío", "angustia existencial"]):
        return "existencial"
    if any(x in texto for x in ["amor", "pareja", "relación", "amistad", "confianza", "comunicación"]):
        return "relacional"
    if any(x in texto for x in ["espiritual", "maya", "leyes universales", "sincronario", "energía", "sincronía"]):
        return "esoterico"
    return "general"

def construir_respuesta(pregunta, kin_info, oraculo, literatura_relevante, perfil):
    resumen_ley = literatura_relevante[0][:400] if literatura_relevante else "Consulta relevante no disponible."
    if perfil == "pragmatico":
        return (
            f"En términos prácticos, el Kin Maya de hoy ({kin_info}) favorece acciones directas y eficientes. "
            f"Recomendación basada en la literatura seleccionada: {resumen_ley}"
        )
    elif perfil == "existencial":
        return (
            f"Desde una visión existencial, el oráculo maya ({kin_info}) invita a reflexionar sobre propósito y transformación. "
            f"Fragmento relevante: {resumen_ley}"
        )
    elif perfil == "relacional":
        return (
            f"Para relaciones humanas, el kin maya actual ({kin_info}) indica oportunidades para colaboración y empatía. "
            f"Sugerencia desde la literatura: {resumen_ley}"
        )
    elif perfil == "esoterico":
        return (
            f"Desde la perspectiva maya y hermética, el oráculo {oraculo} se relaciona con esta ley universal: {resumen_ley}"
        )
    else:
        return (
            f"Bajo la influencia del Kin Maya {kin_info}, se recomienda: {resumen_ley}"
        )

def buscar_leyes_relevantes(pregunta, topk=2):
    # DEMO: Devuelve ejemplos de "leyes universales"
    return [
        "Toda causa tiene su efecto. Aplica tu intención alineada para manifestar en el sincronario.",
        "El principio de vibración: eleva tu frecuencia para sincronizar con oportunidades del día."
    ]

@app.route('/oraculo_inteligente', methods=['POST'])
def oraculo_inteligente():
    data = request.get_json()
    pregunta = data.get("pregunta", "")
    fecha_custom = data.get("fecha")  # Formato: dd/mm/yyyy

    perfil = perfilar_pregunta(pregunta)

    # 1. Saca kin y oráculo del día (o de la fecha dada)
    fecha_analizar = normalizar_fecha(fecha_custom) if fecha_custom else datetime.datetime.now().strftime("%d/%m/%Y")
    params = {
        "filterByFormula": f"{{{FIELD_FECHA}}}='{fecha_analizar}'",
        "fields[]": [FIELD_FECHA, FIELD_KIN_CENTRAL]
    }
    headers = {"Authorization": AIRTABLE_TOKEN}
    r = requests.get(f"{AIRTABLE_API}/{TABLE_FECHAS}", params=params, headers=headers)
    kin = r.json()["records"][0]["fields"][FIELD_KIN_CENTRAL] if r.status_code == 200 and r.json().get("records") else 1

    params2 = {
        "filterByFormula": f"{{{FIELD_KIN_ORACULO}}}={kin}",
        "fields[]": [
            FIELD_IDKIN, FIELD_KIN_ORACULO, FIELD_SELLO, FIELD_NUM_SELLO, FIELD_TONO,
            FIELD_GUIA, FIELD_ANALOGO, FIELD_ANTIPODA, FIELD_OCULTO
        ]
    }
    r2 = requests.get(f"{AIRTABLE_API}/{TABLE_ORACULO}", params=params2, headers=headers)
    oraculo = r2.json()["records"][0]["fields"] if r2.status_code == 200 and r2.json().get("records") else {}

    # 2. Busca literatura relevante (aquí puedes mejorar para buscar en tu índice RAG de Drive según perfil)
    leyes_rel = buscar_leyes_relevantes(pregunta, topk=2)

    # 3. Construye respuesta personalizada
    respuesta_final = construir_respuesta(
        pregunta, kin, oraculo, leyes_rel, perfil
    )

    return api_response("success", "Respuesta estratégica según tu perfil.", {
        "perfil_detectado": perfil,
        "respuesta": respuesta_final,
        "kin_info": kin,
        "oraculo": oraculo,
        "fecha": fecha_analizar
    }), 200

# ========================= ERROR HANDLER =========================

@app.errorhandler(404)
def not_found(e):
    return api_response("error", "Ruta no encontrada (404).", None), 404

@app.errorhandler(500)
def internal_error(e):
    return api_response("error", "Error interno del servidor (500).", None), 500

# ========================= MAIN =========================
@app.route('/drive/service_account_email', methods=['GET'])
def ver_email_service_account():
    return jsonify({"service_account_email": creds.service_account_email})

if __name__ == '__main__':
    app.run(debug=True)
