import os
from flask import Flask, request, jsonify
import requests
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

# Configuración de Google API y Flask
os.environ["GOOGLE_API_USE_CLIENT_CERTIFICATE"] = "false"
os.environ["GOOGLE_API_USE_DISCOVERY_CACHE"] = "false"

app = Flask(__name__)

# Configuración de Airtable
AIRTABLE_TOKEN = os.environ.get("AIRTABLE_TOKEN") or "Bearer patZO88B42WhnVmCl.c09adf5b589ce3ae34cbf769d1d2b412cb9ba0da6ccede1cc91ccd2a5842495c"
BASE_ID = "appe2SiWhVOuEZJEt"
TABLE_FECHAS = "tblFtf5eMJaDoEykE"
TABLE_ORACULO = "tbl3XcK3LRqYEetIO"
AIRTABLE_API = f"https://api.airtable.com/v0/{BASE_ID}"

# Campos de fechas y oráculo
FIELD_FECHA = "fldHzpCRrHNc6EYq5"
FIELD_KIN_CENTRAL = "fld6z8Dipfe2t6rVJ"
FIELD_IDKIN = "fldTBvI5SXibJHk3L"
FIELD_KIN_ORACULO = "fldGGijwtgKdX1kNf"
FIELD_SELLO = "fld2hwTEDuot1z01o"
FIELD_NUM_SELLO = "fldf4lEQV00vX8QGp"
FIELD_TONO = "fldYfpMc0Gj2oNlbz"
FIELD_GUIA = "fldXSdBjPXL61bf9v"
FIELD_ANALOGO = "fldbFsqbkQqCsSiyY"
FIELD_ANTIPODA = "fldDRTvvc75s5DIA1"
FIELD_OCULTO = "fldRO16Xf91ouVIsv"

# Configuración de Google Drive/RAG
SERVICE_ACCOUNT_FILE = 'credentials.json'
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
FOLDER_ID = os.environ.get("FOLDER_ID") or "1tsz9j9ZODDvaOzUQMLlAn2z-H3B2ozjo"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

# Funciones de utilidad
def normalizar_fecha(fecha_input):
    fecha_input = fecha_input.strip().replace("-", "/")
    for fmt in ["%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"]:
        try:
            d = datetime.datetime.strptime(fecha_input, fmt)
            return f"{d.day}/{d.month:02d}/{d.year}"
        except Exception:
            pass
    return fecha_input

def api_response(status, message, data=None):
    return jsonify({
        "status": status,
        "message": message,
        "data": data
    })

# Endpoints de Airtable
@app.route("/fechas_gregorianas", methods=["GET"])
def obtener_kin_por_fecha():
    user_fecha = request.args.get("fecha") or request.args.get("date")
    if not user_fecha:
        return api_response("error", "Debes enviar la fecha como parámetro (?fecha=). Ejemplo: 2/05/2002", None), 400
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
        return api_response("error", "Debes enviar el kin como parámetro (?kin=). Ejemplo: 224", None), 400
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

# Google Drive - Buscar archivos en subcarpetas
def listar_todos_los_archivos(service, parent_id):
    archivos = []
    page_token = None
    while True:
        response = service.files().list(
            q=f"'{parent_id}' in parents and trashed=false",
            fields="nextPageToken, files(id, name, mimeType, parents)",
            pageToken=page_token
        ).execute()
        for file in response.get('files', []):
            if file['mimeType'] == 'application/vnd.google-apps.folder':
                archivos += listar_todos_los_archivos(service, file['id'])  # Recursión para subcarpetas
            else:
                archivos.append(file)
        page_token = response.get('nextPageToken', None)
        if page_token is None:
            break
    return archivos

# Cargar índice de Google Drive
def cargar_indice_drive():
    global drive_index
    try:
        creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        service = build('drive', 'v3', credentials=creds, cache_discovery=False)
        archivos = listar_todos_los_archivos(service, FOLDER_ID)
        if archivos:
            with index_lock:
                drive_index.clear()
                drive_index.extend(archivos)
    except Exception as e:
        print(f"ERROR AL CARGAR INDICE: {str(e)}")

# Iniciar la carga en un thread
def iniciar_carga_indice_thread():
    thread = threading.Thread(target=cargar_indice_drive)
    thread.start()

# Ruta para consultar el índice
@app.route('/drive/index', methods=['GET'])
def listar_documentos():
    with index_lock:
        docs_list = list(drive_index)
    return api_response("success", "Índice actual de Google Drive cargado.", docs_list), 200

# Ruta para refrescar el índice
@app.route('/drive/refresh', methods=['POST'])
def refrescar_indice():
    iniciar_carga_indice_thread()
    return api_response("success", "Índice de Google Drive recargando en background. Vuelve a consultar en 10-20 segundos."), 200

# Ruta para leer documento
@app.route('/drive/read', methods=['POST'])
def leer_documento():
    data = request.get_json()
    file_id = data.get("documentId")
    page_range = data.get("range")
    if not file_id:
        return api_response("error", "Debes indicar documentId (ID de Google Drive).", None), 400
    try:
        creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        drive_service = build('drive', 'v3', credentials=creds, cache_discovery=False)
        file = drive_service.files().get(fileId=file_id, fields='name, mimeType').execute()
        name = file['name']
        mime_type = file['mimeType']

        request_file = drive_service.files().get_media(fileId=file_id)
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
                        page_text = pdf.pages[i].extract_text()
                        if page_text:
                            text += page_text
                else:
                    for page in pdf.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text += page_text
        elif 'wordprocessingml' in mime_type:
            doc = docx.Document(fh)
            for para in doc.paragraphs:
                text += para.text + "\n"
        else:
            text = fh.read().decode('utf-8')

        doc_map[file_id] = text
        return api_response("success", f"Contenido leído de '{name}'.", {
            "name": name,
            "content": text[:10000] + ("... (truncado)" if len(text) > 10000 else "")
        }), 200
    except Exception as e:
        return api_response("error", f"Error al leer documento: {str(e)}", None), 500

# Main function
if __name__ == '__main__':
    app.run(debug=True)
