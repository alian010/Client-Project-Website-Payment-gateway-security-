# core/admin.py
from __future__ import annotations

import json
from django.contrib import admin, messages
from django.db.models import QuerySet
from django.utils.html import format_html
from django.utils.translation import gettext_lazy as _
from django.urls import reverse

from .models import (
    Category,
    Product,
    Cart,
    CartItem,
    Order,
    OrderItem,
    Payment,
    PaymentEvent,
    BlogPost,
)

# ---------------------------
# small helpers
# ---------------------------
def _admin_change_url_for(obj) -> str:
    """Return the admin change URL for any model instance."""
    return reverse(f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change", args=[obj.pk])


# ---------------------------
# Common actions / filters
# ---------------------------
@admin.action(description=_("Mark selected as Active"))
def make_active(modeladmin, request, queryset: QuerySet):
    queryset.update(is_active=True)

@admin.action(description=_("Mark selected as Inactive"))
def make_inactive(modeladmin, request, queryset: QuerySet):
    queryset.update(is_active=False)


class InStockFilter(admin.SimpleListFilter):
    title = _("Stock")
    parameter_name = "in_stock"

    def lookups(self, request, model_admin):
        return (("yes", _("In stock (> 0)")), ("no", _("Out of stock (= 0)")))

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.filter(stock__gt=0)
        if self.value() == "no":
            return queryset.filter(stock=0)
        return queryset


# ---------------------------
# Category admin
# ---------------------------
class ProductInline(admin.TabularInline):
    model = Product
    fields = ("name", "sku", "price", "currency", "is_active")
    extra = 0
    show_change_link = True


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "parent", "display_order", "is_active", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("name", "slug")
    ordering = ("display_order", "name")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    inlines = [ProductInline]
    actions = [make_active, make_inactive]
    list_per_page = 25
    save_on_top = True

    fieldsets = (
        (_("Basic"), {"fields": ("name", "slug", "parent", "is_active", "display_order")}),
        (_("Content"), {"fields": ("description", "icon"), "classes": ("collapse",)}),
        (_("Audit"), {"fields": ("created_at", "updated_at")}),
    )


# ---------------------------
# Product admin
# ---------------------------
@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("thumb", "name", "sku", "category", "price", "currency", "stock", "is_active", "created_at")
    list_filter = ("is_active", "currency", "category", "created_at", InStockFilter)
    search_fields = ("name", "sku", "slug", "short_description", "description")
    ordering = ("-created_at",)
    date_hierarchy = "created_at"

    prepopulated_fields = {"slug": ("name",)}
    list_editable = ("is_active", "stock")
    readonly_fields = ("created_at", "updated_at", "image_preview")
    autocomplete_fields = ["category"]
    list_per_page = 25
    save_on_top = True
    actions = [make_active, make_inactive]

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("category")

    @admin.display(description=_("Image"))
    def thumb(self, obj: Product):
        if obj.image:
            return format_html(
                '<img src="{}" style="height:40px;width:40px;object-fit:cover;border-radius:4px" />',
                obj.image.url,
            )
        return "—"

    @admin.display(description=_("Preview"))
    def image_preview(self, obj: Product):
        if obj.image:
            return format_html('<img src="{}" style="max-height:220px;border-radius:6px" />', obj.image.url)
        return _("No image")

    fieldsets = (
        (_("Basic"), {"fields": ("name", "slug", "sku", "category", "is_active")}),
        (_("Pricing & Inventory"), {"fields": ("price", "currency", "stock")}),
        (_("Media"), {"fields": ("image", "image_preview")}),
        (_("Content"), {"fields": ("short_description", "description"), "classes": ("collapse",)}),
        (_("SEO & Attributes"), {"fields": ("meta_title", "meta_description", "attributes"), "classes": ("collapse",)}),
        (_("Audit"), {"fields": ("created_at", "updated_at")}),
    )


# ---------------------------
# Cart (optional – simple read-only)
# ---------------------------
@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at", "updated_at")
    search_fields = ("user__username", "user__email")
    readonly_fields = ("user", "created_at", "updated_at")
    list_select_related = ("user",)
    inlines = []


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ("cart", "product", "quantity")
    search_fields = ("cart__user__username", "product__name", "product__sku")
    list_select_related = ("cart", "product")


# ---------------------------
# Order admin + inlines
# ---------------------------
class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    can_delete = False
    readonly_fields = ("product_id", "name", "unit_price", "qty", "line_total")
    fields = ("name", "qty", "unit_price", "line_total", "product_id")
    show_change_link = False


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    can_delete = False
    readonly_fields = ("method", "status", "amount", "currency", "provider_ref", "meta_pretty", "created_at", "updated_at")
    fields = ("method", "status", "amount", "currency", "provider_ref", "meta_pretty", "created_at", "updated_at")
    show_change_link = True

    @admin.display(description=_("Meta"))
    def meta_pretty(self, obj: Payment):
        pretty = json.dumps(obj.meta or {}, indent=2, ensure_ascii=False)
        return format_html("<pre style='white-space:pre-wrap'>{}</pre>", pretty)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_display = ("order_code", "status_badge", "email", "user_link", "currency", "subtotal", "total", "created_at")
    list_filter = ("status", "processing_status", "currency", "created_at")
    search_fields = ("order_code", "email", "user__username", "user__email", "provider_order_no")
    list_select_related = ("user",)
    save_on_top = True
    list_per_page = 25

    readonly_fields = ("order_code", "user", "email", "currency", "subtotal", "total", "items_pretty", "created_at", "updated_at")
    # keep status & processing_status & delivery_file editable in admin
    fields = (
        "order_code",
        ("status", "processing_status"),
        "user",
        "email",
        ("currency", "subtotal", "total"),
        "delivery_file",
        "items_pretty",
        "notes",
        ("provider_order_no",),
        ("created_at", "updated_at"),
    )

    inlines = [OrderItemInline, PaymentInline]

    @admin.display(description=_("Items (snapshot)"))
    def items_pretty(self, obj: Order):
        pretty = json.dumps(obj.items_json or [], indent=2, ensure_ascii=False)
        return format_html("<pre style='white-space:pre-wrap'>{}</pre>", pretty)

    @admin.display(description=_("User"))
    def user_link(self, obj: Order):
        if not obj.user_id:
            return "—"
        return format_html("<a href='{}'>{}</a>", _admin_change_url_for(obj.user), obj.user)

    @admin.display(description=_("Status"))
    def status_badge(self, obj: Order):
        color = {
            "pending": "#b45309",     # amber-700
            "paid": "#15803d",        # green-700
            "failed": "#b91c1c",      # red-700
            "cancelled": "#334155",   # slate-700
            "expired": "#6b7280",     # gray-500
        }.get(obj.status, "#334155")
        return format_html(
            "<span style='padding:2px 8px;border-radius:999px;background:{};color:white'>{}</span>",
            color,
            obj.get_status_display(),
        )

    # bulk actions
    actions = ["mark_paid", "mark_cancelled", "export_as_csv"]

    @admin.action(description=_("Mark selected orders as PAID"))
    def mark_paid(self, request, queryset: QuerySet[Order]):
        updated = queryset.update(status="paid")
        self.message_user(request, _(f"{updated} order(s) marked as PAID."), messages.SUCCESS)

    @admin.action(description=_("Mark selected orders as CANCELLED"))
    def mark_cancelled(self, request, queryset: QuerySet[Order]):
        updated = queryset.update(status="cancelled")
        self.message_user(request, _(f"{updated} order(s) cancelled."), messages.WARNING)

    @admin.action(description=_("Export selected orders as CSV"))
    def export_as_csv(self, request, queryset: QuerySet[Order]):
        import csv
        from django.http import HttpResponse

        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = "attachment; filename=orders.csv"
        writer = csv.writer(response)
        writer.writerow(
            ["order_code", "status", "processing_status", "email", "currency", "subtotal", "total", "created_at"]
        )
        for o in queryset:
            writer.writerow(
                [o.order_code, o.status, o.processing_status, o.email, o.currency, o.subtotal, o.total, o.created_at.isoformat()]
            )
        return response


# ---------------------------
# Payment admin
# ---------------------------
class PaymentEventInline(admin.TabularInline):
    model = PaymentEvent
    extra = 0
    can_delete = False
    readonly_fields = ("event", "payload_pretty", "created_at")
    fields = ("created_at", "event", "payload_pretty")

    @admin.display(description=_("Payload"))
    def payload_pretty(self, obj: PaymentEvent):
        pretty = json.dumps(obj.payload or {}, indent=2, ensure_ascii=False)
        return format_html("<pre style='white-space:pre-wrap'>{}</pre>", pretty)


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_display = ("order_link", "method", "status", "amount", "currency", "provider_ref", "created_at")
    list_filter = ("method", "status", "currency", "created_at")
    search_fields = ("order__order_code", "provider_ref")
    readonly_fields = ("order", "method", "status", "amount", "currency", "provider_ref", "meta_pretty", "created_at", "updated_at")
    fields = ("order", ("method", "status"), ("amount", "currency"), "provider_ref", "meta_pretty", ("created_at", "updated_at"))
    inlines = [PaymentEventInline]
    list_select_related = ("order",)
    save_on_top = True

    @admin.display(description=_("Order"))
    def order_link(self, obj: Payment):
        return format_html("<a href='{}'>{}</a>", _admin_change_url_for(obj.order), obj.order.order_code)

    @admin.display(description=_("Meta"))
    def meta_pretty(self, obj: Payment):
        pretty = json.dumps(obj.meta or {}, indent=2, ensure_ascii=False)
        return format_html("<pre style='white-space:pre-wrap'>{}</pre>", pretty)


# ---------------------------
# PaymentEvent admin
# ---------------------------
@admin.register(PaymentEvent)
class PaymentEventAdmin(admin.ModelAdmin):
    date_hierarchy = "created_at"
    list_display = ("payment_link", "event", "created_at")
    list_filter = ("event", "created_at")
    search_fields = ("payment__order__order_code", "event")
    readonly_fields = ("payment", "event", "payload_pretty", "created_at")
    fields = ("payment", "event", "payload_pretty", "created_at")
    list_select_related = ("payment", "payment__order")

    @admin.display(description=_("Payment"))
    def payment_link(self, obj: PaymentEvent):
        return format_html("<a href='{}'>{}</a>", _admin_change_url_for(obj.payment), obj.payment)

    @admin.display(description=_("Payload"))
    def payload_pretty(self, obj: PaymentEvent):
        pretty = json.dumps(obj.payload or {}, indent=2, ensure_ascii=False)
        return format_html("<pre style='white-space:pre-wrap'>{}</pre>", pretty)


# ---------------------------
# Blog admin
# ---------------------------
@admin.register(BlogPost)
class BlogPostAdmin(admin.ModelAdmin):
    list_display = ("title", "author", "is_published", "published_at", "created_at")
    list_filter = ("is_published", "author", "published_at")
    search_fields = ("title", "slug", "excerpt", "content", "tags")
    prepopulated_fields = {"slug": ("title",)}
    ordering = ("-published_at",)
    save_on_top = True
