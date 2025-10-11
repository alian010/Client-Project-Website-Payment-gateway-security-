# core/utils.py
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings

def send_verification_email(email: str, code: str, *, purpose: str = "signup") -> None:
    """
    ইউজারকে ৬-ডিজিট কোড পাঠায় (TXT + HTML দুই ফরম্যাটে)।
    views.py থেকে ev._raw কোডটা এখানে দিন।
    """
    subject = "Your MyShop verification code"
    context = {"code": code, "purpose": purpose}

    text_body = render_to_string("email/verify_code.txt", context)
    html_body = render_to_string("email/verify_code.html", context)

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[email],
    )
    msg.attach_alternative(html_body, "text/html")
    # fail_silently=False রাখলে ভুল কনফিগে error দেখা যাবে
    msg.send(fail_silently=False)
