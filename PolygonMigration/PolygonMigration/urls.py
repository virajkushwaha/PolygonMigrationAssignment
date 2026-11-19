from django.contrib import admin
from django.urls import path, include
from problems import views as problem_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('oauth/google/authorize/', problem_views.google_drive_authorize, name='google_drive_authorize'),
    path('oauth2callback/', problem_views.google_drive_oauth2callback, name='google_drive_oauth2callback'),
    path('', include('problems.urls')),
    path('users/', include('users.urls')),
]
