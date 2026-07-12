from django.contrib import admin

from .models import Customer, OrderLine, Product


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("external_customer_id", "country", "created_at")
    search_fields = ("external_customer_id", "country")
    list_filter = ("country",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("stock_code", "description")
    search_fields = ("stock_code", "description")


@admin.register(OrderLine)
class OrderLineAdmin(admin.ModelAdmin):
    list_display = (
        "invoice_no",
        "product",
        "customer",
        "quantity",
        "unit_price",
        "invoice_date",
        "is_cancellation",
    )
    list_filter = ("is_cancellation",)
    search_fields = ("invoice_no", "product__stock_code", "customer__external_customer_id")
    date_hierarchy = "invoice_date"
    raw_id_fields = ("product", "customer", "source_batch")
