from django.contrib import admin
from django.urls import path

from core.views import (
    home,
    new_batch,
    take_to_production,
    delete_cart,
    edit_load,
    api_cart_check,  # API: /api/magazynek/cart_check/
)
from core import tunel       # widok + API Tunelu
from core import plan_produkcji  # NOWY widok planu produkcji

urlpatterns = [
    # Panel administracyjny
    path("admin/", admin.site.urls),

    # Strona główna / tablica
    path("", home, name="home"),

    # Widok tunelu
    path("tunel/", tunel.tunel_view, name="tunel"),

    # Nowa zakładka: plan produkcji
    path("plan/", plan_produkcji.plan_produkcji, name="plan_produkcji"),

    # Partie i wózki
    path("partia/nowa/", new_batch, name="new_batch"),
    path("cart/<int:cart_id>/delete/", delete_cart, name="delete_cart"),

    # Operacje na ładunkach
    path("load/<int:load_id>/take/", take_to_production, name="take_to_production"),
    path("load/<int:pk>/edit/", edit_load, name="edit_load"),

    # API — magazynek chłodniczy (Tunel)
    path("api/magazynek/lookup/", tunel.magazynek_lookup, name="magazynek_lookup"),
    path("api/magazynek/codes/", tunel.magazynek_codes, name="magazynek_codes"),
    path("api/magazynek/carts/", tunel.api_magazynek_carts,
         name="api_magazynek_carts"),
    path("api/magazynek/cart_info/", tunel.api_magazynek_cart_info,
         name="api_magazynek_cart_info"),

    # API — ogólne
    path("api/magazynek/cart_check/", api_cart_check, name="api_cart_check"),
]
