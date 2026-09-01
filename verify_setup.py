import os
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

client = OpenAI(
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    api_key=os.environ["GEMINI_API_KEY"],
)

response = client.chat.completions.create(
    model="gemini-3.5-flash-lite",
    messages=[
        {"role": "user", "content": "Reply with exactly: GEMINI_OK"}
    ],
    max_tokens=20,
)

print(response.choices[0].message.content)