# core/views.py
from __future__ import annotations

import logging
import os
import uuid
import mimetypes
from hashlib import sha256
from decimal import Decimal, InvalidOperation
from typing import Dict, Any, List, Tuple
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.admin.views.decorators import staff_member_required
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
from django.db import transaction
from django.db.models import Q, Count, Sum
from django.http import (
    HttpRequest, HttpResponse, HttpResponseBadRequest, JsonResponse,
    FileResponse, Http404
)
from django.shortcuts import render, get_object_or_404, redirect
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.csrf import csrf_protect, csrf_exempt
from django.views.decorators.http import require_POST, require_GET

from .models import (
    BlogPost,
    Product, Cart, CartItem,
    Order, OrderItem, Payment
)
from .forms import BlogPostForm

logger = logging.getLogger(__name__)

# Optional Category model (if present)
try:
    from .models import Category  # type: ignore
    HAS_CATEGORY = True
except Exception:
    Category = None  # type: ignore
    HAS_CATEGORY = False

# Optional forms
try:
    from .forms import RegistrationForm, LoginForm  # type: ignore
    HAS_FORMS = True
except Exception:
    RegistrationForm = None  # type: ignore
    LoginForm = None  # type: ignore
    HAS_FORMS = False


# ----------------- common helpers -----------------
CART_SESSION_KEY = "cart"
CART_MAX_PER_ITEM = 10000
DEFAULT_PER_PAGE = 12
MAX_PER_PAGE = 60

def _q2d(x) -> Decimal:
    d = Decimal(x)
    return d.quantize(Decimal("0.01"))

def _parse_decimal(s: str | None) -> Decimal | None:
    if not s:
        return None
    try:
        v = Decimal(s)
        if v < 0:
            v = Decimal("0.00")
        return _q2d(v)
    except (InvalidOperation, ValueError):
        return None

def _parse_int(v: str | None, default: int = 1, *, min_v=1, max_v=1_000_000) -> int:
    try:
        n = int(v) if v is not None else default
    except Exception:
        n = default
    return max(min_v, min(max_v, n))


# ---- Session cart helpers ----
def _get_session_cart(session) -> Dict[str, int]:
    raw = session.get(CART_SESSION_KEY, {})
    cart: Dict[str, int] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                q = int(v)
                if q > 0:
                    cart[str(k)] = q
            except Exception:
                pass
    session[CART_SESSION_KEY] = cart
    session.modified = True
    return cart

def _set_session_cart(session, cart: Dict[str, int]) -> None:
    session[CART_SESSION_KEY] = cart
    session.modified = True

def _clear_session_cart(session) -> None:
    session[CART_SESSION_KEY] = {}
    session.modified = True


# ---- DB cart helpers ----
def _get_user_cart(user: User) -> Cart:
    cart, _ = Cart.objects.get_or_create(user=user)
    return cart

def _add_to_db_cart(user: User, product: Product, qty: int) -> None:
    cart = _get_user_cart(user)
    with transaction.atomic():
        item, created = CartItem.objects.select_for_update().get_or_create(cart=cart, product=product)
        new_qty = item.quantity + qty if not created else qty

        # clamp by stock if tracking
        if product.stock and product.stock > 0:
            new_qty = min(new_qty, int(product.stock))
        new_qty = max(1, min(CART_MAX_PER_ITEM, new_qty))

        item.quantity = new_qty
        item.save()

def _set_db_cart_qty(user: User, product_id: str, qty: int) -> None:
    cart = _get_user_cart(user)
    try:
        product = Product.objects.get(id=product_id)
    except Product.DoesNotExist:
        CartItem.objects.filter(cart=cart, product_id=product_id).delete()
        return

    if qty <= 0:
        CartItem.objects.filter(cart=cart, product=product).delete()
        return

    if product.stock and product.stock > 0:
        qty = min(qty, int(product.stock))
    qty = min(qty, CART_MAX_PER_ITEM)

    CartItem.objects.update_or_create(
        cart=cart, product=product,
        defaults={"quantity": qty}
    )

def _db_cart_count(user: User) -> int:
    cart = _get_user_cart(user)
    return sum(cart.items.values_list("quantity", flat=True))

def _cart_count(request: HttpRequest) -> int:
    if request.user.is_authenticated:
        try:
            return _db_cart_count(request.user)
        except Exception:
            logger.exception("DB cart count failed; falling back to session.")
    return sum(_get_session_cart(request.session).values())

def _merge_session_cart_into_db(request: HttpRequest, user: User) -> None:
    """Login/confirm-এর পর সেশন কার্ট → DB কার্টে মার্জ করুন।"""
    sess = _get_session_cart(request.session)
    if not sess:
        return
    products = {str(p.id): p for p in Product.objects.filter(id__in=sess.keys(), is_active=True)}
    for pid, qty in sess.items():
        product = products.get(pid)
        if not product:
            continue
        try:
            _add_to_db_cart(user, product, qty)
        except Exception:
            logger.exception("Failed merging product %s into DB cart.", pid)
    _clear_session_cart(request.session)


# ----------------- Email confirm (link) helpers -----------------
_SIGNER_SALT = "core.email-confirm"

def _email_checksum(email: str) -> str:
    return sha256((email or "").lower().encode("utf-8")).hexdigest()[:16]

def _make_confirm_token(user: User) -> str:
    """Timestamped, signed token containing user_id and email checksum."""
    signer = TimestampSigner(salt=_SIGNER_SALT)
    value = f"{user.pk}.{_email_checksum(user.email or '')}"
    return signer.sign(value)

def _parse_confirm_token(token: str, *, max_age_seconds: int) -> tuple[int, str]:
    """Returns (user_id, checksum) if ok; raises SignatureExpired/BadSignature on error."""
    signer = TimestampSigner(salt=_SIGNER_SALT)
    raw = signer.unsign(token, max_age=max_age_seconds)
    uid_str, checksum = raw.split(".", 1)
    return int(uid_str), checksum

def _absolute_confirm_url(token: str, request: HttpRequest | None = None) -> str:
    """Prefer settings.SITE_URL (dev-এ http), fallback to request."""
    base = (getattr(settings, "SITE_URL", "") or "").rstrip("/")
    path = reverse("account_confirm", args=[token])
    if base:
        return f"{base}{path}"
    if request is not None:
        return request.build_absolute_uri(path)
    return path

def _send_confirm_link(request: HttpRequest, user: User) -> None:
    """Build confirm URL and send a single email."""
    token = _make_confirm_token(user)
    url = _absolute_confirm_url(token, request)

    subject = "Confirm your email – Gvoiceus"
    text = (
        f"Hi {user.first_name or user.username},\n\n"
        f"Please confirm your email by clicking the link below:\n{url}\n\n"
        "This link will expire soon. If you didn't sign up, just ignore this email."
    )
    from_email = (
        getattr(settings, "DEFAULT_FROM_EMAIL", None)
        or getattr(settings, "EMAIL_HOST_USER", None)
        or "no-reply@myshop.local"
    )
    sent = send_mail(subject, text, from_email, [user.email], fail_silently=False)
    if sent != 1:
        raise RuntimeError("SMTP did not accept the message.")


# ----------------- misc (optional) -----------------
def favicon_redirect(request: HttpRequest) -> HttpResponse:
    """Optional: /favicon.ico → static favicon এ রিডাইরেক্ট (urls.py এ path যোগ করলে 404 কমবে)।"""
    return redirect(static("favicon.ico"))


# ----------------- home / static pages -----------------
def home(request: HttpRequest) -> HttpResponse:
    ctx = {
        "seo": {
            "title": "Welcome to MyShop",
            "description": "Your one-stop shop for amazing products.",
            "robots": "index,follow",
        },
        "cart_count": _cart_count(request),
        # Contact info
        "contact_whatsapp": getattr(settings, "CONTACT_WHATSAPP", "+8801823846863"),
        "contact_telegram": getattr(settings, "CONTACT_TELEGRAM", "asinfluencer_support"),
        "contact_wechat": getattr(settings, "CONTACT_WECHAT", "https://u.wechat.com/kPdFkzE9Jc_SwIrOy6A3Cug?s=2"),
        "contact_email": getattr(settings, "CONTACT_EMAIL", "shakhawat.icpc@gmail.com"),
    }
    return render(request, "home.html", ctx)

def about_view(request: HttpRequest) -> HttpResponse:
    return render(request, "about.html", {
        "seo": {
            "title": "About Us – Asinfluencer",
            "description": "Learn about our digital marketing services and mission.",
            "robots": "index,follow",
        },
        "cart_count": _cart_count(request),
    })

def contract_view(request: HttpRequest) -> HttpResponse:
    """Contact/Contract page."""
    ctx = {
        "seo": {
            "title": "Contact Us – Asinfluencer",
            "description": "Reach us via WhatsApp, Telegram, WeChat, or Email.",
            "robots": "index,follow",
        },
        "cart_count": _cart_count(request),
        "contact_whatsapp": getattr(settings, "CONTACT_WHATSAPP", "+8801823846863"),
        "contact_telegram": getattr(settings, "CONTACT_TELEGRAM", "asinfluencer_support"),
        "contact_wechat": getattr(settings, "CONTACT_WECHAT", "https://u.wechat.com/kPdFkzE9Jc_SwIrOy6A3Cug?s=2"),
        "contact_email": getattr(settings, "CONTACT_EMAIL", "shakhawat.icpc@gmail.com"),
    }
    return render(request, "contract.html", ctx)


# ----------------- categories / products -----------------
def _build_category_nav() -> list[dict]:
    if HAS_CATEGORY and Category is not None:
        qs = Category.objects.filter(is_active=True)
        qs = qs.annotate(count=Count("products", filter=Q(products__is_active=True)))
        return [{"name": c.name, "slug": c.slug, "count": c.count} for c in qs.order_by("name")]

    # Fallback: infer categories from Product.attributes.category (if used)
    names = (
        Product.objects.filter(is_active=True)
        .exclude(attributes__category__isnull=True)
        .exclude(attributes__category__exact="")
        .values_list("attributes__category", flat=True)
        .distinct()
    )
    nav = []
    for name in sorted(set(names)):
        nav.append({"name": name, "slug": slugify(name).lower(), "count": 0})
    return nav


def product_list_view(request: HttpRequest, slug: str | None = None) -> HttpResponse:
    qs = Product.objects.filter(is_active=True)

    active_category_name = None
    if slug:
        if HAS_CATEGORY and Category is not None:
            cat = get_object_or_404(Category.objects.filter(is_active=True), slug=slug)
            active_category_name = cat.name
            qs = qs.filter(category=cat)
        else:
            slug_map = {c["slug"]: c["name"] for c in _build_category_nav()}
            if slug not in slug_map:
                return get_object_or_404(Product, slug="__never__")
            active_category_name = slug_map[slug]
            qs = qs.filter(attributes__category=active_category_name)

    q = (request.GET.get("q") or "").strip()
    if q:
        q = q[:80]
        qs = qs.filter(
            Q(name__icontains=q) |
            Q(sku__icontains=q) |
            Q(short_description__icontains=q) |
            Q(description__icontains=q)
        )

    min_price = _parse_decimal(request.GET.get("min_price"))
    max_price = _parse_decimal(request.GET.get("max_price"))
    if min_price is not None and max_price is not None and min_price > max_price:
        min_price, max_price = max_price, min_price
    if min_price is not None:
        qs = qs.filter(price__gte=min_price)
    if max_price is not None:
        qs = qs.filter(price__lte=max_price)

    if (request.GET.get("in_stock") or "").lower() in {"1", "true", "yes", "on"}:
        qs = qs.filter(stock__gt=0)

    sort = (request.GET.get("sort") or "new").lower()
    order_map = {"new": "-created_at", "price_asc": "price", "price_desc": "-price", "name": "name"}
    qs = qs.order_by(order_map.get(sort, "-created_at"), "-created_at")

    per = _parse_int(request.GET.get("per"), default=12, min_v=1, max_v=MAX_PER_PAGE)
    paginator = Paginator(qs, per)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    context = {
        "products": page_obj,
        "page_obj": page_obj,
        "paginator": paginator,
        "q": q,
        "filters": {
            "min_price": min_price,
            "max_price": max_price,
            "sort": sort,
            "per": per,
            "in_stock": (request.GET.get("in_stock") or "").lower() in {"1","true","yes","on"},
        },
        "categories": _build_category_nav(),
        "active_category": {"slug": slug, "name": active_category_name} if slug else None,
        "seo": {
            "title": f"{active_category_name} – Products" if slug else "All Products",
            "description": "Browse products" + (f" in {active_category_name}" if active_category_name else ""),
            "robots": "index,follow",
        },
        "cart_count": _cart_count(request),
    }
    return render(request, "products/list.html", context)


def product_detail_view(request: HttpRequest, slug: str) -> HttpResponse:
    qs = Product.objects.all()
    if not (request.user.is_authenticated and request.user.is_staff):
        qs = qs.filter(is_active=True)
    product = get_object_or_404(qs, slug=slug)
    related = Product.objects.filter(is_active=True).exclude(id=product.id).order_by("-created_at")[:8]
    return render(request, "products/detail.html", {
        "product": product,
        "related_products": related,
        "categories": _build_category_nav(),
        "seo": {
            "title": product.meta_title or product.name,
            "description": product.meta_description or (product.short_description or "")[:160],
            "robots": "index,follow" if product.is_active else "noindex,nofollow",
        },
        "cart_count": _cart_count(request),
    })


# ----------------- Auth -----------------
@csrf_protect
def account_register(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("home")

    form = RegistrationForm(request.POST or None) if (HAS_FORMS and RegistrationForm) else None

    if request.method == "POST":
        # validate
        if form:
            is_valid = form.is_valid()
            full_name = form.cleaned_data.get("full_name", "").strip() if is_valid else ""
            email = form.cleaned_data.get("email", "").lower() if is_valid else ""
            password = form.cleaned_data.get("password1", "") if is_valid else ""
        else:
            full_name = (request.POST.get("full_name") or "").strip()
            email = (request.POST.get("email") or "").strip().lower()
            password1 = request.POST.get("password1") or ""
            password2 = request.POST.get("password2") or ""
            is_valid = True
            if not full_name or not email or not password1 or not password2:
                messages.error(request, "All fields are required.")
                is_valid = False
            if password1 != password2:
                messages.error(request, "Passwords did not match.")
                is_valid = False
            if User.objects.filter(email__iexact=email).exists():
                messages.error(request, "This email is already registered.")
                is_valid = False
            password = password1

        if is_valid:
            # unique username
            base_un = email.split("@")[0][:20] or "user"
            username = base_un
            i = 1
            while User.objects.filter(username__iexact=username).exists():
                i += 1
                username = f"{base_un}{i}"

            user = User.objects.create_user(
                username=username,
                email=email,
                password=password,
                is_active=False,                  # inactive until email confirmed
                first_name=full_name[:150],
            )

            try:
                _send_confirm_link(request, user)  # request সহ
            except Exception as e:
                logger.exception("Registration: confirm email send failed: %s", e)
                messages.error(request, "Could not send confirmation email right now.")
                user.delete()
                return redirect("register")

            messages.success(request, "Check your inbox for a confirmation link to activate your account.")
            return redirect("login")

    return render(request, "account/register.html", {
        "form": form,
        "cart_count": _cart_count(request),
    })


def account_confirm(request: HttpRequest, token: str) -> HttpResponse:
    """Handle click on confirmation link: activate user + login."""
    max_age = getattr(settings, "EMAIL_CONFIRM_MAX_AGE", 60 * 60 * 24)  # 24h default
    try:
        uid, checksum = _parse_confirm_token(token, max_age_seconds=max_age)
    except SignatureExpired:
        messages.error(request, "This confirmation link has expired. Please register again.")
        return redirect("register")
    except BadSignature:
        messages.error(request, "Invalid confirmation link.")
        return redirect("register")

    user = get_object_or_404(User, pk=uid)
    if _email_checksum(user.email or "") != checksum:
        messages.error(request, "Confirmation link does not match this account.")
        return redirect("register")

    if user.is_active:
        messages.info(request, "Your account is already confirmed. Please sign in.")
        return redirect("login")

    # Activate + login
    user.is_active = True
    user.save(update_fields=["is_active"])
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    try:
        _merge_session_cart_into_db(request, user)
    except Exception:
        logger.exception("Failed to merge session cart after confirm.")
    messages.success(request, "Registration successful. Your email is confirmed.")
    return redirect("home")


@csrf_protect
def account_login(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("home")

    form = LoginForm(request.POST or None) if (HAS_FORMS and LoginForm) else None

    if request.method == "POST":
        # Safe extraction
        if form and hasattr(form, "is_valid") and form.is_valid():
            u_input = (
                form.cleaned_data.get("username_or_email")
                or form.cleaned_data.get("username")
                or form.cleaned_data.get("email")
                or ""
            ).strip()
            p_input = (
                form.cleaned_data.get("password")
                or form.cleaned_data.get("password1")
                or ""
            )
        else:
            u_input = (
                (request.POST.get("username_or_email")
                 or request.POST.get("username")
                 or request.POST.get("email")
                 or "")
            ).strip()
            p_input = request.POST.get("password") or request.POST.get("password1") or ""

        if not u_input or not p_input:
            messages.error(request, "Please provide both username/email and password.")
            return render(request, "account/login.html", {"form": form, "cart_count": _cart_count(request)})

        # email -> username
        u_lookup = u_input
        if "@" in u_lookup:
            try:
                u_lookup = User.objects.get(email__iexact=u_lookup).username
            except User.DoesNotExist:
                u_lookup = "___nope___"

        user = authenticate(request, username=u_lookup, password=p_input)

        if user is None:
            messages.error(request, "Invalid credentials.")
        elif not user.is_active:
            messages.error(request, "Please confirm your email before login (check your inbox).")
        else:
            login(request, user)
            try:
                _merge_session_cart_into_db(request, user)
            except Exception:
                logger.exception("Failed to merge session cart into DB cart after login.")
            messages.success(request, "Welcome back!")
            next_url = request.GET.get("next") or reverse("home")
            return redirect(next_url)

    return render(request, "account/login.html", {"form": form, "cart_count": _cart_count(request)})


@csrf_protect
def account_logout(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        logout(request)
        messages.success(request, "Logged out.")
    return redirect("home")


# ----------------- Cart -----------------
@require_POST
@login_required(login_url="login")
def cart_add_view(request: HttpRequest, slug: str) -> HttpResponse:
    qty = _parse_int(request.POST.get("qty") or request.GET.get("qty"), default=1, min_v=1, max_v=CART_MAX_PER_ITEM)

    qs = Product.objects.all()
    if not (request.user.is_authenticated and request.user.is_staff):
        qs = qs.filter(is_active=True)
    product = get_object_or_404(qs, slug=slug)

    if request.user.is_authenticated:
        _add_to_db_cart(request.user, product, qty)
    else:
        # guest
        cart = _get_session_cart(request.session)
        pid = str(product.id)
        max_allowed = CART_MAX_PER_ITEM
        if product.stock and product.stock > 0:
            max_allowed = min(max_allowed, int(product.stock))
        cart[pid] = min(cart.get(pid, 0) + qty, max_allowed)
        _set_session_cart(request.session, cart)

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse({"ok": True, "count": _cart_count(request)})

    return redirect(
        request.POST.get("next")
        or request.META.get("HTTP_REFERER")
        or reverse("product_detail", args=[slug])
    )

@require_GET
def cart_count_api(request: HttpRequest) -> JsonResponse:
    return JsonResponse({"count": _cart_count(request)})

@require_POST
def cart_update_view(request: HttpRequest, product_id: str) -> HttpResponse:
    action = (request.POST.get("action") or "").lower()

    if request.user.is_authenticated:
        if action == "remove":
            cart = _get_user_cart(request.user)
            CartItem.objects.filter(cart=cart, product_id=product_id).delete()
            return redirect(request.POST.get("next") or reverse("cart_view"))

        if action == "set":
            qty = _parse_int(request.POST.get("qty"), default=1, min_v=0, max_v=CART_MAX_PER_ITEM)
            _set_db_cart_qty(request.user, product_id, qty)
            return redirect(request.POST.get("next") or reverse("cart_view"))

        return HttpResponseBadRequest("Invalid action")

    # guest session branch
    cart = _get_session_cart(request.session)
    pid = str(product_id)

    if action == "remove":
        cart.pop(pid, None)
        _set_session_cart(request.session, cart)
        return redirect(request.POST.get("next") or reverse("cart_view"))

    if action == "set":
        qty = _parse_int(request.POST.get("qty"), default=1, min_v=0, max_v=CART_MAX_PER_ITEM)
        if qty <= 0:
            cart.pop(pid, None)
        else:
            try:
                p = Product.objects.get(id=pid)
                if p.stock and p.stock > 0:
                    qty = min(qty, int(p.stock))
            except Product.DoesNotExist:
                cart.pop(pid, None)
                qty = 0
            if qty > 0:
                cart[pid] = qty
        _set_session_cart(request.session, cart)
        return redirect(request.POST.get("next") or reverse("cart_view"))

    return HttpResponseBadRequest("Invalid action")

def cart_view(request: HttpRequest) -> HttpResponse:
    items: list[dict[str, Any]] = []
    subtotal = Decimal("0.00")

    if request.user.is_authenticated:
        cart = _get_user_cart(request.user)
        db_items = (CartItem.objects.select_related("product")
                    .filter(cart=cart, product__is_active=True))
        for it in db_items:
            p = it.product
            qty = it.quantity
            if p.stock and p.stock > 0:
                qty = min(qty, int(p.stock))
            if qty <= 0:
                continue
            line = _q2d(Decimal(qty) * p.price)
            subtotal += line
            items.append({"product": p, "qty": qty, "unit_price": _q2d(p.price), "line_total": line})
    else:
        cart = _get_session_cart(request.session)
        if cart:
            products = Product.objects.filter(id__in=list(cart.keys()))
            pmap = {str(p.id): p for p in products}
            for pid, qty in cart.items():
                p = pmap.get(pid)
                if not p:
                    continue
                qty = _parse_int(str(qty), default=0, min_v=0, max_v=CART_MAX_PER_ITEM)
                if p.stock and p.stock > 0:
                    qty = min(qty, int(p.stock))
                if qty <= 0:
                    continue
                line = _q2d(Decimal(qty) * p.price)
                subtotal += line
                items.append({"product": p, "qty": qty, "unit_price": _q2d(p.price), "line_total": line})

    return render(request, "cart/cart.html", {
        "items": items,
        "subtotal": _q2d(subtotal),
        "seo": {"title": "Your Cart", "description": "Review your items", "robots": "noindex,nofollow"},
        "cart_count": sum(i["qty"] for i in items),
    })


# =========================
# Checkout & Payments
# =========================

@login_required(login_url="login")
def checkout_view(request: HttpRequest) -> HttpResponse:
    """
    Payment selection page (2Checkout, SSLCommerz, Coin option disabled).
    Subtotal comes from the current cart (DB-backed).
    """
    cart = _get_user_cart(request.user)
    items_qs = CartItem.objects.select_related("product").filter(cart=cart, product__is_active=True)

    if not items_qs.exists():
        messages.info(request, "Your cart is empty.")
        return redirect("cart_view")

    items: List[dict] = []
    subtotal = Decimal("0.00")
    currencies = set()

    for it in items_qs:
        p = it.product
        qty = it.quantity
        if p.stock and p.stock > 0:
            qty = min(qty, int(p.stock))
        if qty <= 0:
            continue
        line = _q2d(Decimal(qty) * p.price)
        subtotal += line
        currencies.add(p.currency or "USD")
        items.append({
            "id": str(p.id),
            "name": p.name,
            "qty": qty,
            "unit_price": _q2d(p.price),
            "line_total": line,
            "currency": p.currency or "USD",
        })

    if not items:
        messages.info(request, "Your cart is empty.")
        return redirect("cart_view")

    # NOTE: multi-currency cart not supported in this simple flow.
    if len(currencies) > 1:
        messages.error(request, "Mixed currency cart isn't supported. Please keep one currency.")
        return redirect("cart_view")

    currency = next(iter(currencies)) if currencies else "USD"

    return render(request, "checkout/choose_payment.html", {
        "items": items,
        "subtotal": _q2d(subtotal),
        "currency": currency,
        "cart_count": _cart_count(request),
    })


def _generate_order_code(prefix: str = "GV") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8].upper()}"

def _build_cart_snapshot(user: User) -> Tuple[List[dict], Decimal, str]:
    """
    Returns (items_list, subtotal, currency)
    """
    cart = _get_user_cart(user)
    items_qs = CartItem.objects.select_related("product").filter(cart=cart, product__is_active=True)

    items: List[dict] = []
    subtotal = Decimal("0.00")
    currencies = set()

    for it in items_qs:
        p = it.product
        qty = it.quantity
        if p.stock and p.stock > 0:
            qty = min(qty, int(p.stock))
        if qty <= 0:
            continue
        line = _q2d(Decimal(qty) * p.price)
        subtotal += line
        currencies.add(p.currency or "USD")
        items.append({
            "product_id": str(p.id),
            "name": p.name,
            "unit_price": str(_q2d(p.price)),
            "qty": qty,
            "line_total": str(line),
            "currency": p.currency or "USD",
        })

    if not items:
        raise ValueError("Cart is empty")

    if len(currencies) > 1:
        raise ValueError("Mixed currency cart is not supported")

    currency = next(iter(currencies)) if currencies else "USD"
    return items, _q2d(subtotal), currency

def _create_order_from_cart(request: HttpRequest) -> Order:
    """
    Create Order (+ OrderItem copies) from current user's DB cart.
    """
    user = request.user
    items, subtotal, currency = _build_cart_snapshot(user)
    order = Order.objects.create(
        order_code=_generate_order_code("GV"),
        user=user,
        email=user.email or "",
        currency=currency,
        subtotal=subtotal,
        total=subtotal,   # no shipping/tax in this demo
        status="pending",
        items_json=items,
    )
    # optional relational copies
    bulk_items = []
    for it in items:
        bulk_items.append(OrderItem(
            order=order,
            product_id=uuid.UUID(it["product_id"]),
            name=it["name"],
            unit_price=Decimal(it["unit_price"]),
            qty=int(it["qty"]),
            line_total=Decimal(it["line_total"]),
        ))
    OrderItem.objects.bulk_create(bulk_items, ignore_conflicts=True)
    return order


# ---------- 2Checkout (International) ----------
@login_required(login_url="login")
def pay_with_2checkout(request: HttpRequest) -> HttpResponse:
    """
    Redirect the customer to 2Checkout hosted page.
    NOTE: Your Payment.METHOD choices are ('stripe','sslcommerz','coin').
    We’ll store this payment as 'stripe' to fit the existing choices.
    """
    buy_link = (
        getattr(settings, "TWOCHECKOUT_BUY_LINK", "")
        or os.environ.get("TWOCHECKOUT_BUY_LINK", "")
    )
    if not buy_link:
        messages.error(request, "2Checkout buy link is not configured.")
        return redirect("checkout")

    try:
        order = _create_order_from_cart(request)
    except ValueError as e:
        messages.error(request, str(e))
        return redirect("cart_view")
    except Exception:
        logger.exception("Failed creating order for 2Checkout")
        messages.error(request, "Could not start checkout. Please try again.")
        return redirect("cart_view")

    # Return URLs
    base = (getattr(settings, "SITE_URL", request.build_absolute_uri("/")).rstrip("/"))
    success_url = base + reverse("checkout_success") + f"?oc={order.order_code}"
    cancel_url  = base + reverse("checkout_cancel")  + f"?oc={order.order_code}"

    params = {
        "currency": (order.currency or "USD"),
        "amount": str(order.total),
        "merchant_order_id": str(order.id),
        "order-ext-ref": order.order_code,
        "return-url": success_url,
        "return-type": "redirect",
        "x_receipt_link_url": success_url,
        "cancel-url": cancel_url,
    }
    seller = getattr(settings, "TWOCHECKOUT_SELLER_ID", "") or os.environ.get("TWOCHECKOUT_SELLER_ID", "")
    if seller:
        params["seller_id"] = seller

    Payment.objects.create(
        order=order,
        method="stripe",             # <- fits your current choices
        status="processing",
        amount=order.total,
        currency=order.currency,
        provider_ref="",
        meta={"start_params": params, "provider": "2checkout"},
    )

    url = f"{buy_link}?{urlencode(params)}"
    return redirect(url, permanent=False)


# ---------- SSLCommerz (Bangladesh) ----------
@login_required(login_url="login")
def pay_with_sslcommerz(request: HttpRequest) -> HttpResponse:
    try:
        import requests  # type: ignore
    except Exception:
        messages.error(request, "Python 'requests' package not installed. Run: pip install requests")
        return redirect("checkout")

    store_id = getattr(settings, "SSLC_STORE_ID", "") or os.environ.get("SSLC_STORE_ID", "")
    store_pass = (
        getattr(settings, "SSLC_STORE_PASS", "")
        or getattr(settings, "SSLC_STORE_PASSWD", "")
        or os.environ.get("SSLC_STORE_PASS", "")
        or os.environ.get("SSLC_STORE_PASSWD", "")
    )
    sandbox = str(getattr(settings, "SSLC_SANDBOX", "True")).lower() == "true"

    if not store_id or not store_pass:
        messages.error(request, "SSLCommerz credentials missing. Set SSLC_STORE_ID and SSLC_STORE_PASS.")
        return redirect("checkout")

    # helper: phone
    def _pick_phone() -> str:
        raw = ""
        try:
            raw = getattr(getattr(request.user, "profile", None), "phone", "") or ""
        except Exception:
            raw = ""
        if not raw:
            raw = getattr(settings, "CUSTOMER_DEFAULT_PHONE", "") or getattr(settings, "CONTACT_WHATSAPP", "")
        digits = "".join(ch for ch in str(raw) if ch.isdigit())
        if not (6 <= len(digits) <= 20):
            digits = "01700000000"
        return digits

    # convert to BDT if need
    def _to_bdt(amount: Decimal, currency: str) -> tuple[Decimal, Decimal]:
        cur = (currency or "BDT").upper()
        if cur == "BDT":
            return _q2d(amount), Decimal("1.00")

        def _get(name: str, default: str) -> Decimal:
            raw = getattr(settings, name, os.environ.get(name, default))
            try:
                return Decimal(str(raw))
            except Exception:
                return Decimal(default)

        rates: dict[str, Decimal] = {
            "USD": _get("FX_USD_BDT", "120.00"),
            "EUR": _get("FX_EUR_BDT", "130.00"),
            "GBP": _get("FX_GBP_BDT", "140.00"),
        }
        rate = rates.get(cur)
        if not rate:
            raise ValueError(f"Currency {cur} is not supported for SSLCommerz without a rate.")
        return _q2d(amount * rate), rate

    try:
        order = _create_order_from_cart(request)
    except ValueError as e:
        messages.error(request, str(e))
        return redirect("cart_view")
    except Exception:
        logger.exception("Failed creating order for SSLCommerz")
        messages.error(request, "Could not start checkout. Please try again.")
        return redirect("cart_view")

    try:
        amount_bdt, fx_rate = _to_bdt(order.total, order.currency or "BDT")
    except ValueError as e:
        messages.error(request, str(e))
        order.status = "cancelled"
        order.save(update_fields=["status"])
        return redirect("checkout")

    if amount_bdt < Decimal("10.00"):
        messages.error(request, f"Amount too small for SSLCommerz: {amount_bdt} BDT (min 10.00).")
        order.status = "cancelled"
        order.save(update_fields=["status"])
        return redirect("checkout")

    customer_phone = _pick_phone()

    tran_id = f"GV{uuid.uuid4().hex[:10].upper()}"
    base = (getattr(settings, "SITE_URL", request.build_absolute_uri("/")).rstrip("/"))
    success_url = base + reverse("checkout_success") + f"?oc={order.order_code}"
    fail_url    = base + reverse("checkout_cancel")  + f"?oc={order.order_code}"
    cancel_url  = fail_url

    init_url = "https://sandbox.sslcommerz.com/gwprocess/v4/api.php" if sandbox \
        else "https://securepay.sslcommerz.com/gwprocess/v4/api.php"

    payload = {
        "store_id": store_id,
        "store_passwd": store_pass,
        "total_amount": str(amount_bdt.quantize(Decimal("0.01"))),
        "currency": "BDT",
        "tran_id": tran_id,
        "success_url": success_url,
        "fail_url": fail_url,
        "cancel_url": cancel_url,

        "cus_name": request.user.first_name or request.user.username,
        "cus_email": order.email or "noreply@example.com",
        "cus_add1": "N/A",
        "cus_city": "Dhaka",
        "cus_postcode": "1200",
        "cus_country": "Bangladesh",
        "cus_phone": customer_phone,

        "shipping_method": "NO",
        "product_name": "Cart Items",
        "product_category": "Ecommerce",
        "product_profile": "general",
        "emi_option": "0",
    }

    try:
        import requests  # type: ignore
        resp = requests.post(init_url, data=payload, timeout=20)
        raw = resp.text[:2000]
        data = {}
        try:
            data = resp.json()
        except Exception:
            for kv in raw.split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    data[k] = v
        gateway_url = (data.get("GatewayPageURL") or "").strip()
        status = (data.get("status") or "").upper()
        reason = data.get("failedreason") or data.get("desc") or data.get("error") or ""
        logger.info("SSLC init code=%s status=%s url=%s reason=%s body=%s",
                    resp.status_code, status, gateway_url, reason, raw)
    except Exception as e:
        logger.exception("SSLCommerz init failed: %s", e)
        messages.error(request, "SSLCommerz error. Please try again later.")
        return redirect("checkout")

    if not gateway_url or status != "SUCCESS":
        msg = reason or "Could not get gateway URL from SSLCommerz."
        messages.error(request, f"SSLCommerz init failed: {msg}")
        return redirect("checkout")

    Payment.objects.create(
        order=order,
        method="sslcommerz",
        status="processing",
        amount=amount_bdt,          # BDT amount charged
        currency="BDT",
        provider_ref=tran_id,
        meta={
            "init_response": data,
            "original_total": str(_q2d(order.total)),
            "original_currency": order.currency,
            "fx_rate_to_bdt": str(fx_rate),
            "charged_bdt": str(amount_bdt),
            "sandbox": sandbox,
        },
    )

    return redirect(gateway_url, permanent=False)


# ---------- Coin (option only / disabled) ----------
@login_required(login_url="login")
def pay_with_coin(request: HttpRequest) -> HttpResponse:
    messages.info(request, "Coin payment: coming soon.")
    return redirect("checkout")


# ---------- 2Checkout Webhook (INS/IPN) ----------
def _verify_tco_signature(payload: dict, secret_word: str | None) -> bool:
    if settings.DEBUG:
        return True
    if not secret_word:
        return False
    # TODO: implement proper verification
    return False

@require_POST
@csrf_exempt
def two_co_webhook(request: HttpRequest) -> HttpResponse:
    payload = request.POST.dict()
    merchant_order_id = payload.get("merchant_order_id") or payload.get("REFNOEXT") or payload.get("ref")
    status_text = (payload.get("ORDERSTATUS") or payload.get("status") or "").lower()

    try:
        verified = _verify_tco_signature(payload, getattr(settings, "TWOCHECKOUT_SECRET_WORD", None))
    except Exception as e:
        logger.exception("2CO webhook signature verify error: %s", e)
        return HttpResponse(status=400)

    if not verified and not settings.DEBUG:
        logger.warning("2CO webhook signature invalid.")
        return HttpResponse(status=400)

    if merchant_order_id:
        try:
            order = Order.objects.get(id=merchant_order_id)
        except Order.DoesNotExist:
            code = payload.get("order-ext-ref") or payload.get("ORDERREF") or ""
            if code:
                try:
                    order = Order.objects.get(order_code=code)
                except Order.DoesNotExist:
                    return HttpResponse(status=200)
            else:
                return HttpResponse(status=200)

        if "approved" in status_text or "complete" in status_text or "paid" in status_text or status_text == "success":
            order.status = "paid"
            if order.user_id:
                try:
                    cart = Cart.objects.get(user_id=order.user_id)
                    cart.items.all().delete()
                except Cart.DoesNotExist:
                    pass
        elif "declined" in status_text or "refunded" in status_text or "failed" in status_text:
            order.status = "failed"
        elif "canceled" in status_text or "cancelled" in status_text:
            order.status = "canceled"
        else:
            order.status = "pending"

        order.provider_order_no = payload.get("order_number") or payload.get("REFNO") or order.provider_order_no
        d = order.data or {}
        d["webhook"] = payload
        d["webhook_last_at"] = str(timezone.now())
        order.data = d
        order.save(update_fields=["status", "provider_order_no", "data", "updated_at"])

        pay = order.payments.order_by("-created_at").first()
        if pay and pay.method in {"stripe", "sslcommerz", "coin"}:
            if order.status == "paid":
                pay.status = "succeeded"
            elif order.status in {"failed", "canceled"}:
                pay.status = "cancelled"
            else:
                pay.status = "processing"
            if payload.get("REFNO"):
                pay.provider_ref = payload["REFNO"]
            pay.meta = {**(pay.meta or {}), "webhook": payload}
            pay.save(update_fields=["status", "provider_ref", "meta", "updated_at"])

    return HttpResponse(status=200)


# ---------- Checkout return pages ----------
def checkout_success(request: HttpRequest) -> HttpResponse:
    oc = request.GET.get("oc") or ""
    order = get_object_or_404(Order, order_code=oc)

    pay = order.payments.order_by("-created_at").first()
    if pay and pay.status in {"processing", "created"}:
        pay.status = "succeeded"
        pay.save(update_fields=["status"])
        order.status = "paid"
        if hasattr(order, "processing_status"):
            order.processing_status = "running"
        order.save(update_fields=["status", "processing_status"] if hasattr(order, "processing_status") else ["status"])

        if order.user_id:
            try:
                cart = Cart.objects.get(user_id=order.user_id)
                cart.items.all().delete()
            except Cart.DoesNotExist:
                pass

    return render(request, "checkout/success.html", {"order": order, "cart_count": _cart_count(request)})

def checkout_cancel(request: HttpRequest) -> HttpResponse:
    oc = request.GET.get("oc") or ""
    try:
        order = Order.objects.get(order_code=oc)
        if order.status == "pending":
            order.status = "cancelled"
            order.save(update_fields=["status"])
        pay = order.payments.order_by("-created_at").first()
        if pay and pay.status in {"processing", "created"}:
            pay.status = "cancelled"
            pay.save(update_fields=["status"])
    except Order.DoesNotExist:
        order = None

    messages.warning(request, "Payment was cancelled.")
    return render(request, "checkout/cancel.html", {"order": order, "cart_count": _cart_count(request)})


def _cart_count(request: HttpRequest) -> int:
    """Small helper if you use it in templates."""
    if not request.user.is_authenticated:
        return 0
    try:
        return CartItem.objects.filter(cart__user=request.user).aggregate(c=Sum("quantity"))["c"] or 0
    except Exception:
        return 0


def _clear_user_db_cart(user) -> None:
    try:
        cart = Cart.objects.get(user=user)
    except Cart.DoesNotExist:
        return
    CartItem.objects.filter(cart=cart).delete()


@staff_member_required
def admin_orders_view(request: HttpRequest) -> HttpResponse:
    status = (request.GET.get("status") or "").lower().strip()
    if status == "canceled":
        status = "cancelled"

    qs = Order.objects.select_related("user").order_by("-created_at")
    if status in {"new", "pending", "paid", "failed", "cancelled"}:
        qs = qs.filter(status=status)

    by_status = (
        Order.objects.values("status")
        .annotate(total=Count("id"), amount=Sum("subtotal"))
    )
    summary = {
        row["status"]: {"count": row["total"] or 0, "amount": row["amount"] or Decimal("0.00")}
        for row in by_status
    }
    for key in ["new", "pending", "paid", "failed", "cancelled"]:
        summary.setdefault(key, {"count": 0, "amount": Decimal("0.00")})

    total_orders = sum(v["count"] for v in summary.values())
    total_revenue = sum(v["amount"] for v in summary.values())

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    return render(request, "orders/admin_list.html", {
        "orders": page_obj,
        "page_obj": page_obj,
        "paginator": paginator,
        "status_filter": status,
        "summary": summary,
        "total_orders": total_orders,
        "total_revenue": total_revenue,
        "status_tabs": ["new", "pending", "paid", "cancelled", "failed"],
        "cart_count": _cart_count(request),
        "seo": {"title": "Admin · Orders", "robots": "noindex,nofollow"},
    })


@login_required(login_url="login")
def my_orders_view(request: HttpRequest) -> HttpResponse:
    qs = Order.objects.filter(user=request.user).order_by("-created_at")

    status = (request.GET.get("status") or "").lower()
    if status in {"new", "pending", "paid", "failed", "canceled", "cancelled"}:
        qs = qs.filter(status=status)

    by_status = (
        Order.objects.filter(user=request.user)
        .values("status").annotate(total=Count("id"), amount=Sum("subtotal"))
    )
    summary = {row["status"]: {"count": row["total"], "amount": row["amount"] or 0} for row in by_status}
    total_orders = sum(v["count"] for v in summary.values())
    total_paid = summary.get("paid", {}).get("count", 0)

    paginator = Paginator(qs, 15)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    return render(request, "orders/my_orders.html", {
        "page_obj": page_obj,
        "paginator": paginator,
        "orders": page_obj.object_list,
        "status_filter": status,
        "summary": summary,
        "total_orders": total_orders,
        "total_paid": total_paid,
        "cart_count": _cart_count(request),
        "seo": {"title": "My Orders", "robots": "noindex,nofollow"},
    })


# ----- Detail pages -----

@login_required(login_url="login")
def my_order_detail(request: HttpRequest, pk: str) -> HttpResponse:
    """
    User can only view their own PAID orders.
    Shows progress + items + order-level delivery file (if any) + user upload (locked after first).
    """
    order = get_object_or_404(Order.objects.select_related("user"), pk=pk, user=request.user)
    if order.status != "paid":
        messages.warning(request, "This order is not available yet. Complete payment first.")
        return redirect("my_orders")

    items = order.items.order_by("id")
    return render(request, "orders/my_order_detail.html", {
        "order": order,
        "items": items,
        "has_item_file_field": hasattr(OrderItem, "delivery_file"),
        "cart_count": _cart_count(request),
        "seo": {"title": f"Order {order.order_code}", "robots": "noindex,nofollow"},
    })


@staff_member_required
def admin_order_detail(request: HttpRequest, pk: str) -> HttpResponse:
    """
    Staff can view any order. Admin upload (delivery_file), and see user's uploaded file (user_file).
    """
    order = get_object_or_404(Order.objects.select_related("user"), pk=pk)
    items = order.items.order_by("id")
    return render(request, "orders/admin_order_detail.html", {
        "order": order,
        "items": items,
        "has_item_file_field": hasattr(OrderItem, "delivery_file"),
        "cart_count": _cart_count(request),
        "seo": {"title": f"Admin · {order.order_code}", "robots": "noindex,nofollow"},
    })


# ===== Staff: toggle running/complete on PAID orders =====
@staff_member_required
@require_POST
def staff_order_toggle_processing(request: HttpRequest, pk: str):
    order = get_object_or_404(Order, pk=pk)
    if order.status != "paid":
        messages.error(request, "Only PAID orders can be marked running/complete.")
        return redirect(request.POST.get("next") or reverse("admin_orders"))

    order.processing_status = "complete" if order.processing_status == "running" else "running"
    order.save(update_fields=["processing_status", "updated_at"])
    messages.success(request, f"Order {order.order_code} marked as {order.processing_status}.")
    return redirect(request.POST.get("next") or reverse("admin_orders"))


# ===== Staff: upload/delete/download order-level file =====
@staff_member_required
@require_POST
def staff_order_upload_file(request: HttpRequest, **kwargs) -> HttpResponse:
    order_pk = kwargs.get("pk") or kwargs.get("order_id")
    order = get_object_or_404(Order, pk=order_pk)

    f = request.FILES.get("output")
    if not f:
        messages.error(request, "No file selected.")
        return redirect(request.POST.get("next") or reverse("admin_order_detail", args=[order.id]))
    if f.size > 50 * 1024 * 1024:
        messages.error(request, "File too large (max 50MB).")
        return redirect(request.POST.get("next") or reverse("admin_order_detail", args=[order.id]))

    allowed = {"application/pdf", "application/zip", "text/plain", "application/octet-stream"}
    guessed, _ = mimetypes.guess_type(f.name)
    if guessed and guessed not in allowed:
        messages.warning(request, f"Uncommon type ({guessed}). Uploading anyway.")

    order.delivery_file = f
    if order.status == "paid" and hasattr(order, "processing_status"):
        order.processing_status = "complete"
    order.save(update_fields=["delivery_file", "processing_status", "updated_at"]
               if hasattr(order, "processing_status") else ["delivery_file", "updated_at"])

    messages.success(request, f"File attached to {order.order_code}.")
    return redirect(request.POST.get("next") or reverse("admin_order_detail", args=[order.id]))


@login_required(login_url="login")
def order_file_download(request: HttpRequest, pk: str) -> HttpResponse:
    order = get_object_or_404(Order, pk=pk)
    if not (request.user.is_staff or (order.user_id == request.user.id)):
        raise Http404("Not allowed.")
    if not getattr(order, "delivery_file", None):
        messages.error(request, "No delivery file attached to this order.")
        return redirect("my_orders" if not request.user.is_staff else "admin_orders")

    try:
        fh = order.delivery_file.open("rb")
    except Exception:
        messages.error(request, "File not found.")
        return redirect("my_orders" if not request.user.is_staff else "admin_orders")

    filename = os.path.basename(order.delivery_file.name)
    ctype, _ = mimetypes.guess_type(filename)
    return FileResponse(fh, as_attachment=True, filename=filename,
                        content_type=ctype or "application/octet-stream")


@staff_member_required
@require_POST
def staff_order_delete_file(request: HttpRequest, **kwargs) -> HttpResponse:
    order_pk = kwargs.get("pk") or kwargs.get("order_id")
    order = get_object_or_404(Order, pk=order_pk)
    if order.delivery_file:
        order.delivery_file.delete(save=False)
        order.delivery_file = None
        order.save(update_fields=["delivery_file", "updated_at"])
        messages.success(request, "File removed.")
    else:
        messages.info(request, "No file to remove.")
    return redirect(request.POST.get("next") or reverse("admin_order_detail", args=[order.id]))


# ===== Per-item actions (Admin → User delivery) =====
@staff_member_required
@require_POST
def staff_item_toggle_processing(request: HttpRequest, item_id: int) -> HttpResponse:
    item = get_object_or_404(OrderItem.objects.select_related("order"), id=item_id)
    if item.order.status != "paid":
        messages.error(request, "Only PAID orders' items can be toggled.")
        return redirect("admin_order_detail", pk=item.order_id)

    item.processing_status = "complete" if item.processing_status == "running" else "running"
    item.save(update_fields=["processing_status"])
    messages.success(request, f"Item '{item.name}' marked {item.processing_status}.")
    return redirect("admin_order_detail", pk=item.order_id)


@staff_member_required
@require_POST
def staff_item_upload_file(request: HttpRequest, item_id: int) -> HttpResponse:
    """
    Admin uploads/updates the delivery file for an item (visible to user).
    """
    item = get_object_or_404(OrderItem.objects.select_related("order"), id=item_id)
    f = request.FILES.get("file") or request.FILES.get("output")
    if not f:
        messages.error(request, "No file selected.")
        return redirect("admin_order_detail", pk=item.order_id)
    if f.size > 50 * 1024 * 1024:
        messages.error(request, "File too large (max 50MB).")
        return redirect("admin_order_detail", pk=item.order_id)

    allowed = {"application/pdf", "application/zip", "text/plain", "application/octet-stream"}
    guessed, _ = mimetypes.guess_type(f.name)
    if guessed and guessed not in allowed:
        messages.warning(request, f"Uncommon type ({guessed}). Uploading anyway.")

    item.delivery_file = f     # Admin → User
    if item.order.status == "paid":
        item.processing_status = "complete"
    item.save(update_fields=["delivery_file", "processing_status"])

    messages.success(request, f"File uploaded for '{item.name}'.")
    return redirect("admin_order_detail", pk=item.order_id)


@staff_member_required
@require_POST
def staff_item_delete_file(request: HttpRequest, item_id: int) -> HttpResponse:
    item = get_object_or_404(OrderItem.objects.select_related("order"), id=item_id)
    if item.delivery_file:
        item.delivery_file.delete(save=False)
        item.delivery_file = None
        item.save(update_fields=["delivery_file"])
        messages.success(request, "Item file removed.")
    else:
        messages.info(request, "No file to remove.")
    return redirect("admin_order_detail", pk=item.order_id)


@login_required(login_url="login")
def item_file_download(request: HttpRequest, item_id: int) -> HttpResponse:
    """
    Download the Admin→User per-item delivery file.
    Allowed for the order owner or any staff.
    URL name used in templates: item_file_download
    """
    item = get_object_or_404(OrderItem.objects.select_related("order"), id=item_id)
    order = item.order

    if not (request.user.is_staff or order.user_id == request.user.id):
        raise Http404("Not allowed.")

    if not item.delivery_file:
        messages.error(request, "No file attached for this item.")
        return redirect("my_order_detail", pk=order.id) if not request.user.is_staff else redirect("admin_order_detail", pk=order.id)

    try:
        fh = item.delivery_file.open("rb")
    except Exception:
        messages.error(request, "File not found.")
        return redirect("my_order_detail", pk=order.id) if not request.user.is_staff else redirect("admin_order_detail", pk=order.id)

    filename = os.path.basename(item.delivery_file.name)
    ctype, _ = mimetypes.guess_type(filename)
    return FileResponse(fh, as_attachment=True, filename=filename,
                        content_type=ctype or "application/octet-stream")


# ===== NEW: User → Admin per-item files (issue/requirement uploads) =====
@login_required(login_url="login")
@require_POST
def user_item_upload_file(request: HttpRequest, item_id: int) -> HttpResponse:
    """
    User uploads a per-item file (issues/requirements). Locked after first upload.
    Visible to admin in admin_order_detail (admin can download/delete).
    """
    item = get_object_or_404(OrderItem.objects.select_related("order"), id=item_id)
    order = item.order
    if order.user_id != request.user.id:
        raise Http404("Not allowed.")
    if order.status != "paid":
        messages.error(request, "You can upload files after payment.")
        return redirect("my_order_detail", pk=order.id)

    # Lock after first upload
    if getattr(item, "user_file", None):
        messages.info(request, "You've already uploaded a file for this item.")
        return redirect("my_order_detail", pk=order.id)

    f = request.FILES.get("file") or request.FILES.get("output")
    if not f:
        messages.error(request, "No file selected.")
        return redirect("my_order_detail", pk=order.id)
    if f.size > 50 * 1024 * 1024:
        messages.error(request, "File too large (max 50MB).")
        return redirect("my_order_detail", pk=order.id)

    allowed = {"application/pdf", "application/zip", "text/plain", "application/octet-stream"}
    guessed, _ = mimetypes.guess_type(f.name)
    if guessed and guessed not in allowed:
        messages.warning(request, f"Uncommon type ({guessed}). Uploading anyway.")

    item.user_file = f  # User → Admin
    item.save(update_fields=["user_file"])

    messages.success(request, "File uploaded. Our team will review it.")
    return redirect("my_order_detail", pk=order.id)


@login_required(login_url="login")
def item_user_file_download(request: HttpRequest, item_id: int) -> HttpResponse:
    """
    Download the user→admin per-item file.
    Owner or staff can download.
    """
    item = get_object_or_404(OrderItem.objects.select_related("order"), id=item_id)
    order = item.order
    if not (request.user.is_staff or order.user_id == request.user.id):
        raise Http404("Not allowed.")

    if not item.user_file:
        messages.error(request, "No user file uploaded for this item.")
        return redirect("my_order_detail", pk=order.id) if not request.user.is_staff else redirect("admin_order_detail", pk=order.id)

    try:
        fh = item.user_file.open("rb")
    except Exception:
        messages.error(request, "File not found.")
        return redirect("my_order_detail", pk=order.id) if not request.user.is_staff else redirect("admin_order_detail", pk=order.id)

    filename = os.path.basename(item.user_file.name)
    ctype, _ = mimetypes.guess_type(filename)
    return FileResponse(fh, as_attachment=True, filename=filename,
                        content_type=ctype or "application/octet-stream")


@staff_member_required
@require_POST
def staff_item_user_file_delete(request: HttpRequest, item_id: int) -> HttpResponse:
    """
    Admin can delete the user-uploaded file (user-side has no delete/replace).
    """
    item = get_object_or_404(OrderItem.objects.select_related("order"), id=item_id)
    if item.user_file:
        item.user_file.delete(save=False)
        item.user_file = None
        item.save(update_fields=["user_file"])
        messages.success(request, "User file removed.")
    else:
        messages.info(request, "No user file to remove.")
    return redirect("admin_order_detail", pk=item.order_id)

# ----------------- Blog -----------------
def blog_list_view(request: HttpRequest) -> HttpResponse:
    qs = BlogPost.objects.filter(is_published=True).order_by("-published_at")
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(title__icontains=q) |
            Q(excerpt__icontains=q) |
            Q(content__icontains=q) |
            Q(tags__icontains=q)
        )

    paginator = Paginator(qs, 9)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    return render(request, "blog/list.html", {
        "seo": {"title": "Blog", "description": "Latest posts & updates", "robots": "index,follow"},
        "page_obj": page_obj,
        "paginator": paginator,
        "posts": page_obj.object_list,
        "q": q,
        "cart_count": _cart_count(request),
    })

def blog_detail_view(request: HttpRequest, slug: str) -> HttpResponse:
    post = get_object_or_404(BlogPost, slug=slug)
    if not post.is_published and not (request.user.is_authenticated and request.user.is_staff):
        messages.warning(request, "This post is not published yet.")
        return redirect("blog_list")

    return render(request, "blog/detail.html", {
        "seo": {"title": post.title, "description": post.excerpt[:150] if post.excerpt else post.title, "robots": "index,follow" if post.is_published else "noindex,nofollow"},
        "post": post,
        "cart_count": _cart_count(request),
    })

@staff_member_required
def staff_blog_create_view(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = BlogPostForm(request.POST, request.FILES)
        if form.is_valid():
            obj: BlogPost = form.save(commit=False)
            obj.author = request.user
            obj.save()
            messages.success(request, "Post created.")
            return redirect(obj.get_absolute_url())
    else:
        form = BlogPostForm()
    return render(request, "blog/form.html", {
        "form": form, "is_edit": False,
        "seo": {"title": "New Post · Blog", "robots": "noindex,nofollow"},
        "cart_count": _cart_count(request),
    })

@staff_member_required
def staff_blog_edit_view(request: HttpRequest, pk: str) -> HttpResponse:
    post = get_object_or_404(BlogPost, pk=pk)
    if request.method == "POST":
        form = BlogPostForm(request.POST, request.FILES, instance=post)
        if form.is_valid():
            form.save()
            messages.success(request, "Post updated.")
            return redirect(post.get_absolute_url())
    else:
        form = BlogPostForm(instance=post)
    return render(request, "blog/form.html", {
        "form": form, "is_edit": True, "post": post,
        "seo": {"title": f"Edit: {post.title}", "robots": "noindex,nofollow"},
        "cart_count": _cart_count(request),
    })

@staff_member_required
@require_POST
def staff_blog_delete_view(request: HttpRequest, pk: str) -> HttpResponse:
    post = get_object_or_404(BlogPost, pk=pk)
    post.delete()
    messages.success(request, "Post deleted.")
    return redirect("blog_list")
