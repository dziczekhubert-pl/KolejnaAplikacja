# core/views.py

import re
from django import forms
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.contrib import messages
from django.utils import timezone
from django.db.models import Prefetch, Q, Sum
from django.urls import reverse  # ← potrzebne do zbudowania edit_url

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
        # Jeżeli w modelu jest enum Status z TAKEN_TO_PRODUCTION — użyj go.
        status_value = getattr(getattr(Load, "Status", None),
                               "TAKEN_TO_PRODUCTION", "TAKEN_TO_PRODUCTION")
        return base & ~Q(status=status_value)
    except Exception:
        pass

    # Fallback
    return base


def _normalize_kind(raw: str | None) -> str | None:
    """
    Przyjmuje value lub label z choices i zwraca kanoniczne value (np. 'NAT').
    Gdy brak choices – zwraca wejście.
    """
    if not raw:
        return None
    field = Load._meta.get_field("product_kind")
    choices = list(field.choices or [])
    if not choices:
        return raw

    value_to_label = {str(v): str(l) for v, l in choices}
    label_to_value = {str(l).lower(): str(v) for v, l in choices}

    raw_s = str(raw)
    if raw_s in value_to_label:          # już jest value
        return raw_s
    return label_to_value.get(raw_s.lower())


def _extract_digits(val) -> str | None:
    """
    Zwraca pierwszą sekwencję cyfr (np. z 'T6' -> '6').
    """
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    m = re.search(r"(\d+)", s)
    return m.group(1) if m else None


def _get_attr(obj, names: list[str]):
    """
    Zwraca pierwszy istniejący atrybut z listy names albo None.
    """
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    return None


def _extract_tank_from_objects(load: Load | None, cart: Cart | None) -> str | None:
    """
    Próbuje znaleźć numer tanka w Load lub Cart (oraz ewentualnym FK cart.tank).
    Zwraca sam numer jako string (np. '6'), albo None.
    """
    # 1) Load – typowe nazwy
    if load is not None:
        cand = _get_attr(
            load, ["tank_no", "tank_number", "tankNr", "tanknr", "tank"])
        dn = _extract_digits(cand)
        if dn:
            return dn
        # fallback: może opisowe pola zawierają "T6"
        for n in ["label", "name", "title", "description", "desc"]:
            cand = _get_attr(load, [n])
            dn = _extract_digits(cand)
            if dn:
                return dn

    # 2) Cart – typowe nazwy na wózku
    if cart is not None:
        cand = _get_attr(
            cart, ["tank_no", "tank_number", "tankNr", "tanknr", "tank"])
        dn = _extract_digits(cand)
        if dn:
            return dn

        # 3) Cart.tank jako FK (jeśli istnieje)
        if hasattr(cart, "tank") and cart.tank is not None:
            cand = _get_attr(cart.tank, ["number", "nr", "no", "id", "name"])
            dn = _extract_digits(cand)
            if dn:
                return dn

    return None


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
        # dzięki prefetch „loads” zawiera tylko aktywne ładunki
        load = cart.loads.last() if hasattr(cart, "loads") else None
        if not load:
            return 3  # puste na końcu
        try:
            kind_display = load.get_product_kind_display()
        except Exception:
            kind_display = (load.product_kind or "").strip()
        return ORDER_MAP.get(kind_display, 3)

    # rzutuj numer na str, aby uniknąć porównań int vs str
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
    `create_cart_if_missing` (ustawianą z frontendu), aby utworzyć wózek,
    jeśli nie istnieje. Wózki zajęte są pomijane.
    """
    if request.method == "POST":
        common = BatchCommonForm(request.POST)
        formset = BatchRowFormSet(request.POST, prefix="rows")
        # flaga globalna z formularza (frontend zapyta użytkownika w banerze)
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

                    # Spróbuj pobrać wózek; jeśli nie ma – albo utwórz (gdy flaga), albo pomiń
                    cart = Cart.objects.filter(number=cart_number).first()
                    if not cart:
                        if create_if_missing:
                            cart = Cart.objects.create(number=cart_number)
                        else:
                            skipped_missing.append(str(cart_number))
                            continue

                    # Jeżeli wózek ma już aktywny ładunek – pomijamy
                    has_active = Load.objects.filter(
                        Q(cart=cart) & _available_q()).exists()
                    if has_active:
                        skipped_occupied.append(str(cart_number))
                        continue

                    # Utwórz aktywny ładunek
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
    """
    load = get_object_or_404(Load, pk=load_id)
    # Zakładamy, że w modelu istnieje metoda mark_taken() ustawiająca status/timestamp.
    if hasattr(load, "status") and getattr(getattr(Load, "Status", None), "TAKEN_TO_PRODUCTION", None):
        # jeżeli masz enum i pole status
        if load.status == Load.Status.TAKEN_TO_PRODUCTION:
            messages.info(request, "Ten załadunek został już zdjęty.")
        else:
            load.mark_taken()
            messages.success(
                request, "Załadunek zdjęty do produkcji. Wózek wrócił jako pusty.")
    else:
        # fallback: samo wywołanie metody
        try:
            load.mark_taken()
            messages.success(
                request, "Załadunek zdjęty do produkcji. Wózek wrócił jako pusty.")
        except Exception:
            messages.error(
                request, "Nie udało się oznaczyć ładunku jako zdjęty do produkcji.")
    return redirect("home")


# >>> Usuwanie wózka razem z całą historią
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
    Walidacja połówki kg jest w modelu (validate_half_kg).
    """
    edited_by = forms.CharField(
        label="Edytował",
        max_length=100,
        required=True,
    )

    class Meta:
        model = Load
        fields = ["total_weight_kg"]  # masa edytowalna z modelu
        widgets = {
            "total_weight_kg": forms.NumberInput(attrs={"step": "0.5", "min": "0.0", "max": "500.0"})
        }


def edit_load(request, pk):
    """
    Formularz edycji masy ładunku oraz zapis informacji kto zmienił.
    Po zapisie ustawia `edited_by` i `edited_at` (+ inkrementacja `version` jeśli istnieje).
    """
    load = get_object_or_404(Load, pk=pk)

    initial = {}
    if request.user.is_authenticated:
        try:
            initial["edited_by"] = request.user.get_username()
        except Exception:
            initial["edited_by"] = str(request.user)

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
            return redirect("home")
    else:
        form = EditLoadForm(instance=load, initial=initial)

    return render(request, "core/edit_load.html", {
        "form": form,
        "load": load,
        "title": f"Edycja masy – Wózek {load.cart.number if load.cart else '—'}",
    })


# ------------------------------- API -------------------------------

@require_GET
def magazynek_codes(request):
    """
    Zwraca listę dostępnych kodów (unikalnych) dla wybranego rodzaju „na magazynku”.
    Akceptuje zarówno value (np. 'NAT') jak i label (np. 'Naturalny').
    Odp: { "codes": [101, 203, ...], "kind_normalized": "NAT" }
    """
    kind = _normalize_kind(request.GET.get("kind"))
    if not kind:
        return JsonResponse({"codes": [], "kind_normalized": None})

    qs = (
        Load.objects
        .filter(_available_q(), product_kind=kind)
        .values_list("product_code", flat=True)
        .order_by("product_code")
        .distinct()
    )
    return JsonResponse({"codes": list(qs), "kind_normalized": kind})


@require_GET
def magazynek_lookup(request):
    """
    Zwraca najświeższą datę pakowania (packing_date) dla (kind, code) „na magazynku”.
    Akceptuje zarówno value jak i label rodzaju; code może być int lub str.
    Odp: { "found": bool, "latest_packing_date_iso": "YYYY-MM-DD" }
    """
    kind = _normalize_kind(request.GET.get("kind"))
    code_raw = request.GET.get("code")

    # rzutowanie kodu
    code = None
    if code_raw is not None:
        try:
            code = int(code_raw)
        except (TypeError, ValueError):
            code = str(code_raw).strip()

    if not kind or code in (None, ""):
        return JsonResponse({"found": False, "reason": "bad_params"}, status=400)

    obj = (
        Load.objects
        .filter(_available_q(), product_kind=kind, product_code=code)
        .order_by("-packing_date", "-id")
        .first()
    )
    if not obj:
        return JsonResponse({"found": False})

    date_iso = obj.packing_date.isoformat() if obj.packing_date else None
    return JsonResponse({"found": True, "latest_packing_date_iso": date_iso})


@require_GET
def api_guess_packing_date(request):
    """
    Proste API: najnowsza packing_date dla (kind, code) – niezależnie od stanu.
    Akceptuje zarówno value jak i label rodzaju; code może być int lub str.
    Odp: { "packing_date": "YYYY-MM-DD" | null }
    """
    kind = _normalize_kind(request.GET.get("kind"))
    code_raw = request.GET.get("code")

    if not kind or code_raw is None:
        return JsonResponse({"packing_date": None})

    try:
        code = int(code_raw)
    except (TypeError, ValueError):
        code = str(code_raw).strip()

    date = (
        Load.objects
        .filter(product_kind=kind, product_code=code)
        .order_by("-packing_date")
        .values_list("packing_date", flat=True)
        .first()
    )

    return JsonResponse({"packing_date": date.isoformat() if date else None})


@require_GET
def api_magazynek_carts(request):
    """
    Zwraca numery wózków dostępnych dla (kind, code) NA MAGAZYNKU.
    GET: ?kind=NAT&code=123
    Odp: { "carts": [12, 34, 57] }
    """
    kind = _normalize_kind(request.GET.get("kind"))
    code_raw = request.GET.get("code")

    if not kind or code_raw is None:
        return JsonResponse({"carts": []})

    try:
        code = int(code_raw)
    except (TypeError, ValueError):
        code = str(code_raw).strip()

    carts_qs = (
        Load.objects
        .filter(_available_q(), product_kind=kind, product_code=code)
        .values_list("cart__number", flat=True)
        .order_by("cart__number")
        .distinct()
    )
    return JsonResponse({"carts": list(carts_qs)})


@require_GET
def api_magazynek_cart_info(request):
    """
    Zwraca masę (total_weight_kg) aktywnego ładunku dla podanego wózka (NA MAGAZYNKU)
    w trójce (kind, code, cart), numer tanka (tank_no), a także load_id i edit_url,
    jeżeli rekord istnieje i jest edytowalny.

    GET: ?kind=NAT&code=123&cart=42
    Odp:
      {
        "cart": 42,
        "total_weight_kg": 123.5,
        "tank_no": "6",
        "load_id": 987,
        "edit_url": "/loads/987/edit/"
      }
    albo gdy brak aktywnego: {"cart": 42, "total_weight_kg": None, "tank_no": None, "load_id": None, "edit_url": None}
    """
    kind = _normalize_kind(request.GET.get("kind"))
    code_raw = request.GET.get("code")
    cart_raw = request.GET.get("cart")

    if not kind or code_raw is None or cart_raw is None:
        return JsonResponse({
            "cart": cart_raw,
            "total_weight_kg": None,
            "tank_no": None,
            "load_id": None,
            "edit_url": None,
        })

    # rzutowania kodu i numeru wózka
    try:
        code = int(code_raw)
    except (TypeError, ValueError):
        code = str(code_raw).strip()

    try:
        cart_no = int(cart_raw)
    except (TypeError, ValueError):
        cart_no = str(cart_raw).strip()

    obj = (
        Load.objects
        .select_related("cart")
        .filter(
            _available_q(),
            product_kind=kind,
            product_code=code,
            cart__number=cart_no,
        )
        .order_by("-id")
        .first()
    )

    weight = obj.total_weight_kg if obj else None
    tank_no = _extract_tank_from_objects(obj, obj.cart if obj else None)

    # Jeśli brak w Load/Cart, spróbuj wydedukować z ostatnich ładunków tego wózka
    if tank_no is None:
        last_with_tank = (
            Load.objects
            .select_related("cart")
            .filter(cart__number=cart_no)
            .order_by("-id")
            .first()
        )
        if last_with_tank:
            tank_no = _extract_tank_from_objects(
                last_with_tank, last_with_tank.cart)

    load_id = obj.pk if obj else None
    edit_url = reverse("edit_load", args=[load_id]) if load_id else None

    return JsonResponse({
        "cart": cart_no,
        "total_weight_kg": weight,
        "tank_no": tank_no,
        "load_id": load_id,
        "edit_url": edit_url,
    })


@require_GET
def api_cart_check(request):
    """
    Sprawdza czy wózek istnieje i czy jest zajęty aktywnym ładunkiem.
    GET: /api/magazynek/cart_check/?number=42
    Odp: { "exists": bool, "occupied": bool, "label": "Wózek 42" }
    """
    number_raw = (request.GET.get("number") or "").strip()
    if not number_raw:
        return JsonResponse({"exists": False, "occupied": False, "label": ""})

    # numer może być tekstowy, nie wymuszamy int
    cart = Cart.objects.filter(number=number_raw).first()
    if not cart:
        return JsonResponse({"exists": False, "occupied": False, "label": f"Wózek {number_raw}"})

    # zajętość = ma aktywny ładunek wg _available_q()
    occupied = Load.objects.filter(cart=cart).filter(_available_q()).exists()
    return JsonResponse({"exists": True, "occupied": occupied, "label": f"Wózek {cart.number}"})
