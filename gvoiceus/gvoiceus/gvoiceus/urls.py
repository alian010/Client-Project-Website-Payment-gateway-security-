# gvoiceus/urls.py
from django.contrib import admin
from django.urls import path, include, reverse_lazy
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.conf.urls.static import static

# hCaptcha-সহ কাস্টম ফর্ম থাকলে সেটাই নেব, না পেলে fallback
try:
    from core.forms import PasswordResetWithHCaptchaForm as _PwdForm
except Exception:
    from django.contrib.auth.forms import PasswordResetForm as _PwdForm

# --- Password Reset (ratelimit ছাড়া) ---
_password_reset_view = auth_views.PasswordResetView.as_view(
    template_name="account/password_reset_form.html",
    email_template_name="account/emails/password_reset_email.txt",
    html_email_template_name="account/emails/password_reset_email.html",
    subject_template_name="account/emails/password_reset_subject.txt",
    success_url=reverse_lazy("password_reset_done"),
    form_class=_PwdForm,  # <- hCaptcha form (বা fallback)
)

password_reset_view = _password_reset_view  # ratelimit নেই

urlpatterns = [
    path("admin/", admin.site.urls),

    # App routes
    path("", include("core.urls")),

    # ---- Password reset flow ----
    path("password-reset/", password_reset_view, name="password_reset"),
    path(
        "password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="account/password_reset_done.html"
        ),
        name="password_reset_done",
    ),
    path(
        "reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="account/password_reset_confirm.html",
            success_url=reverse_lazy("password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path(
        "reset/done/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="account/password_reset_complete.html"
        ),
        name="password_reset_complete",
    ),
]

# Dev এ static/media serve (optional)
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=getattr(settings, "MEDIA_ROOT", None))
    urlpatterns += static(settings.STATIC_URL, document_root=getattr(settings, "STATIC_ROOT", None))
