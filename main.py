import requests
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
import faiss
import json
import uvicorn
import numpy as np
from sentence_transformers import SentenceTransformer
import os
import base64
from io import BytesIO
from PIL import Image
import pytesseract
from dotenv import load_dotenv
load_dotenv()

AIPIPE_API_KEY = os.getenv("AIPIPE_API_KEY")
AIPIPE_LLM_URL = "https://aipipe.org/openai/v1/chat/completions"

pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

HEADERS = {
    "Authorization": AIPIPE_API_KEY,
    "Content-Type": "application/json"
}

# Load FAISS index and metadata
index = faiss.read_index("semantic_index.faiss")

with open("embedded_chunks.jsonl", "r") as f:
    embedded_chunks = [json.loads(line) for line in f if line.strip()]


with open("metadata.json", "r", encoding="utf-8") as f:
    metadata = json.load(f)

# Load local embedding model
model = SentenceTransformer("all-MiniLM-L6-v2")

# Define request body structure
class QueryRequest(BaseModel):
    question: str
    image: Optional[str] = None  # base64 string

# Set up FastAPI app
app = FastAPI()
# Tell FastAPI where the templates are
templates = Jinja2Templates(directory="templates")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow any origin
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Semantic search logic
def get_relevant_chunks(question: str, k: int = 5) -> List[dict]:
    embedding = model.encode([question])[0].astype("float32")
    distances, indices = index.search(np.array([embedding]), k)
    results = []

    for idx in indices[0]:
        if idx < len(metadata):
            chunk_info = {
                "text": embedded_chunks[idx]["text"],
                "url": metadata[idx].get("url", ""),
                "title": metadata[idx].get("title", ""),
            }
            results.append(chunk_info)
    return results

def synthesize_answer(question: str, context_chunks: List[dict]) -> str:
    context = "\n\n".join(chunk["text"] for chunk in context_chunks)

    system_prompt = (
        "You are a helpful AI assistant answering student questions using the provided course materials. "
        "Use only the given context to answer. If the answer is not found, say 'I couldn't find an exact answer.'"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"}
    ]

    payload = {
        "model": "gpt-4o-mini",
        "messages": messages,
        "temperature": 0.2
    }

    response = requests.post(AIPIPE_LLM_URL, headers=HEADERS, json=payload)

    if response.ok:
        content = response.json()["choices"][0]["message"]["content"].strip()
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict) and "answer" in parsed:
                return parsed  # Return dict
        except json.JSONDecodeError:
            pass
        return {"answer": content}  # Return simple string answer
    else:
        return {"answer": f"LLM error: {response.status_code} - {response.text}"}


# API endpoint
@app.post("/api/")
async def answer_query(query: QueryRequest):
    # Step 1: Decode and load image if provided
    image_text = ""
    if query.image:
        try:
            # Check if it's a URL or base64
            if query.image.startswith("http://") or query.image.startswith("https://"):
                response = requests.get(query.image)
                image = Image.open(BytesIO(response.content))
            elif query.image.startswith("file://"):
                local_path = query.image.replace("file://", "")
                image = Image.open(local_path)
            else:
                image_data = base64.b64decode(query.image)
                image = Image.open(BytesIO(image_data))

            # Run OCR
            image_text = pytesseract.image_to_string(image)
            print("Extracted from image:", image_text)

        except Exception as e:
            print("Error processing image:", e)

    # Combine image text with question
    full_question = query.question
    if image_text.strip():
        full_question += "\n\nText extracted from image:\n" + image_text
    
    relevant_chunks = get_relevant_chunks(query.question, k=5)
    answer = synthesize_answer(query.question, relevant_chunks)
    
    if isinstance(answer, dict) and "answer" in answer:
        answer = answer["answer"]
    # else, answer is already a string

    links = [
        {"url": chunk["url"], "text": chunk["title"] or chunk["url"]}
        for chunk in relevant_chunks if chunk["url"]
    ]

    return {
        "answer": answer,
        "links": links
    }

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# Run the app
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))  # default to 8000 for local
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)


