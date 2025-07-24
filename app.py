from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# Configuración Airtable con token integrado
AIRTABLE_TOKEN = "patIzO1ts0j7pjPRH.73eed8d970fdfa657bc45431541360721dcd7663b3e3088e6e8154cbc2b50c8b"
BASE_ID = "appe2S1ihV0uEZJEt"
TABLE_FECHAS = "tblFtf5eMJaDoEykE"  # Tabla fechas_gregorianas
TABLE_ORACULO = "tbl3XcK3LRqYEetIO"  # Tabla oraculo_tzolkin

HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_TOKEN}",
    "Content-Type": "application/json"
}

def obtener_kin_por_fecha(fecha):
    url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_FECHAS}"
    params = {"filterByFormula": f"{{Fecha}}='{fecha}'"}
    response = requests.get(url, headers=HEADERS, params=params).json()
    if response.get("records"):
        return response["records"][0]["fields"].get("Kin")
    return None

def obtener_oraculo_por_kin(kin):
    url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ORACULO}"
    params = {"filterByFormula": f"{{Kin}}={kin}"}
    response = requests.get(url, headers=HEADERS, params=params).json()
    if response.get("records"):
        return response["records"][0]["fields"]
    return None

@app.route("/oraculo", methods=["GET"])
def oraculo():
    fecha = request.args.get("fecha")
    if not fecha:
        return jsonify({"error": "Debes enviar el parámetro 'fecha'"}), 400
    kin = obtener_kin_por_fecha(fecha)
    if not kin:
        return jsonify({"error": "No se encontró Kin para esa fecha"}), 404
    oraculo_data = obtener_oraculo_por_kin(kin)
    return jsonify({"fecha": fecha, "kin": kin, "oraculo": oraculo_data})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
