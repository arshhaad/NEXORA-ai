# Nexora AI

A Django-powered AI chat assistant with Google OAuth, RAG, and Gemini streaming.

## Project structure

```
NEXORA/
├── ai/          ← Python virtual environment
├── nexora/      ← Django project (run everything from here)
│   ├── manage.py
│   ├── .env     ← secrets (never committed)
│   ├── nexora/  ← Django settings, urls, wsgi
│   └── nexora_ai/ ← main app (views, models, templates, RAG engine)
├── .gitignore
└── README.md
```

## Run the project

```bash
# From NEXORA/ root:
cd nexora
..\ai\Scripts\python.exe manage.py runserver
```

Then open http://127.0.0.1:8000

## Setup

```bash
# 1. Install dependencies (already done if venv exists)
..\ai\Scripts\python.exe -m pip install django "django-allauth[socialaccount]==65.3.1" google-generativeai sentence-transformers faiss-cpu pypdf2 python-dotenv

# 2. Edit nexora/.env — add your Gemini API key
GEMINI_API_KEY=AIzaSy...

# 3. Migrate
..\ai\Scripts\python.exe manage.py migrate

# 4. Run
..\ai\Scripts\python.exe manage.py runserver
```

## .env keys (nexora/.env)

| Key | Description |
|-----|-------------|
| `DJANGO_SECRET_KEY` | Django secret |
| `GOOGLE_CLIENT_ID` | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth secret |
| `GEMINI_API_KEY` | Gemini AI key from aistudio.google.com |

## URL map

| URL | Page |
|-----|------|
| `/` | Landing page (home) |
| `/login/` | Sign in with Google |
| `/signup/` | Sign up with Google |
| `/chat/` | AI Chat (login required) |
| `/settings/` | User settings |
| `/admin/` | Django admin |
