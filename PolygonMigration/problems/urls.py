from django.urls import path
from . import views

app_name = 'problems'

urlpatterns = [
    path('', views.index, name='index'),
    path('oauth/google/authorize/', views.google_drive_authorize, name='google_drive_authorize'),
    path('oauth2callback/', views.google_drive_oauth2callback, name='google_drive_oauth2callback'),
]
