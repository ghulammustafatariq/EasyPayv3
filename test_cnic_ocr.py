import google.generativeai as genai
from PIL import Image
import os

print("--- TESTING GEMINI VISION OCR ---")

# 1. Setup your keys and paths
API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
if not API_KEY:
    raise ValueError("Set GEMINI_API_KEY in environment before running this script.")
IMAGE_PATH = "C:/Users/GHULAMMUSTAFA/Downloads/WhatsApp Image 2026-04-07 at 9.45.44 AM.jpeg"  # <-- PUT YOUR IMAGE PATH HERE

genai.configure(api_key=API_KEY)

try:
    # 2. Check if the image actually exists on your computer
    if not os.path.exists(IMAGE_PATH):
        raise FileNotFoundError(f"Could not find the image at: {IMAGE_PATH}")

    # 3. Open the image using Pillow
    print(f"Loading image from {IMAGE_PATH}...")
    img = Image.open(IMAGE_PATH)
    
    # 4. Load the Gemini Flash model
    model = genai.GenerativeModel("gemini-2.5-flash")
    
    # 5. The Prompt: Tell Gemini exactly what we want it to look for
    prompt = """
    Look at this Pakistani CNIC card. Extract the following information:
    - Full Name
    - Father / Husband Name
    - CNIC Number (Identity Number)
    - Date of Birth
    - Date of Expiry
    
    Format the output clearly as a list. Do not include any other text.
    """
    
    # 6. Send BOTH the prompt and the image to Gemini
    print("Sending image to Google for analysis (this might take a few seconds)...")
    response = model.generate_content([prompt, img])
    
    # 7. Print the results!
    print("\n✅ EXTRACTION SUCCESSFUL!\n")
    print(response.text)

except Exception as e:
    print("\n❌ FAILED. The error is:")
    print(e)