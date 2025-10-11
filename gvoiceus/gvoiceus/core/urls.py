# core/urls.py
from django.urls import path
from . import views

urlpatterns = [
    # Home / Static pages
    path("", views.home, name="home"),
    path("contact/", views.contract_view, name="contact"),
    path("about/", views.about_view, name="about"),

    # ----- Staff orders: list + order detail + order-level actions
    path("staff/orders/", views.admin_orders_view, name="admin_orders"),
    path("staff/orders/<uuid:pk>/", views.admin_order_detail, name="admin_order_detail"),

    # order-level progress toggle (kept both names for compatibility)
    path("staff/orders/<uuid:pk>/toggle/", views.staff_order_toggle_processing, name="staff_order_toggle"),
    path("staff/orders/toggle/<uuid:pk>/", views.staff_order_toggle_processing, name="staff_order_toggle_processing"),

    # order-level file (if you still expose this)
    path("staff/orders/<uuid:order_id>/upload/", views.staff_order_upload_file, name="staff_order_upload_file"),
    path("staff/orders/<uuid:order_id>/delete-file/", views.staff_order_delete_file, name="staff_order_delete_file"),

    # ----- Per-item (STAFF) actions — names used in template
    path("staff/orders/item/<int:item_id>/toggle/", views.staff_item_toggle_processing, name="staff_item_toggle"),
    path("staff/orders/item/<int:item_id>/upload/", views.staff_item_upload_file, name="staff_item_upload_file"),
    path("staff/orders/item/<int:item_id>/delete/", views.staff_item_delete_file, name="staff_item_delete_file"),

    # ----- User orders
    path("account/orders/", views.my_orders_view, name="my_orders"),
    path("account/orders/<uuid:pk>/", views.my_order_detail, name="my_order_detail"),

    # downloads
    path("account/orders/<uuid:pk>/download/", views.order_file_download, name="order_file_download"),
    path("account/orders/item/<int:item_id>/download/", views.item_file_download, name="item_file_download"),

    # ----- Products
    path("products/", views.product_list_view, name="products"),
    path("products/category/<slug:slug>/", views.product_list_view, name="products_by_category"),
    path("product/<slug:slug>/", views.product_detail_view, name="product_detail"),

    # ----- Cart
    path("cart/", views.cart_view, name="cart_view"),
    path("cart/add/<slug:slug>/", views.cart_add_view, name="cart_add"),
    path("cart/update/<uuid:product_id>/", views.cart_update_view, name="cart_update"),
    path("api/cart/count/", views.cart_count_api, name="cart_count_api"),

    # ----- Auth
    path("register/", views.account_register, name="register"),
    path("login/", views.account_login, name="login"),
    path("logout/", views.account_logout, name="logout"),
    path("account/confirm/<str:token>/", views.account_confirm, name="account_confirm"),

    # Favicon
    path("favicon.ico", views.favicon_redirect, name="favicon"),

    # ----- Checkout & Payments
    path("checkout/", views.checkout_view, name="checkout"),
    path("checkout/pay/2checkout/", views.pay_with_2checkout, name="pay_with_2checkout"),
    path("checkout/pay/sslcommerz/", views.pay_with_sslcommerz, name="pay_with_sslcommerz"),
    path("checkout/pay/coin/", views.pay_with_coin, name="pay_with_coin"),
    path("checkout/success/", views.checkout_success, name="checkout_success"),
    path("checkout/cancel/", views.checkout_cancel, name="checkout_cancel"),

    # ----- Blog
    path("blog/", views.blog_list_view, name="blog_list"),
    path("blog/<slug:slug>/", views.blog_detail_view, name="blog_detail"),
    path("staff/blog/new/", views.staff_blog_create_view, name="staff_blog_new"),
    path("staff/blog/<uuid:pk>/edit/", views.staff_blog_edit_view, name="staff_blog_edit"),
    path("staff/blog/<uuid:pk>/delete/", views.staff_blog_delete_view, name="staff_blog_delete"),
    
        # User → Admin (per-item) upload
    path("account/orders/item/<int:item_id>/upload/", views.user_item_upload_file, name="user_item_upload_file"),

    # User-file download (owner/staff দু’জনেই ডাউনলোড করতে পারবে)
    path("orders/item/<int:item_id>/user-file/download/", views.item_user_file_download, name="item_user_file_download"),

    # Admin-only: user-file delete
    path("staff/orders/item/<int:item_id>/user-file/delete/", views.staff_item_user_file_delete, name="staff_item_user_file_delete"),

]
