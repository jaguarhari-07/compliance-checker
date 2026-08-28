import io
import json
import os
import sqlite3
import time
import traceback
from flask import Flask, jsonify, request
from flask_cors import CORS
from google import genai
from google.genai import types
from google.genai.errors import ServerError

app = Flask(__name__)
CORS(app)

DB_NAME = 'scans.db'

api_key = os.getenv("GEMINI_API_KEY")
ai_client = genai.Client(api_key=api_key)

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_name TEXT,
            compliance_score INTEGER,
            status TEXT,
            missing_declarations TEXT,
            scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_db()

@app.route('/scan', methods=['POST'])
def scan_commodity():
    if 'image' not in request.files:
        return jsonify({'error': 'No image file provided'}), 400

    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    try:
        image_bytes = file.read()
        mime_type = file.mimetype or 'image/jpeg'
        img_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

        prompt = """
        You are an expert Indian Legal Metrology Act compliance auditor for packaged commodities.
        Analyze this packaging image and verify if it clearly displays these required declarations:
        1. Name and address of Manufacturer / Packer / Importer
        2. Country of Origin
        3. Common / Generic Name of Commodity
        4. Net Quantity
        5. Month and Year of Manufacture / Packing / Import
        6. Maximum Retail Price (MRP inclusive of all taxes)
        7. Customer Care Details (Name, Address, Telephone, Email)

        Return ONLY a raw JSON object matching this exact structure:
        {
            "product_name": "String (detected product name)",
            "compliance_score": Number (integer 0-100 based on presence of declarations),
            "status": "PASS" or "NON-COMPLIANT",
            "missing_declarations": ["List of missing required fields"]
        }
        """

        # Model failover strategy to prevent 503 crashes
        models_to_try = ['gemini-3.6-flash', 'gemini-2.5-flash']
        response = None

        for model_name in models_to_try:
            try:
                response = ai_client.models.generate_content(
                    model=model_name,
                    contents=[img_part, prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    )
                )
                break  # Exit loop if the request succeeds
            except ServerError as err:
                if err.code == 503:
                    time.sleep(1)  # Brief delay before trying fallback model
                    continue
                raise err

        if not response:
            raise Exception("Failed to receive a response from Gemini models.")

        audit_result = json.loads(response.text)

        product_name = audit_result.get("product_name", "Unknown Product")
        score = audit_result.get("compliance_score", 0)
        status = audit_result.get("status", "NON-COMPLIANT")
        missing_list = audit_result.get("missing_declarations", [])
        missing_str = ", ".join(missing_list) if missing_list else "None"

        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO scans (product_name, compliance_score, status, missing_declarations)
            VALUES (?, ?, ?, ?)
        ''', (product_name, score, status, missing_str))
        conn.commit()
        scan_id = cursor.lastrowid
        conn.close()

        return jsonify({
            'scan_id': scan_id,
            'product_name': product_name,
            'compliance_score': score,
            'status': status,
            'missing_declarations': missing_list
        }), 200

    except Exception as e:
        print("\n--- ERROR TRACEBACK ---")
        traceback.print_exc()
        print("-----------------------\n")
        return jsonify({
            'error': 'Failed to process compliance scan',
            'details': str(e)
        }), 500

@app.route('/history', methods=['GET'])
def get_history():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, product_name, compliance_score, status, missing_declarations, scanned_at 
        FROM scans 
        ORDER BY scanned_at DESC
    ''')
    rows = cursor.fetchall()
    conn.close()

    history = [
        {
            'id': row[0],
            'product_name': row[1],
            'compliance_score': row[2],
            'status': row[3],
            'missing_declarations': row[4].split(', ') if row[4] != "None" else [],
            'scanned_at': row[5]
        } for row in rows
    ]

    return jsonify(history), 200

if __name__ == '__main__':
    app.run(debug=True, port=5000)