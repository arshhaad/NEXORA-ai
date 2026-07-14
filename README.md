# Nexora AI

A Django-powered AI chat assistant with Google OAuth authentication.

## Features
- Google Sign-In (OAuth 2.0 via django-allauth)
- Persistent chat conversations
- AI response backend (plug in OpenAI / Gemini)
- User settings — avatar, AI model, theme
- Dark-themed responsive UI

## Stack
- **Backend**: Django 6.x, django-allauth
- **Database**: SQLite (dev) / PostgreSQL (prod)
- **Auth**: Google OAuth 2.0

## Setup

```bash
# 1. Create and activate venv
python -m venv ai
ai\Scripts\activate        # Windows
source ai/bin/activate     # macOS/Linux

# 2. Install dependencies
pip install django "django-allauth[socialaccount]==65.3.1"

# 3. Apply migrations
cd nexora
python manage.py migrate

# 4. Add your Google OAuth credentials in nexora/settings.py
#    SOCIALACCOUNT_PROVIDERS > google > APP > client_id / secret

# 5. Create superuser & set Site domain in admin
python manage.py createsuperuser
# Admin > Sites > set domain to 127.0.0.1:8000

# 6. Run
python manage.py runserver
```

## Google OAuth Setup
1. [Google Cloud Console](https://console.cloud.google.com) → APIs & Services → Credentials
2. Create OAuth 2.0 Client ID (Web application)
3. Authorized redirect URI: `http://127.0.0.1:8000/accounts/google/login/callback/`
4. Paste Client ID and Secret into `settings.py`

## URL Map
| URL | Page |
|-----|------|
| `/` | Login |
| `/signup/` | Sign Up |
| `/chat/` | Chat |
| `/settings/` | Settings |
| `/admin/` | Django Admin |
