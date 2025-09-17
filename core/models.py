from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import F, Q
from django.utils import timezone


# --- Walidator: tylko połówki kilograma (0.0, 0.5, 1.0, 1.5, ...) ---
def validate_half_kg(value):
    """
    Masa musi być wielokrotnością 0,5 kg — walidacja na poziomie aplikacji.
    (Dodatkowo są limity min/max przez walidatory polowe.)
    """
    d = Decimal(value)
    if (d * 2) % 1 != 0:
        raise ValidationError(
            "Masa musi być wielokrotnością 0,5 kg (np. 123.0 lub 123.5)."
        )


# --- Dziennik zdarzeń (do eksportu/rotacji do JSONL) ---
class EventLog(models.Model):
    ts = models.DateTimeField(auto_now_add=True, db_index=True)
    model = models.CharField(max_length=32)
    action = models.CharField(max_length=16)
    ref_id = models.IntegerField(db_index=True)
    payload = models.JSONField()

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["model", "action"]),
            models.Index(fields=["ts"]),
            models.Index(fields=["ref_id"]),
        ]

    def __str__(self):
        return f"[{self.ts:%Y-%m-%d %H:%M:%S}] {self.model}#{self.ref_id} {self.action}"


class Cart(models.Model):
    number = models.CharField("Numer wózka", max_length=20, unique=True)
    capacity_kg = models.DecimalField(
        "Pojemność [kg]", max_digits=7, decimal_places=2, default=Decimal("430.00")
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["number"]

    def __str__(self):
        return f"Wózek {self.number}"

    @property
    def active_load(self):
        return (
            self.loads.filter(status=Load.Status.IN_COLD_ROOM)
            .order_by("-produced_at", "-created_at")
            .first()
        )

    @property
    def is_free(self):
        return self.active_load is None


# --- QuerySet ułatwiający typowe zapytania i filtracje ---
class LoadQuerySet(models.QuerySet):
    def in_cold_room(self):
        return self.filter(status=Load.Status.IN_COLD_ROOM)

    def taken(self):
        return self.filter(status=Load.Status.TAKEN_TO_PRODUCTION)

    def active_for_cart(self, cart):
        return self.in_cold_room().filter(cart=cart)

    def fifo_for_cart(self, cart):
        return self.active_for_cart(cart).order_by("created_at")


class Load(models.Model):
    class Status(models.TextChoices):
        IN_COLD_ROOM = "IN_COLD_ROOM", "W magazynku"
        TAKEN_TO_PRODUCTION = "TAKEN_TO_PRODUCTION", "Zdjęty do produkcji"

    class Shift(models.TextChoices):
        I = "I", "I"
        II = "II", "II"
        III = "III", "III"

    class Kind(models.TextChoices):
        NATURALNY = "Naturalny", "Naturalny"
        ZIOLOWY = "Ziołowy", "Ziołowy"
        POMIDOROWY = "Pomidorowy", "Pomidorowy"

    # Wymagane przy dodawaniu
    packing_date = models.DateField(
        "Data pakowania", default=timezone.localdate)
    production_shift = models.CharField(
        "Zmiana", max_length=4, choices=Shift.choices, default=Shift.I
    )
    product_kind = models.CharField(
        "Rodzaj batonu", max_length=20, choices=Kind.choices, default=Kind.NATURALNY
    )
    product_code = models.PositiveSmallIntegerField(
        "Kod", validators=[MinValueValidator(1), MaxValueValidator(365)], default=1
    )

    # Informacje dodatkowe/etykieta
    handled_by = models.CharField("Wprowadził", max_length=100, blank=True)
    flavor = models.CharField("Smak", max_length=50, blank=True)
    tank = models.CharField("Tank", max_length=50, blank=True)

    # Relacja do wózka
    cart = models.ForeignKey(Cart, related_name="loads",
                             on_delete=models.PROTECT)

    # Sztuki
    pieces = models.PositiveIntegerField(
        "Sztuk na wózku",
        default=66,
        validators=[MinValueValidator(1), MaxValueValidator(66)],
    )

    # Masa aktualna
    total_weight_kg = models.DecimalField(
        "Masa [kg]",
        max_digits=4,
        decimal_places=1,
        validators=[
            MinValueValidator(
                Decimal("0.0"), message="Masa nie może być ujemna."),
            MaxValueValidator(
                Decimal("500.0"), message="Masa wózka nie może przekraczać 500 kg."
            ),
            validate_half_kg,
        ],
        help_text="Podaj masę w kilogramach w skokach co 0,5 kg (np. 123.0, 123.5).",
    )

    # Masa początkowa (ustalana automatycznie przy pierwszym zapisie)
    initial_weight_kg = models.DecimalField(
        "Masa początkowa [kg]",
        max_digits=4,
        decimal_places=1,
        validators=[
            MinValueValidator(Decimal("0.0")),
            MaxValueValidator(Decimal("500.0")),
            validate_half_kg,
        ],
        null=True,
        blank=True,
        editable=False,
    )

    # Czas/status
    produced_at = models.DateTimeField(
        "Czas wyprodukowania", default=timezone.now)
    status = models.CharField(
        max_length=32, choices=Status.choices, default=Status.IN_COLD_ROOM
    )
    taken_at = models.DateTimeField("Czas zdjęcia", null=True, blank=True)

    # Edycje masy
    edited_by = models.CharField("Edytował", max_length=100, blank=True)
    edited_at = models.DateTimeField("Czas edycji", null=True, blank=True)

    # Audyt / wersjonowanie
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    version = models.PositiveIntegerField(
        default=0, help_text="Wewnętrzna wersja rekordu do CAS."
    )

    # Manager/QuerySet
    objects = LoadQuerySet.as_manager()

    class Meta:
        ordering = ["-produced_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["produced_at"]),
            models.Index(fields=["cart", "status"]),
            models.Index(fields=["status", "produced_at"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["cart"],
                condition=Q(status="IN_COLD_ROOM"),
                name="unique_active_load_per_cart",
            ),
            models.CheckConstraint(
                check=Q(pieces__gte=1) & Q(pieces__lte=66),
                name="pieces_in_1_66",
            ),
            models.CheckConstraint(
                check=Q(product_code__gte=1) & Q(product_code__lte=365),
                name="product_code_1_365",
            ),
        ]

    def __str__(self):
        return f"{self.product_kind} {self.product_code} @ Wózek {self.cart.number}"

    @property
    def is_active(self):
        return self.status == Load.Status.IN_COLD_ROOM

    def save(self, *args, **kwargs):
        """
        Przy pierwszym zapisie ustal `initial_weight_kg` = `total_weight_kg`
        (tylko jeśli dotąd było puste). Późniejsze edycje NIE zmieniają wartości początkowej.
        """
        if self.initial_weight_kg is None and self.total_weight_kg is not None:
            self.initial_weight_kg = self.total_weight_kg
        super().save(*args, **kwargs)

    # ---------------------
    # Operacje na statusie
    # ---------------------
    @transaction.atomic
    def mark_taken(self):
        """
        Oznacz ładunek jako zdjęty do produkcji (CAS przez pole `version`).
        Metoda idempotentna — jeśli już zdjęty, zwraca self.
        """
        if self.status == Load.Status.TAKEN_TO_PRODUCTION:
            return self

        affected = (
            Load.objects.filter(
                pk=self.pk, status=Load.Status.IN_COLD_ROOM, version=self.version
            ).update(
                status=Load.Status.TAKEN_TO_PRODUCTION,
                taken_at=timezone.now(),
                version=F("version") + 1,
                updated_at=timezone.now(),
            )
        )
        if affected != 1:
            self.refresh_from_db()
            return self

        self.refresh_from_db()
        return self
