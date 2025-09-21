# core/tunel.py
from __future__ import annotations

from urllib.parse import urlencode
import re
import json
from typing import Optional, Iterable, List, Dict, Any

from django import forms
from django.db import models, transaction
from django.shortcuts import render, redirect
from django.contrib import messages
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.urls import reverse

from .models import Load, TunnelDay, TunnelRow
from .forms import TunnelEntryForm  # do renderu GET


# ------------------- mini-form do walidacji wyłącznie daty -------------------
class _DateForm(forms.Form):
    production_date = forms.DateField(input_formats=["%Y-%m-%d"])


# ------------------- istniejące helpery (bez zmian koncepcyjnych) -------------------
def _available_q() -> Q:
    """
    Zwraca Q oznaczające 'aktywny na magazynku' dla modelu Load.
    Preferuj status, ale zachowaj kompatybilność z ewentualnymi polami 'taken_*'.
    """
    base = Q(cart__isnull=False)
    for field_name in ("taken_to_production_at", "taken_at"):
        try:
            Load._meta.get_field(field_name)
            return base & Q(**{f"{field_name}__isnull": True})
        except Exception:
            pass
    try:
        Load._meta.get_field("status")
        return base & ~Q(status=getattr(Load.Status, "TAKEN_TO_PRODUCTION", "TAKEN_TO_PRODUCTION"))
    except Exception:
        return base


def _code_not_empty_q() -> Q:
    field = Load._meta.get_field("product_code")
    if isinstance(field, (models.CharField, models.TextField)):
        return ~Q(product_code__isnull=True) & ~Q(product_code="")
    return ~Q(product_code__isnull=True)


def _normalize_kind(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    field = Load._meta.get_field("product_kind")
    choices = list(field.choices or [])
    if not choices:
        return raw
    value_to_label = {str(v): str(l) for v, l in choices}
    label_to_value = {str(l).lower(): str(v) for v, l in choices}
    raw_s = str(raw)
    if raw_s in value_to_label:
        return raw_s
    return label_to_value.get(raw_s.lower())


def _get_codes_for_kind(selected_kind: Optional[str]):
    if not selected_kind:
        return []
    qs = (
        Load.objects
        .filter(_available_q(), product_kind=selected_kind)
        .filter(_code_not_empty_q())
        .values_list("product_code", flat=True)
        .distinct()
        .order_by("product_code")
    )
    return [(str(c), str(c)) for c in qs]


def _extract_digits(val) -> Optional[str]:
    if val is None:
        return None
    s = str(val).strip()
    if not s:
        return None
    m = re.search(r"(\d+)", s)
    return m.group(1) if m else None


def _get_attr(obj, names: list[str]):
    for n in names:
        if hasattr(obj, n):
            return getattr(obj, n)
    return None


def _extract_tank_from_objects(load: Optional[Load], cart) -> Optional[str]:
    if load is not None:
        cand = _get_attr(
            load, ["tank_no", "tank_number", "tankNr", "tanknr", "tank"])
        dn = _extract_digits(cand)
        if dn:
            return dn
        for n in ["label", "name", "title", "description", "desc"]:
            cand = _get_attr(load, [n])
            dn = _extract_digits(cand)
            if dn:
                return dn
    if cart is not None:
        cand = _get_attr(
            cart, ["tank_no", "tank_number", "tankNr", "tanknr", "tank"])
        dn = _extract_digits(cand)
        if dn:
            return dn
        if hasattr(cart, "tank") and cart.tank is not None:
            cand = _get_attr(cart.tank, ["number", "nr", "no", "id", "name"])
            dn = _extract_digits(cand)
            if dn:
                return dn
    return None


# ----------------------- POMOC: parsowanie POST -----------------------
def _clean_str(v) -> str:
    return (v or "").strip()


def _to_int(v) -> Optional[int]:
    try:
        return int(str(v).strip())
    except Exception:
        return None


def _to_dec1(v) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return round(float(str(v).replace(",", ".").strip()), 1)
    except Exception:
        return None


def _split_carts_csv(s: str) -> list[str]:
    s = (s or "").strip()
    if not s:
        return []
    return [p.strip() for p in s.split(",") if p.strip()]


# ----------------------- NOWE: waga z uwzględnieniem snapshotu -----------------------
def _weight_with_snapshot(load: Optional[Load]) -> Optional[float]:
    """
    Zwraca wagę do wyświetlenia/sumowania:
    - jeżeli ładunek jest zdjęty i ma snapshot, użyj snapshotu,
    - w przeciwnym razie użyj bieżącej total_weight_kg.
    """
    if not load:
        return None
    try:
        status_val = getattr(load, "status", None)
        snapshot = getattr(load, "cart_weight_snapshot", None)
        if status_val == getattr(Load.Status, "TAKEN_TO_PRODUCTION", "TAKEN_TO_PRODUCTION") and snapshot is not None:
            return float(snapshot)
    except Exception:
        pass
    try:
        return float(load.total_weight_kg) if load.total_weight_kg is not None else None
    except Exception:
        return None


def _sum_weights_for(kind: str, code: str | int, cart_nos: Iterable[str]) -> float:
    """
    Suma kg dla wskazanych wózków na podstawie *ostatniego* Load
    (dla każdego numeru), z preferencją na snapshot jeśli zdjęty.
    """
    if not cart_nos:
        return 0.0

    try:
        code_val = int(code)
    except Exception:
        code_val = code

    total = 0.0
    for no in cart_nos:
        obj = (
            Load.objects
            .select_related("cart")
            .filter(product_kind=kind, product_code=code_val, cart__number=str(no))
            .order_by("-id")
            .first()
        )
        w = _weight_with_snapshot(obj)
        if w is not None:
            total += w

    return round(total, 1)


def _collect_rows_from_post(request) -> List[Dict[str, Any]]:
    """
    Zbiera wiersz serwerowy + wszystkie wiersze klienckie ([]).
    Zwraca listę słowników gotowych do utworzenia TunnelRow.
    """
    rows: List[Dict[str, Any]] = []

    # --- 1) wiersz serwerowy
    pkind = _clean_str(request.POST.get("product_kind"))
    pcode = _clean_str(request.POST.get("product_code"))
    if pkind and pcode:
        taken_csv = _clean_str(request.POST.get("taken_carts"))
        carts = _split_carts_csv(taken_csv)
        rows.append({
            "product_kind": pkind,
            "product_code": _to_int(pcode) or pcode,
            "bar_production_date": request.POST.get("bar_production_date") or None,
            "cooling_time_min": _to_int(request.POST.get("cooling_time_min")),
            "temp_tunnel": _to_dec1(request.POST.get("temp_tunnel")),
            "temp_inlet": _to_dec1(request.POST.get("temp_inlet")),
            "temp_shell_out": _to_dec1(request.POST.get("temp_shell_out")),
            "temp_core_out": _to_dec1(request.POST.get("temp_core_out")),
            "taken_carts_csv": taken_csv,
            "sum_taken_kg": _sum_weights_for(pkind, pcode, carts),
        })

    # --- 2) wiersze dynamiczne ([] pola)
    kinds = request.POST.getlist("product_kind[]")
    codes = request.POST.getlist("product_code[]")
    dates = request.POST.getlist("bar_production_date[]")
    times = request.POST.getlist("cooling_time_min[]")
    t_tunnel = request.POST.getlist("temp_tunnel[]")
    t_in = request.POST.getlist("temp_inlet[]")
    t_shell = request.POST.getlist("temp_shell_out[]")
    t_core = request.POST.getlist("temp_core_out[]")
    taken = request.POST.getlist("taken_carts[]")

    n = max(len(kinds), len(codes), len(dates), len(times), len(
        t_tunnel), len(t_in), len(t_shell), len(t_core), len(taken))
    for i in range(n):
        pkind = _clean_str(kinds[i] if i < len(kinds) else "")
        pcode = _clean_str(codes[i] if i < len(codes) else "")
        if not (pkind and pcode):
            continue
        taken_csv = _clean_str(taken[i] if i < len(taken) else "")
        carts = _split_carts_csv(taken_csv)
        rows.append({
            "product_kind": pkind,
            "product_code": _to_int(pcode) or pcode,
            "bar_production_date": (dates[i] if i < len(dates) else None) or None,
            "cooling_time_min": _to_int(times[i] if i < len(times) else None),
            "temp_tunnel": _to_dec1(t_tunnel[i] if i < len(t_tunnel) else None),
            "temp_inlet": _to_dec1(t_in[i] if i < len(t_in) else None),
            "temp_shell_out": _to_dec1(t_shell[i] if i < len(t_shell) else None),
            "temp_core_out": _to_dec1(t_core[i] if i < len(t_core) else None),
            "taken_carts_csv": taken_csv,
            "sum_taken_kg": _sum_weights_for(pkind, pcode, carts),
        })

    # nadaj kolejność (tak jak przyszło)
    for idx, r in enumerate(rows):
        r["order_no"] = idx

    return rows


# ---------------------------- WIDOK TUNELU ----------------------------
def tunel_view(request):
    """
    Ekran „Tunel”: wybór i zapis danych dla wybranej daty.
    """
    # Choices „Rodzaj batona”
    kind_field = Load._meta.get_field("product_kind")
    kind_choices = list(kind_field.choices or []) or [
        ("NAT", "Naturalny"),
        ("ZIO", "Ziołowy"),
        ("POM", "Pomidorowy"),
    ]

    # filtr rodzaju (do ograniczenia kodów w pierwszym – serwerowym – wierszu)
    selected_kind_raw = request.GET.get("kind")
    selected_kind = _normalize_kind(selected_kind_raw)
    code_choices = _get_codes_for_kind(selected_kind)

    # utrzymujemy datę z query, by po zapisie wrócić na ten sam dzień
    date_q = request.GET.get("date")

    if request.method == "POST":
        # 1) walidacja daty
        date_form = _DateForm(request.POST)
        if not date_form.is_valid():
            messages.error(request, "Podaj prawidłową datę (górny selektor).")
            prod_date_fallback = request.POST.get(
                "production_date") or timezone.localdate().isoformat()
            return redirect(f"{reverse('tunel')}?date={prod_date_fallback}")

        # Używaj obiektu date (nie stringa) przy pracy z modelem
        prod_date_obj = date_form.cleaned_data["production_date"]
        prod_date_iso = prod_date_obj.isoformat()

        # 2) wszystkie wiersze
        rows = _collect_rows_from_post(request)
        if not rows:
            messages.error(
                request, "Dodaj przynajmniej jeden wiersz z danymi.")
            return redirect(f"{reverse('tunel')}?date={prod_date_iso}")

        # 3) zapis dnia (pełne nadpisanie)
        with transaction.atomic():
            day, _ = TunnelDay.objects.get_or_create(date=prod_date_obj)
            day.rows.all().delete()
            TunnelRow.objects.bulk_create(
                [TunnelRow(day=day, **r) for r in rows], batch_size=100
            )

        messages.success(
            request, f"Zapisano dane tunelu dla dnia {prod_date_iso}.")
        return redirect(f"{reverse('tunel')}?date={prod_date_iso}")

    # ---------------- GET ----------------
    # initial daty: jeżeli ?date=..., użyj go; inaczej dziś
    if date_q:
        initial_date_obj = parse_date(date_q)
    else:
        initial_date_obj = timezone.localdate()

    # fallback, gdy ?date ma zły format
    if initial_date_obj is None:
        initial_date_obj = timezone.localdate()
    initial_date_iso = initial_date_obj.isoformat()

    initial = {"production_date": initial_date_iso}
    if selected_kind:
        initial["product_kind"] = selected_kind

    form = TunnelEntryForm(
        initial=initial,
        kind_choices=kind_choices,
        code_choices=([("", "— wybierz kod —")] + code_choices) if selected_kind else [
            ("", "— najpierw wybierz rodzaj —")],
    )

    # (opcjonalnie) lista wszystkich kodów „na stanie”
    codes_all_qs = (
        Load.objects
        .filter(_available_q())
        .filter(_code_not_empty_q())
        .values_list("product_code", flat=True)
        .distinct()
        .order_by("product_code")
    )
    codes_all_choices = [(str(c), str(c)) for c in codes_all_qs]

    # >>> PREFILL: wczytaj zapisane wiersze dla initial_date
    prefill_rows: List[Dict[str, Any]] = []
    day = TunnelDay.objects.filter(
        date=initial_date_obj).prefetch_related("rows").first()
    if day:
        for r in day.rows.all().order_by("order_no", "id"):
            carts_list = [x for x in _split_carts_csv(r.taken_carts_csv)]
            prefill_rows.append({
                "product_kind": r.product_kind,
                "product_code": str(r.product_code),
                "bar_production_date": r.bar_production_date.isoformat() if r.bar_production_date else None,
                "cooling_time_min": r.cooling_time_min,
                "temp_tunnel": float(r.temp_tunnel) if r.temp_tunnel is not None else None,
                "temp_inlet": float(r.temp_inlet) if r.temp_inlet is not None else None,
                "temp_shell_out": float(r.temp_shell_out) if r.temp_shell_out is not None else None,
                "temp_core_out": float(r.temp_core_out) if r.temp_core_out is not None else None,
                # dwa aliasy na wózki – frontend toleruje oba:
                "taken_carts": carts_list,                 # np. ["12","34"]
                # np. [{"no":"12"},{"no":"34"}]
                "carts": [{"no": no} for no in carts_list],
                "sum_taken_kg": float(r.sum_taken_kg or 0),

            })

    return render(request, "core/tunel.html", {
        "form": form,
        "kind_choices": kind_choices,
        "code_choices": code_choices,
        "selected_kind": selected_kind,
        "codes_all_choices": codes_all_choices,
        # JSON do JS (uwaga: szablon używa |escapejs):
        "prefill_rows_json": json.dumps(prefill_rows, ensure_ascii=False),
    })


# --------------------- API (spójne z frontendem) ---------------------
@require_GET
def magazynek_codes(request):
    """
    GET /api/magazynek/codes/?kind=...
    Zwraca listę dostępnych kodów dla danego rodzaju.
    """
    kind = _normalize_kind(request.GET.get("kind"))
    if not kind:
        return JsonResponse({"codes": [], "kind_normalized": None})
    qs = (
        Load.objects
        .filter(_available_q(), product_kind=kind)
        .filter(_code_not_empty_q())
        .values_list("product_code", flat=True)
        .order_by("product_code")
        .distinct()
    )
    return JsonResponse({"codes": list(qs), "kind_normalized": kind})


@require_GET
def magazynek_lookup(request):
    """
    GET /api/magazynek/lookup/?kind=...&code=...
    Zwraca found + ostatnią datę pakowania.
    """
    kind = _normalize_kind(request.GET.get("kind"))
    code_raw = request.GET.get("code")
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
    GET /api/magazynek/guess_packing_date/?kind=...&code=...
    (pomocnicze)
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
    GET /api/magazynek/carts/?kind=...&code=...
    Zwraca listę numerów wózków (stringi).
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
@require_GET
def api_magazynek_cart_info(request):
    """
    GET /api/magazynek/cart_info/?kind=...&code=...&cart=...
    Zwraca szczegóły wózka w kontekście danego rodzaju+kod:
      - total_weight_kg (z uwzględnieniem snapshotu jeśli zdjęty),
      - tank_no,
      - load_id,
      - edit_url / take_url (tylko gdy wózek jest na magazynku),
      - is_taken (jawny stan: True jeżeli zdjęty).
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
            "take_url": None,
            "is_taken": None,
        })

    try:
        code = int(code_raw)
    except (TypeError, ValueError):
        code = str(code_raw).strip()
    try:
        cart_no = int(cart_raw)
    except (TypeError, ValueError):
        cart_no = str(cart_raw).strip()

    # Bierzemy najnowszy Load dla danego (kind, code, cart)
    obj = (
        Load.objects
        .select_related("cart")
        .filter(product_kind=kind, product_code=code, cart__number=cart_no)
        .order_by("-id")
        .first()
    )

    # Waga (ze snapshotem jeśli zdjęty)
    weight = _weight_with_snapshot(obj)

    # Tank – jak dotąd
    tank_no = _extract_tank_from_objects(obj, obj.cart if obj else None)
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

    # Ustal is_taken w sposób defensywny
    is_taken = False
    if obj:
        if hasattr(obj, "status") and getattr(Load, "Status", None):
            is_taken = (obj.status == Load.Status.TAKEN_TO_PRODUCTION)
        elif hasattr(obj, "taken_at"):
            is_taken = (obj.taken_at is not None)

    # Linki tylko gdy NA MAGAZYNKU (nie zdjęty)
    if obj and not is_taken:
        base = reverse("edit_load", args=[load_id])
        qs = urlencode({"next": reverse("tunel")})
        edit_url = f"{base}?{qs}"
        try:
            take_url = reverse("load_take", args=[load_id])
        except Exception:
            take_url = None
    else:
        edit_url = None
        take_url = None

    return JsonResponse({
        "cart": cart_no,
        "total_weight_kg": weight,
        "tank_no": tank_no,
        "load_id": load_id,
        "edit_url": edit_url,
        "take_url": take_url,
        "is_taken": is_taken,
    })
