# core/views.py

from django import forms
from django.shortcuts import render, redirect, get_object_or_404, resolve_url
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.contrib import messages
from django.utils import timezone
from django.db.models import Prefetch, Q
from django.utils.http import url_has_allowed_host_and_scheme

from .models import Cart, Load
from .forms import BatchCommonForm, BatchRowFormSet


# ------------------------ HELPERY WSPÓLNE ------------------------

def _available_q() -> Q:
    """
    „Na magazynku”: przypięte do wózka i nie zdjęte do produkcji.
    Preferuje pole timestamp 'taken_at', w przeciwnym razie pole 'status' z enumem.
    W ostateczności tylko warunek cart__isnull=False.
    """
    base = Q(cart__isnull=False)

    # Wariant 1: timestamp 'taken_at'
    try:
        Load._meta.get_field("taken_at")
        return base & Q(taken_at__isnull=True)
    except Exception:
        pass

    # Wariant 2: status enum
    try:
        Load._meta.get_field("status")
        status_value = getattr(getattr(Load, "Status", None),
                               "TAKEN_TO_PRODUCTION", "TAKEN_TO_PRODUCTION")
        return base & ~Q(status=status_value)
    except Exception:
        pass

    # Fallback
    return base


def _safe_next(request, fallback_name="home"):
    """
    Zwraca bezpieczny URL do powrotu:
    1) ?next= z GET/POST, 2) Referer (jeśli z tej samej domeny),
    3) resolve_url(fallback_name).
    """
    next_raw = request.POST.get("next") or request.GET.get("next") or ""
    if next_raw and url_has_allowed_host_and_scheme(next_raw, allowed_hosts={request.get_host()}):
        return next_raw
    ref = request.META.get("HTTP_REFERER", "")
    if ref and url_has_allowed_host_and_scheme(ref, allowed_hosts={request.get_host()}):
        return ref
    return resolve_url(fallback_name)


# ----------------------------- WIDOKI UI -----------------------------

def home(request):
    """
    Pokazuje tylko AKTYWNE ładunki (na magazynku).
    Dodatkowo prefetchujemy do wózków tylko aktywne ładunki.
    Sortowanie: Naturalny -> Ziołowy -> Pomidorowy -> puste.
    """
    active_loads_qs = (
        Load.objects
        .select_related("cart")
        .filter(_available_q())
    )

    carts_qs = (
        Cart.objects
        .all()
        .prefetch_related(Prefetch("loads", queryset=active_loads_qs.order_by("id")))
    )

    ORDER_MAP = {"Naturalny": 0, "Ziołowy": 1, "Pomidorowy": 2}

    def rank_for_cart(cart):
        load = cart.loads.last() if hasattr(cart, "loads") else None
        if not load:
            return 3  # puste na końcu
        try:
            kind_display = load.get_product_kind_display()
        except Exception:
            kind_display = (load.product_kind or "").strip()
        return ORDER_MAP.get(kind_display, 3)

    carts = sorted(
        carts_qs,
        key=lambda c: (rank_for_cart(c), str(c.number or "")),
    )

    return render(
        request,
        "core/board.html",
        {"carts": carts, "loads": active_loads_qs}
    )


# >>> PARTIA – wiele wózków na raz
def new_batch(request):
    """
    Tworzenie partii: wiele wierszy (formset). Szanuje flagę POST
    `create_cart_if_missing`, aby utworzyć wózek, jeśli nie istnieje.
    Wózki zajęte są pomijane.

    DODANE: jeżeli w wierszu podano `tare_kg`, zapisujemy ją na wózku (Cart.tare_kg).
    """
    if request.method == "POST":
        common = BatchCommonForm(request.POST)
        formset = BatchRowFormSet(request.POST, prefix="rows")
        create_if_missing = request.POST.get("create_cart_if_missing") == "1"

        if common.is_valid() and formset.is_valid():
            created_count = 0
            skipped_occupied = []
            skipped_missing = []

            for row in formset:
                if row.cleaned_data and not row.cleaned_data.get("DELETE"):
                    cart_number = row.cleaned_data["cart_number"]
                    weight = row.cleaned_data["total_weight_kg"]
                    tank = row.cleaned_data.get("tank")
                    tare_kg = row.cleaned_data.get("tare_kg")  # <— NOWE

                    cart = Cart.objects.filter(number=cart_number).first()
                    if not cart:
                        if create_if_missing:
                            cart = Cart.objects.create(number=cart_number)
                        else:
                            skipped_missing.append(str(cart_number))
                            continue

                    # Jeśli podano tarę – zapisz/uzupełnij na wózku
                    if tare_kg is not None:
                        cart.tare_kg = tare_kg
                        # updated_at o ile istnieje – zaktualizuje się automatycznie
                        try:
                            cart.save(update_fields=["tare_kg", "updated_at"])
                        except Exception:
                            cart.save(update_fields=["tare_kg"])

                    # Nie twórz nowego ładunku, jeśli na wózku jest aktywny
                    has_active = Load.objects.filter(
                        Q(cart=cart) & _available_q()).exists()
                    if has_active:
                        skipped_occupied.append(str(cart_number))
                        continue

                    Load.objects.create(
                        cart=cart,
                        packing_date=common.cleaned_data["packing_date"],
                        production_shift=common.cleaned_data["production_shift"],
                        product_kind=common.cleaned_data["product_kind"],
                        product_code=common.cleaned_data["product_code"],
                        handled_by=common.cleaned_data.get("handled_by", ""),
                        total_weight_kg=weight,
                        tank=tank,
                        produced_at=timezone.now(),
                    )
                    created_count += 1

            if created_count:
                messages.success(
                    request, f"Dodano {created_count} wózków w partii.")
            if skipped_occupied:
                messages.warning(
                    request, f"Pominięto (zajęte): {', '.join(skipped_occupied)}")
            if skipped_missing:
                if create_if_missing:
                    messages.warning(
                        request, f"Pominięto (nie udało się utworzyć): {', '.join(skipped_missing)}")
                else:
                    messages.warning(
                        request, f"Pominięto (brak wózka w systemie): {', '.join(skipped_missing)}")
            return redirect("home")
    else:
        today = timezone.localdate()
        day_of_year = today.timetuple().tm_yday
        common = BatchCommonForm(initial={
            "packing_date": today,
            "product_code": day_of_year,  # domyślnie: numer dnia roku
        })
        formset = BatchRowFormSet(prefix="rows", initial=[])

    return render(request, "core/batch.html", {
        "common": common,
        "formset": formset,
        "title": "Nowa partia wózków z batonami",
    })


@require_POST
def take_to_production(request, load_id):
    """
    Oznacz ładunek jako zdjęty do produkcji; po redirectcie zniknie z tablicy,
    bo home() pokazuje tylko aktywne ładunki.

    DODANE:
    - redirect respektuje ?next= (np. powrót do /tunel/?date=...),
    - snapshot masy zapisywany jest w modelu przez Load.mark_taken()
      (pole cart_weight_snapshot).
    """
    next_url = _safe_next(request, fallback_name="home")

    load = get_object_or_404(Load, pk=load_id)
    if hasattr(load, "status") and getattr(getattr(Load, "Status", None), "TAKEN_TO_PRODUCTION", None):
        if load.status == Load.Status.TAKEN_TO_PRODUCTION:
            messages.info(request, "Ten załadunek został już zdjęty.")
        else:
            load.mark_taken()
            messages.success(
                request, "Załadunek zdjęty do produkcji. Wózek wrócił jako pusty.")
    else:
        try:
            load.mark_taken()
            messages.success(
                request, "Załadunek zdjęty do produkcji. Wózek wrócił jako pusty.")
        except Exception:
            messages.error(
                request, "Nie udało się oznaczyć ładunku jako zdjęty do produkcji.")

    return redirect(next_url)


@require_POST
def delete_cart(request, cart_id):
    cart = get_object_or_404(Cart, pk=cart_id)
    Load.objects.filter(cart=cart).delete()
    cart_number = cart.number
    cart.delete()
    messages.success(
        request, f"Wózek {cart_number} oraz cała jego historia zostały usunięte.")
    return redirect("home")


# ----------------------------- EDYCJA MASY -----------------------------

class EditLoadForm(forms.ModelForm):
    """
    Minimalny formularz do edycji masy + kto dokonał zmiany.
    Walidacja połówki kg zakładana w modelu (validate_half_kg).
    """
    edited_by = forms.CharField(
        label="Edytował",
        max_length=100,
        required=True,
    )

    class Meta:
        model = Load
        fields = ["total_weight_kg"]
        widgets = {
            "total_weight_kg": forms.NumberInput(attrs={"step": "0.5", "min": "0.0", "max": "800.0"})
        }


def edit_load(request, pk):
    """
    Formularz edycji masy ładunku oraz zapis informacji kto zmienił.
    Po zapisie ustawia `edited_by` i `edited_at` (+ inkrementacja `version` jeśli istnieje).
    Wspiera parametr `next` do powrotu (np. /tunel/).
    """
    load = get_object_or_404(Load, pk=pk)

    initial = {}
    if request.user.is_authenticated:
        try:
            initial["edited_by"] = request.user.get_username()
        except Exception:
            initial["edited_by"] = str(request.user)

    next_url = _safe_next(request, fallback_name="home")

    if request.method == "POST":
        form = EditLoadForm(request.POST, instance=load, initial=initial)
        if form.is_valid():
            load.total_weight_kg = form.cleaned_data["total_weight_kg"]
            load.edited_by = form.cleaned_data["edited_by"].strip()
            load.edited_at = timezone.now()
            if hasattr(load, "version"):
                load.version = (load.version or 0) + 1
            update_fields = ["total_weight_kg", "edited_by", "edited_at"]
            if hasattr(load, "version"):
                update_fields.append("version")
            if hasattr(load, "updated_at"):
                update_fields.append("updated_at")
            load.save(update_fields=update_fields)
            messages.success(request, "Zaktualizowano masę wózka.")
            return redirect(next_url)
    else:
        form = EditLoadForm(instance=load, initial=initial)

    return render(request, "core/edit_load.html", {
        "form": form,
        "load": load,
        "next_url": next_url,
        "title": f"Edycja masy – Wózek {load.cart.number if load.cart else '—'}",
    })


# ------------------------------- API -------------------------------

@require_GET
def cart_info(request):
    """
    Zwraca dane wózka po numerze – używane przez edycję masy do automatycznego
    wczytania tary.
    GET: /api/cart-info/?number=44
    Odp:
      {
        "exists": bool,
        "tare_kg": 23.5 | null,
        "capacity_kg": 430.0 | null,
        "is_free": bool
      }
    """
    number = (request.GET.get("number") or "").strip()
    if not number:
        return JsonResponse({"exists": False})

    try:
        cart = Cart.objects.get(number=number)
    except Cart.DoesNotExist:
        return JsonResponse({"exists": False})

    return JsonResponse({
        "exists": True,
        "tare_kg": float(cart.tare_kg) if cart.tare_kg is not None else None,
        "capacity_kg": float(cart.capacity_kg) if cart.capacity_kg is not None else None,
        "is_free": bool(getattr(cart, "is_free", True)),
    })


@require_GET
def api_cart_check(request):
    """
    (Legacy) Sprawdza czy wózek istnieje i czy jest zajęty aktywnym ładunkiem.
    GET: /api/magazynek/cart_check/?number=42
    Odp:
      {
        "exists": bool,
        "occupied": bool,
        "label": "Wózek 42",
        "tare_kg": 23.5 | null
      }
    """
    number_raw = (request.GET.get("number") or "").strip()
    if not number_raw:
        return JsonResponse({"exists": False, "occupied": False, "label": "", "tare_kg": None})

    cart = Cart.objects.filter(number=number_raw).first()
    if not cart:
        return JsonResponse({"exists": False, "occupied": False, "label": f"Wózek {number_raw}", "tare_kg": None})

    occupied = Load.objects.filter(cart=cart).filter(_available_q()).exists()
    tare_val = float(cart.tare_kg) if cart.tare_kg is not None else None

    return JsonResponse({
        "exists": True,
        "occupied": occupied,
        "label": f"Wózek {cart.number}",
        "tare_kg": tare_val,
    })
