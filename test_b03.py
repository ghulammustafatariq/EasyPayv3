import asyncio
from app.core.security import hash_password, verify_password
from app.core.encryption import encrypt_sensitive, decrypt_sensitive

async def run_tests():
    print("--- TESTING B03 SECURITY MODULE ---")
    
    # 1. Test Password Hashing
    password = "MySuperSecretPassword123!"
    hashed = hash_password(password)
    is_valid = verify_password(password, hashed)
    print(f"Password Hashing Valid: {is_valid}")
    
    # 2. Test AES-256 Encryption
    cnic = "35202-1234567-8"
    encrypted_cnic = encrypt_sensitive(cnic)
    decrypted_cnic = decrypt_sensitive(encrypted_cnic)
    print(f"Original CNIC: {cnic}")
    print(f"Decrypted CNIC matches: {cnic == decrypted_cnic}")
    
    if is_valid and (cnic == decrypted_cnic):
        print("✅ B03 IS WORKING PERFECTLY!")

if __name__ == "__main__":
    asyncio.run(run_tests())