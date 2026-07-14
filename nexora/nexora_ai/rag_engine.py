"""
Nexora RAG Engine
-----------------
Retrieval-Augmented Generation using:
  - Google Gemini 1.5 Flash  (LLM)
  - sentence-transformers     (embeddings)
  - FAISS                     (vector store)
  - PyPDF2                    (PDF parsing)
"""

import os
import re
import pickle
import logging
import numpy as np
from pathlib import Path
from typing import Generator

import google.generativeai as genai
import faiss
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
EMBED_MODEL_NAME = "all-MiniLM-L6-v2"   # fast, lightweight, 384-dim
CHUNK_SIZE = 500          # characters per chunk
CHUNK_OVERLAP = 80        # overlap between chunks
TOP_K = 4                 # number of context chunks to retrieve
MAX_HISTORY = 10          # last N messages sent as conversation history

SYSTEM_PROMPT = """You are Nexora AI, an intelligent assistant similar to ChatGPT and Gemini.
You are helpful, accurate, and conversational. You can:
- Answer any general question with deep knowledge
- Write, explain, and debug code in any language
- Summarise and reason over uploaded documents (RAG context)
- Help with writing, analysis, brainstorming, math, and research

When relevant context from documents is provided, use it to ground your answer.
Format your responses with markdown — use **bold**, `code`, ```code blocks```, bullet points, and headers where appropriate.
Be concise for simple questions and thorough for complex ones."""

# ── Singleton embedding model (loaded once) ────────────────────────────────
_embed_model: SentenceTransformer | None = None

def get_embed_model() -> SentenceTransformer:
    global _embed_model
    if _embed_model is None:
        _embed_model = SentenceTransformer(EMBED_MODEL_NAME)
    return _embed_model


# ── Text chunking ──────────────────────────────────────────────────────────

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks."""
    text = re.sub(r'\s+', ' ', text).strip()
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return [c for c in chunks if len(c.strip()) > 30]


# ── Document parsing ───────────────────────────────────────────────────────

def extract_text_from_file(file_path: str) -> str:
    """Extract plain text from .pdf or .txt files."""
    path = Path(file_path)
    if path.suffix.lower() == '.pdf':
        try:
            import PyPDF2
            text_parts = []
            with open(path, 'rb') as f:
                reader = PyPDF2.PdfReader(f)
                for page in reader.pages:
                    t = page.extract_text()
                    if t:
                        text_parts.append(t)
            return '\n'.join(text_parts)
        except Exception as e:
            logger.error(f"PDF parse error: {e}")
            return ""
    else:
        return path.read_text(encoding='utf-8', errors='ignore')


# ── FAISS vector store per conversation ───────────────────────────────────

class ConversationVectorStore:
    """
    Stores document chunks as FAISS vectors for a single conversation.
    Persisted to disk at <base_dir>/<conv_id>.pkl
    """

    def __init__(self, conv_id: int, base_dir: str):
        self.conv_id = conv_id
        self.path = Path(base_dir) / f"vs_{conv_id}.pkl"
        self.chunks: list[str] = []
        self.index: faiss.IndexFlatL2 | None = None
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, 'rb') as f:
                    data = pickle.load(f)
                self.chunks = data['chunks']
                self.index = faiss.deserialize_index(data['index'])
            except Exception:
                self.chunks = []
                self.index = None

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            'chunks': self.chunks,
            'index': faiss.serialize_index(self.index) if self.index else None,
        }
        with open(self.path, 'wb') as f:
            pickle.dump(data, f)

    def add_document(self, text: str):
        model = get_embed_model()
        new_chunks = chunk_text(text)
        if not new_chunks:
            return
        embeddings = model.encode(new_chunks, show_progress_bar=False).astype('float32')
        dim = embeddings.shape[1]
        if self.index is None:
            self.index = faiss.IndexFlatL2(dim)
        self.index.add(embeddings)
        self.chunks.extend(new_chunks)
        self.save()

    def retrieve(self, query: str, top_k: int = TOP_K) -> list[str]:
        if self.index is None or len(self.chunks) == 0:
            return []
        model = get_embed_model()
        q_vec = model.encode([query], show_progress_bar=False).astype('float32')
        k = min(top_k, len(self.chunks))
        distances, indices = self.index.search(q_vec, k)
        return [self.chunks[i] for i in indices[0] if i < len(self.chunks)]

    def delete(self):
        if self.path.exists():
            self.path.unlink()

    @property
    def has_documents(self) -> bool:
        return self.index is not None and len(self.chunks) > 0


# ── Main AI response function ──────────────────────────────────────────────

def get_rag_response(
    user_message: str,
    history: list[dict],          # [{'role':'user'/'model', 'parts':[text]}]
    vector_store: ConversationVectorStore | None = None,
    model_name: str = "nexora-1",
) -> Generator[str, None, None]:
    """
    Streaming RAG response generator.
    Yields text chunks as they arrive from Gemini.
    """
    if not GEMINI_API_KEY:
        yield "⚠ **Gemini API key not set.** Add `GEMINI_API_KEY` to your environment or `settings.py`."
        return

    # Map nexora model names → Gemini models
    gemini_model_map = {
        "nexora-1":     "gemini-1.5-flash",
        "nexora-pro":   "gemini-1.5-pro",
        "nexora-ultra": "gemini-1.5-pro",  # upgrade when 2.0 ultra is GA
    }
    gemini_model = gemini_model_map.get(model_name, "gemini-1.5-flash")

    # Build RAG context
    rag_context = ""
    if vector_store and vector_store.has_documents:
        chunks = vector_store.retrieve(user_message)
        if chunks:
            rag_context = "\n\n---\n**Relevant document context:**\n" + \
                          "\n\n".join(f"[{i+1}] {c}" for i, c in enumerate(chunks)) + \
                          "\n---\n"

    # Build final user turn
    final_user_turn = user_message
    if rag_context:
        final_user_turn = f"{rag_context}\n\nUser question: {user_message}"

    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel(
            model_name=gemini_model,
            system_instruction=SYSTEM_PROMPT,
        )

        # Build chat history (last MAX_HISTORY turns)
        chat_history = history[-(MAX_HISTORY * 2):]  # user+model pairs

        chat = model.start_chat(history=chat_history)
        response = chat.send_message(final_user_turn, stream=True)

        for chunk in response:
            if chunk.text:
                yield chunk.text

    except Exception as e:
        logger.error(f"Gemini error: {e}")
        yield f"\n\n⚠ **AI Error:** {str(e)}"


def build_gemini_history(messages) -> list[dict]:
    """Convert DB Message queryset → Gemini chat history format."""
    history = []
    for msg in messages:
        role = 'user' if msg.role == 'user' else 'model'
        history.append({'role': role, 'parts': [msg.content]})
    return history
