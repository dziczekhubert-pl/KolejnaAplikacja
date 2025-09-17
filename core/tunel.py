# core/tunel.py
from django.shortcuts import render, redirect
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q

from .models import Load
from .forms import TunnelEntryForm


# --- helper: wykryj filtr „na magazynku” (nie zdjęte do produkcji) ---
def _available_q() -> Q:
    """
    Zwraca Q, które wybiera ładunki „dostępne”:
      - przypięte do wózka (cart__isnull=False)
      - i nie zdjęte do produkcji (na podstawie istniejących pól w modelu)
    Obsługiwane warianty:
      - taken_to_production_at__isnull=True
      - taken_at__isnull=True
      - status != Load.Status.TAKEN_TO_PRODUCTION
    """
    base = Q(cart__isnull=False)

    # 1) timestamp zdjęcia do produkcji (najczęstsze)
    for field_name in ("taken_to_production_at", "taken_at"):
        try:
            Load._meta.get_field(field_name)
            return base & Q(**{f"{field_name}__isnull": True})
        except Exception:
            pass

    # 2) status enum/choices
    try:
        Load._meta.get_field("status")
        return base & ~Q(status=getattr(Load.Status, "TAKEN_TO_PRODUCTION", "TAKEN_TO_PRODUCTION"))
    except Exception:
        # 3) brak rozpoznawalnego pola — wróć tylko base (lepiej pokazać niż nic)
        return base


AVAILABLE_Q = _available_q()


# --- helper: normalizacja kind do wartości z choices (np. 'NAT', 'ZIO', 'POM') ---
def _normalize_kind(raw: str | None) -> str | None:
    if not raw:
        return None
    field = Load._meta.get_field("product_kind")
    choices = list(field.choices or [])
    if not choices:
        return raw  # brak choices – zwróć jak przyszło

    value_to_label = {str(v): str(l) for v, l in choices}
    label_to_value = {str(l).lower(): str(v) for v, l in choices}

    raw_s = str(raw)
    if raw_s in value_to_label:        # już jest value
        return raw_s
    return label_to_value.get(raw_s.lower())  # może przyszła etykieta


def _get_codes_for_kind(selected_kind: str | None):
    """
    Zwraca listę (value, label) dla rozwijki „Kod” ograniczoną do wybranego rodzaju
    i tylko dla ładunków dostępnych (na wózkach, nie zdjętych).
    """
    if not selected_kind:
        return []

    qs = (
        Load.objects
        .filter(AVAILABLE_Q, product_kind=selected_kind)
        .exclude(product_code__isnull=True)
        .values_list("product_code", flat=True)
        .distinct()
        .order_by("product_code")
    )
    return [(str(c), str(c)) for c in qs]


def tunel_view(request):
    """
    Ekran „Tunel”: wybór rodzaju/kodu z dostępnych ładunków (jeszcze nie zdjętych).
    Ustawiamy dynamiczne choices formularza tak, by „Kod” zależał od „Rodzaju”.
    """
    # Choices „Rodzaj batona” z modelu (fallback jeśli brak)
    kind_field = Load._meta.get_field("product_kind")
    kind_choices = list(kind_field.choices or []) or [
        ("NAT", "Naturalny"),
        ("ZIO", "Ziołowy"),
        ("POM", "Pomidorowy"),
    ]

    # Rodzaj wybrany przez użytkownika (GET ?kind=...), normalizacja do value
    selected_kind_raw = request.GET.get("kind")
    selected_kind = _normalize_kind(selected_kind_raw)

    # „Kod” ograniczamy dopiero po wyborze rodzaju
    code_choices = _get_codes_for_kind(selected_kind)

    if request.method == "POST":
        form = TunnelEntryForm(request.POST)
        # Dopnij dynamiczne choices także w POST (walidacja selectów!)
        if "product_kind" in form.fields:
            form.fields["product_kind"].choices = kind_choices
        if "product_code" in form.fields:
            form.fields["product_code"].choices = code_choices or [
                ("", "— najpierw wybierz rodzaj —")]

        if form.is_valid():
            # TODO: tu Twoja logika zapisu, jeżeli ma coś powstać w DB.
            messages.success(request, "Zapisano wpis do tunelu.")
            return redirect("tunel")
    else:
        # GET – initial ustawia aktualną datę i wstępny wybór rodzaju (jeśli jest w query)
        initial = {
            "production_date": timezone.localdate(),
        }
        if selected_kind:
            initial["product_kind"] = selected_kind

        form = TunnelEntryForm(initial=initial)

        # Wstrzykuj dynamiczne choices do formularza (ważne dla renderu)
        if "product_kind" in form.fields:
            form.fields["product_kind"].choices = kind_choices
        if "product_code" in form.fields:
            if selected_kind:
                form.fields["product_code"].choices = [
                    ("", "— wybierz kod —")] + code_choices
            else:
                form.fields["product_code"].choices = [
                    ("", "— najpierw wybierz rodzaj —")]

    # (opcjonalnie) lista wszystkich dostępnych kodów „na stanie” – jeśli gdzieś potrzebujesz w UI
    codes_all_qs = (
        Load.objects
        .filter(AVAILABLE_Q)
        .exclude(product_code__isnull=True)
        .values_list("product_code", flat=True)
        .distinct()
        .order_by("product_code")
    )
    codes_all_choices = [(str(c), str(c)) for c in codes_all_qs]

    context = {
        "form": form,
        "kind_choices": kind_choices,
        "code_choices": code_choices,
        "selected_kind": selected_kind,
        "codes_all_choices": codes_all_choices,
    }
    return render(request, "core/tunel.html", context)
