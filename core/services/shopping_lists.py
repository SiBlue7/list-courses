from decimal import Decimal
from hashlib import sha256
import json

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import Q, prefetch_related_objects

from core.forms import HomeHubRecipeForm, ManualItemQuickAddForm
from core.models import (
    HomeHubItemAddition,
    Recipe,
    ShoppingList,
    ShoppingListItem,
    ShoppingListRecipe,
    ShoppingListRecipeIngredient,
)


DISPLAY_QUANTITY = Decimal('0.01')
METRIC_UNITS = {'kg': 'g', 'l': 'ml'}
HOUSEHOLD_FACTORS = {
    'cc': 'grams_per_teaspoon',
    'cs': 'grams_per_tablespoon',
    'pincee': 'grams_per_pinch',
}


def _conversion_factor(ingredient, unit):
    if unit in METRIC_UNITS:
        return Decimal('1000')
    field = HOUSEHOLD_FACTORS.get(unit)
    return getattr(ingredient, field) if field else None


def _converted_unit(unit, factor):
    return METRIC_UNITS.get(unit, 'g') if factor is not None else unit


def _converted_quantity(quantity, factor):
    converted = (quantity * (factor if factor is not None else 1)).quantize(DISPLAY_QUANTITY)
    if quantity and not converted:
        raise ValidationError({'quantity': 'La quantité convertie est inférieure à 0,01 g.'})
    return converted


def open_shopping_lists():
    return ShoppingList.objects.filter(is_closed=False).order_by('-created_at', '-id')


class ActiveListChanged(Exception):
    pass


class IdempotencyConflict(Exception):
    pass


def _lock_shopping_list(list_id):
    return ShoppingList.objects.select_for_update(
        no_key=connection.features.has_select_for_no_key_update,
    ).get(pk=list_id)


@transaction.atomic
def add_manual_item(shopping_list, ingredient, quantity, unit):
    shopping_list = _lock_shopping_list(shopping_list.pk)
    if shopping_list.is_closed:
        raise ActiveListChanged

    factor = _conversion_factor(ingredient, unit)
    estimated = factor is not None and unit in HOUSEHOLD_FACTORS
    unit = _converted_unit(unit, factor)
    quantity = _converted_quantity(quantity, factor)
    _normalize_legacy_metric_items(shopping_list, ingredient, unit)

    item = _find_shopping_item(shopping_list, ingredient, ingredient.name, unit)
    if item is None:
        _validate_item_quantity(quantity)
        ShoppingListItem.objects.create(
            shopping_list=shopping_list, ingredient=ingredient, name=ingredient.name,
            quantity=quantity, unit=unit, is_estimated=estimated,
        )
        return True

    total = (item.quantity + quantity).quantize(DISPLAY_QUANTITY)
    _validate_item_quantity(total)
    item.quantity = total
    item.per_person_quantity = None
    item.is_estimated |= estimated
    item.save(update_fields=['quantity', 'per_person_quantity', 'is_estimated'])
    return False


def _claim_homehub_addition(list_id, key, request_data):
    encoded = json.dumps(request_data, sort_keys=True, separators=(',', ':')).encode()
    digest = sha256(encoded).hexdigest()
    try:
        with transaction.atomic():
            HomeHubItemAddition.objects.create(key=key, request_digest=digest)
    except IntegrityError:
        receipt = HomeHubItemAddition.objects.get(pk=key)
        if receipt.request_digest != digest:
            raise IdempotencyConflict
        return None

    if connection.vendor == 'postgresql':
        # Keep list creation and closure from changing the active list until commit.
        with connection.cursor() as cursor:
            table = connection.ops.quote_name(ShoppingList._meta.db_table)
            cursor.execute(f'LOCK TABLE {table} IN SHARE MODE')

    active_list = open_shopping_lists().first()
    if active_list is None or active_list.pk != list_id:
        raise ActiveListChanged
    return active_list


@transaction.atomic
def add_homehub_item(list_id, key, payload):
    active_list = _claim_homehub_addition(list_id, key, [list_id, payload])
    if active_list is None:
        return

    form = ManualItemQuickAddForm(payload)
    if not form.is_valid():
        raise ValidationError(form.errors.as_data())
    add_manual_item(
        active_list,
        form.cleaned_data['ingredient_id'],
        form.cleaned_data['quantity'],
        form.cleaned_data['unit'],
    )


@transaction.atomic
def add_homehub_recipe(list_id, key, payload):
    active_list = _claim_homehub_addition(list_id, key, ['recipe', list_id, payload])
    if active_list is None:
        return

    recipe = Recipe.objects.select_for_update(
        no_key=connection.features.has_select_for_no_key_update,
    ).get(pk=payload['recipe_id'])
    prefetch_related_objects([recipe], 'ingredients__ingredient')
    form = HomeHubRecipeForm(payload, recipe=recipe)
    if not form.is_valid():
        raise ValidationError(form.errors.as_data())
    add_recipe_to_shopping_list(
        active_list,
        recipe,
        form.cleaned_data['people_count'],
        form.cleaned_data['included_recipe_ingredient_ids'],
    )


def quantity_for_people(quantity_per_person, people_count):
    return (quantity_per_person * Decimal(people_count)).quantize(DISPLAY_QUANTITY)


def _find_shopping_item(shopping_list, ingredient, name, unit):
    items = ShoppingListItem.objects.filter(shopping_list=shopping_list, unit=unit)
    if ingredient:
        return items.filter(ingredient=ingredient).first()
    return items.filter(ingredient__isnull=True, name=name).first()


def _normalize_legacy_metric_items(shopping_list, ingredient, unit):
    legacy_unit = {'g': 'kg', 'ml': 'l'}.get(unit)
    if legacy_unit is None:
        return
    related = shopping_list.items.filter(ingredient=ingredient)
    legacy = list(related.filter(unit=legacy_unit).order_by('id'))
    if not legacy:
        return
    base = list(related.filter(unit=unit).order_by('id'))
    items = base + legacy
    if any(item.checked != items[0].checked for item in items):
        return
    total = sum(item.quantity for item in base) + sum(item.quantity * 1000 for item in legacy)
    try:
        _validate_item_quantity(total)
    except ValidationError:
        return

    item = items[0]
    item.quantity = total
    item.unit = unit
    item.per_person_quantity = None
    item.is_estimated = any(previous.is_estimated for previous in items)
    item.save(update_fields=['quantity', 'unit', 'per_person_quantity', 'is_estimated'])
    related.filter(pk__in=[previous.pk for previous in items[1:]]).delete()
    ShoppingListRecipeIngredient.objects.filter(
        shopping_list_recipe__shopping_list=shopping_list,
        ingredient=ingredient, unit=legacy_unit, conversion_factor__isnull=True,
    ).update(conversion_factor=Decimal('1000'))


def _apply_quantity_delta(shopping_list, ingredient, name, unit, delta, estimated=False):
    delta = delta.quantize(DISPLAY_QUANTITY)
    if delta == 0:
        return

    item = _find_shopping_item(shopping_list, ingredient, name, unit)
    if item:
        new_quantity = (item.quantity + delta).quantize(DISPLAY_QUANTITY)
        if new_quantity <= 0:
            item.delete()
            return

        _validate_item_quantity(new_quantity)
        item.quantity = new_quantity
        item.per_person_quantity = None
        item.name = name
        item.is_estimated |= estimated
        fields = ['quantity', 'per_person_quantity', 'name', 'is_estimated']
        if ingredient and item.ingredient_id is None:
            item.ingredient = ingredient
            fields.append('ingredient')
        item.save(update_fields=fields)
        return

    if delta > 0:
        _validate_item_quantity(delta)
        ShoppingListItem.objects.create(
            shopping_list=shopping_list,
            ingredient=ingredient,
            name=name,
            unit=unit,
            quantity=delta,
            per_person_quantity=None,
            is_estimated=estimated,
        )


def _validate_item_quantity(quantity):
    try:
        ShoppingListItem._meta.get_field('quantity').run_validators(quantity)
    except ValidationError as error:
        raise ValidationError({'quantity': error}) from error


def _recipe_contribution(entry_ingredient, people_count):
    quantity = quantity_for_people(entry_ingredient.quantity_per_person, people_count)
    return _converted_quantity(quantity, entry_ingredient.conversion_factor)


def _apply_recipe_delta(shopping_list, entry_ingredient, delta):
    factor = entry_ingredient.conversion_factor
    _apply_quantity_delta(
        shopping_list,
        entry_ingredient.ingredient,
        entry_ingredient.display_name,
        _converted_unit(entry_ingredient.unit, factor),
        delta,
        estimated=factor is not None and entry_ingredient.unit in HOUSEHOLD_FACTORS and delta > 0,
    )


@transaction.atomic
def add_recipe_to_shopping_list(shopping_list, recipe, people_count, included_recipe_ingredient_ids):
    _lock_shopping_list(shopping_list.pk)
    included_ids = {int(ingredient_id) for ingredient_id in included_recipe_ingredient_ids}
    recipe_entry = ShoppingListRecipe.objects.create(
        shopping_list=shopping_list,
        recipe=recipe,
        recipe_name=recipe.name,
        people_count=people_count,
    )

    prefetch_related_objects([recipe], 'ingredients__ingredient')
    for recipe_ingredient in recipe.ingredients.all():
        ingredient = recipe_ingredient.ingredient
        unit = recipe_ingredient.unit
        factor = _conversion_factor(ingredient, unit)
        is_included = recipe_ingredient.id in included_ids
        entry_ingredient = ShoppingListRecipeIngredient.objects.create(
            shopping_list_recipe=recipe_entry,
            ingredient=ingredient,
            name=ingredient.name,
            unit=unit,
            conversion_factor=factor,
            quantity_per_person=recipe_ingredient.quantity_per_person,
            is_included=is_included,
        )

        if is_included:
            _normalize_legacy_metric_items(shopping_list, ingredient, _converted_unit(unit, factor))
            _apply_recipe_delta(shopping_list, entry_ingredient, _recipe_contribution(entry_ingredient, people_count))

    return recipe_entry


@transaction.atomic
def update_recipe_entry(recipe_entry, people_count, included_entry_ingredient_ids):
    _lock_shopping_list(recipe_entry.shopping_list_id)
    recipe_entry = ShoppingListRecipe.objects.select_for_update().get(pk=recipe_entry.pk)
    # Keep row locking on recipe ingredients only; PostgreSQL rejects FOR UPDATE
    # on the nullable side of the select_related('ingredient') outer join.
    entry_ingredients = list(recipe_entry.ingredients.select_for_update())
    included_ids = {int(ingredient_id) for ingredient_id in included_entry_ingredient_ids}
    old_people_count = recipe_entry.people_count

    for entry_ingredient in entry_ingredients:
        old_quantity = Decimal('0.00')
        if entry_ingredient.is_included:
            old_quantity = _recipe_contribution(entry_ingredient, old_people_count)

        new_is_included = entry_ingredient.id in included_ids
        new_quantity = Decimal('0.00')
        if new_is_included:
            new_quantity = _recipe_contribution(entry_ingredient, people_count)

        delta = (new_quantity - old_quantity).quantize(DISPLAY_QUANTITY)
        _apply_recipe_delta(recipe_entry.shopping_list, entry_ingredient, delta)

        if entry_ingredient.is_included != new_is_included:
            entry_ingredient.is_included = new_is_included
            entry_ingredient.save(update_fields=['is_included'])

    if recipe_entry.people_count != people_count:
        recipe_entry.people_count = people_count
        recipe_entry.save(update_fields=['people_count'])

    return recipe_entry


@transaction.atomic
def remove_recipe_entry(recipe_entry):
    _lock_shopping_list(recipe_entry.shopping_list_id)
    recipe_entry = ShoppingListRecipe.objects.select_for_update().get(pk=recipe_entry.pk)
    entry_ingredients = list(recipe_entry.ingredients.select_for_update())

    for entry_ingredient in entry_ingredients:
        if not entry_ingredient.is_included:
            continue

        quantity = _recipe_contribution(entry_ingredient, recipe_entry.people_count)
        _apply_recipe_delta(recipe_entry.shopping_list, entry_ingredient, -quantity)

    recipe_entry.delete()


@transaction.atomic
def remove_shopping_item(item):
    _lock_shopping_list(item.shopping_list_id)
    item = ShoppingListItem.objects.filter(pk=item.pk).first()
    if item is None:
        return
    units = Q(unit=item.unit, conversion_factor__isnull=True)
    if item.unit == 'g':
        units |= Q(unit__in=('kg', *HOUSEHOLD_FACTORS), conversion_factor__isnull=False)
    elif item.unit == 'ml':
        units |= Q(unit='l', conversion_factor__isnull=False)
    entry_ingredients = ShoppingListRecipeIngredient.objects.filter(
        units,
        shopping_list_recipe__shopping_list=item.shopping_list,
        is_included=True,
    )
    if item.ingredient_id:
        entry_ingredients = entry_ingredients.filter(ingredient=item.ingredient)
    else:
        entry_ingredients = entry_ingredients.filter(ingredient__isnull=True, name=item.name)

    entry_ingredients.update(is_included=False)
    item.delete()
