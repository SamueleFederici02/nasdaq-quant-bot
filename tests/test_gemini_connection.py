"""
Script di verifica della connessione a Google Gemini API.
"""
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(ROOT_DIR))

from config import GEMINI_API_KEY

def test_gemini():
    if not GEMINI_API_KEY or GEMINI_API_KEY == "INSERISCI_QUI_LA_TUA_GEMINI_KEY":
        print("[-] ERRORE: Chiave GEMINI_API_KEY non impostata nel file .env!")
        return False

    try:
        from google import genai
        print("[*] Connessione a Google Gemini Flash in corso...")
        client = genai.Client(api_key=GEMINI_API_KEY)
        
        # Test con prompt di sentiment finanziario
        prompt = (
            "Sei un analista quantitativo. Valuta questa notizia per AAPL: "
            "'Apple reports record Q4 revenue of $94.9 billion, beating Wall Street estimates.' "
            "Rispondi SOLO in JSON con questo formato esatto: "
            "{\"sentiment\": 0.85, \"emergency_exit\": false, \"reason\": \"breve spiegazione\"}"
        )
        
        response = client.models.generate_content(
            model='gemini-3.8-flash',
            contents=prompt
        )
        
        print("\n=======================================================")
        print(" [+] CONNESSIONE A GOOGLE GEMINI RIUSCITA CON SUCCESSO! ")
        print("=======================================================")
        print(f" Risposta del Modello (Gemini Flash):\n{response.text.strip()}")
        print("=======================================================\n")
        return True
    except Exception as e:
        print(f"\n[-] ERRORE durante il test Gemini: {e}\n")
        return False

if __name__ == "__main__":
    test_gemini()
