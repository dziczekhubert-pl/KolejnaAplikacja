from django.contrib import admin
from .models import Cart, Load


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ("number", "capacity_kg", "is_free")
    search_fields = ("number",)

    def is_free(self, obj):
        return obj.is_free
    is_free.boolean = True
    is_free.short_description = "Wolny?"


@admin.register(Load)
class LoadAdmin(admin.ModelAdmin):
    list_display = (
        "product_code",
        "product_kind",
        "packing_date",
        "production_shift",
        "cart",
        "pieces",
        "total_weight_kg",
        "status",
        "produced_at",
        "taken_at",
    )
    list_filter = (
        "status",
        "product_kind",
        "production_shift",
        "packing_date",
        "cart",
    )
    search_fields = (
        "product_code",
        "product_kind",
        "cart__number",
    )
