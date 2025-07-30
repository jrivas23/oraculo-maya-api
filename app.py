
import os
import datetime
import threading
import io

from flask import Flask, request, jsonify
import requests

# ========== GOOGLE ENV FIXES ==========
os.environ["GOOGLE_API_USE_CLIENT_CERTIFICATE"] = "false"
os.environ["GOOGLE_API_USE_DISCOVERY_CACHE"] = "false"

from googleapiclient.discovery import build
from google.oauth2 import service_account
import pdfplumber
import docx
import openai
import faiss
import numpy as np

# ========== FLASK APP ==========
app = Flask(__name__)

# ========== AIRTABLE CONFIG ==========
AIRTABLE_TOKEN = os.environ.get("AIRTABLE_TOKEN") or "Bearer patZO88B42WhnVmCl.c09adf5b589ce3ae34cbf769d1d2b412cb9ba0da6ccede1cc91ccd2a5842495c"
BASE_ID = "appe2SiWhVOuEZJEt"
TABLE_FECHAS = "tblFtf5eMJaDoEykE"
TABLE_ORACULO = "tbl3XcK3LRqYEetIO"
AIRTABLE_API = f"https://api.airtable.com/v0/{BASE_ID}"

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

# ========== GOOGLE DRIVE/RAG CONFIG ==========
SERVICE_ACCOUNT_FILE = 'credentials.json'
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
FOLDER_ID = os.environ.get("FOLDER_ID") or "1tsz9j9ZODDvaOzUQMLlAn2z-H3B2ozjo"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

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

def api_response(status, message, data=None):
    return jsonify({
        "status": status,
        "message": message,
        "data": data
    })

# ========== AIRTABLE HELPERS ==========
def get_kin_from_date(fecha):
    params = {
        "filterByFormula": f"{{{FIELD_FECHA}}}='{fecha}'",
        "fields[]": [FIELD_FECHA, FIELD_KIN_CENTRAL]
    }
    headers = {"Authorization": AIRTABLE_TOKEN}
    r = requests.get(f"{AIRTABLE_API}/{TABLE_FECHAS}", params=params, headers=headers)
    records = r.json().get("records", [])
    if not records:
        return None
    return records[0]["fields"].get(FIELD_KIN_CENTRAL)

def get_oraculo_from_kin(kin):
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
        return None
    return records[0]["fields"]

# ========== GOOGLE DRIVE & RAG ==========

drive_index = []
index_lock = threading.Lock()
faiss_index = None
docs = []
doc_map = {}

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
                archivos += listar_todos_los_archivos(service, file['id'])
            else:
                archivos.append(file)
        page_token = response.get('nextPageToken', None)
        if page_token is None:
            break
    return archivos

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
        print("ERROR AL CARGAR INDICE:", str(e))

def iniciar_carga_indice_thread():
    thread = threading.Thread(target=cargar_indice_drive)
    thread.start()

iniciar_carga_indice_thread()

# ========== FLASK ENDPOINTS ==========

@app.route("/", methods=["GET"])
def home():
    return api_response(
        "success",
        "¡Flask está vivo y responde!",
        {
            "how_to": [
                "/fechas_gregorianas?fecha=2/05/2002",
                "/oraculo_tzolkin?kin=224",
                "/oraculo (POST)",
                "/drive/index",
                "/drive/refresh (POST)",
                "/drive/read (POST)",
                "/drive/search (POST)"
            ]
        }
    )

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
    kin = get_kin_from_date(fecha)
    if not kin:
        return api_response(
            "not_found",
            f"No se encontró Kin para la fecha {fecha}.",
            None
        ), 404
    return api_response(
        "success",
        f"Kin encontrado para la fecha {fecha}.",
        {"fecha": fecha, "Kin Central": kin}
    ), 200

@app.route("/oraculo_tzolkin", methods=["GET"])
def obtener_oraculo_por_kin():
    kin = request.args.get("kin")
    if not kin:
        return api_response(
            "error",
            "Debes enviar el kin como parámetro (?kin=). Ejemplo: 224",
            None
        ), 400
    oraculo = get_oraculo_from_kin(kin)
    if not oraculo:
        return api_response(
            "not_found",
            f"No se encontró oráculo para Kin {kin}.",
            None
        ), 404
    return api_response(
        "success",
        f"Oráculo encontrado para Kin {kin}.",
        oraculo
    ), 200

@app.route('/drive/index', methods=['GET'])
def listar_documentos():
    with index_lock:
        docs_list = list(drive_index)
    return api_response("success", "Índice actual de Google Drive cargado.", docs_list), 200

@app.route('/drive/refresh', methods=['POST'])
def refrescar_indice():
    iniciar_carga_indice_thread()
    return api_response("success", "Índice de Google Drive recargando en background. Vuelve a consultar en 10-20 segundos."), 200

@app.route('/drive/read', methods=['POST'])
def leer_documento():
    data = request.get_json()
    file_id = data.get("documentId")
    page_range = data.get("range")  # formato: "1-5", opcional

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
        elif 'wordprocessingml' in mime_type:  # DOCX
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

# ====== RAG FAISS INDEX (solo para búsqueda avanzada) ======
def cargar_faiss_y_embeddings():
    global faiss_index, docs, doc_map
    if faiss_index is not None and len(docs) > 0:
        return
    creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    drive_service = build('drive', 'v3', credentials=creds)
    with index_lock:
        files = list(drive_index)
    docs = []
    embeddings = []
    max_files = 20  # Limita para no saturar RAM
    for i, doc in enumerate(files[:max_files]):
        file_id = doc["id"]
        if file_id not in doc_map:
            req = drive_service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            from googleapiclient.http import MediaIoBaseDownload
            downloader = MediaIoBaseDownload(fh, req)
            done = False
            while not done:
                status, done = downloader.next_chunk()
            fh.seek(0)
            text = ""
            if 'pdf' in doc['mimeType']:
                with pdfplumber.open(fh) as pdf:
                    for page in pdf.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text += page_text
            elif 'wordprocessingml' in doc['mimeType']:
                docx_doc = docx.Document(fh)
                for para in docx_doc.paragraphs:
                    text += para.text + "\n"
            else:
                text = fh.read().decode("utf-8")
            doc_map[file_id] = text
        text = doc_map[file_id]
        chunks = [text[i:i+1000] for i in range(0, len(text), 1000)]
        for chunk in chunks:
            docs.append(chunk)
            emb = openai.Embedding.create(input=chunk, model="text-embedding-ada-002")['data'][0]['embedding']
            embeddings.append(emb)
    if embeddings:
        dim = len(embeddings[0])
        faiss_index = faiss.IndexFlatL2(dim)
        faiss_index.add(np.array(embeddings, dtype=np.float32))

@app.route('/drive/search', methods=['POST'])
def buscar_contenido():
    data = request.get_json()
    query = data.get("query")
    if not query:
        return api_response("error", "Debes indicar 'query' a buscar.", None), 400
    try:
        cargar_faiss_y_embeddings()
        emb = openai.Embedding.create(input=query, model="text-embedding-ada-002")['data'][0]['embedding']
        D, I = faiss_index.search(np.array([emb], dtype=np.float32), k=3)
        resultados = [docs[i] for i in I[0] if i < len(docs)]
        return api_response("success", "Resultados de búsqueda semántica.", {
            "query": query,
            "results": resultados
        }), 200
    except Exception as e:
        return api_response("error", f"Error en la búsqueda semántica: {str(e)}", None), 500

# ========== ANALISIS INTEGRADO ORÁCULO MAYA + ESCUELAS ==========

def detectar_perfil_usuario(texto):
    texto = texto.lower()
    if "pragmático" in texto or "productividad" in texto or "practical" in texto:
        return "pragmatico"
    elif "jung" in texto or "psicología" in texto or "jungiano" in texto:
        return "psicologia"
    elif "maya" in texto or "argüelles" in texto or "onda encantada" in texto or "sincronario" in texto:
        return "maya"
    elif "leyes universales" in texto or "magia" in texto or "esoterismo" in texto:
        return "magia"
    return "general"

def obtener_literatura_para_perfil(perfil):
    if perfil == "pragmatico":
        return ["Carl Jung", "Psicología profunda", "Productividad"]
    elif perfil == "psicologia":
        return ["Carl Jung", "Psicología profunda"]
    elif perfil == "maya":
        return ["José Argüelles", "El Factor Maya", "Sincronario 13:20"]
    elif perfil == "magia":
        return ["Escuela de Magia", "Leyes Universales"]
    return ["José Argüelles", "Sincronario 13:20", "Jung", "Escuela de Magia"]

@app.route("/oraculo", methods=["POST"])
def oraculo_maya():
    data = request.get_json()
    fecha_nacimiento = data.get("fecha_nacimiento")
    profundizar = data.get("profundizar", False)
    perfil_usuario = data.get("perfil_usuario", "")

    if not fecha_nacimiento:
        return api_response("error", "Debes enviar 'fecha_nacimiento' (formato DD/MM/YYYY).", None), 400

    fecha_nacimiento = normalizar_fecha(fecha_nacimiento)
    kin_natal = get_kin_from_date(fecha_nacimiento)
    if not kin_natal:
        return api_response("not_found", f"No se encontró Kin para la fecha {fecha_nacimiento}.", None), 404

    oraculo_natal = get_oraculo_from_kin(kin_natal)
    if not oraculo_natal:
        return api_response("not_found", f"No se encontró oráculo para Kin {kin_natal}.", None), 404

    # 1. Calcular el kin del cumpleaños anterior (a la fecha de hoy)
    hoy = datetime.datetime.now()
    fecha_cumple_este_ano = datetime.datetime(
        year=hoy.year, month=int(fecha_nacimiento.split("/")[1]), day=int(fecha_nacimiento.split("/")[0])
    )
    if hoy < fecha_cumple_este_ano:
        fecha_cumple_anterior = fecha_cumple_este_ano.replace(year=hoy.year-1)
    else:
        fecha_cumple_anterior = fecha_cumple_este_ano
    kin_cumple_anterior = get_kin_from_date(fecha_cumple_anterior.strftime("%-d/%m/%Y"))
    # 2. Kin del año maya anterior (siempre 26/07 del año anterior si hoy < 26/julio, o de este año si ya pasó)
    anio_para_ano_maya = hoy.year if hoy.month > 7 or (hoy.month == 7 and hoy.day >= 26) else hoy.year - 1
    fecha_ano_maya = f"26/07/{anio_para_ano_maya}"
    kin_ano_maya = get_kin_from_date(fecha_ano_maya)
    # 3. Kin del día de hoy (kin tierra)
    fecha_hoy_str = hoy.strftime("%-d/%m/%Y")
    kin_hoy = get_kin_from_date(fecha_hoy_str)

    # 4. Calcular constante (suma de 3 kines mod 260, sin incluir kin de hoy)
    try:
        suma_constante = sum([int(k) for k in [kin_natal, kin_cumple_anterior, kin_ano_maya] if k])
        constante = (suma_constante % 260) or 260
    except Exception:
        constante = None

    # 5. Kin línea de tiempo personal = (constante + kin_hoy) % 260
    kin_tiempo_personal = None
    if constante and kin_hoy:
        kin_tiempo_personal = (int(constante) + int(kin_hoy)) % 260 or 260

    # 6. Buscar la mision de la onda encantada (tono 1 antes del natal)
    tono_natal = oraculo_natal.get(FIELD_TONO)
    num_kin_natal = int(kin_natal)
    kin_mision = num_kin_natal - int(tono_natal) + 1
    if kin_mision < 1:
        kin_mision = kin_mision + 260
    oraculo_mision = get_oraculo_from_kin(kin_mision)

    # 7. Decidir escuela según pregunta del usuario
    perfil_detectado = detectar_perfil_usuario(perfil_usuario)
    escuelas = obtener_literatura_para_perfil(perfil_detectado)

    # 8. Analizar y preparar la respuesta
    analisis = f"Análisis estratégico para fecha de nacimiento {fecha_nacimiento} (Kin {kin_natal}):\n\n"
    analisis += "- **Perfil natal:**\n"
    analisis += f"  - Sello: {oraculo_natal.get(FIELD_SELLO)}\n"
    analisis += f"  - Tono: {oraculo_natal.get(FIELD_TONO)}\n"
    analisis += f"  - Guía: {oraculo_natal.get(FIELD_GUIA)}\n"
    analisis += f"  - Análogo: {oraculo_natal.get(FIELD_ANALOGO)}\n"
    analisis += f"  - Antípoda: {oraculo_natal.get(FIELD_ANTIPODA)}\n"
    analisis += f"  - Oculto: {oraculo_natal.get(FIELD_OCULTO)}\n\n"
    if oraculo_mision:
        analisis += f"- **Misión de la onda encantada (tono 1, Kin {kin_mision}):**\n"
        analisis += f"  - Sello: {oraculo_mision.get(FIELD_SELLO)}\n"
        analisis += f"  - Tono: {oraculo_mision.get(FIELD_TONO)}\n\n"
    analisis += "Aspectos positivos y negativos de cada energía los puedes explorar más con las escuelas recomendadas.\n\n"
    analisis += f"**Escuelas sugeridas para profundización:** {', '.join(escuelas)}.\n"

    # Si profundizar, incluir constante y análisis de línea de tiempo
    if profundizar and constante and kin_tiempo_personal:
        analisis += f"\nConstante personal calculada: {constante}\n"
        analisis += f"Kin línea de tiempo personal de hoy ({fecha_hoy_str}): {kin_tiempo_personal}\n"
        oraculo_linea = get_oraculo_from_kin(kin_tiempo_personal)
        if oraculo_linea:
            analisis += "- **Energía del día (personal):**\n"
            analisis += f"  - Sello: {oraculo_linea.get(FIELD_SELLO)}\n"
            analisis += f"  - Tono: {oraculo_linea.get(FIELD_TONO)}\n"
            analisis += f"  - Guía: {oraculo_linea.get(FIELD_GUIA)}\n"
            analisis += f"  - Análogo: {oraculo_linea.get(FIELD_ANALOGO)}\n"
            analisis += f"  - Antípoda: {oraculo_linea.get(FIELD_ANTIPODA)}\n"
            analisis += f"  - Oculto: {oraculo_linea.get(FIELD_OCULTO)}\n"

    analisis += "\n¿Quieres profundizar en algún aspecto o necesitas explicación sencilla? Pregúntame sobre tu Kin, misión o energía del día."

    return api_response("success", "Análisis oráculo maya generado.", analisis), 200

@app.errorhandler(404)
def not_found(e):
    return api_response("error", "Ruta no encontrada (404).", None), 404

@app.errorhandler(500)
def internal_error(e):
    return api_response("error", "Error interno del servidor (500).", None), 500

# ========== MAIN ==========
if __name__ == '__main__':
    app.run(debug=True)
