from django.urls import path
from . import views

urlpatterns = [
    # Auth
    path('login/',   views.login_view,   name='login'),
    path('signup/',  views.signup_view,  name='signup'),
    path('logout/',  views.logout_view,  name='logout'),
    # Chat
    path('chat/',                              views.chat_view,          name='chat'),
    path('chat/new/',                          views.new_conversation,   name='new_conversation'),
    path('chat/stream/',                       views.stream_message,     name='stream_message'),
    path('chat/delete/<int:conv_id>/',         views.delete_conversation,name='delete_conversation'),
    # Documents (RAG)
    path('chat/upload/',                       views.upload_document,    name='upload_document'),
    path('chat/document/delete/<int:doc_id>/', views.delete_document,    name='delete_document'),
    # Settings
    path('settings/', views.settings_view, name='settings'),
    # Root
    path('', views.home_view, name='home'),
]
