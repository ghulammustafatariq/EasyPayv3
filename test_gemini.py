import google.generativeai as genai
import os

print("--- TESTING GEMINI API ---")

# 1. Read API key from environment for safety
API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
if not API_KEY:
    raise ValueError("Set GEMINI_API_KEY in environment before running this script.")
genai.configure(api_key=API_KEY)

try:
    # 2. Load the lightning-fast Flash model
    model = genai.GenerativeModel("gemini-2.5-flash")
    
    # 3. Send a simple test prompt
    print("Connecting to Google servers...")
    response = model.generate_content("Hello! Give me a one-line joke about Python programming.")
    
    # 4. Print the result!
    print("\n✅ SUCCESS! Gemini says:")
    print(response.text)

except Exception as e:
    print("\n❌ FAILED. The error is:")
    print(e)