# core/models.py
from __future__ import annotations

import uuid
import mimetypes
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinLengthValidator, MinValueValidator, RegexValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


# ===============================
# Shared helpers & validators
# ===============================
def product_image_upload_to(instance, filename: str) -> str:
    """
    Randomized filenames so original names/paths don't leak.
    Uses a UUID folder even if instance.id doesn't exist yet.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"products/{instance.id or uuid.uuid4()}/{uuid.uuid4().hex}.{ext}"


def validate_image_mime(file):
    """
    Light MIME validation (Pillow also validates; this is an extra guard).
    """
    mime, _ = mimetypes.guess_type(file.name)
    if mime not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValidationError("Only JPEG, PNG, or WEBP images are allowed.")


SLUG_RE = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
slug_validator = RegexValidator(
    regex=SLUG_RE,
    message="Slug must be lowercase letters/numbers with hyphens only (no spaces).",
)

CURRENCY_CHOICES = (
    ("USD", "US Dollar"),
    ("EUR", "Euro"),
    ("BDT", "Bangladeshi Taka"),
    ("INR", "Indian Rupee"),
)


# ===============================
# Category
# ===============================
class Category(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=150, unique=True)
    slug = models.SlugField(max_length=160, unique=True, validators=[slug_validator])

    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )

    description = models.TextField(blank=True)
    icon = models.CharField(max_length=64, blank=True, help_text="Optional icon name (for UI)")
    display_order = models.PositiveIntegerField(default=0, help_text="Smaller shows first")
    is_active = models.BooleanField(default=True)

    # Audit
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["display_order", "name"]
        indexes = [
            models.Index(fields=["slug"], name="idx_category_slug"),
            models.Index(fields=["is_active"], name="idx_category_active"),
            models.Index(fields=["display_order"], name="idx_category_order"),
        ]

    def __str__(self) -> str:
        return f"{self.parent.name + ' › ' if self.parent else ''}{self.name}"

    def clean(self):
        if self.slug:
            self.slug = self.slug.strip().lower()
            slug_validator(self.slug)

    def save(self, *args, **kwargs):
        if not self.slug and self.name:
            self.slug = slugify(self.name).lower()
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("products_by_category", kwargs={"slug": self.slug})


# ===============================
# Product
# ===============================
class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    category = models.ForeignKey(
        "Category",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="products",
        help_text="Select the category this product belongs to.",
    )

    name = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(
        max_length=220,
        unique=True,
        validators=[slug_validator],
        help_text="Lowercase letters, numbers and hyphens only.",
    )
    sku = models.CharField(
        max_length=64,
        unique=True,
        validators=[MinLengthValidator(3)],
        help_text="Internal stock keeping unit (unique).",
    )

    price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default="USD")

    stock = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    image = models.ImageField(
        upload_to=product_image_upload_to,
        null=True,
        blank=True,
        validators=[validate_image_mime],
        help_text="JPEG/PNG/WEBP only.",
    )

    short_description = models.CharField(max_length=250, blank=True)
    description = models.TextField(blank=True)

    attributes = models.JSONField(default=dict, blank=True)

    meta_title = models.CharField(max_length=255, blank=True)
    meta_description = models.CharField(max_length=300, blank=True)

    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["slug"], name="idx_product_slug"),
            models.Index(fields=["is_active", "stock"], name="idx_product_active_stock"),
            models.Index(fields=["created_at"], name="idx_product_created"),
            models.Index(fields=["category"], name="idx_product_category"),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(price__gte=0), name="chk_product_price_nonneg"),
            models.CheckConstraint(check=models.Q(stock__gte=0), name="chk_product_stock_nonneg"),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.sku})"

    def clean(self):
        if self.slug:
            self.slug = self.slug.strip().lower()
            slug_validator(self.slug)
        if self.sku:
            self.sku = self.sku.strip().upper()
        if self.attributes and not isinstance(self.attributes, dict):
            raise ValidationError({"attributes": "Attributes must be a JSON object (key/value)."})
        if self.is_active and (self.price is None or self.price < Decimal("0.00")):
            raise ValidationError({"price": "Active products must have a non-negative price."})

    def save(self, *args, **kwargs):
        if not self.slug and self.name:
            self.slug = slugify(self.name).lower()
        if self.price is not None:
            self.price = self.price.quantize(Decimal("0.01"))
        super().save(*args, **kwargs)

    @property
    def in_stock(self) -> bool:
        return self.is_active and self.stock > 0

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("product_detail", kwargs={"slug": self.slug})


# ===============================
# Persistent User Cart (DB-backed)
# ===============================
class Cart(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="cart")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user"], name="idx_cart_user"),
        ]

    def __str__(self) -> str:
        return f"Cart<{self.user}>"


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = ("cart", "product")
        indexes = [
            models.Index(fields=["cart"], name="idx_cartitem_cart"),
            models.Index(fields=["product"], name="idx_cartitem_product"),
        ]

    def __str__(self) -> str:
        return f"{self.product} x {self.quantity}"


# ===============================
# Orders & Payments
# ===============================
def order_file_upload_to(instance, filename) -> str:
    """
    Order delivery file path
    Example: order_files/2025/10/GV-XXXXXX_<uuid>.ext
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    y = timezone.now().strftime("%Y")
    m = timezone.now().strftime("%m")
    code = (instance.order_code or "ORDER").replace("/", "_")
    return f"order_files/{y}/{m}/{code}_{uuid.uuid4().hex}.{ext}"


def item_file_upload_to(instance, filename) -> str:
    """
    Per-OrderItem delivery file path
    Example: order_items/2025/10/GV-XXXXXX/<item_id>_<uuid>.ext
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    y = timezone.now().strftime("%Y")
    m = timezone.now().strftime("%m")
    code = (instance.order.order_code if instance.order_id else "ORDER").replace("/", "_")
    return f"order_items/{y}/{m}/{code}/{instance.id}_{uuid.uuid4().hex}.{ext}"


class Order(models.Model):
    STATUS = (
        ("pending", "Pending"),
        ("paid", "Paid"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
        ("expired", "Expired"),
    )
    PROCESSING_CHOICES = (
        ("running", "Running"),
        ("complete", "Complete"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order_code = models.CharField(max_length=24, unique=True, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="orders",
    )
    email = models.EmailField()

    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default="USD")
    subtotal = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))]
    )
    total = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))]
    )

    status = models.CharField(max_length=16, choices=STATUS, default="pending")

    # Cart snapshot + notes
    items_json = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True)

    # Order-level progress + (optional) order-level delivery file
    processing_status = models.CharField(
        max_length=20, choices=PROCESSING_CHOICES, default="running", blank=True
    )
    delivery_file = models.FileField(upload_to=order_file_upload_to, null=True, blank=True)

    # Gateway helpers
    provider_order_no = models.CharField(max_length=64, blank=True)
    data = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["order_code"], name="idx_order_code"),
            models.Index(fields=["status"], name="idx_order_status"),
            models.Index(fields=["created_at"], name="idx_order_created"),
            models.Index(fields=["processing_status"], name="idx_order_processing_status"),
        ]

    def __str__(self) -> str:
        return f"{self.order_code} ({self.status})"


# core/models.py (OrderItem এ)
class OrderItem(models.Model):
    PROCESSING_CHOICES = (
        ("running", "Running"),
        ("complete", "Complete"),
    )
    id = models.BigAutoField(primary_key=True)
    order = models.ForeignKey("Order", on_delete=models.CASCADE, related_name="items")
    product_id = models.UUIDField()
    name = models.CharField(max_length=200)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    qty = models.PositiveIntegerField()
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    # Admin → User ডেলিভারি ফাইল (আগে থেকেই ছিল)
    processing_status = models.CharField(max_length=20, choices=PROCESSING_CHOICES, default="running", blank=True)
    delivery_file = models.FileField(upload_to=item_file_upload_to, null=True, blank=True)

    # ✅ নতুন: User → Admin ফাইল (issue/requirements)
    user_file = models.FileField(upload_to=item_file_upload_to, null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["order"], name="idx_orderitem_order")]

    def __str__(self):
        return f"{self.name} x{self.qty}"


class Payment(models.Model):
    METHOD = (
        ("twocheckout", "2Checkout"),
        ("sslcommerz", "SSLCommerz"),
        ("stripe", "Stripe"),
        ("coin", "Coin/Manual"),
    )
    STATUS = (
        ("created", "Created"),
        ("awaiting_manual_review", "Awaiting Manual Review"),
        ("processing", "Processing"),
        ("succeeded", "Succeeded"),
        ("failed", "Failed"),
        ("cancelled", "Cancelled"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    method = models.CharField(max_length=16, choices=METHOD)
    status = models.CharField(max_length=32, choices=STATUS, default="created")

    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default="USD")

    provider_ref = models.CharField(max_length=128, blank=True)
    meta = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["method"], name="idx_payment_method"),
            models.Index(fields=["status"], name="idx_payment_status"),
            models.Index(fields=["created_at"], name="idx_payment_created"),
        ]

    def __str__(self) -> str:
        return f"{self.method} {self.amount} {self.currency} [{self.status}]"


class PaymentEvent(models.Model):
    id = models.BigAutoField(primary_key=True)
    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="events")
    event = models.CharField(max_length=64)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["event"], name="idx_payevent_event"),
            models.Index(fields=["created_at"], name="idx_payevent_created"),
        ]

    def __str__(self) -> str:
        return f"{self.event} @ {self.created_at:%Y-%m-%d %H:%M:%S}"


# ===============================
# Blog / Articles (simple CMS)
# ===============================
def blog_image_upload_to(instance, filename):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "jpg"
    return f"blog/{instance.id or uuid.uuid4()}/{uuid.uuid4().hex}.{ext}"


class BlogPost(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="blog_posts")

    title = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=220, unique=True, help_text="URL slug (auto-generated if empty)")

    excerpt = models.CharField(max_length=300, blank=True, help_text="Short summary (optional)")
    content = models.TextField(help_text="Write your post (HTML allowed for admins).")

    cover = models.ImageField(
        upload_to=blog_image_upload_to,
        null=True,
        blank=True,
        help_text="Optional cover image",
        validators=[validate_image_mime],
    )

    tags = models.CharField(max_length=200, blank=True, help_text="Comma separated tags (optional)")

    is_published = models.BooleanField(default=True)
    published_at = models.DateTimeField(default=timezone.now)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at", "-created_at"]
        indexes = [
            models.Index(fields=["slug"], name="idx_blog_slug"),
            models.Index(fields=["is_published", "published_at"], name="idx_blog_pub"),
            models.Index(fields=["created_at"], name="idx_blog_created"),
        ]

    def __str__(self) -> str:
        return self.title

    def clean(self):
        if not self.slug and self.title:
            self.slug = slugify(self.title).lower()
        if self.slug:
            self.slug = self.slug.strip().lower()

    def save(self, *args, **kwargs):
        if not self.slug and self.title:
            self.slug = slugify(self.title).lower()
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("blog_detail", kwargs={"slug": self.slug})
