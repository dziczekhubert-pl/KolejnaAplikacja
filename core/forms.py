from decimal import Decimal, ROUND_HALF_UP

from django import forms
from django.core.exceptions import ValidationError
from django.forms import formset_factory
from django.utils import timezone

from .models import Load


# --- pomocnicze: zaokrąglenie do 0.5 kg i ucięcie do 1 miejsca po przecinku ---
def round_to_half_kg(value: Decimal) -> Decimal:
    """
    Zaokrąglanie do najbliższej połówki kilograma:
      12.24 -> 12.0
      12.26 -> 12.5
      12.74 -> 12.5
      12.76 -> 13.0
    Zwraca Decimal z 1 miejscem po przecinku (np. 12.5).
    """
    if value is None:
        return value
    half_steps = (value * 2).to_integral_value(rounding=ROUND_HALF_UP)
    rounded = Decimal(half_steps) / Decimal(2)
    return rounded.quantize(Decimal("0.1"))


# >>> Partia – wspólne pola dla wszystkich wózków w partii:
class BatchCommonForm(forms.Form):
    packing_date = forms.DateField(
        label="Data pakowania",
        widget=forms.DateInput(attrs={"type": "date"}),
        initial=timezone.localdate,  # callable -> dzisiejsza data
    )
    production_shift = forms.ChoiceField(
        label="Zmiana",
        choices=Load.Shift.choices,
        initial=Load.Shift.I,
    )
    product_kind = forms.ChoiceField(
        label="Rodzaj batona",
        choices=Load.Kind.choices,
        initial=Load.Kind.NATURALNY,
    )
    product_code = forms.IntegerField(
        label="Kod",
        min_value=1,
        max_value=365,
        widget=forms.NumberInput(),
        initial=1,
    )
    handled_by = forms.CharField(
        label="Wprowadził",
        max_length=100,
        required=False,
        widget=forms.TextInput(),
    )


class BatchRowForm(forms.Form):
    cart_number = forms.CharField(
        label="Numer wózka",
        max_length=20,
        widget=forms.TextInput(attrs={
            # hooki pod JS autouzupelniania tary po numerze
            "autocomplete": "off",
            "data-autofill": "cart-tare",  # np. nasłuch w JS
        })
    )

    total_weight_kg = forms.DecimalField(
        label="Masa [kg]",
        min_value=Decimal("0.0"),
        max_value=Decimal("800.0"),
        decimal_places=1,  # spójnie z modelem
        widget=forms.NumberInput(
            attrs={"step": "0.5", "min": "0",
                   "max": "800", "inputmode": "decimal"}
        ),
    )

    # NOWE: Tara wózka (opcjonalna), będzie podpowiadana po wpisaniu numeru wózka
    tare_kg = forms.DecimalField(
        label="Tara [kg]",
        required=False,
        min_value=Decimal("0.0"),
        max_value=Decimal("800.0"),
        decimal_places=1,
        widget=forms.NumberInput(
            attrs={"step": "0.5", "min": "0",
                   "max": "800", "inputmode": "decimal"}
        ),
        help_text="Jeśli podasz, zapisze się na wózku (co 0,5 kg).",
    )

    tank = forms.CharField(label="Tank", max_length=50, required=False)

    def clean_total_weight_kg(self):
        val = self.cleaned_data.get("total_weight_kg")
        if val is None:
            return val
        rounded = round_to_half_kg(Decimal(val))
        if rounded > Decimal("800.0"):
            raise forms.ValidationError(
                "Masa wózka nie może przekraczać 800 kg.")
        return rounded

    def clean_tare_kg(self):
        val = self.cleaned_data.get("tare_kg")
        if val in (None, ""):
            return None
        rounded = round_to_half_kg(Decimal(val))
        if rounded < Decimal("0.0") or rounded > Decimal("800.0"):
            raise forms.ValidationError("Tara musi być w zakresie 0–800 kg.")
        return rounded


class TunnelEntryForm(forms.Form):
    """
    Formularz jednego wiersza tabeli tunelu.
    choices przekazujemy dynamicznie w __init__.
    UWAGA: Pola product_kind i product_code są celowo optional,
    żeby pusty pierwszy wiersz nie blokował zapisu.
    """
    production_date = forms.DateField(
        label="Data produkcji",
        widget=forms.DateInput(attrs={"type": "date"}),
        initial=timezone.localdate,  # dzisiejsza data
    )

    # ↓↓↓ KLUCZOWA ZMIANA: required=False
    product_kind = forms.ChoiceField(
        label="Rodzaj batona", choices=(), required=False)
    product_code = forms.ChoiceField(label="Kod", choices=(), required=False)

    # NIEEDYTOWALNE w UI (readonly) — ale wysyła się w POST
    bar_production_date = forms.DateField(
        label="Data produkcji batona",
        required=False,
        widget=forms.DateInput(attrs={
            "type": "date",
            "readonly": "readonly",
            "tabindex": "-1",
            "aria-readonly": "true",
            # stylistyka „wyłączonego” pola pozostawiamy frontowi (CSS)
        }),
    )

    cooling_time_min = forms.IntegerField(
        label="Czas chłodzenia [min.]",
        min_value=0,
        required=False,
        widget=forms.NumberInput(attrs={"step": 1}),
    )
    temp_tunnel = forms.DecimalField(
        label="Temperatura w tunelu chłodniczym [-24/-27°C]",
        required=False,
        widget=forms.NumberInput(attrs={"step": "0.1"}),
    )
    temp_inlet = forms.DecimalField(
        label="Temperatura batona na wejściu do tunelu [+1/+3°C]",
        required=False,
        widget=forms.NumberInput(attrs={"step": "0.1"}),
    )
    temp_shell_out = forms.DecimalField(
        label="Temperatura otoczki batona po wyjściu z tunelu [-3.5/-5°C]",
        required=False,
        widget=forms.NumberInput(attrs={"step": "0.1"}),
    )
    temp_core_out = forms.DecimalField(
        label="Temperatura środka batona po wyjściu z tunelu [+1/-1°C]",
        required=False,
        widget=forms.NumberInput(attrs={"step": "0.1"}),
    )

    def __init__(self, *args, kind_choices=(), code_choices=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product_kind"].choices = tuple(kind_choices)
        self.fields["product_code"].choices = tuple(code_choices)

    # --- pomocnicze: najświeższy ładunek na magazynku dla (kind, code) ---
    def _get_latest_load_in_cold_store(self, kind, code):
        """
        Zwraca najświeższy Load „na magazynku” dla danego rodzaju i kodu.
        Jeśli w projekcie stan magazynku oznacza się inaczej, zmień filtr(y) poniżej.
        """
        qs = Load.objects.filter(product_kind=kind, product_code=code)
        try:
            qs = qs.filter(cart__isnull=False)
        except Exception:
            pass
        # Jeżeli masz pole taken_at / taken_to_production_at -> tu można zawęzić:
        try:
            qs = qs.filter(taken_at__isnull=True)
        except Exception:
            pass

        return qs.order_by("-packing_date", "-id").first()

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("product_kind")
        code = cleaned.get("product_code")

        # Jeśli pierwszy wiersz jest pusty – nic nie wymuszamy, po prostu wyjdź.
        if not kind or not code:
            cleaned["bar_production_date"] = None
            return cleaned

        # ChoiceField zwykle daje string — spróbujmy rzutować
        code_cast = code
        try:
            code_cast = int(code)
        except (TypeError, ValueError):
            pass

        latest = self._get_latest_load_in_cold_store(kind, code_cast)
        # NIE rzucamy błędu, jeśli brak – UI i API pilnują listy kodów,
        # a w backendzie zapis dnia i tak parsujemy ręcznie.
        cleaned["bar_production_date"] = getattr(latest, "packing_date", None)
        return cleaned


# Zaczynamy bez żadnych wierszy – pierwszy dodasz przyciskiem „Dodaj wózek”
BatchRowFormSet = formset_factory(BatchRowForm, extra=0, can_delete=True)
