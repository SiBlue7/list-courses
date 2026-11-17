from decimal import Decimal
from urllib.parse import urlencode
from collections import OrderedDict
from functools import wraps
import json
from uuid import UUID

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import InvalidPage, Paginator
from django.db import transaction
from django.db.models import ProtectedError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.crypto import constant_time_compare
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt

from .forms import (
    AddRecipesForm,
    IngredientCategoryForm,
    IngredientForm,
    ManualItemQuickAddForm,
    RecipeForm,
    RecipeIngredientQuickAddForm,
    RecipePeopleForm,
    RegistrationForm,
    ShoppingListRecipeEntryForm,
    ShoppingListForm,
    UNIT_CHOICES_WITH_EMPTY,
)
from .models import Ingredient, IngredientCategory, Recipe, RecipeIngredient, ShoppingList, ShoppingListItem, ShoppingListRecipe
from .services.shopping_lists import (
    ActiveListChanged,
    IdempotencyConflict,
    add_homehub_item,
    add_homehub_recipe,
    add_manual_item,
    add_recipe_to_shopping_list,
    open_shopping_lists,
    quantity_for_people,
    remove_recipe_entry,
    remove_shopping_item,
    update_recipe_entry,
)


def _extract_ingredient_filters(source):
    query = (source.get('q') or '').strip()
    selected_category = (source.get('category') or '').strip()
    return query, selected_category


def _filter_ingredients(query, selected_category):
    ingredients = Ingredient.objects.select_related('category').order_by('name')

    if query:
        ingredients = ingredients.filter(name__icontains=query)

    if selected_category == 'none':
        ingredients = ingredients.filter(category__isnull=True)
    elif selected_category:
        try:
            selected_category_id = int(selected_category)
        except ValueError:
            selected_category = ''
        else:
            ingredients = ingredients.filter(category_id=selected_category_id)

    return ingredients, selected_category


def _redirect_with_ingredient_filters(route_name, route_kwargs, query, selected_category):
    url = reverse(route_name, kwargs=route_kwargs)
    params = {}
    if query:
        params['q'] = query
    if selected_category:
        params['category'] = selected_category
    if params:
        return f"{url}?{urlencode(params)}"
    return url


def _shopping_items_grouped_by_category(shopping_list):
    items = list(shopping_list.items.select_related('ingredient__category'))

    def sort_key(item):
        category_name = ''
        if item.ingredient and item.ingredient.category:
            category_name = item.ingredient.category.name

        has_category = bool(category_name)
        category_sort = category_name.lower() if has_category else 'zzzzzzzz'
        item_name_sort = item.display_name.lower()
        return (category_sort, item.checked, item_name_sort)

    grouped = OrderedDict()
    for item in sorted(items, key=sort_key):
        if item.ingredient and item.ingredient.category:
            label = item.ingredient.category.name
        else:
            label = 'Sans catégorie'

        if label not in grouped:
            grouped[label] = []
        grouped[label].append(item)

    groups = []
    for label, grouped_items in grouped.items():
        checked_count = sum(1 for item in grouped_items if item.checked)
        total_count = len(grouped_items)
        groups.append(
            {
                'label': label,
                'entries': grouped_items,
                'checked_count': checked_count,
                'total_count': total_count,
                'remaining_count': total_count - checked_count,
                'is_complete': total_count > 0 and checked_count == total_count,
            }
        )

    return groups


def _shopping_list_progress(item_groups):
    total_count = sum(group['total_count'] for group in item_groups)
    checked_count = sum(group['checked_count'] for group in item_groups)
    remaining_count = total_count - checked_count
    percent = 0
    if total_count:
        percent = int((checked_count / total_count) * 100)

    return {
        'total_count': total_count,
        'checked_count': checked_count,
        'remaining_count': remaining_count,
        'percent': percent,
        'is_complete': total_count > 0 and remaining_count == 0,
    }


def register(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    form = RegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        user = form.save()
        login(request, user)
        messages.success(request, 'Compte créé. Bienvenue !')
        return redirect('dashboard')

    return render(request, 'core/register.html', {'form': form})


@login_required
def dashboard(request):
    open_lists = open_shopping_lists()
    active_list = open_lists.first()

    return render(
        request,
        'core/dashboard.html',
        {
            'active_list': active_list,
            'open_lists': open_lists,
            'recipes_count': Recipe.objects.count(),
        },
    )


@login_required
def ingredient_list(request):
    categories = IngredientCategory.objects.all().order_by('name')
    search_query, selected_category = _extract_ingredient_filters(request.GET)
    ingredients, selected_category = _filter_ingredients(search_query, selected_category)

    ingredient_form = IngredientForm(prefix='ingredient')
    category_form = IngredientCategoryForm(prefix='category')

    if request.method == 'POST':
        if 'create_category' in request.POST:
            category_form = IngredientCategoryForm(request.POST, prefix='category')
            if category_form.is_valid():
                category_form.save()
                messages.success(request, 'Catégorie créée.')
                return redirect('ingredient_list')
        else:
            ingredient_form = IngredientForm(request.POST, prefix='ingredient')
            if ingredient_form.is_valid():
                ingredient_form.save()
                messages.success(request, 'Ingrédient créé.')
                return redirect('ingredient_list')

    if request.GET.get('partial') == '1':
        return render(
            request,
            'core/partials/ingredient_catalog.html',
            {
                'ingredients': ingredients,
                'search_query': search_query,
                'selected_category': selected_category,
            },
        )

    return render(
        request,
        'core/ingredient_list.html',
        {
            'ingredients': ingredients,
            'categories': categories,
            'ingredient_form': ingredient_form,
            'category_form': category_form,
            'search_query': search_query,
            'selected_category': selected_category,
        },
    )


@login_required
def ingredient_edit(request, ingredient_id):
    ingredient = get_object_or_404(Ingredient, id=ingredient_id)
    if request.method == 'POST':
        search_query = (request.POST.get('list_q') or '').strip()
        selected_category = (request.POST.get('list_category') or '').strip()
    else:
        search_query, selected_category = _extract_ingredient_filters(request.GET)

    form = IngredientForm(request.POST or None, instance=ingredient)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Ingrédient modifié.')
        return redirect(
            _redirect_with_ingredient_filters(
                'ingredient_list',
                {},
                search_query,
                selected_category,
            )
        )

    return render(
        request,
        'core/ingredient_edit.html',
        {
            'ingredient': ingredient,
            'form': form,
            'search_query': search_query,
            'selected_category': selected_category,
        },
    )


@login_required
def ingredient_delete(request, ingredient_id):
    ingredient = get_object_or_404(Ingredient, id=ingredient_id)
    search_query, selected_category = _extract_ingredient_filters(request.POST if request.method == 'POST' else request.GET)

    if request.method == 'POST':
        try:
            ingredient.delete()
            messages.success(request, 'Ingrédient supprimé.')
        except ProtectedError:
            messages.error(
                request,
                "Impossible de supprimer cet ingrédient car il est utilisé dans une ou plusieurs recettes.",
            )

    return redirect(
        _redirect_with_ingredient_filters(
            'ingredient_list',
            {},
            search_query,
            selected_category,
        )
    )


@login_required
def recipe_list(request):
    recipes = Recipe.objects.all()
    return render(request, 'core/recipe_list.html', {'recipes': recipes})


@login_required
def recipe_create(request):
    form = RecipeForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        recipe = form.save(commit=False)
        recipe.owner = request.user
        recipe.save()
        messages.success(request, 'Recette créée. Ajoutez les ingrédients.')
        return redirect('recipe_detail', recipe_id=recipe.id)
    return render(request, 'core/recipe_form.html', {'form': form, 'mode': 'create'})


@login_required
def recipe_detail(request, recipe_id):
    recipe = get_object_or_404(Recipe, id=recipe_id)

    if request.method == 'POST':
        ingredient_query, selected_category = _extract_ingredient_filters(request.POST)
        add_form = RecipeIngredientQuickAddForm(request.POST)

        if add_form.is_valid():
            ingredient_ref = add_form.cleaned_data['ingredient_id']
            unit = add_form.cleaned_data['unit']
            quantity = add_form.cleaned_data['quantity_per_person']

            existing = RecipeIngredient.objects.filter(
                recipe=recipe,
                ingredient=ingredient_ref,
                unit=unit,
            ).first()

            if existing:
                existing.quantity_per_person = (existing.quantity_per_person + quantity).quantize(Decimal('0.01'))
                existing.save(update_fields=['quantity_per_person'])
                messages.success(request, 'Ingrédient déjà présent: quantité mise à jour.')
            else:
                RecipeIngredient.objects.create(
                    recipe=recipe,
                    ingredient=ingredient_ref,
                    unit=unit,
                    quantity_per_person=quantity,
                )
                messages.success(request, 'Ingrédient ajouté.')
        else:
            messages.error(request, "Impossible d'ajouter cet ingrédient. Vérifiez la quantité.")

        return redirect(
            _redirect_with_ingredient_filters(
                'recipe_detail',
                {'recipe_id': recipe.id},
                ingredient_query,
                selected_category,
            )
        )

    ingredient_categories = IngredientCategory.objects.all().order_by('name')
    ingredient_query, selected_category = _extract_ingredient_filters(request.GET)
    ingredients, selected_category = _filter_ingredients(ingredient_query, selected_category)

    context = {
        'recipe': recipe,
        'ingredients': ingredients,
        'ingredient_categories': ingredient_categories,
        'ingredient_query': ingredient_query,
        'selected_category': selected_category,
        'unit_choices': UNIT_CHOICES_WITH_EMPTY,
    }

    if request.GET.get('partial') == 'recipe_ingredients':
        return render(request, 'core/partials/recipe_ingredient_results.html', context)

    return render(request, 'core/recipe_detail.html', context)


@login_required
def recipe_delete(request, recipe_id):
    recipe = get_object_or_404(Recipe, id=recipe_id)
    if request.method == 'POST':
        recipe.delete()
        messages.success(request, 'Recette supprimée.')
        return redirect('recipe_list')
    return render(request, 'core/recipe_delete.html', {'recipe': recipe})


@login_required
def recipe_ingredient_delete(request, ingredient_id):
    ingredient = get_object_or_404(RecipeIngredient, id=ingredient_id)
    recipe_id = ingredient.recipe.id
    if request.method == 'POST':
        ingredient.delete()
        messages.success(request, 'Ingrédient supprimé.')
        return redirect('recipe_detail', recipe_id=recipe_id)
    return render(request, 'core/ingredient_delete.html', {'ingredient': ingredient})


def _get_list_for_user(list_id):
    return get_object_or_404(ShoppingList, id=list_id)


@login_required
def shopping_list_create(request):
    form = ShoppingListForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        shopping_list = form.save(commit=False)
        shopping_list.owner = request.user
        shopping_list.save()
        messages.success(request, 'Liste créée.')
        return redirect('shopping_list_detail', list_id=shopping_list.id)

    return render(request, 'core/shopping_list_form.html', {'form': form, 'mode': 'create'})


@login_required
def shopping_list_detail(request, list_id):
    shopping_list = _get_list_for_user(list_id)

    if request.method == 'POST':
        ingredient_query, selected_category = _extract_ingredient_filters(request.POST)

        if shopping_list.is_closed:
            messages.warning(request, "La liste est clôturée, impossible d'ajouter des éléments.")
            return redirect(
                _redirect_with_ingredient_filters(
                    'shopping_list_detail',
                    {'list_id': shopping_list.id},
                    ingredient_query,
                    selected_category,
                )
            )

        add_form = ManualItemQuickAddForm(request.POST)
        if add_form.is_valid():
            try:
                created = add_manual_item(
                    shopping_list,
                    add_form.cleaned_data['ingredient_id'],
                    add_form.cleaned_data['quantity'],
                    add_form.cleaned_data['unit'],
                )
            except ActiveListChanged:
                messages.warning(request, "La liste est clôturée, impossible d'ajouter des éléments.")
            except ValidationError as error:
                messages.error(request, error.messages[0])
            else:
                if created:
                    messages.success(request, 'Ingrédient ajouté à la liste.')
                else:
                    messages.success(request, 'Ingrédient déjà présent: quantité mise à jour.')
        else:
            messages.error(request, "Impossible d'ajouter cet ingrédient. Vérifiez la quantité.")

        return redirect(
            _redirect_with_ingredient_filters(
                'shopping_list_detail',
                {'list_id': shopping_list.id},
                ingredient_query,
                selected_category,
            )
        )

    ingredient_categories = IngredientCategory.objects.all().order_by('name')
    ingredient_query, selected_category = _extract_ingredient_filters(request.GET)
    ingredients, selected_category = _filter_ingredients(ingredient_query, selected_category)
    item_groups = _shopping_items_grouped_by_category(shopping_list)

    context = {
        'shopping_list': shopping_list,
        'item_groups': item_groups,
        'item_progress': _shopping_list_progress(item_groups),
        'recipe_entries': shopping_list.recipe_entries.prefetch_related('ingredients', 'recipe'),
        'ingredients': ingredients,
        'ingredient_categories': ingredient_categories,
        'ingredient_query': ingredient_query,
        'selected_category': selected_category,
        'unit_choices': UNIT_CHOICES_WITH_EMPTY,
    }

    if request.GET.get('partial') == 'shopping_list_ingredients':
        return render(request, 'core/partials/shopping_list_ingredient_results.html', context)

    return render(request, 'core/shopping_list_detail.html', context)


@login_required
def shopping_list_add_recipes(request, list_id):
    shopping_list = _get_list_for_user(list_id)
    if shopping_list.is_closed:
        messages.warning(request, "La liste est clôturée, impossible d'ajouter des recettes.")
        return redirect('shopping_list_detail', list_id=shopping_list.id)

    recipes = Recipe.objects.all().prefetch_related('ingredients__ingredient')
    form = AddRecipesForm(
        request.POST or None,
        recipes=recipes,
    )

    if request.method == 'POST' and form.is_valid():
        selected = form.selected_recipes()
        if not selected:
            form.add_error(None, 'Sélectionnez au moins une recette.')
        else:
            try:
                with transaction.atomic():
                    for recipe, people, included_ids in selected:
                        add_recipe_to_shopping_list(shopping_list, recipe, people, included_ids)
            except ValidationError as error:
                form.add_error(None, error.messages[0])
            else:
                messages.success(request, 'Recettes ajoutées à la liste.')
                return redirect('shopping_list_detail', list_id=shopping_list.id)

    return render(
        request,
        'core/shopping_list_add_recipes.html',
        {
            'shopping_list': shopping_list,
            'form': form,
        },
    )


@login_required
def shopping_list_update_people(request, list_id):
    shopping_list = _get_list_for_user(list_id)
    messages.info(request, "Le nombre de personnes se choisit maintenant lors de l'ajout des recettes.")
    return redirect('shopping_list_detail', list_id=shopping_list.id)


@login_required
def shopping_list_recipe_update(request, list_id, entry_id):
    shopping_list = _get_list_for_user(list_id)
    recipe_entry = get_object_or_404(ShoppingListRecipe, id=entry_id, shopping_list=shopping_list)

    if shopping_list.is_closed:
        messages.warning(request, 'La liste est clôturée, impossible de modifier une recette.')
        return redirect('shopping_list_detail', list_id=shopping_list.id)

    form = ShoppingListRecipeEntryForm(request.POST or None, recipe_entry=recipe_entry)
    if request.method == 'POST' and form.is_valid():
        try:
            update_recipe_entry(
                recipe_entry,
                form.cleaned_data['people_count'],
                form.included_ingredient_ids(),
            )
        except ValidationError as error:
            form.add_error(None, error.messages[0])
        else:
            messages.success(request, 'Recette mise à jour dans la liste.')
            return redirect('shopping_list_detail', list_id=shopping_list.id)

    return render(
        request,
        'core/shopping_list_recipe_form.html',
        {
            'shopping_list': shopping_list,
            'recipe_entry': recipe_entry,
            'form': form,
        },
    )


@login_required
def shopping_list_recipe_remove(request, list_id, entry_id):
    shopping_list = _get_list_for_user(list_id)
    recipe_entry = get_object_or_404(ShoppingListRecipe, id=entry_id, shopping_list=shopping_list)

    if request.method == 'POST':
        if shopping_list.is_closed:
            messages.warning(request, 'La liste est clôturée, impossible de retirer une recette.')
            return redirect('shopping_list_detail', list_id=shopping_list.id)

        remove_recipe_entry(recipe_entry)
        messages.success(request, 'Recette retirée de la liste.')

    return redirect('shopping_list_detail', list_id=shopping_list.id)


@login_required
def shopping_list_toggle_item(request, list_id, item_id):
    shopping_list = _get_list_for_user(list_id)
    item = get_object_or_404(ShoppingListItem, id=item_id, shopping_list=shopping_list)

    if request.method == 'POST':
        if shopping_list.is_closed:
            messages.warning(request, 'La liste est clôturée, impossible de modifier les achats.')
            return redirect('shopping_list_detail', list_id=shopping_list.id)
        item.checked = not item.checked
        item.save(update_fields=['checked'])

    return redirect('shopping_list_detail', list_id=shopping_list.id)


@login_required
def shopping_list_remove_item(request, list_id, item_id):
    shopping_list = _get_list_for_user(list_id)
    item = get_object_or_404(ShoppingListItem, id=item_id, shopping_list=shopping_list)
    if request.method == 'POST':
        if shopping_list.is_closed:
            messages.warning(request, 'La liste est clôturée, impossible de supprimer des éléments.')
            return redirect('shopping_list_detail', list_id=shopping_list.id)
        remove_shopping_item(item)
        messages.success(request, 'Ingrédient supprimé de la liste.')
    return redirect('shopping_list_detail', list_id=shopping_list.id)


@login_required
def shopping_list_close(request, list_id):
    shopping_list = _get_list_for_user(list_id)
    if request.method == 'POST':
        shopping_list.close()
        messages.success(request, 'Liste clôturée et archivée.')
        return redirect('shopping_list_archive')
    return render(request, 'core/shopping_list_close.html', {'shopping_list': shopping_list})


@login_required
def shopping_list_archive(request):
    closed_lists = ShoppingList.objects.filter(is_closed=True).order_by('-closed_at')
    return render(request, 'core/shopping_list_archive.html', {'closed_lists': closed_lists})


@login_required
def shopping_list_active(request):
    active_list = open_shopping_lists().first()
    if not active_list:
        messages.info(request, 'Aucune liste active. Créez-en une nouvelle.')
        return redirect('shopping_list_create')
    return redirect('shopping_list_detail', list_id=active_list.id)


def _homehub_endpoint(method):
    def decorate(view):
        @never_cache
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            scheme, _, token = request.headers.get('Authorization', '').partition(' ')
            if (
                not settings.HOMEHUB_API_TOKEN
                or scheme.lower() != 'bearer'
                or not constant_time_compare(token, settings.HOMEHUB_API_TOKEN)
            ):
                return JsonResponse(
                    {'error': 'unauthorized'}, status=401, headers={'WWW-Authenticate': 'Bearer'},
                )
            if request.method != method:
                return JsonResponse(
                    {'error': 'method_not_allowed'}, status=405, headers={'Allow': method},
                )
            return view(request, *args, **kwargs)
        return wrapped
    return decorate


@_homehub_endpoint('GET')
def api_active_shopping_list(request):
    active_list = open_shopping_lists().first()
    if active_list is None:
        return JsonResponse({'list': None})

    groups = [
        {
            'label': group['label'],
            'items': [
                {
                    'id': item.id,
                    'name': item.display_name,
                    'quantity': str(item.quantity),
                    'unit': item.unit,
                    'unit_label': item.get_unit_display(),
                    'checked': item.checked,
                }
                for item in group['entries']
            ],
        }
        for group in _shopping_items_grouped_by_category(active_list)
    ]
    return JsonResponse({'list': {'id': active_list.id, 'name': active_list.name, 'groups': groups}})


@_homehub_endpoint('GET')
def api_ingredients(request):
    ingredients, _ = _filter_ingredients(request.GET.get('q', '').strip(), '')
    return _homehub_catalog_page(
        request, ingredients, 'ingredients',
        units=[{'code': code, 'label': label} for code, label in UNIT_CHOICES_WITH_EMPTY],
    )


@_homehub_endpoint('GET')
def api_recipes(request):
    query = request.GET.get('q', '').strip()
    return _homehub_catalog_page(
        request, Recipe.objects.filter(name__icontains=query).order_by('name', 'id'), 'recipes',
    )


def _homehub_catalog_page(request, queryset, key, **extra):
    try:
        page = Paginator(queryset, 30).page(request.GET.get('page', 1))
    except InvalidPage:
        return JsonResponse({'error': 'invalid_page'}, status=400)

    return JsonResponse({
        key: [{'id': entry.id, 'name': entry.name} for entry in page],
        'next_page': page.next_page_number() if page.has_next() else None,
        **extra,
    })


@_homehub_endpoint('GET')
def api_recipe_detail(request, recipe_id):
    recipe = Recipe.objects.filter(pk=recipe_id).first()
    if recipe is None:
        return JsonResponse({'error': 'recipe_not_found'}, status=404)

    form = RecipePeopleForm({'people_count': request.GET.get('people_count', 1)})
    if not form.is_valid():
        return _homehub_validation_error(form.errors)
    people_count = form.cleaned_data['people_count']
    return JsonResponse({
        'id': recipe.id,
        'name': recipe.name,
        'people_count': people_count,
        'ingredients': [
            {
                'id': line.id,
                'ingredient_id': line.ingredient_id,
                'name': line.display_name,
                'quantity_per_person': str(line.quantity_per_person),
                'quantity': str(quantity_for_people(line.quantity_per_person, people_count)),
                'unit': line.unit,
                'unit_label': line.get_unit_display(),
            }
            for line in recipe.ingredients.select_related('ingredient')
        ],
    })


@csrf_exempt
@_homehub_endpoint('POST')
def api_add_shopping_item(request, list_id):
    return _homehub_addition(
        request, list_id, {'ingredient_id': int, 'quantity': str, 'unit': str}, add_homehub_item,
    )


@csrf_exempt
@_homehub_endpoint('POST')
def api_add_recipe(request, list_id):
    return _homehub_addition(
        request, list_id,
        {'recipe_id': int, 'people_count': int, 'included_recipe_ingredient_ids': list},
        add_homehub_recipe,
    )


def _homehub_validation_error(fields):
    return JsonResponse({'error': 'validation_error', 'fields': fields}, status=400)


def _homehub_addition(request, list_id, field_types, add):
    if request.content_type != 'application/json':
        return JsonResponse({'error': 'unsupported_media_type'}, status=415)
    try:
        key = UUID(request.headers.get('Idempotency-Key', ''))
    except ValueError:
        return JsonResponse({'error': 'invalid_idempotency_key'}, status=400)
    try:
        payload = json.loads(request.body)
    except (ValueError, RecursionError):
        return JsonResponse({'error': 'invalid_json'}, status=400)

    if (
        not isinstance(payload, dict)
        or payload.keys() != field_types.keys()
        or any(type(payload[field]) is not expected for field, expected in field_types.items())
    ):
        return JsonResponse({'error': 'invalid_payload'}, status=400)

    try:
        add(list_id, key, payload)
    except Recipe.DoesNotExist:
        return JsonResponse({'error': 'recipe_not_found'}, status=404)
    except ActiveListChanged:
        return JsonResponse({'error': 'active_list_changed'}, status=409)
    except IdempotencyConflict:
        return JsonResponse({'error': 'idempotency_conflict'}, status=409)
    except ValidationError as error:
        return _homehub_validation_error(error.message_dict)
    return JsonResponse({'list_id': list_id})



