from flask import Flask, request, jsonify
import requests
import os
import datetime
import threading

app = Flask(__name__)

@app.route("/", methods=["GET"])
def home():
    return jsonify({
        "status": "ok",
        "message": "¡Flask está vivo y responde!",
        "how_to": "Consulta /fechas_gregorianas?fecha=2/05/2002 o /oraculo_tzolkin?kin=224"
    })

# Para Google Drive y RAG
from googleapiclient.discovery import build
from google.oauth2 import service_account
import pdfplumber
import docx
import openai
import faiss
import numpy as np
import io

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

# === GOOGLE DRIVE & RAG CONFIGURACIÓN ===
SERVICE_ACCOUNT_FILE = 'credentials.json'
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
FOLDER_ID = os.environ.get("FOLDER_ID") or "1tsz9j9ZODDvaOzUQMLlAn2z-H3B2ozjo"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

# Inicializa Google Drive Service
creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
drive_service = build('drive', 'v3', credentials=creds)

# FAISS (búsqueda semántica)
dimension = 1536  # openai ada-002
index = faiss.IndexFlatL2(dimension)
docs = []
doc_map = {}

app = Flask(__name__)

# RAM para índice
drive_index = []

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

# Al iniciar el server, carga el índice (en un thread por rapidez)
def iniciar_indice():
    thread = threading.Thread(target=cargar_indice_drive)
    thread.start()

iniciar_indice()  # Lo hace al levantar el Flask

@app.route('/drive/index', methods=['GET'])
def listar_documentos():
    return jsonify({"documents": drive_index})

@app.route('/drive/refresh', methods=['POST'])
def refrescar_indice():
    cargar_indice_drive()
    return jsonify({"status": "Índice recargado", "total": len(drive_index)})

@app.route('/drive/read', methods=['POST'])
def leer_documento():
    data = request.json
    doc_id = data.get("documentId")
    doc = next((d for d in drive_index if d["id"] == doc_id), None)
    if not doc:
        return jsonify({"error": "Documento no encontrado"}), 404
    creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    service = build('drive', 'v3', credentials=creds)
    request_file = service.files().get_media(fileId=doc_id)
    fh = io.BytesIO()
    from googleapiclient.http import MediaIoBaseDownload
    downloader = MediaIoBaseDownload(fh, request_file)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    fh.seek(0)
    text = ""
    if 'pdf' in doc['mimeType']:
        with pdfplumber.open(fh) as pdf:
            for page in pdf.pages:
                text += page.extract_text() + "\n"
    elif 'document' in doc['mimeType']:
        d = docx.Document(fh)
        text = "\n".join([p.text for p in d.paragraphs])
    else:
        text = fh.read().decode("utf-8")
    return jsonify({"name": doc['name'], "content": text[:10000]})

# (Opcional: endpoint para búsqueda semántica avanzada)
# @app.route('/drive/search', methods=['POST'])

# ========== DRIVE: LISTAR DOCUMENTOS ==========
@app.route('/drive/index', methods=['GET'])
def listar_documentos():
    try:
        results = drive_service.files().list(
            q=f"'{FOLDER_ID}' in parents and trashed=false",
            pageSize=50,
            fields="files(id, name, mimeType)"
        ).execute()
        items = results.get('files', [])
        return api_response("success", "Documentos listados correctamente.", items), 200
    except Exception as e:
        return api_response("error", f"Error al listar documentos: {str(e)}", None), 500

# ========== DRIVE: LEER DOCUMENTO ==========
@app.route('/drive/read', methods=['POST'])
def leer_documento():
    data = request.get_json()
    file_id = data.get("documentId")
    page_range = data.get("range")  # formato: "1-5", opcional

    if not file_id:
        return api_response("error", "Debes indicar documentId (ID de Google Drive).", None), 400

    try:
        file = drive_service.files().get(fileId=file_id, fields='name, mimeType').execute()
        name = file['name']
        mime_type = file['mimeType']

        request_file = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = googleapiclient.http.MediaIoBaseDownload(fh, request_file)
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
        # Opcional: agregar a FAISS para búsqueda RAG
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

# ========== DRIVE: BÚSQUEDA SEMÁNTICA (RAG) ==========
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
