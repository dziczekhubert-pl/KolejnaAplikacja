from django.contrib import admin
from .models import Cart, Load, TunnelDay, TunnelRow, ProductionPlan


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ("number", "capacity_kg", "is_free_flag")
    search_fields = ("number",)
    ordering = ("number",)

    def is_free_flag(self, obj):
        return obj.is_free
    is_free_flag.boolean = True
    is_free_flag.short_description = "Wolny?"


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
        "initial_weight_kg",
        "status",
        "produced_at",
        "taken_at",
        "cart_weight_snapshot",
    )
    list_filter = (
        "status",
        "product_kind",
        "production_shift",
        "packing_date",
        "cart",
    )
    search_fields = ("product_code", "product_kind", "cart__number")
    list_select_related = ("cart",)
    readonly_fields = ("initial_weight_kg", "edited_at", "cart_weight_snapshot")
    date_hierarchy = "produced_at"
    ordering = ("-produced_at",)


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


# ---------------- PLAN PRODUKCJI (trwały) ----------------

@admin.register(ProductionPlan)
class ProductionPlanAdmin(admin.ModelAdmin):
    list_display = ("slug", "days_count", "updated_at", "updated_by")
    search_fields = ("slug", "updated_by")
    readonly_fields = ("updated_at",)
    ordering = ("slug",)
