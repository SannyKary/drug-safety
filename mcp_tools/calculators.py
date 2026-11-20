"""Чистые формулы. Клиенты вызывают их только через серверы MCP."""

from math import sqrt
from typing import Annotated, Literal

from pydantic import Field
from typing_extensions import TypedDict

Weight = Annotated[float, Field(strict=True, ge=10, le=350, description="Масса, кг")]
Height = Annotated[float, Field(strict=True, ge=70, le=250, description="Рост, см")]
BMI_SOURCE = "https://www.cdc.gov/bmi/adult-calculator/bmi-categories.html"
BSA_SOURCE = "https://pubmed.ncbi.nlm.nih.gov/3657876/"
GCS_SOURCE = "https://www.glasgowcomascale.org/"
EGFR_SOURCE = "https://doi.org/10.1056/NEJMoa2102953"
QT_SOURCE = "https://www.fda.gov/media/71372/download"


class Calculation(TypedDict):
    value: int | float
    unit: str
    category: str
    color: str
    components: dict[str, int | float | str]
    interpretation: str
    source: str


def result(value, unit, category, color, components, interpretation, source):
    return {
        "value": value,
        "unit": unit,
        "category": category,
        "color": color,
        "components": components,
        "interpretation": interpretation,
        "source": source,
    }


def bmi(weight_kg: Weight, height_cm: Height) -> Calculation:
    """Индекс массы тела (ИМТ, BMI). Категории CDC для взрослых от 20 лет."""
    height_m = height_cm / 100
    value = weight_kg / height_m**2
    if value < 18.5:
        category, color = "Недостаточная масса", "orange"
    elif value < 25:
        category, color = "Диапазон здоровой массы", "green"
    elif value < 30:
        category, color = "Избыточная масса", "orange"
    else:
        category, color = "Диапазон ожирения", "red"
    return result(round(value, 2), "кг/м²", category, color, {
        "Масса, кг": weight_kg, "Рост, м": height_m, "Формула": "масса / рост²"
    }, "Скрининговый показатель. При интерпретации учитывают другие данные о здоровье.", BMI_SOURCE)


def bsa(weight_kg: Weight, height_cm: Height) -> Calculation:
    """Площадь поверхности тела (ППТ, BSA) по формуле Мостеллера."""
    value = sqrt(weight_kg * height_cm / 3600)
    return result(round(value, 2), "м²", "Площадь поверхности тела", "gray", {
        "Масса, кг": weight_kg, "Рост, см": height_cm, "Формула": "√(масса × рост / 3600)"
    }, "Расчёт площади. У формулы нет шкалы риска.", BSA_SOURCE)


def gcs(
    eye: Annotated[int, Field(strict=True, ge=1, le=4, description="Открывание глаз, баллы")],
    verbal: Annotated[int, Field(strict=True, ge=1, le=5, description="Речевой ответ, баллы")],
    motor: Annotated[int, Field(strict=True, ge=1, le=6, description="Двигательный ответ, баллы")],
) -> Calculation:
    """Шкала комы Глазго (ШКГ, GCS). Сумма трёх отдельно оценённых ответов."""
    total = eye + verbal + motor
    if total <= 8:
        category, color = "3–8 баллов", "red"
    elif total <= 12:
        category, color = "9–12 баллов", "orange"
    else:
        category, color = "13–15 баллов", "green"
    return result(total, "баллов", category, color, {
        "Глаза (E)": eye, "Речь (V)": verbal, "Движение (M)": motor
    }, "Документируйте каждый ответ отдельно. Сумма применима, если оценены все три ответа.", GCS_SOURCE)


def egfr(
    age_years: Annotated[int, Field(strict=True, ge=18, le=120, description="Возраст, лет")],
    sex: Annotated[Literal["female", "male"], Field(description="Пол для коэффициента уравнения")],
    creatinine_mg_dl: Annotated[float, Field(strict=True, ge=0.05, le=40, description="Креатинин, мг/дл")],
) -> Calculation:
    """Расчётная СКФ (рСКФ, eGFR) по CKD-EPI 2021 для взрослых."""
    kappa, alpha, factor = (0.7, -0.241, 1.012) if sex == "female" else (0.9, -0.302, 1)
    ratio = creatinine_mg_dl / kappa
    value = 142 * min(ratio, 1)**alpha * max(ratio, 1)**-1.2 * 0.9938**age_years * factor
    return result(round(value, 1), "мл/мин/1,73 м²", "Оценка по CKD-EPI 2021", "gray", {
        "Возраст, лет": age_years, "Креатинин, мг/дл": creatinine_mg_dl, "κ": kappa, "α": alpha
    }, "Сопоставляйте оценку с предыдущими измерениями и клиническим контекстом.", EGFR_SOURCE)


def qtc(
    qt_ms: Annotated[float, Field(strict=True, ge=150, le=800, description="Измеренный QT, мс")],
    rr_seconds: Annotated[float, Field(strict=True, ge=0.2, le=3, description="Предшествующий RR, секунды")],
    formula: Literal["Fridericia", "Bazett"] = "Fridericia",
) -> Calculation:
    """Интервал QT с поправкой на частоту (QTc). По умолчанию применяется формула Фридеричиа."""
    exponent = 1 / 3 if formula == "Fridericia" else 1 / 2
    value = qt_ms / rr_seconds**exponent
    return result(round(value, 1), "мс", formula, "gray", {
        "QT, мс": qt_ms, "RR, с": rr_seconds, "Степень RR": round(exponent, 3)
    }, "Результат зависит от качества измерения ЭКГ и метода коррекции.", QT_SOURCE)


STDIO_CALCULATORS = (bmi, bsa, gcs)
NETWORK_CALCULATORS = (*STDIO_CALCULATORS, egfr, qtc)
