from django.contrib import admin
from .models import Cart, Load, TunnelDay, TunnelRow


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


# ---------------- TUNEL ----------------

class TunnelRowInline(admin.TabularInline):
    model = TunnelRow
    extra = 0
    fields = (
        "order_no",
        "product_kind",
        "product_code",
        "bar_production_date",
        "cooling_time_min",
        "temp_tunnel",
        "temp_inlet",
        "temp_shell_out",
        "temp_core_out",
        "taken_carts_csv",
        "sum_taken_kg",
    )
    readonly_fields = ("sum_taken_kg",)
    ordering = ("order_no",)


@admin.register(TunnelDay)
class TunnelDayAdmin(admin.ModelAdmin):
    list_display = ("date", "created_at", "updated_at")
    date_hierarchy = "date"
    ordering = ("-date",)
    inlines = [TunnelRowInline]
