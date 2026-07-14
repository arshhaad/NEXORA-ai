import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib import messages

from .models import UserProfile, Conversation, Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_or_create_profile(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


# ---------------------------------------------------------------------------
# Auth views  (login/signup handled entirely by allauth + Google)
# ---------------------------------------------------------------------------

def login_view(request):
    """Render the Google-only login page."""
    if request.user.is_authenticated:
        return redirect('chat')
    return render(request, 'login.html')


def signup_view(request):
    """Signup redirects to login — Google handles everything."""
    if request.user.is_authenticated:
        return redirect('chat')
    return render(request, 'signup.html')


def logout_view(request):
    logout(request)
    return redirect('login')


# ---------------------------------------------------------------------------
# Chat views
# ---------------------------------------------------------------------------

@login_required
def chat_view(request):
    profile = get_or_create_profile(request.user)
    conversations = Conversation.objects.filter(user=request.user)

    conv_id = request.GET.get('conv')
    active_conv = None
    chat_messages = []

    if conv_id:
        active_conv = get_object_or_404(Conversation, id=conv_id, user=request.user)
        chat_messages = active_conv.messages.all()

    context = {
        'profile': profile,
        'conversations': conversations,
        'active_conv': active_conv,
        'chat_messages': chat_messages,
    }
    return render(request, 'chatpage.html', context)


@login_required
@require_POST
def new_conversation(request):
    conv = Conversation.objects.create(user=request.user, title='New Chat')
    return redirect(f'/chat/?conv={conv.id}')


@login_required
@require_POST
def send_message(request):
    """AJAX — receive JSON message, return AI reply."""
    try:
        data = json.loads(request.body)
        conv_id = data.get('conv_id')
        user_text = data.get('message', '').strip()
    except (json.JSONDecodeError, KeyError):
        return JsonResponse({'error': 'Invalid request'}, status=400)

    if not user_text:
        return JsonResponse({'error': 'Empty message'}, status=400)

    if conv_id:
        conv = get_object_or_404(Conversation, id=conv_id, user=request.user)
    else:
        conv = Conversation.objects.create(user=request.user, title=user_text[:60])

    Message.objects.create(conversation=conv, role='user', content=user_text)

    if conv.messages.count() == 1:
        conv.title = user_text[:60]
        conv.save()

    ai_reply = generate_ai_reply(user_text)
    Message.objects.create(conversation=conv, role='assistant', content=ai_reply)

    return JsonResponse({
        'reply': ai_reply,
        'conv_id': conv.id,
        'conv_title': conv.title,
    })


@login_required
@require_POST
def delete_conversation(request, conv_id):
    conv = get_object_or_404(Conversation, id=conv_id, user=request.user)
    conv.delete()
    return redirect('chat')


# ---------------------------------------------------------------------------
# Settings view
# ---------------------------------------------------------------------------

@login_required
def settings_view(request):
    profile = get_or_create_profile(request.user)
    avatar_colors = ['#7c3aed', '#06b6d4', '#ec4899', '#f59e0b', '#10b981', '#ef4444', '#3b82f6', '#8b5cf6']

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'update_profile':
            avatar_color = request.POST.get('avatar_color', '#7c3aed')
            ai_model = request.POST.get('ai_model', 'nexora-1')
            theme = request.POST.get('theme', 'dark')

            profile.avatar_color = avatar_color
            profile.ai_model = ai_model
            profile.theme = theme
            profile.save()
            messages.success(request, 'Profile updated successfully.')

        elif action == 'delete_account':
            request.user.delete()
            return redirect('login')

        return redirect('settings')

    context = {'profile': profile, 'avatar_colors': avatar_colors}
    return render(request, 'settings.html', context)


# ---------------------------------------------------------------------------
# AI stub — replace with real API call
# ---------------------------------------------------------------------------

def generate_ai_reply(user_text: str) -> str:
    greetings = ['hi', 'hello', 'hey', 'hola']
    if any(g in user_text.lower() for g in greetings):
        return "Hello! I'm Nexora, your AI assistant. How can I help you today?"
    if '?' in user_text:
        return (
            f'That\'s a great question. I\'m in demo mode — connect an AI provider '
            f'in views.py → generate_ai_reply() to get real answers about: "{user_text}"'
        )
    return (
        f'I received: "{user_text}". This is a demo response. '
        'Connect OpenAI, Gemini, etc. in generate_ai_reply() for real responses.'
    )
