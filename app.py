# app_mejorado.py

import os
import datetime
import threading
import io
import json
import time
from functools import lru_cache
from dotenv import load_dotenv

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import requests
import pytz

# ========== LIBRERÍAS DE GOOGLE E IA ==========
from googleapiclient.discovery import build
from google.oauth2 import service_account
from googleapiclient.http import MediaIoBaseDownload
from googleapiclient.errors import HttpError
from google.cloud import storage
import pdfplumber
import docx
import google.generativeai as genai
from google.api_core import exceptions as google_exceptions
import faiss
import numpy as np

# ========== CONFIGURACIÓN INICIAL DE LA APP ==========
load_dotenv() 

app = Flask(__name__)
CORS(app)

class Config:
    # --- Configuración de Airtable ---
    AIRTABLE_TOKEN = os.environ.get("AIRTABLE_TOKEN")
    BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appe2SiWhVOuEZJEt")
    TABLE_FECHAS = os.environ.get("AIRTABLE_TABLE_FECHAS", "tblFtf5eMJaDoEykE")
    TABLE_ORACULO = os.environ.get("AIRTABLE_TABLE_ORACULO", "tbl3XcK3LRqYEetIO")
    AIRTABLE_API_URL = f"https://api.airtable.com/v0/{BASE_ID}"

    # ## MEJORA: Se usa una estructura dual. Nombres para las fórmulas y IDs para leer los datos.
    # Nombres de Campos (para filterByFormula)
    FIELD_NAME_FECHA = "Fecha"
    FIELD_NAME_KIN_ORACULO = "Kin Central"
    
    # IDs de Campos (para leer los datos de la respuesta, mucho más robusto)
    FIELD_ID_FECHA = "fldHzpCRrHNc6EYq5"
    FIELD_ID_KIN_CENTRAL_FECHAS = "fld6z8Dipfe2t6rVJ"
    
    FIELD_ID_IDKIN = "fldTBvI5SXibJHk3L"
    FIELD_ID_KIN_CENTRAL_ORACULO = "fldGGijwtgKdX1kNf"
    FIELD_ID_SELLO = "fld2hwTEDuot1z01o"
    FIELD_ID_NUM_SELLO = "fldf4lEQV00vX8QGp"
    FIELD_ID_TONO = "fldYfpMc0Gj2oNlbz"
    FIELD_ID_GUIA = "fldXSdBjPXL61bf9v"
    FIELD_ID_ANALOGO = "fldbFsqbkQqCsSiyY"
    FIELD_ID_ANTIPODA = "fldDRTvvc75s5DIA1"
    FIELD_ID_OCULTO = "fldRO16Xf91ouVIsv"

    # --- Configuración de Google Drive & Gemini ---
    SERVICE_ACCOUNT_FILE = 'credentials.json'
    SCOPES = ['https://www.googleapis.com/auth/drive.readonly', 'https://www.googleapis.com/auth/devstorage.read_write']
    FOLDER_ID = os.environ.get("DRIVE_FOLDER_ID")
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    EMBEDDING_MODEL = "models/embedding-001"
    GENERATION_MODEL = "gemini-1.5-flash"
    # ## MEJORA: Nuevas variables para la "bóveda" del índice en Google Cloud Storage
    GCS_BUCKET_NAME = os.environ.get("GCS_BUCKET_NAME")
    
    # ## MEJORA: Nombres de los archivos del cerebro desarmado
    GCS_BLOB_NAME_METADATA = "rag_metadata.json"
    GCS_BLOB_NAME_FAISS = "faiss_index.bin"
    GCS_BLOB_NAME_CHUNKS = "doc_chunks.json"

    # --- Configuración de la Aplicación ---
    TIMEZONE = pytz.timezone(os.environ.get("TIMEZONE", "America/Bogota"))
    RENDER_DISK_PATH = os.environ.get("RENDER_DISK_PATH", ".")
    RAG_STATE_FILE = os.path.join(RENDER_DISK_PATH, "rag_index_state.json")

app.config.from_object(Config)

if not app.config["GEMINI_API_KEY"]:
    raise ValueError("La variable de entorno GEMINI_API_KEY no está configurada.")
genai.configure(api_key=app.config["GEMINI_API_KEY"])

# ========== CACHE Y ESTADO GLOBAL ==========
# ... (sin cambios)
drive_file_index = []
faiss_index = None
doc_chunks = []
chunk_to_file_id = [] 
index_lock = threading.Lock()

# ========== FUNCIONES HELPERS Y UTILIDADES ==========
# ... (sin cambios)
def api_response(status, message, data=None):
    return jsonify({"status": status, "message": message, "data": data or {}})

def normalizar_fecha_str(fecha_input):
    if not isinstance(fecha_input, str): return None
    fecha_input = fecha_input.strip().replace("-", "/")
    for fmt in ["%d/%m/%Y", "%d/%m/%y", "%Y/%m/%d", "%d-%m-%Y"]:
        try:
            dt_obj = datetime.datetime.strptime(fecha_input, fmt)
            return f"{dt_obj.day:02d}/{dt_obj.month:02d}/{dt_obj.year}"
        except (ValueError, TypeError):
            pass
    return None

# ========== LÓGICA DE AIRTABLE ==========
@lru_cache(maxsize=512)
def get_kin_from_date(fecha_str):
    # ## CORRECCIÓN: Se intentan múltiples formatos de fecha para máxima compatibilidad.
    try:
        parts = fecha_str.split('/')
        day_with_zero = parts[0]
        month_with_zero = parts[1]
        year = parts[2]
        day_without_zero = str(int(day_with_zero))
        month_without_zero = str(int(month_with_zero))
        
        formatos_a_probar = list(set([
            f"{day_with_zero}/{month_with_zero}/{year}",
            f"{day_without_zero}/{month_without_zero}/{year}",
            f"{day_without_zero}/{month_with_zero}/{year}",
            f"{day_with_zero}/{month_without_zero}/{year}",
        ]))
    except (ValueError, IndexError):
        formatos_a_probar = [fecha_str]

    for fecha in formatos_a_probar:
        headers = {"Authorization": f"Bearer {app.config['AIRTABLE_TOKEN']}"}
        params = {
            "filterByFormula": f"{{{app.config['FIELD_NAME_FECHA']}}}='{fecha}'",
            "returnFieldsByFieldId": "true" 
        }
        try:
            r = requests.get(f"{app.config['AIRTABLE_API_URL']}/{app.config['TABLE_FECHAS']}", params=params, headers=headers)
            r.raise_for_status()
            records = r.json().get("records", [])
            if records:
                return records[0]['fields'].get(app.config['FIELD_ID_KIN_CENTRAL_FECHAS'])
        except requests.exceptions.RequestException as e:
            print(f"Error en la petición a Airtable (get_kin_from_date): {e}")
            return None
    return None

@lru_cache(maxsize=260)
def get_oraculo_from_kin(kin):
    headers = {"Authorization": f"Bearer {app.config['AIRTABLE_TOKEN']}"}
    params = {
        "filterByFormula": f"{{{app.config['FIELD_NAME_KIN_ORACULO']}}}={kin}",
        "returnFieldsByFieldId": "true"
    }
    try:
        r = requests.get(f"{app.config['AIRTABLE_API_URL']}/{app.config['TABLE_ORACULO']}", params=params, headers=headers)
        r.raise_for_status()
        records = r.json().get("records", [])
        return records[0]['fields'] if records else None
    except requests.exceptions.RequestException as e:
        print(f"Error en la petición a Airtable (get_oraculo_from_kin): {e}")
        return None

# ========== LÓGICA DE GOOGLE DRIVE Y RAG ==========
def _download_index_from_gcs():
    """Descarga el archivo de estado del índice desde Google Cloud Storage."""
    if not app.config["GCS_BUCKET_NAME"]:
        print("  -> Nombre del bucket de GCS no configurado. Omitiendo descarga.")
        return False
        
    try:
        print(f"  -> Intentando descargar el índice desde GCS bucket: {app.config['GCS_BUCKET_NAME']}")
        creds = service_account.Credentials.from_service_account_file(
            app.config['SERVICE_ACCOUNT_FILE'], scopes=app.config['SCOPES']
        )
        storage_client = storage.Client(credentials=creds)
        bucket = storage_client.bucket(app.config["GCS_BUCKET_NAME"])
        blob = bucket.blob(app.config["GCS_BLOB_NAME"])
        
        if blob.exists():
            blob.download_to_filename(app.config["RAG_STATE_FILE"])
            print("  -> ¡Éxito! Cerebro del Oráculo descargado desde la bóveda.")
            return True
        else:
            print("  -> No se encontró un cerebro pre-construido en la bóveda. Se construirá uno nuevo.")
            return False
    except Exception as e:
        print(f"Error al descargar el índice desde GCS: {e}")
        return False
# ... (sin cambios en esta sección)
def _load_metadata():
    path = os.path.join(app.config["DATA_DIR"], app.config["GCS_BLOB_NAME_METADATA"])
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"files": {}}

def _save_metadata(metadata):
    path = os.path.join(app.config["DATA_DIR"], app.config["GCS_BLOB_NAME_METADATA"])
    with open(path, 'w') as f:
        json.dump(metadata, f, indent=2)

def _download_from_gcs(blob_name, destination_path):
    if not app.config["GCS_BUCKET_NAME"]: return False
    try:
        creds = service_account.Credentials.from_service_account_file(app.config['SERVICE_ACCOUNT_FILE'], scopes=app.config['SCOPES'])
        storage_client = storage.Client(credentials=creds)
        bucket = storage_client.bucket(app.config["GCS_BUCKET_NAME"])
        blob = bucket.blob(blob_name)
        if blob.exists():
            blob.download_to_filename(destination_path)
            print(f"  -> ¡Éxito! Archivo '{blob_name}' descargado desde la bóveda.")
            return True
        return False
    except Exception as e:
        print(f"Error al descargar '{blob_name}' desde GCS: {e}")
        return False


def _get_drive_service():
    try:
        creds = service_account.Credentials.from_service_account_file(
            app.config['SERVICE_ACCOUNT_FILE'], scopes=app.config['SCOPES']
        )
        return build('drive', 'v3', credentials=creds, cache_discovery=False)
    except FileNotFoundError:
        print("ERROR: El archivo 'credentials.json' no fue encontrado.")
        return None
    except Exception as e:
        print(f"Error al crear el servicio de Drive: {e}")
        return None

def _download_and_parse_drive_file(drive_service, file_id, mime_type):
    fh = io.BytesIO()
    try:
        if "google-apps.document" in mime_type:
            request = drive_service.files().export_media(fileId=file_id, mimeType='text/plain')
            parser_mime_type = 'text/plain'
        elif "google-apps" in mime_type:
            return ""
        else:
            request = drive_service.files().get_media(fileId=file_id)
            parser_mime_type = mime_type
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        fh.seek(0)
    except HttpError as error:
        print(f"Error al descargar el archivo {file_id} de Google Drive: {error}")
        return ""
    text = ""
    try:
        if 'pdf' in parser_mime_type:
            with pdfplumber.open(fh) as pdf:
                text = "".join(page.extract_text() or "" for page in pdf.pages)
        elif 'wordprocessingml' in parser_mime_type:
            doc = docx.Document(fh)
            text = "\n".join(para.text for para in doc.paragraphs)
        else:
            text = fh.read().decode('utf-8', errors='ignore')
    except Exception as e:
        print(f"Error al parsear el contenido del archivo {file_id}: {e}")
        return ""
    return text

def _listar_archivos_recursivamente(service, folder_id):
    all_files = []
    page_token = None
    while True:
        try:
            response = service.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields="nextPageToken, files(id, name, mimeType, modifiedTime)",
                pageToken=page_token
            ).execute()
            for file in response.get('files', []):
                if file.get('mimeType') == 'application/vnd.google-apps.folder':
                    all_files.extend(_listar_archivos_recursivamente(service, file.get('id')))
                else:
                    all_files.append(file)
            page_token = response.get('nextPageToken', None)
            if page_token is None:
                break
        except HttpError as error:
            print(f"Error al listar archivos en la carpeta {folder_id}: {error}")
            break
    return all_files

def get_embedding_with_retries(chunk, task_type, max_retries=5):
    retries = 0
    delay = 1.0
    while retries < max_retries:
        try:
            embedding_result = genai.embed_content(
                model=app.config["EMBEDDING_MODEL"],
                content=chunk,
                task_type=task_type
            )
            return embedding_result['embedding']
        except (google_exceptions.DeadlineExceeded, google_exceptions.ServiceUnavailable, google_exceptions.InternalServerError, google_exceptions.ResourceExhausted) as e:
            retries += 1
            print(f"  -> Error de red ({type(e).__name__}) al generar embedding. Reintentando en {delay:.1f}s... (Intento {retries}/{max_retries})")
            time.sleep(delay)
            delay *= 2
        except Exception as e:
            print(f"Error inesperado generando embedding: {e}")
            return None
    print(f"  -> Fallo al generar embedding después de {max_retries} intentos.")
    return None

def background_intelligent_sync():
    global faiss_index, doc_chunks, chunk_to_file_id
    print("Iniciando sincronización inteligente del índice RAG...")
     # ## MEJORA: Lógica de arranque profesional
    # 1. Comprobar si el cerebro ya existe localmente (en el disco de Render)
    if not os.path.exists(app.config["RAG_STATE_FILE"]):
        print("  -> No se encontró un cerebro local en el disco.")
        # 2. Si no existe, intentar descargarlo de la bóveda
        _download_index_from_gcs()   
    service = _get_drive_service()
    if not service: return

    # ## MEJORA: Lógica de arranque profesional con cerebro desarmado
    # 1. Comprobar si el cerebro ya existe localmente
    metadata_path = os.path.join(app.config["DATA_DIR"], app.config["GCS_BLOB_NAME_METADATA"])
    faiss_path = os.path.join(app.config["DATA_DIR"], app.config["GCS_BLOB_NAME_FAISS"])
    chunks_path = os.path.join(app.config["DATA_DIR"], app.config["GCS_BLOB_NAME_CHUNKS"])

    if not all(os.path.exists(p) for p in [metadata_path, faiss_path, chunks_path]):
        print("  -> No se encontró un cerebro local completo. Intentando descargar desde la bóveda...")
        _download_from_gcs(app.config["GCS_BLOB_NAME_METADATA"], metadata_path)
        _download_from_gcs(app.config["GCS_BLOB_NAME_FAISS"], faiss_path)
        _download_from_gcs(app.config["GCS_BLOB_NAME_CHUNKS"], chunks_path)

    # Cargar el índice FAISS en memoria (esto es eficiente)
    global faiss_index
    if os.path.exists(faiss_path) and faiss_index is None:
        try:
            faiss_index = faiss.read_index(faiss_path)
            print(f"Índice FAISS con {faiss_index.ntotal} vectores cargado en memoria.")
        except Exception as e:
            print(f"Error al cargar el índice FAISS: {e}")
    processed_files = rag_state.get("files", {})
    
    all_found_files = _listar_archivos_recursivamente(service, app.config['FOLDER_ID'])
    print(f"--> ¡Exploración completa! Se encontraron {len(all_found_files)} archivos en total (incluyendo subcarpetas).")
    print("--> Ahora, comenzando el análisis para procesar los archivos...")
    
    current_drive_files = {f["id"]: f for f in all_found_files}

    files_to_add_or_update = []
    processed_ids = set(processed_files.keys())
    current_ids = set(current_drive_files.keys())

    new_ids = current_ids - processed_ids
    for file_id in new_ids:
        files_to_add_or_update.append(current_drive_files[file_id])
        print(f"  [NUEVO] Detectado nuevo archivo: {current_drive_files[file_id]['name']}")

    for file_id in processed_ids.intersection(current_ids):
        if processed_files[file_id]["modifiedTime"] != current_drive_files[file_id]["modifiedTime"]:
            files_to_add_or_update.append(current_drive_files[file_id])
            print(f"  [MODIFICADO] Detectado archivo modificado: {current_drive_files[file_id]['name']}")
    
    deleted_ids = processed_ids - current_ids
    if deleted_ids:
        print(f"  [ELIMINADO] Se eliminarán {len(deleted_ids)} archivos del índice.")

    if not files_to_add_or_update and not deleted_ids:
        print("Sincronización finalizada. No se encontraron cambios.")
        with index_lock:

            if faiss_index is None and rag_state["embeddings"]:
                embeddings = np.array(rag_state["embeddings"], dtype=np.float32)
                if embeddings.size > 0:
                    dimension = embeddings.shape[1]
                    faiss_index = faiss.IndexFlatL2(dimension)
                    faiss_index.add(embeddings)
                    doc_chunks = rag_state["chunks"]
                    chunk_to_file_id = rag_state["chunk_map"]
                    print("Índice FAISS existente cargado en memoria.")
        return

    if deleted_ids:
        new_embeddings, new_chunks, new_chunk_map = [], [], []
        for i, file_id in enumerate(rag_state["chunk_map"]):
            if file_id not in deleted_ids:
                new_embeddings.append(rag_state["embeddings"][i])
                new_chunks.append(rag_state["chunks"][i])
                new_chunk_map.append(file_id)
        rag_state["embeddings"], rag_state["chunks"], rag_state["chunk_map"] = new_embeddings, new_chunks, new_chunk_map
        for file_id in deleted_ids:
            if file_id in rag_state["files"]:
                del rag_state["files"][file_id]

    for file_info in files_to_add_or_update:
        file_id = file_info['id']
        
        mime_type = file_info.get('mimeType', '')
        supported_mimes = [
            'application/pdf',
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'text/plain', 'text/csv',
            'application/vnd.google-apps.document'
        ]
        
        if not any(supported in mime_type for supported in supported_mimes):
            print(f"  -> Omitiendo y recordando archivo no soportado: {file_info['name']} (Tipo: {mime_type})")
            rag_state["files"][file_id] = {"name": file_info["name"], "modifiedTime": file_info["modifiedTime"], "status": "omitted"}
            continue

        if file_id in processed_files:
            new_embeddings, new_chunks, new_chunk_map = [], [], []
            for i, f_id in enumerate(rag_state["chunk_map"]):
                if f_id != file_id:
                    new_embeddings.append(rag_state["embeddings"][i])
                    new_chunks.append(rag_state["chunks"][i])
                    new_chunk_map.append(f_id)
            rag_state["embeddings"], rag_state["chunks"], rag_state["chunk_map"] = new_embeddings, new_chunks, new_chunk_map

        text = _download_and_parse_drive_file(service, file_id, mime_type)
        if text:
            print(f"  -> Procesando chunks para: {file_info['name']}")
            chunks = [text[i:i+1500] for i in range(0, len(text), 1000)]
            for chunk in chunks:
                embedding = get_embedding_with_retries(chunk, task_type="RETRIEVAL_DOCUMENT")
                if embedding:
                    rag_state["embeddings"].append(embedding)
                    rag_state["chunks"].append(chunk)
                    rag_state["chunk_map"].append(file_id)
            rag_state["files"][file_id] = {"name": file_info["name"], "modifiedTime": file_info["modifiedTime"], "status": "processed"}
        else:
            rag_state["files"][file_id] = {"name": file_info["name"], "modifiedTime": file_info["modifiedTime"], "status": "no_text"}


    _save_rag_state(rag_state)
    with index_lock:
        if rag_state["embeddings"]:
            embeddings = np.array(rag_state["embeddings"], dtype=np.float32)
            if embeddings.size > 0:
                dimension = embeddings.shape[1]
                faiss_index = faiss.IndexFlatL2(dimension)
                faiss_index.add(embeddings)
                doc_chunks = rag_state["chunks"]
                chunk_to_file_id = rag_state["chunk_map"]
                print(f"Índice FAISS actualizado exitosamente. Total chunks: {len(doc_chunks)}")
        else:
            faiss_index, doc_chunks, chunk_to_file_id = None, [], []
            print("Índice FAISS vacío tras la actualización.")

def force_rebuild_index():
    print("Forzando reconstrucción completa del índice...")
    if os.path.exists(app.config["RAG_STATE_FILE"]):
        os.remove(app.config["RAG_STATE_FILE"])
    with index_lock:
        global faiss_index, doc_chunks, chunk_to_file_id
        faiss_index, doc_chunks, chunk_to_file_id = None, [], []
    background_intelligent_sync()

# ========== LÓGICA DE ANÁLISIS Y PERFILAMIENTO ==========
def _crear_perfil_psicologico(oraculo_natal):
    """
    Analiza el oráculo natal para determinar si el perfil del usuario es pragmático.
    """
    sellos_pragmaticos = ["Espejo", "Perro", "Guerrero", "Tierra"]
    tonos_pragmaticos = [5, 8, 10, 11]

    sello = oraculo_natal.get(app.config['FIELD_ID_SELLO'])
    tono = oraculo_natal.get(app.config['FIELD_ID_TONO'])

    if sello in sellos_pragmaticos or tono in tonos_pragmaticos:
        return "pragmatico"
    return "neofito"

def _generar_analisis_con_gemini(perfil, oraculo_natal, oraculo_mision, oraculo_tierra, oraculo_linea_tiempo):
    """
    Construye el prompt y llama a Gemini para generar el análisis de texto.
    """
    datos_oraculo = f"""
    - Oráculo Natal (La Esencia del Individuo):
      - Arquetipo Esencial (Sello): {oraculo_natal.get(app.config['FIELD_ID_SELLO'])}
      - Frecuencia Operativa (Tono): {oraculo_natal.get(app.config['FIELD_ID_TONO'])}
      - Brújula Interna (Guía): {oraculo_natal.get(app.config['FIELD_ID_GUIA'])}
      - Aliado Consciente (Análogo): {oraculo_natal.get(app.config['FIELD_ID_ANALOGO'])}
      - Desafío Principal (Antípoda): {oraculo_natal.get(app.config['FIELD_ID_ANTIPODA'])}
      - Potencial Oculto (Oculto): {oraculo_natal.get(app.config['FIELD_ID_OCULTO'])}
    - Misión de Vida (El Propósito de Fondo):
      - Arquetipo de la Misión: {oraculo_mision.get(app.config['FIELD_ID_SELLO'])}
    - Energía del Día para la Tierra (El Clima Colectivo):
      - Arquetipo: {oraculo_tierra.get(app.config['FIELD_ID_SELLO'])}
      - Frecuencia: {oraculo_tierra.get(app.config['FIELD_ID_TONO'])}
    - Línea de Tiempo Personal (Tu Clima Personal Hoy):
      - Arquetipo: {oraculo_linea_tiempo.get(app.config['FIELD_ID_SELLO'])}
      - Frecuencia: {oraculo_linea_tiempo.get(app.config['FIELD_ID_TONO'])}
    """
    
    prompt_base = f"""
    Eres un consejero experto en psicología profunda (Carl Jung) y sabiduría universal (Escuela de Magia del Amor). Tu misión es traducir los datos arquetípicos del Sincronario Maya a un lenguaje claro, cercano y profundo para el usuario.
    **REGLA DE ORO: NUNCA uses términos técnicos mayas como 'Kin', 'Sello', 'Tono', 'Guía', 'Análogo', 'Antípoda' u 'Oculto'.** Traduce siempre estos conceptos a su significado práctico y universal.

    Estructura tu respuesta en tres actos:

    **Acto 1: Tu Esencia.**
    Comienza hablándole al usuario sobre su perfil fundamental. Usa los datos del "Oráculo Natal" y la "Misión de Vida" para describir su arquetipo esencial, su ritmo natural, su brújula interna, sus aliados, sus desafíos y su potencial oculto. Habla de sus luces (potenciales) y sombras (áreas de crecimiento).

    **Acto 2: El Clima Energético de Hoy.**
    Ahora, describe el panorama del día de la consulta.
    - Primero, explica el "Clima Colectivo" (Energía de la Tierra), describiendo la lección o la oportunidad que el día presenta para todos.
    - Luego, explica su "Clima Personal" (Línea de Tiempo Personal), usando una analogía para mostrar cómo la energía general del día resuena con él de forma única.

    **Acto 3: La Invitación.**
    Cierra tu análisis con una pregunta abierta que invite a la siguiente fase de la conversación.

    **Datos para el Análisis:**
    {datos_oraculo}
    """

    if perfil == "pragmatico":
        prompt_final = prompt_base + "\n**Instrucción Adicional:** El perfil de esta persona es pragmático y lógico. Usa un lenguaje directo, estructurado y enfocado en la psicología profunda. Traduce los arquetipos a conceptos junguianos y ofrece consejos prácticos y accionables. Finaliza con la pregunta: 'Ahora que tienes este mapa de tus energías y las del día, ¿sobre qué tema, relación o desafío específico te gustaría que profundicemos juntos?'"
    else: # Perfil Neofito
        prompt_final = prompt_base + "\n**Instrucción Adicional:** El perfil de esta persona es más intuitivo o nuevo en estos temas. Usa un lenguaje sencillo, amoroso y lleno de analogías. El objetivo es que se sienta comprendido y empoderado. Finaliza con la pregunta: 'Ahora que hemos visto un mapa de tus energías y las del día, ¿qué aspecto de tu vida te gustaría que exploremos juntos?'"
    
    try:
        model = genai.GenerativeModel(app.config['GENERATION_MODEL'])
        
        retries = 0
        delay = 2.0
        max_retries = 3
        while retries < max_retries:
            try:
                response = model.generate_content(prompt_final)
                return response.text
            except (google_exceptions.ResourceExhausted, google_exceptions.ServiceUnavailable, google_exceptions.DeadlineExceeded) as e:
                retries += 1
                if retries == max_retries:
                    raise e
                print(f"  -> Error de cuota/servicio ({type(e).__name__}) al generar análisis. Reintentando en {delay:.1f}s...")
                time.sleep(delay)
                delay *= 2

    except Exception as e:
        print(f"Error al generar análisis con Gemini: {e}")
        return f"Hubo un error al generar el análisis después de varios intentos: {e}"


# ========== LÓGICA DE ANÁLISIS Y PERFILAMIENTO ==========
# ... (sin cambios en esta sección)
def _crear_perfil_psicologico(oraculo_natal):
    sellos_pragmaticos = ["Espejo", "Perro", "Guerrero", "Tierra"]
    tonos_pragmaticos = [5, 8, 10, 11]
    sello = oraculo_natal.get(app.config['FIELD_ID_SELLO'])
    tono = oraculo_natal.get(app.config['FIELD_ID_TONO'])
    if sello in sellos_pragmaticos or tono in tonos_pragmaticos:
        return "pragmatico"
    return "neofito"

def _generar_analisis_con_gemini(perfil, oraculo_natal, oraculo_mision, oraculo_tierra, oraculo_linea_tiempo):
    datos_oraculo = f"""
    - Oráculo Natal (Tu Esencia):
      - Sello: {oraculo_natal.get(app.config['FIELD_ID_SELLO'])}
      - Tono: {oraculo_natal.get(app.config['FIELD_ID_TONO'])}
      - Guía: {oraculo_natal.get(app.config['FIELD_ID_GUIA'])}
      - Análogo: {oraculo_natal.get(app.config['FIELD_ID_ANALOGO'])}
      - Antípoda: {oraculo_natal.get(app.config['FIELD_ID_ANTIPODA'])}
      - Oculto: {oraculo_natal.get(app.config['FIELD_ID_OCULTO'])}
    - Misión de Vida (Tu Propósito de Fondo):
      - Sello de la Misión: {oraculo_mision.get(app.config['FIELD_ID_SELLO'])}
    - Energía del Día para la Tierra (El Clima Colectivo):
      - Sello: {oraculo_tierra.get(app.config['FIELD_ID_SELLO'])}
      - Tono: {oraculo_tierra.get(app.config['FIELD_ID_TONO'])}
      - Guía: {oraculo_tierra.get(app.config['FIELD_ID_GUIA'])}
      - Análogo: {oraculo_tierra.get(app.config['FIELD_ID_ANALOGO'])}
      - Antípoda: {oraculo_tierra.get(app.config['FIELD_ID_ANTIPODA'])}
      - Oculto: {oraculo_tierra.get(app.config['FIELD_ID_OCULTO'])}
    - Tu Línea de Tiempo Personal (Tu Clima Personal Hoy):
      - Sello: {oraculo_linea_tiempo.get(app.config['FIELD_ID_SELLO'])}
      - Tono: {oraculo_linea_tiempo.get(app.config['FIELD_ID_TONO'])}
      - Guía: {oraculo_linea_tiempo.get(app.config['FIELD_ID_GUIA'])}
      - Análogo: {oraculo_linea_tiempo.get(app.config['FIELD_ID_ANALOGO'])}
      - Antípoda: {oraculo_linea_tiempo.get(app.config['FIELD_ID_ANTIPODA'])}
      - Oculto: {oraculo_linea_tiempo.get(app.config['FIELD_ID_OCULTO'])}
    """
    if perfil == "pragmatico":
        prompt = f"""
        Eres un experto en psicología profunda y arquetipos junguianos. Realiza un análisis comparativo y pragmático para una persona, basado en sus datos del Oráculo Maya.
        1.  **Análisis Natal:** Primero, analiza su Oráculo Natal y Misión de Vida. Traduce los conceptos mayas a un lenguaje psicológico y práctico (Sello = arquetipo esencial, Tono = frecuencia operativa, etc.). Describe los potenciales y sombras de estos 7 aspectos natales.
        2.  **Análisis del Día:** Luego, analiza la "Energía del Día para la Tierra" y la "Línea de Tiempo Personal". Explica cómo el "clima colectivo" (Tierra) interactúa con el "clima personal" del individuo (Línea de Tiempo).
        3.  **Síntesis y Consejo:** Ofrece una síntesis que conecte su perfil natal con las energías del día. Dale un consejo práctico y accionable sobre cómo navegar el día de la consulta.
        Al final, debes preguntar "¿Qué aspecto de tu vida o desafío te gustaría explorar hoy con más detalle? También puedes solicitar este mismo análisis para una fecha diferente si lo deseas.".
        Datos para el Análisis:
        {datos_oraculo}
        """
    else: # Perfil Neofito
        prompt = f"""
        Eres un guía espiritual sabio y cercano. Explica de manera sencilla y aterrizada las energías de una persona para el día de hoy, basadas en el Sincronario Maya.
        1.  **Tu Esencia:** Comienza explicando su Oráculo Natal y su Misión de Vida. Usa un lenguaje fácil de entender (luz y sombra) para describir estos 7 aspectos que son su base.
        2.  **La Energía de Hoy:** Luego, explica el "Clima Colectivo" (la energía del día para todos) y su "Clima Personal" (su Línea de Tiempo Personal). Haz una analogía para que entienda cómo la energía general del día le afecta de una manera única.
        3.  **Consejo del Día:** Ofrece una síntesis amorosa y un consejo práctico sobre cómo puede aprovechar mejor las energías del día, conectando su esencia con lo que está sucediendo hoy.
        El objetivo es que la persona se sienta comprendida y empoderada. Al final, debes preguntar "¿Qué aspecto de tu vida te gustaría que exploremos juntos hoy? Si quieres, también podemos hacer este mismo análisis para otra fecha.".
        Datos para el Análisis:
        {datos_oraculo}
        """
    try:
        model = genai.GenerativeModel(app.config['GENERATION_MODEL'])
        
        retries = 0
        delay = 2.0
        max_retries = 3
        while retries < max_retries:
            try:
                response = model.generate_content(prompt)
                return response.text
            except (google_exceptions.ResourceExhausted, google_exceptions.ServiceUnavailable, google_exceptions.DeadlineExceeded) as e:
                retries += 1
                if retries == max_retries:
                    raise e
                print(f"  -> Error de cuota/servicio ({type(e).__name__}) al generar análisis. Reintentando en {delay:.1f}s...")
                time.sleep(delay)
                delay *= 2

    except Exception as e:
        print(f"Error al generar análisis con Gemini: {e}")
        return f"Hubo un error al generar el análisis después de varios intentos: {e}"

# ========== ENDPOINTS DE LA API ==========
@app.route("/")
def home():
    return api_response("success", "Oráculo Maya API v6.13 (Corrección Definitiva) - Powered by Gemini")

@app.route("/kin")
def kin_endpoint():
    fecha_str = request.args.get("fecha")
    if not fecha_str:
        return api_response("error", "Parámetro 'fecha' es requerido."), 400
    
    fecha_norm = normalizar_fecha_str(fecha_str)
    if not fecha_norm:
        return api_response("error", f"Formato de fecha inválido: {fecha_str}"), 400
        
    kin = get_kin_from_date(fecha_norm)
    if kin:
        return api_response("success", "Kin encontrado", {"fecha": fecha_norm, "kin": kin})
    else:
        return api_response("not_found", f"No se encontró Kin para la fecha {fecha_norm}."), 404

@app.route("/oraculo")
def oraculo_endpoint():
    kin_str = request.args.get("kin")
    if not kin_str:
        return api_response("error", "Parámetro 'kin' es requerido."), 400
    
    oraculo = get_oraculo_from_kin(kin_str)
    if oraculo:
        return api_response("success", "Oráculo encontrado", oraculo)
    else:
        return api_response("not_found", f"No se encontró oráculo para el Kin {kin_str}."), 404

@app.route("/analisis", methods=["POST"])
def analisis_integrado():
    data = request.get_json()
    if not data or "fecha_nacimiento" not in data:
        return api_response("error", "Cuerpo de la petición debe ser JSON con 'fecha_nacimiento'."), 400

    fecha_nac_norm = normalizar_fecha_str(data["fecha_nacimiento"])
    if not fecha_nac_norm:
        return api_response("error", f"Formato de fecha de nacimiento inválido: {data['fecha_nacimiento']}"), 400
    
    hoy_dt = datetime.datetime.now(app.config['TIMEZONE'])
    fecha_consulta_norm = normalizar_fecha_str(data.get("fecha_consulta", hoy_dt.strftime("%d/%m/%Y")))
    if not fecha_consulta_norm:
        return api_response("error", f"Formato de fecha de consulta inválido: {data.get('fecha_consulta')}"), 400

    kin_natal = get_kin_from_date(fecha_nac_norm)
    if not kin_natal: 
        return api_response("not_found", f"Dato Faltante: No se encontró el Kin para la fecha de nacimiento ({fecha_nac_norm}). Por favor, verifica que la fecha exista en tu Airtable."), 404
        
    oraculo_natal = get_oraculo_from_kin(kin_natal)
    if not oraculo_natal: 
        return api_response("not_found", f"Dato Faltante: No se encontró Oráculo para el Kin natal ({kin_natal})."), 404

    try:
        tono_natal = int(oraculo_natal.get(app.config['FIELD_ID_TONO'], 0))
        kin_mision_num = (int(kin_natal) - tono_natal + 1)
        if kin_mision_num < 1: kin_mision_num += 260
        oraculo_mision = get_oraculo_from_kin(kin_mision_num)
    except (ValueError, TypeError) as e:
        return api_response("error", f"Error en los cálculos de la misión: {e}"), 500

    if not oraculo_mision: 
        return api_response("not_found", f"Dato Faltante: No se encontró Oráculo para la misión (Kin {kin_mision_num})."), 404
        
    fecha_nac_dt = datetime.datetime.strptime(fecha_nac_norm, "%d/%m/%Y")
    fecha_consulta_dt = datetime.datetime.strptime(fecha_consulta_norm, "%d/%m/%Y")

    fecha_cumple_este_ano = fecha_nac_dt.replace(year=fecha_consulta_dt.year)
    fecha_cumple_a_usar = (
        fecha_cumple_este_ano.replace(year=fecha_consulta_dt.year - 1)
        if fecha_consulta_dt < fecha_cumple_este_ano
        else fecha_cumple_este_ano
    )
    ano_maya_inicio_ano = (
        fecha_consulta_dt.year if fecha_consulta_dt.month > 7 or (fecha_consulta_dt.month == 7 and fecha_consulta_dt.day >= 26) else fecha_consulta_dt.year - 1
    )
    fecha_ano_maya_str = f"26/07/{ano_maya_inicio_ano}"
    
    kin_cumple = get_kin_from_date(fecha_cumple_a_usar.strftime("%d/%m/%Y"))
    kin_ano_maya = get_kin_from_date(fecha_ano_maya_str)

    if not all([kin_cumple, kin_ano_maya]): 
        return api_response("not_found", f"Dato Faltante: No se pudo encontrar el Kin para la fecha de cumpleaños ({fecha_cumple_a_usar.strftime('%d/%m/%Y')}) o del Año Nuevo Maya ({fecha_ano_maya_str})."), 404

    try:
        constante_personal_kin = ((int(kin_natal) + int(kin_ano_maya) + int(kin_cumple) - 1) % 260) + 1
    except (ValueError, TypeError) as e:
        return api_response("error", f"Error en los cálculos de la constante: {e}"), 500

    kin_tierra = get_kin_from_date(fecha_consulta_norm)
    if not kin_tierra: 
        return api_response("not_found", f"Dato Faltante: No se encontró Kin para la fecha de consulta ({fecha_consulta_norm})."), 404
    
    oraculo_tierra = get_oraculo_from_kin(kin_tierra)
    if not oraculo_tierra: 
        return api_response("not_found", f"Dato Faltante: No se encontró Oráculo para el Kin de la Tierra ({kin_tierra})."), 404
    
    try:
        kin_linea_tiempo_num = (constante_personal_kin + int(kin_tierra)) % 260 or 260
        oraculo_linea_tiempo = get_oraculo_from_kin(kin_linea_tiempo_num)
    except (ValueError, TypeError) as e:
        return api_response("error", f"Error en los cálculos de la línea de tiempo: {e}"), 500
        
    if not oraculo_linea_tiempo: 
        return api_response("not_found", f"Dato Faltante: No se encontró Oráculo para la línea de tiempo (Kin {kin_linea_tiempo_num})."), 404

    perfil = _crear_perfil_psicologico(oraculo_natal)
    
    oraculo_constante = get_oraculo_from_kin(constante_personal_kin)
    texto_analisis = _generar_analisis_con_gemini(perfil, oraculo_natal, oraculo_mision, oraculo_tierra, oraculo_linea_tiempo)

    respuesta_final = {
        "pasos_calculo": {
            "fecha_nacimiento_norm": fecha_nac_norm,
            "kin_natal": kin_natal,
            "fecha_consulta_norm": fecha_consulta_norm,
            "fecha_cumpleanos_usada": fecha_cumple_a_usar.strftime("%d/%m/%Y"),
            "fecha_ano_maya_usada": fecha_ano_maya_str,
            "kin_cumpleanos": kin_cumple,
            "kin_ano_maya": kin_ano_maya,
            "constante_personal_kin": constante_personal_kin,
            "kin_tierra": kin_tierra,
            "linea_tiempo_personal_kin": kin_linea_tiempo_num
        },
        "oraculos_calculados": {
            "natal": oraculo_natal,
            "mision": oraculo_mision,
            "constante_personal": oraculo_constante,
            "tierra": oraculo_tierra,
            "linea_tiempo_personal": oraculo_linea_tiempo
        },
        "analisis_generado": texto_analisis
    }

    perfil = _crear_perfil_psicologico(oraculo_natal)
    
    texto_analisis = _generar_analisis_con_gemini(perfil, oraculo_natal, oraculo_mision, oraculo_tierra, oraculo_linea_tiempo)

    return api_response("success", "Análisis generado.", {"analisis": texto_analisis})

@app.route('/rag/search', methods=['POST'])
def rag_search_endpoint():
    data = request.get_json()
    query = data.get("query")
    if not query:
        return api_response("error", "Debes indicar 'query' a buscar."), 400
    
    with index_lock:
        if faiss_index is None or not doc_chunks:
            return api_response("service_unavailable", "El índice de búsqueda RAG no está listo o está vacío."), 503

    try:
        emb = get_embedding_with_retries(query, task_type="RETRIEVAL_QUERY")
        if emb:
            _, I = faiss_index.search(np.array([emb], dtype=np.float32), k=3)
            
            # ## MEJORA: Se leen solo los chunks necesarios del archivo en disco.
            # Esto evita cargar los 667MB en memoria.
            chunks_path = os.path.join(app.config["DATA_DIR"], app.config["GCS_BLOB_NAME_CHUNKS"])
            with open(chunks_path, 'r') as f:
                all_chunks = json.load(f)
            
            resultados = [all_chunks[i] for i in I[0]]
            return api_response("success", "Resultados de búsqueda semántica.", {"query": query, "results": resultados})
        else:
            return api_response("error", "No se pudo generar el embedding para la búsqueda."), 500
    except Exception as e:
        return api_response("error", f"Error en la búsqueda semántica: {str(e)}", None), 500

@app.route('/rag/status')
def rag_status_endpoint():
    with index_lock:
        status = {
            "drive_files_found": len(drive_file_index),
            "is_faiss_index_built": faiss_index is not None,
            "indexed_chunks_count": len(doc_chunks)
        }
    return api_response("success", "Estado del sistema RAG.", status)

@app.route('/rag/sync', methods=['POST'])
def rag_sync_endpoint():
    threading.Thread(target=background_intelligent_sync).start()
    return api_response("success", "La sincronización inteligente ha comenzado en segundo plano. Consulta el estado en /rag/status en unos minutos.")

@app.route('/rag/rebuild', methods=['POST'])
def rag_rebuild_endpoint():
    threading.Thread(target=force_rebuild_index).start()
    return api_response("success", "La reconstrucción completa del índice ha comenzado en segundo plano. Consulta el estado en /rag/status en unos minutos.")

@app.route('/rag/download_state', methods=['GET'])
def download_rag_state():
    try:
        return send_file(app.config["RAG_STATE_FILE"], as_attachment=True)
    except FileNotFoundError:
        return api_response("not_found", "El archivo de estado del índice (rag_index_state.json) no existe aún."), 404

# ========== INICIALIZACIÓN DE LA APLICACIÓN ==========
with app.app_context():
    threading.Thread(target=background_intelligent_sync).start()

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)), debug=False)