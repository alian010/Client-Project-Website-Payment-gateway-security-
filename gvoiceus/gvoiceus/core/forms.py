# core/forms.py
from __future__ import annotations
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from .models import BlogPost
from django.contrib.auth.forms import PasswordResetForm
from hcaptcha.fields import hCaptchaField

User = get_user_model()

class RegistrationForm(forms.Form):
    full_name = forms.CharField(max_length=150)
    email = forms.EmailField()
    password1 = forms.CharField(widget=forms.PasswordInput)
    password2 = forms.CharField(widget=forms.PasswordInput)
    accept_tos = forms.BooleanField()

    def clean_email(self):
        email = (self.cleaned_data["email"] or "").strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cd = super().clean()
        p1, p2 = cd.get("password1"), cd.get("password2")
        if p1 != p2:
            self.add_error("password2", "Passwords do not match.")
        if p1:
            validate_password(p1)
        return cd


class LoginForm(forms.Form):
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)
    remember = forms.BooleanField(required=False)


class VerifyEmailForm(forms.Form):
    email = forms.EmailField()
    code = forms.CharField(min_length=6, max_length=6)

#===============================
# Blog / Articles (simple CMS)

class BlogPostForm(forms.ModelForm):
    class Meta:
        model = BlogPost
        fields = ["title", "slug", "excerpt", "content", "cover", "tags", "is_published", "published_at"]
        widgets = {
            "published_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "content": forms.Textarea(attrs={"rows": 12}),
            "excerpt": forms.Textarea(attrs={"rows": 3}),
        }
        
        
class PasswordResetWithHCaptchaForm(PasswordResetForm):
    hcaptcha = hCaptchaField(label="")