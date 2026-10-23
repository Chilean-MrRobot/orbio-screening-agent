"""Esquemas Pydantic de la configuración de negocio en YAML.

Ver `specs/001-configuracion-yaml.md`.
"""

from __future__ import annotations

from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field, model_validator

from screening.domain import ChannelName, Country, Language, ScreeningData, Stage, VehicleType

Operator = Literal[
    "equals",
    "not_equals",
    "greater_than",
    "less_than",
    "greater_or_equal",
    "less_or_equal",
    "in",
    "not_in",
    "exists",
    "not_exists",
    "between",
]

# Funciones genéricas del motor de reglas; la implementación se registra en código.
SpecialCondition = Literal["license_compatible", "city_in_service_area"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def field_path_exists(path: str, model: type[BaseModel] = ScreeningData) -> bool:
    """Valida rutas como `delivery_experience.years` contra el modelo de dominio."""
    head, _, rest = path.partition(".")
    info = model.model_fields.get(head)
    if info is None:
        return False
    if not rest:
        return True
    annotation = info.annotation
    candidates = get_args(annotation) if get_origin(annotation) else (annotation,)
    for candidate in candidates:
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            return field_path_exists(rest, candidate)
    return False


# --- Cliente y LLM ----------------------------------------------------------------


class ClientConfig(StrictModel):
    name: str
    agent_name: str
    recruiter_sla_hours: int = Field(gt=0)


class LLMConfig(StrictModel):
    conversation_model: str
    extraction_model: str
    classifier_model: str
    judge_model: str
    embedding_model: str
    extraction_temperature: float | None = None


# --- Regiones ---------------------------------------------------------------------


class City(StrictModel):
    name: str
    aliases: list[str] = Field(default_factory=list)


class LicenseSpec(StrictModel):
    vehicles: list[VehicleType] = Field(min_length=1)
    min_years: dict[VehicleType, float] = Field(
        default_factory=dict,
        description="Antigüedad mínima de la licencia para un vehículo (p. ej. B → moto: 3).",
    )


class RegionConfig(StrictModel):
    country: Country
    timezone: str
    phone_prefix: str
    form_of_address: Literal["tu", "usted"]
    vocabulary: dict[str, str]
    cities: list[City] = Field(min_length=1)
    licenses: dict[str, LicenseSpec] = Field(min_length=1)


# --- Reglas eliminatorias ---------------------------------------------------------


class Condition(StrictModel):
    """Nodo de condición: exactamente uno de `field`, `all`, `any`, `not` o `special`."""

    field: str | None = None
    operator: Operator | None = None
    value: Any = None
    values: list[Any] | None = None
    min: float | None = None
    max: float | None = None
    all: list[Condition] | None = None
    any: list[Condition] | None = None
    not_: Condition | None = Field(default=None, alias="not")
    special: SpecialCondition | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> Condition:
        kinds = [
            self.field is not None,
            self.all is not None,
            self.any is not None,
            self.not_ is not None,
            self.special is not None,
        ]
        if sum(kinds) != 1:
            raise ValueError(
                "una condición debe tener exactamente uno de: field, all, any, not, special"
            )
        if self.field is not None:
            self._check_field_condition()
        return self

    def _check_field_condition(self) -> None:
        if not field_path_exists(self.field or ""):
            raise ValueError(f"campo desconocido en condición: {self.field!r}")
        if self.operator is None:
            raise ValueError(f"la condición sobre {self.field!r} necesita 'operator'")
        if self.operator in ("in", "not_in") and not self.values:
            raise ValueError(f"el operador {self.operator!r} necesita 'values'")
        if self.operator == "between" and (self.min is None or self.max is None):
            raise ValueError("el operador 'between' necesita 'min' y 'max'")
        needs_value = self.operator not in ("in", "not_in", "between", "exists", "not_exists")
        if needs_value and "value" not in self.model_fields_set:
            raise ValueError(f"el operador {self.operator!r} necesita 'value'")


class KnockoutRule(StrictModel):
    name: str
    description: str = ""
    enabled: bool = True
    stage: Stage
    requires: list[str] = Field(min_length=1, description="Campos necesarios para evaluarla.")
    condition: Condition
    reason_code: str
    message_template: str

    @model_validator(mode="after")
    def _check_requires(self) -> KnockoutRule:
        unknown = [f for f in self.requires if not field_path_exists(f)]
        if unknown:
            raise ValueError(f"la regla {self.name!r} requiere campos desconocidos: {unknown}")
        return self


# --- Puntaje ----------------------------------------------------------------------


class ScoringBand(StrictModel):
    """Tramo [min, max]; gana el primer tramo que calza. `value` es la fracción del peso."""

    min: float | None = None
    max: float | None = None
    value: float = Field(ge=0, le=1)


class ScoringComponent(StrictModel):
    name: str
    weight: float = Field(gt=0)
    type: Literal["bands", "mapping", "boolean", "days_until"]
    field: str
    bands: list[ScoringBand] | None = None
    mapping: dict[str, float] | None = None

    @model_validator(mode="after")
    def _check_type(self) -> ScoringComponent:
        if not field_path_exists(self.field):
            raise ValueError(f"campo desconocido en componente {self.name!r}: {self.field!r}")
        if self.type in ("bands", "days_until") and not self.bands:
            raise ValueError(f"el componente {self.name!r} de tipo {self.type!r} necesita 'bands'")
        if self.type == "mapping":
            if not self.mapping:
                raise ValueError(f"el componente {self.name!r} necesita 'mapping'")
            if any(not 0 <= v <= 1 for v in self.mapping.values()):
                raise ValueError(
                    f"los valores de 'mapping' en {self.name!r} deben estar entre 0 y 1"
                )
        return self


class ScoringConfig(StrictModel):
    max_score: float = Field(gt=0)
    components: list[ScoringComponent] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_weights(self) -> ScoringConfig:
        total = sum(c.weight for c in self.components)
        if abs(total - self.max_score) > 1e-9:
            raise ValueError(f"los pesos suman {total}, deben sumar max_score={self.max_score}")
        return self


# --- Cadencias --------------------------------------------------------------------


class BusinessHours(StrictModel):
    timezone: str
    days: list[int] = Field(min_length=1, description="1=lunes … 7=domingo")
    start_hour: int = Field(ge=0, le=23)
    end_hour: int = Field(ge=1, le=24)

    @model_validator(mode="after")
    def _check_range(self) -> BusinessHours:
        if any(not 1 <= d <= 7 for d in self.days):
            raise ValueError("los días deben estar entre 1 (lunes) y 7 (domingo)")
        if self.start_hour >= self.end_hour:
            raise ValueError("start_hour debe ser menor que end_hour")
        return self


class CadenceStep(StrictModel):
    name: str
    after_hours: float = Field(ge=0, description="Horas desde que empieza el silencio.")
    channel: Literal[ChannelName.WHATSAPP, ChannelName.SMS]
    template: str
    closes: bool = Field(default=False, description="Si es el último toque: pasa a no_response.")


class Cadence(StrictModel):
    name: str
    trigger: Literal["never_responded", "abandoned_mid_screening"]
    steps: list[CadenceStep] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_order(self) -> Cadence:
        hours = [s.after_hours for s in self.steps]
        if hours != sorted(hours):
            raise ValueError(f"los pasos de {self.name!r} deben ir en orden de after_hours")
        return self


class CadencesConfig(StrictModel):
    business_hours: dict[Country, BusinessHours]
    queue_outside_hours: bool = True
    on_candidate_reply: Literal["cancel_pending"] = "cancel_pending"
    on_new_silence: Literal["restart_from_current_stage"] = "restart_from_current_stage"
    cadences: list[Cadence] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_unique_triggers(self) -> CadencesConfig:
        triggers = [c.trigger for c in self.cadences]
        if len(triggers) != len(set(triggers)):
            raise ValueError("solo puede haber una cadencia por trigger")
        return self


# --- Raíz -------------------------------------------------------------------------


class AppConfig(StrictModel):
    client: ClientConfig
    llm: LLMConfig
    regions: dict[Country, RegionConfig]
    knockout_rules: list[KnockoutRule]
    scoring: ScoringConfig
    cadences: CadencesConfig
    templates: dict[str, dict[Language, str]]

    @model_validator(mode="after")
    def _check_references(self) -> AppConfig:
        for country, region in self.regions.items():
            if region.country != country:
                raise ValueError(f"la región {country} declara country={region.country}")

        names = [r.name for r in self.knockout_rules]
        if len(names) != len(set(names)):
            raise ValueError("hay reglas eliminatorias con nombre repetido")

        for key, texts in self.templates.items():
            missing = set(Language) - set(texts)
            if missing:
                raise ValueError(f"a la plantilla {key!r} le faltan idiomas: {sorted(missing)}")

        referenced = {r.message_template for r in self.knockout_rules}
        referenced |= {s.template for c in self.cadences.cadences for s in c.steps}
        unknown = sorted(referenced - set(self.templates))
        if unknown:
            raise ValueError(f"plantillas referenciadas que no existen: {unknown}")
        return self
