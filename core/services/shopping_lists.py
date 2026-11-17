from decimal import Decimal
from hashlib import sha256
import json

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction

from core.forms import ManualItemQuickAddForm
from core.models import (
    HomeHubItemAddition,
    ShoppingList,
    ShoppingListItem,
    ShoppingListRecipe,
    ShoppingListRecipeIngredient,
)


DISPLAY_QUANTITY = Decimal('0.01')


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

    item = _find_shopping_item(shopping_list, ingredient, ingredient.name, unit)
    if item is None:
        ShoppingListItem.objects.create(
            shopping_list=shopping_list, ingredient=ingredient, name=ingredient.name,
            quantity=quantity, unit=unit,
        )
        return True

    total = (item.quantity + quantity).quantize(DISPLAY_QUANTITY)
    try:
        ShoppingListItem._meta.get_field('quantity').run_validators(total)
    except ValidationError as error:
        raise ValidationError({'quantity': error}) from error
    item.quantity = total
    item.per_person_quantity = None
    item.save(update_fields=['quantity', 'per_person_quantity'])
    return False


@transaction.atomic
def add_homehub_item(list_id, key, payload):
    request_data = json.dumps([list_id, payload], sort_keys=True, separators=(',', ':'))
    digest = sha256(request_data.encode()).hexdigest()
    try:
        with transaction.atomic():
            HomeHubItemAddition.objects.create(key=key, request_digest=digest)
    except IntegrityError:
        receipt = HomeHubItemAddition.objects.get(pk=key)
        if receipt.request_digest != digest:
            raise IdempotencyConflict
        return

    if connection.vendor == 'postgresql':
        # Keep list creation and closure from changing the active list until commit.
        with connection.cursor() as cursor:
            table = connection.ops.quote_name(ShoppingList._meta.db_table)
            cursor.execute(f'LOCK TABLE {table} IN SHARE MODE')

    active_list = open_shopping_lists().first()
    if active_list is None or active_list.pk != list_id:
        raise ActiveListChanged

    form = ManualItemQuickAddForm(payload)
    if not form.is_valid():
        raise ValidationError(form.errors.as_data())
    add_manual_item(
        active_list,
        form.cleaned_data['ingredient_id'],
        form.cleaned_data['quantity'],
        form.cleaned_data['unit'],
    )


def _quantity_for_people(quantity_per_person, people_count):
    return (quantity_per_person * Decimal(people_count)).quantize(DISPLAY_QUANTITY)


def _find_shopping_item(shopping_list, ingredient, name, unit):
    items = ShoppingListItem.objects.filter(shopping_list=shopping_list, unit=unit)
    if ingredient:
        return items.filter(ingredient=ingredient).first()
    return items.filter(ingredient__isnull=True, name=name).first()


def _apply_quantity_delta(shopping_list, ingredient, name, unit, delta):
    delta = delta.quantize(DISPLAY_QUANTITY)
    if delta == 0:
        return

    item = _find_shopping_item(shopping_list, ingredient, name, unit)
    if item:
        new_quantity = (item.quantity + delta).quantize(DISPLAY_QUANTITY)
        if new_quantity <= 0:
            item.delete()
            return

        item.quantity = new_quantity
        item.per_person_quantity = None
        item.name = name
        if ingredient and item.ingredient_id is None:
            item.ingredient = ingredient
            item.save(update_fields=['quantity', 'per_person_quantity', 'name', 'ingredient'])
        else:
            item.save(update_fields=['quantity', 'per_person_quantity', 'name'])
        return

    if delta > 0:
        ShoppingListItem.objects.create(
            shopping_list=shopping_list,
            ingredient=ingredient,
            name=name,
            unit=unit,
            quantity=delta,
            per_person_quantity=None,
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

    for recipe_ingredient in recipe.ingredients.select_related('ingredient'):
        ingredient = recipe_ingredient.ingredient
        is_included = recipe_ingredient.id in included_ids
        entry_ingredient = ShoppingListRecipeIngredient.objects.create(
            shopping_list_recipe=recipe_entry,
            ingredient=ingredient,
            name=ingredient.name,
            unit=recipe_ingredient.unit,
            quantity_per_person=recipe_ingredient.quantity_per_person,
            is_included=is_included,
        )

        if is_included:
            quantity = _quantity_for_people(entry_ingredient.quantity_per_person, people_count)
            _apply_quantity_delta(shopping_list, ingredient, ingredient.name, entry_ingredient.unit, quantity)

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
            old_quantity = _quantity_for_people(entry_ingredient.quantity_per_person, old_people_count)

        new_is_included = entry_ingredient.id in included_ids
        new_quantity = Decimal('0.00')
        if new_is_included:
            new_quantity = _quantity_for_people(entry_ingredient.quantity_per_person, people_count)

        delta = (new_quantity - old_quantity).quantize(DISPLAY_QUANTITY)
        _apply_quantity_delta(
            recipe_entry.shopping_list,
            entry_ingredient.ingredient,
            entry_ingredient.display_name,
            entry_ingredient.unit,
            delta,
        )

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

        quantity = _quantity_for_people(entry_ingredient.quantity_per_person, recipe_entry.people_count)
        _apply_quantity_delta(
            recipe_entry.shopping_list,
            entry_ingredient.ingredient,
            entry_ingredient.display_name,
            entry_ingredient.unit,
            -quantity,
        )

    recipe_entry.delete()


@transaction.atomic
def remove_shopping_item(item):
    _lock_shopping_list(item.shopping_list_id)
    entry_ingredients = ShoppingListRecipeIngredient.objects.filter(
        shopping_list_recipe__shopping_list=item.shopping_list,
        is_included=True,
        unit=item.unit,
    )
    if item.ingredient_id:
        entry_ingredients = entry_ingredients.filter(ingredient=item.ingredient)
    else:
        entry_ingredients = entry_ingredients.filter(ingredient__isnull=True, name=item.name)

    entry_ingredients.update(is_included=False)
    item.delete()
