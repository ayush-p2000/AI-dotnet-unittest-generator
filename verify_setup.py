import os
import sys
import urllib.request
import json
from dotenv import load_dotenv
from openai import OpenAI

# Ensure UTF-8 output on Windows terminals
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

print("=" * 60)
print("  AI UNIT TEST GENERATOR - MULTI-AGENT CONNECTIVITY CHECK")
print("=" * 60)

# 1. Check Local Ollama (Qwen 3 Coder)
ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
ollama_model = os.environ.get("OLLAMA_MODEL", "qwen3-coder:latest")
print(f"\n[1] Checking Local Ollama ({ollama_url})...")
try:
    tags_url = ollama_url.replace("/v1", "/api/tags").rstrip("/")
    if "/api/tags" not in tags_url:
        tags_url = "http://localhost:11434/api/tags"
    req = urllib.request.Request(tags_url, headers={"User-Agent": "AI-TestGen-Verify"})
    with urllib.request.urlopen(req, timeout=3) as resp:
        if resp.status == 200:
            data = json.loads(resp.read().decode())
            models = [m["name"] for m in data.get("models", [])]
            print("    [OK] Ollama Service: ONLINE")
            print(f"    [OK] Installed Models: {', '.join(models) if models else 'None'}")

            # Test prompt completion
            print(f"    Testing generation with model '{ollama_model}'...")
            client = OpenAI(base_url=ollama_url, api_key="ollama")
            res = client.chat.completions.create(
                model=ollama_model,
                messages=[{"role": "user", "content": "Reply with exactly: OLLAMA_QWEN_OK"}],
                max_tokens=15,
            )
            reply = res.choices[0].message.content.strip()
            print(f"    [OK] Response: {reply}")
            print("    [READY] LOCAL QWEN 3 CODER IS FULLY OPERATIONAL")
except Exception as e:
    print(f"    [FAIL] Ollama not reachable or error: {e}")
    print("    [INFO] To use Local Qwen 3 Coder, start Ollama ('ollama serve') and run 'ollama pull qwen3-coder:latest'.")

# 2. Check Google Gemini
gemini_key = os.environ.get("GEMINI_API_KEY")
print(f"\n[2] Checking Google Gemini API...")
if not gemini_key or gemini_key.startswith("your_"):
    print("    [SKIP] GEMINI_API_KEY not configured in .env")
else:
    try:
        gemini_client = OpenAI(
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            api_key=gemini_key,
        )
        res = gemini_client.chat.completions.create(
            model="gemini-3.5-flash-lite",
            messages=[{"role": "user", "content": "Reply with exactly: GEMINI_OK"}],
            max_tokens=15,
        )
        reply = res.choices[0].message.content.strip()
        print(f"    [OK] Response: {reply}")
        print("    [READY] GOOGLE GEMINI IS FULLY OPERATIONAL")
    except Exception as e:
        print(f"    [FAIL] Gemini verification error: {e}")

# 3. Check .NET SDK
print(f"\n[3] Checking .NET SDK...")
try:
    from testgen.dotnet import find_dotnet, ensure_dotnet_env
    ensure_dotnet_env()
    dotnet_bin = find_dotnet()
    if dotnet_bin:
        import subprocess
        proc = subprocess.run([dotnet_bin, "--version"], capture_output=True, text=True)
        version = proc.stdout.strip()
        print(f"    [OK] dotnet binary: {dotnet_bin}")
        print(f"    [OK] .NET SDK Version: {version}")
        print("    [READY] .NET SDK IS FULLY OPERATIONAL")
    else:
        print("    [FAIL] .NET SDK ('dotnet') not found.")
        print("    [INFO] Please install .NET 8+ SDK: https://dotnet.microsoft.com/download")
except Exception as e:
    print(f"    [FAIL] Could not verify .NET SDK: {e}")

print("\n" + "=" * 60)
print("  VERIFICATION COMPLETE")
print("=" * 60)