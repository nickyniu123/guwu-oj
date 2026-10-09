from django.contrib import admin
from django.http import Http404
from django.urls import path, include
from django.views.generic import TemplateView
from django_prometheus.exports import ExportToDjangoView

from django.conf.urls import handler404, handler403
from oj_project.views import custom_404_view, custom_403_view

handler404 = custom_404_view
handler403 = custom_403_view

from django.conf import settings
from django.conf.urls.static import static


def _superuser_metrics(request):
    """Serve Prometheus metrics only to authenticated superusers.

    Unauthenticated and non-superuser requests get a plain 404 so the
    endpoint's existence is not revealed.
    """
    user = getattr(request, 'user', None)
    if not (user is not None and user.is_authenticated and user.is_superuser):
        raise Http404
    return ExportToDjangoView(request)


urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('problems.urls')),
    path('users/', include('users.urls')),
    path('submissions/', include('submissions.urls')),
    path('contests/', include('contests.urls')),
    path('handbook/', include('handbook.urls')),
    path('search/', include('search.urls')),
    path('health/', include('health.urls')),
    path('devlog/', include('devlog.urls')),
    path('ai/', include('ai_assistant.urls')),
    path('tickets/', include('tickets.urls')),
    path('internal/judge/', include('submissions.internal_urls')),
    # /metrics is gated to superusers and answers 404 (not 403) to everyone
    # else so its existence is not advertised.
    path('metrics', _superuser_metrics, name='prometheus-django-metrics'),
    path('privacy-policy/', TemplateView.as_view(template_name='legal/privacy_policy.html'), name='privacy_policy'),
    path('terms-of-service/', TemplateView.as_view(template_name='legal/terms_of_service.html'), name='terms_of_service'),
]

# Media files (problem images) — only active in DEBUG, and only when media is
# served locally.  When R2 is enabled MEDIA_URL is an absolute CDN URL (and
# MEDIA_ROOT is unused), which static() cannot serve.  In production nginx
# aliases /media/ directly to MEDIA_ROOT, so Django never sees these.
if settings.DEBUG and not settings.R2_ENABLED:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
