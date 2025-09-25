# core/plan_produkcji.py
from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Tuple
from datetime import date, timedelta

from django.db.models import Q, Sum
from django.shortcuts import render, redirect
from django.utils import timezone
from django.contrib.auth import get_user

from .models import Load, ProductionPlan

# --- KONFIGURACJA ---
KINDS: List[str] = ["Naturalny", "Ziołowy", "Pomidorowy"]
MAX_DAYS = 7
SER_KG = Decimal("0.15")  # 1 szt. = X kg
PLAN_SLUG = "default"     # jeden globalny plan; zmień jeśli potrzebujesz wielu


def _available_q() -> Q:
    base = Q(cart__isnull=False)
    try:
        Load._meta.get_field("taken_at")
        return base & Q(taken_at__isnull=True)
    except Exception:
        pass
    try:
        Load._meta.get_field("status")
        return base & Q(status="AVAILABLE")
    except Exception:
        pass
    return base


def _available_kg_by_kind() -> Dict[str, Decimal]:
    out = {k: Decimal("0") for k in KINDS}
    qs = (
        Load.objects.filter(_available_q())
        .values("product_kind")
        .annotate(kg=Sum("total_weight_kg"))
    )
    for row in qs:
        kind = row["product_kind"]
        kg = Decimal(row["kg"] or 0)
        if kind in out:
            out[kind] += kg
    return out


def _parse_date(s: str, fallback: date) -> date:
    try:
        y, m, d = (int(x) for x in s.split("-"))
        return date(y, m, d)
    except Exception:
        return fallback


def _load_plan_from_db() -> tuple[int, Dict[int, str], Dict[int, Dict[str, int]]]:
    """
    Zwraca (days_count, dates, pcs) z bazy.
    dates: {idx:int -> 'YYYY-MM-DD'}
    pcs:   {idx:int -> {kind:str -> szt:int}}
    """
    try:
        plan = ProductionPlan.objects.get(slug=PLAN_SLUG)
    except ProductionPlan.DoesNotExist:
        return 1, {1: timezone.localdate().isoformat()}, {1: {k: 0 for k in KINDS}}

    # klucze w JSON mogą być stringami — zmapuj na inty
    raw_dates = plan.dates or {}
    raw_pcs = plan.pcs or {}
    dates = {int(i): str(v) for i, v in raw_dates.items()}
    pcs: Dict[int, Dict[str, int]] = {}
    for i_str, row in raw_pcs.items():
        i = int(i_str)
        pcs[i] = {}
        for k in KINDS:
            try:
                pcs[i][k] = int(row.get(k, 0))
            except Exception:
                pcs[i][k] = 0
    return int(plan.days_count or 1), dates, pcs


def _save_plan_to_db(days_count: int, dates: Dict[int, str], pcs: Dict[int, Dict[str, int]], user_label: str = "") -> None:
    # zapisujemy JSON z kluczami jako stringi (czytelny i stabilny)
    dates_json = {str(i): dates[i] for i in dates}
    pcs_json = {str(i): {k: int(pcs[i].get(k, 0)) for k in KINDS} for i in pcs}
    ProductionPlan.objects.update_or_create(
        slug=PLAN_SLUG,
        defaults={
            "days_count": days_count,
            "dates": dates_json,
            "pcs": pcs_json,
            "updated_by": user_label[:64] if user_label else "",
        },
    )


def plan_produkcji(request):
    today = timezone.localdate()

    # ---- GET: start z bazy; POST: przyjmij formularz i zapisz do bazy ----
    if request.method == "POST":
        # a) liczba dni
        try:
            days_count = max(
                1, min(int(request.POST.get("days", "1")), MAX_DAYS))
        except ValueError:
            days_count = 1

        # b) daty i sztuki
        day_labels: List[Tuple[int, date]] = []
        last = today
        for i in range(1, days_count + 1):
            di = (request.POST.get(f"date_{i}") or "").strip()
            if di:
                d = _parse_date(di, last + timedelta(days=1))
            else:
                d = last + timedelta(days=1) if i > 1 else today
            if day_labels and d <= day_labels[-1][1]:
                d = day_labels[-1][1] + timedelta(days=1)
            day_labels.append((i, d))
            last = d

        dates: Dict[int, str] = {i: d.isoformat() for i, d in day_labels}

        pcs: Dict[int, Dict[str, int]] = {
            i: {k: 0 for k in KINDS} for i, _ in day_labels}
        for i, _ in day_labels:
            for k in KINDS:
                raw = (request.POST.get(f"d{i}_{k}") or "").replace(" ", "")
                try:
                    pcs[i][k] = max(int(raw), 0)
                except ValueError:
                    pcs[i][k] = 0

        # c) zapis do bazy i PRG
        user = get_user(request)
        who = ""
        if getattr(user, "is_authenticated", False):
            who = user.get_username() or (getattr(user, "email", "") or "")
        _save_plan_to_db(days_count, dates, pcs, user_label=who)
        return redirect("plan_produkcji")

    # ---- GET: odczyt z bazy i render ----
    days_count_db, dates_db, pcs_db = _load_plan_from_db()
    days_count = max(1, min(days_count_db, MAX_DAYS))

    # ułóż rosnąco po indeksie (1..N)
    indices = sorted([i for i in dates_db.keys() if 1 <= i <= days_count])
    if not indices:
        indices = [1]
        dates_db = {1: today.isoformat()}
        pcs_db = {1: {k: 0 for k in KINDS}}

    # Wczytaj zapotrzebowanie (szt → kg)
    demand_pcs = {i: {k: int(pcs_db.get(i, {}).get(k, 0))
                      for k in KINDS} for i in indices}
    demand_kg = {
        i: {k: Decimal(demand_pcs[i][k]) * SER_KG for k in KINDS} for i in indices}
    total_demand_kg = {k: sum(demand_kg[i][k] for i in indices) for k in KINDS}
    grand_total_kg = sum(total_demand_kg.values())

    # Dostępne na magazynku (z modeli)
    available_by_kind = _available_kg_by_kind()
    available_total_kg = sum(available_by_kind.values())

    # Do wyprodukowania (globalnie)
    to_make_by_kind = {
        k: max(
            total_demand_kg[k] - available_by_kind.get(k, Decimal("0")), Decimal("0"))
        for k in KINDS
    }
    to_make_total_kg = sum(to_make_by_kind.values())

    # Bilans dzienny (kumulacja braków)
    remaining = {k: available_by_kind.get(k, Decimal("0")) for k in KINDS}
    cumulative_missing_kg = {k: Decimal("0") for k in KINDS}
    daily_missing_kg = {i: {k: Decimal("0") for k in KINDS} for i in indices}

    for i in indices:
        for k in KINDS:
            need = demand_kg[i][k]
            used = min(need, remaining[k])
            shortage = need - used
            remaining[k] -= used
            cumulative_missing_kg[k] += shortage
            daily_missing_kg[i][k] = cumulative_missing_kg[k]

    # Wiersze do tabeli
    rows = []
    for i in indices:
        d_iso = dates_db.get(i) or today.isoformat()
        # na potrzeby <input type="date"> przekaż date/datetime – szablon używa |date:"Y-m-d"
        y, m, d = (int(x) for x in d_iso.split("-"))
        disp_date = date(y, m, d)
        items = []
        for k in KINDS:
            items.append({
                "kind": k,
                "pcs": demand_pcs[i][k],
                "missing_kg": daily_missing_kg[i][k],
            })
        rows.append({"idx": i, "date": disp_date, "items": items})

    sum_kind_list = [{"kind": k, "sum_kg": total_demand_kg[k]} for k in KINDS]
    available_kind_list = [
        {"kind": k, "kg": available_by_kind.get(k, Decimal("0"))} for k in KINDS]
    to_make_kind_list = [{"kind": k, "kg": to_make_by_kind[k]} for k in KINDS]

    add_row_colspan = 1 + 2 * len(KINDS)

    ctx = {
        "title": "Produkcja batonów",
        "KINDS": KINDS,
        "SER_KG": SER_KG,
        "rows": rows,
        "days_count": len(indices),
        "sum_kind_list": sum_kind_list,
        "grand_total_kg": grand_total_kg,
        "available_kind_list": available_kind_list,
        "available_total_kg": available_total_kg,
        "to_make_kind_list": to_make_kind_list,
        "to_make_total_kg": to_make_total_kg,
        "now": timezone.now(),
        "add_row_colspan": add_row_colspan,
    }
    return render(request, "core/plan.html", ctx)
