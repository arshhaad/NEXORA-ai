"""
Nexora RAG Engine
-----------------
Retrieval-Augmented Generation using:
  - Google Gemini (via google-genai SDK)
  - sentence-transformers  (embeddings)
  - FAISS                  (vector store)
  - PyPDF2                 (PDF parsing)
"""

import os
import re
import pickle
import logging
from pathlib import Path
from typing import Generator

from google import genai
from google.genai import types
import faiss
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────
EMBED_MODEL_NAME = "all-MiniLM-L6-v2"
CHUNK_SIZE   = 500
CHUNK_OVERLAP = 80
TOP_K        = 4
MAX_HISTORY  = 10   # message pairs kept in context

SYSTEM_PROMPT = (
    "You are Nexora AI, an intelligent assistant like ChatGPT and Gemini. "
    "You are helpful, accurate, and conversational. You can:\n"
    "- Answer any general question with deep knowledge\n"
    "- Write, explain, and debug code in any language\n"
    "- Summarise and reason over uploaded documents (RAG context)\n"
    "- Help with writing, analysis, brainstorming, math, and research\n\n"
    "When relevant context from documents is provided, use it to ground your answer. "
    "Format responses with markdown — **bold**, `code`, ```code blocks```, "
    "bullet points, and headers where appropriate. "
    "Be concise for simple questions and thorough for complex ones."
)

# Model name map: nexora brand → Gemini model id
GEMINI_MODEL_MAP = {
    "nexora-1":     "gemini-2.0-flash",
    "nexora-pro":   "gemini-2.0-flash",
    "nexora-ultra": "gemini-2.5-flash-preview-05-20",
}

# ── Singleton embedding model ──────────────────────────────────────────────
_embed_model: SentenceTransformer | None = None

def get_embed_model() -> SentenceTransformer:
    global _embed_model
    if _embed_model is None:
        _embed_model = SentenceTransformer(EMBED_MODEL_NAME)
    return _embed_model


# ── Text chunking ──────────────────────────────────────────────────────────

def chunk_text(text: str, chunk_size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = re.sub(r'\s+', ' ', text).strip()
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start:start + chunk_size])
        start += chunk_size - overlap
    return [c for c in chunks if len(c.strip()) > 30]


# ── Document parsing ───────────────────────────────────────────────────────

def extract_text_from_file(file_path: str) -> str:
    path = Path(file_path)
    if path.suffix.lower() == '.pdf':
        try:
            import PyPDF2
            parts = []
            with open(path, 'rb') as f:
                for page in PyPDF2.PdfReader(f).pages:
                    t = page.extract_text()
                    if t:
                        parts.append(t)
            return '\n'.join(parts)
        except Exception as e:
            logger.error(f"PDF parse error: {e}")
            return ""
    return path.read_text(encoding='utf-8', errors='ignore')


# ── FAISS vector store (per conversation) ─────────────────────────────────

class ConversationVectorStore:
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
                self.index  = faiss.deserialize_index(data['index'])
            except Exception:
                self.chunks, self.index = [], None

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, 'wb') as f:
            pickle.dump({
                'chunks': self.chunks,
                'index':  faiss.serialize_index(self.index) if self.index else None,
            }, f)

    def add_document(self, text: str):
        new_chunks = chunk_text(text)
        if not new_chunks:
            return
        embeddings = get_embed_model().encode(
            new_chunks, show_progress_bar=False).astype('float32')
        if self.index is None:
            self.index = faiss.IndexFlatL2(embeddings.shape[1])
        self.index.add(embeddings)
        self.chunks.extend(new_chunks)
        self.save()

    def retrieve(self, query: str, top_k: int = TOP_K) -> list[str]:
        if not self.has_documents:
            return []
        q_vec = get_embed_model().encode(
            [query], show_progress_bar=False).astype('float32')
        k = min(top_k, len(self.chunks))
        _, indices = self.index.search(q_vec, k)
        return [self.chunks[i] for i in indices[0] if i < len(self.chunks)]

    def delete(self):
        if self.path.exists():
            self.path.unlink()

    @property
    def has_documents(self) -> bool:
        return self.index is not None and bool(self.chunks)


# ── Gemini client (singleton) ──────────────────────────────────────────────
_genai_client: genai.Client | None = None

def _get_client() -> genai.Client:
    global _genai_client
    if _genai_client is None:
        from django.conf import settings
        api_key = getattr(settings, 'GEMINI_API_KEY', '') or os.environ.get('GEMINI_API_KEY', '')
        _genai_client = genai.Client(api_key=api_key)
    return _genai_client


# ── Streaming RAG response ─────────────────────────────────────────────────

def get_rag_response(
    user_message: str,
    history: list[dict],
    vector_store: ConversationVectorStore | None = None,
    model_name: str = "nexora-1",
) -> Generator[str, None, None]:
    """
    Streams response chunks from Gemini with optional RAG context injection.
    history format: [{'role': 'user'|'model', 'parts': ['text']}]
    """
    from django.conf import settings
    api_key = getattr(settings, 'GEMINI_API_KEY', '') or os.environ.get('GEMINI_API_KEY', '')

    if not api_key or api_key == 'YOUR_GEMINI_API_KEY_HERE':
        yield (
            "⚠ **Gemini API key not configured.**\n\n"
            "Add your key to `nexora/.env`:\n"
            "```\nGEMINI_API_KEY=AIzaSy...\n```\n"
            "Get a free key at [aistudio.google.com](https://aistudio.google.com/app/apikey)"
        )
        return

    gemini_model = GEMINI_MODEL_MAP.get(model_name, "gemini-2.0-flash")

    # ── Build RAG context ──────────────────────────────────────────────
    rag_context = ""
    if vector_store and vector_store.has_documents:
        chunks = vector_store.retrieve(user_message)
        if chunks:
            rag_context = (
                "\n\n---\n**Relevant document context:**\n"
                + "\n\n".join(f"[{i+1}] {c}" for i, c in enumerate(chunks))
                + "\n---\n"
            )

    final_message = (
        f"{rag_context}\n\nUser question: {user_message}"
        if rag_context else user_message
    )

    # ── Build chat history for new SDK ────────────────────────────────
    # google-genai Content objects: role must be 'user' or 'model'
    sdk_history: list[types.Content] = []
    for turn in history[-(MAX_HISTORY * 2):]:
        role = turn.get('role', 'user')
        text = turn['parts'][0] if turn.get('parts') else ''
        sdk_history.append(
            types.Content(role=role, parts=[types.Part(text=text)])
        )

    try:
        client = _get_client()

        # Add system instruction as a config parameter
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.7,
            max_output_tokens=8192,
        )

        # Build full contents: history + current user message
        contents = sdk_history + [
            types.Content(role='user', parts=[types.Part(text=final_message)])
        ]

        # Stream response
        for chunk in client.models.generate_content_stream(
            model=gemini_model,
            contents=contents,
            config=config,
        ):
            if chunk.text:
                yield chunk.text

    except Exception as e:
        logger.error(f"Gemini error: {e}")
        yield f"\n\n⚠ **AI Error:** {e}"


# ── History helper ─────────────────────────────────────────────────────────

def build_gemini_history(messages) -> list[dict]:
    """Convert DB Message queryset → history dict list for get_rag_response."""
    return [
        {
            'role':  'user' if msg.role == 'user' else 'model',
            'parts': [msg.content],
        }
        for msg in messages
    ]
