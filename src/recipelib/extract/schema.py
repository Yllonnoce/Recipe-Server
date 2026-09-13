"""The structured recipe the extractor produces. Doubles as the JSON schema
handed to Ollama so the model's output is constrained to this shape."""
from __future__ import annotations

from pydantic import BaseModel, Field

from ..domain.categories import NAMES as CATEGORY_NAMES


class IngredientDraft(BaseModel):
    group: str | None = Field(None, description="The section heading this ingredient is listed under, copied as written (e.g. 'For the stock', 'Chowder', 'Topping'); null only when the recipe has a single undivided ingredient list")
    raw: str = Field(..., description="The ingredient line copied exactly as written in the text")
    quantity: float | None = Field(None, description="Amount as a decimal number (1/2 -> 0.5), or null")
    quantity_max: float | None = Field(None, description="Upper amount when a range is given (2-3 -> 3), else null")
    unit: str | None = Field(None, description="Unit exactly as written (cups, tbsp, g, cloves...), or null")
    name: str = Field(..., description="The ingredient itself without amount, unit or preparation")
    preparation: str | None = Field(None, description="How it is prepared: 'chopped', 'melted', else null")
    optional: bool = Field(False, description="True when the text says it is optional")


class StepDraft(BaseModel):
    group: str | None = Field(None, description="The section heading this step belongs to, copied as written (e.g. 'Make the stock', 'Chowder'); null only when the steps are one undivided list")
    text: str = Field(..., description="One instruction step, in the original wording")


class IngredientSection(BaseModel):
    heading: str | None = Field(..., description="The section heading exactly as written in the text ('For the lobster stock', 'Chowder', 'Topping'). null ONLY when the whole recipe has one undivided ingredient list")
    ingredients: list[IngredientDraft] = Field(..., description="The ingredient lines listed under this heading, in order")


class StepSection(BaseModel):
    heading: str | None = Field(..., description="The section heading exactly as written ('Make the stock', 'For the chowder'). null ONLY when the steps are one undivided list")
    steps: list[StepDraft] = Field(..., description="The steps under this heading, in order")


class RecipeDraft(BaseModel):
    is_recipe: bool = Field(..., description="False when the text contains no cooking recipe")
    title: str = Field(..., description="The recipe's name")
    description: str | None = Field(None, description="One or two sentences introducing the dish, else null")
    ingredient_sections: list[IngredientSection] = Field(..., description="One entry per ingredient section, in document order. A recipe with parts (stock + chowder, cake + frosting) has one section per part")
    step_sections: list[StepSection] = Field(..., description="One entry per step section, in document order")
    prep_minutes: int | None = None
    cook_minutes: int | None = None
    total_minutes: int | None = None
    servings: float | None = Field(None, description="Number of servings as a number, else null")
    yield_text: str | None = Field(None, description="Yield as written, e.g. '12 muffins', else null")
    cuisine: str | None = Field(None, description="Cuisine such as italian, mexican, thai, else null")
    course: str | None = Field(None, description="breakfast, lunch, dinner, dessert, snack, side, drink, appetizer, else null")
    categories: list[str] = Field(default_factory=list, description="One or more of: " + ", ".join(CATEGORY_NAMES))
    language: str = Field("en", description="Two-letter language code of the recipe text")
    confidence: float = Field(..., description="0 to 1: how confident you are that the extraction is complete and accurate")


SYSTEM_PROMPT = """You are a recipe extraction engine. You receive the text of a printed or scanned web page,
cookbook page or note, and return exactly one recipe as JSON matching the given schema.

Rules:
- Extract the main recipe only. Ignore ads, comments, navigation, related recipes and page boilerplate.
- SECTIONS AND ORDER MATTER. Many recipes are made of parts (a stock, a sauce, a filling, a topping, a
  marinade, a frosting), each with its own ingredient list and its own steps under a heading. Return ONE
  ingredient_section per part, with the heading copied exactly and that part's ingredients in their
  original order, and ONE step_section per part the same way. Keep the parts in the order the text presents
  them. Never merge parts into a single list and never reorder anything. First look for the headings: a
  short line above a run of ingredient lines ("For the stock", "Lobster stock", "CHOWDER", "Topping:")
  is a section heading, not an ingredient.
- A recipe with no parts is a single section whose heading is null. If the ingredients have sections but
  the steps do not (or the reverse), use headings on the side that has them and one null section on the other.
- Copy every ingredient line verbatim into "raw" before splitting it into quantity, unit, name, preparation.
- Never invent ingredients or steps that are not in the text. If something is unreadable, leave it out.
- Keep the original language and wording. Fractions become decimals (1/2 -> 0.5, 1 1/2 -> 1.5).
- Ranges: quantity is the low end, quantity_max the high end.
- Ingredient sub-headings ("For the sauce:") go in "group" on each ingredient under them.
- Steps are one action each, in order; drop step numbers.
- Times in minutes. Look for prep, cook and total times and the number of servings anywhere in the
  text, including header lines like "Serves 4 · Prep 15 min · Cook 45 min".
- Use the first introductory sentence(s) about the dish as the description when present.
- "to taste" or unquantified ingredients get quantity null, never 0.
- If the text is not a recipe at all, set is_recipe to false and leave the lists empty.
- categories: choose every one that fits from the allowed list (a shrimp pasta is Dinner, Pasta and Seafood).
- confidence: 0.9+ when ingredients and steps are clearly present and complete; lower when guessing.
"""
