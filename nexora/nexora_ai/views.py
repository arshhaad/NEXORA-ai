import json
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from django.conf import settings

from .models import UserProfile, Conversation, Message, Document
from .rag_engine import (
    ConversationVectorStore, get_rag_response,
    build_gemini_history, extract_text_from_file,
)

logger = logging.getLogger(__name__)
VECTOR_STORE_DIR = str(getattr(settings, 'VECTOR_STORE_DIR', settings.BASE_DIR / 'vector_stores'))


# ── Helpers ────────────────────────────────────────────────────────────────

def get_or_create_profile(user):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile

def get_vector_store(conv_id: int) -> ConversationVectorStore:
    return ConversationVectorStore(conv_id, VECTOR_STORE_DIR)


# ── Auth ───────────────────────────────────────────────────────────────────

def login_view(request):
    if request.user.is_authenticated:
        return redirect('chat')
    return render(request, 'login.html')

def signup_view(request):
    if request.user.is_authenticated:
        return redirect('chat')
    return render(request, 'signup.html')

def logout_view(request):
    logout(request)
    return redirect('login')


# ── Chat views ─────────────────────────────────────────────────────────────

@login_required
def chat_view(request):
    profile = get_or_create_profile(request.user)
    conversations = Conversation.objects.filter(user=request.user)

    conv_id = request.GET.get('conv')
    active_conv = None
    chat_messages = []
    documents = []

    if conv_id:
        active_conv = get_object_or_404(Conversation, id=conv_id, user=request.user)
        chat_messages = list(active_conv.messages.all())
        documents = list(active_conv.documents.all())

    context = {
        'profile': profile,
        'conversations': conversations,
        'active_conv': active_conv,
        'chat_messages': chat_messages,
        'documents': documents,
    }
    return render(request, 'chatpage.html', context)


@login_required
@require_POST
def new_conversation(request):
    conv = Conversation.objects.create(user=request.user, title='New Chat')
    return redirect(f'/chat/?conv={conv.id}')


@login_required
@require_POST
def delete_conversation(request, conv_id):
    conv = get_object_or_404(Conversation, id=conv_id, user=request.user)
    # Clean up vector store
    vs = get_vector_store(conv.id)
    vs.delete()
    conv.delete()
    return redirect('chat')


# ── Streaming chat endpoint ────────────────────────────────────────────────

@login_required
def stream_message(request):
    """
    SSE streaming endpoint.
    GET params: message, conv_id (optional)
    Streams: data: <chunk>\n\n
    """
    if request.method not in ('GET', 'POST'):
        return JsonResponse({'error': 'Method not allowed'}, status=405)

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
        except Exception:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)
        user_text = data.get('message', '').strip()
        conv_id = data.get('conv_id')
    else:
        user_text = request.GET.get('message', '').strip()
        conv_id = request.GET.get('conv_id')

    if not user_text:
        return JsonResponse({'error': 'Empty message'}, status=400)

    profile = get_or_create_profile(request.user)

    # Get or create conversation
    if conv_id:
        try:
            conv = Conversation.objects.get(id=conv_id, user=request.user)
        except Conversation.DoesNotExist:
            conv = Conversation.objects.create(user=request.user, title=user_text[:60])
    else:
        conv = Conversation.objects.create(user=request.user, title=user_text[:60])

    # Save user message
    Message.objects.create(conversation=conv, role='user', content=user_text)

    # Auto-title from first message
    if conv.messages.filter(role='user').count() == 1:
        conv.title = user_text[:60]
        conv.save()

    # Build Gemini history (exclude current message)
    history_qs = conv.messages.all().order_by('created_at')[:-1]
    gemini_history = build_gemini_history(history_qs)

    # Vector store for this conversation
    vs = get_vector_store(conv.id)

    def event_stream():
        full_response = []
        try:
            # First chunk: metadata
            yield f"data: {json.dumps({'type': 'meta', 'conv_id': conv.id, 'conv_title': conv.title})}\n\n"

            # Stream AI response
            for chunk in get_rag_response(
                user_message=user_text,
                history=gemini_history,
                vector_store=vs,
                model_name=profile.ai_model,
            ):
                full_response.append(chunk)
                yield f"data: {json.dumps({'type': 'chunk', 'text': chunk})}\n\n"

            # Save complete assistant message
            complete = ''.join(full_response)
            Message.objects.create(conversation=conv, role='assistant', content=complete)
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            logger.error(f"Stream error: {e}")
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response


# ── Document upload & management ───────────────────────────────────────────

@login_required
@require_POST
def upload_document(request):
    conv_id = request.POST.get('conv_id')
    if not conv_id:
        return JsonResponse({'error': 'conv_id required'}, status=400)

    conv = get_object_or_404(Conversation, id=conv_id, user=request.user)

    uploaded_file = request.FILES.get('file')
    if not uploaded_file:
        return JsonResponse({'error': 'No file provided'}, status=400)

    allowed_extensions = {'.pdf', '.txt', '.md'}
    ext = '.' + uploaded_file.name.rsplit('.', 1)[-1].lower() if '.' in uploaded_file.name else ''
    if ext not in allowed_extensions:
        return JsonResponse({'error': f'Unsupported file type. Allowed: {", ".join(allowed_extensions)}'}, status=400)

    max_size = 10 * 1024 * 1024  # 10 MB
    if uploaded_file.size > max_size:
        return JsonResponse({'error': 'File too large (max 10 MB)'}, status=400)

    # Save to DB + disk
    doc = Document.objects.create(
        conversation=conv,
        file=uploaded_file,
        filename=uploaded_file.name,
        file_size=uploaded_file.size,
    )

    # Index into FAISS
    try:
        text = extract_text_from_file(doc.file.path)
        if text.strip():
            vs = get_vector_store(conv.id)
            vs.add_document(text)
            doc.indexed = True
            doc.save()
    except Exception as e:
        logger.error(f"Document indexing error: {e}")
        return JsonResponse({'error': f'Indexing failed: {e}'}, status=500)

    return JsonResponse({
        'id': doc.id,
        'filename': doc.filename,
        'file_size': doc.file_size,
        'indexed': doc.indexed,
    })


@login_required
@require_POST
def delete_document(request, doc_id):
    doc = get_object_or_404(Document, id=doc_id, conversation__user=request.user)
    conv_id = doc.conversation.id
    try:
        doc.file.delete(save=False)
    except Exception:
        pass
    doc.delete()
    # Rebuild vector store from remaining docs
    _rebuild_vector_store(conv_id)
    return JsonResponse({'status': 'deleted'})


def _rebuild_vector_store(conv_id: int):
    """Re-index all remaining documents for a conversation."""
    vs = get_vector_store(conv_id)
    vs.chunks = []
    vs.index = None
    remaining = Document.objects.filter(conversation_id=conv_id, indexed=True)
    for doc in remaining:
        try:
            text = extract_text_from_file(doc.file.path)
            if text.strip():
                vs.add_document(text)
        except Exception as e:
            logger.error(f"Rebuild error for doc {doc.id}: {e}")
    vs.save()


# ── Settings ───────────────────────────────────────────────────────────────

@login_required
def settings_view(request):
    profile = get_or_create_profile(request.user)
    avatar_colors = ['#7c3aed', '#06b6d4', '#ec4899', '#f59e0b', '#10b981', '#ef4444', '#3b82f6', '#8b5cf6']

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'update_profile':
            profile.avatar_color = request.POST.get('avatar_color', '#7c3aed')
            profile.ai_model = request.POST.get('ai_model', 'nexora-1')
            profile.theme = request.POST.get('theme', 'dark')
            profile.save()
            messages.success(request, 'Profile updated successfully.')

        elif action == 'delete_account':
            request.user.delete()
            return redirect('login')

        return redirect('settings')

    context = {'profile': profile, 'avatar_colors': avatar_colors}
    return render(request, 'settings.html', context)
