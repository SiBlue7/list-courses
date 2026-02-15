from decimal import Decimal
from urllib.parse import urlencode
from collections import OrderedDict

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db.models import ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import (
    AddRecipesForm,
    IngredientCategoryForm,
    IngredientForm,
    ManualItemQuickAddForm,
    PeopleCountForm,
    RecipeForm,
    RecipeIngredientQuickAddForm,
    RegistrationForm,
    ShoppingListForm,
    UNIT_CHOICES_WITH_EMPTY,
)
from .models import Ingredient, IngredientCategory, Recipe, RecipeIngredient, ShoppingList, ShoppingListItem


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

    return [{'label': label, 'entries': grouped_items} for label, grouped_items in grouped.items()]


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
    open_lists = ShoppingList.objects.filter(is_closed=False).order_by('-created_at')
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
            ingredient_ref = add_form.cleaned_data['ingredient_id']
            quantity = add_form.cleaned_data['quantity']
            unit = add_form.cleaned_data['unit']

            existing = ShoppingListItem.objects.filter(
                shopping_list=shopping_list,
                ingredient=ingredient_ref,
                unit=unit,
                per_person_quantity__isnull=True,
            ).first()

            if existing:
                existing.quantity = (existing.quantity + quantity).quantize(Decimal('0.01'))
                existing.save(update_fields=['quantity'])
                messages.success(request, 'Ingrédient déjà présent: quantité mise à jour.')
            else:
                ShoppingListItem.objects.create(
                    shopping_list=shopping_list,
                    ingredient=ingredient_ref,
                    name=ingredient_ref.name,
                    unit=unit,
                    quantity=quantity,
                    per_person_quantity=None,
                )
                messages.success(request, 'Ingrédient ajouté à la liste.')
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

    context = {
        'shopping_list': shopping_list,
        'item_groups': _shopping_items_grouped_by_category(shopping_list),
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
        default_people=shopping_list.people_count,
    )

    if request.method == 'POST' and form.is_valid():
        selected = form.selected_recipes()
        if not selected:
            form.add_error(None, 'Sélectionnez au moins une recette.')
        else:
            list_people = max(shopping_list.people_count, 1)
            for recipe, people in selected:
                ratio = Decimal(people) / Decimal(list_people)
                for recipe_ingredient in recipe.ingredients.all():
                    per_person = (recipe_ingredient.quantity_per_person * ratio).quantize(Decimal('0.0001'))
                    existing = ShoppingListItem.objects.filter(
                        shopping_list=shopping_list,
                        ingredient=recipe_ingredient.ingredient,
                        unit=recipe_ingredient.unit,
                        per_person_quantity__isnull=False,
                    ).first()
                    if existing:
                        existing.per_person_quantity = (existing.per_person_quantity + per_person).quantize(
                            Decimal('0.0001')
                        )
                        existing.quantity = (Decimal(list_people) * existing.per_person_quantity).quantize(
                            Decimal('0.01')
                        )
                        existing.name = recipe_ingredient.ingredient.name
                        existing.save(update_fields=['per_person_quantity', 'quantity', 'name'])
                    else:
                        quantity = (Decimal(list_people) * per_person).quantize(Decimal('0.01'))
                        ShoppingListItem.objects.create(
                            shopping_list=shopping_list,
                            ingredient=recipe_ingredient.ingredient,
                            name=recipe_ingredient.ingredient.name,
                            unit=recipe_ingredient.unit,
                            quantity=quantity,
                            per_person_quantity=per_person,
                        )
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
    if shopping_list.is_closed:
        messages.warning(request, 'La liste est clôturée, impossible de modifier le nombre de personnes.')
        return redirect('shopping_list_detail', list_id=shopping_list.id)

    form = PeopleCountForm(request.POST or None, instance=shopping_list)
    if request.method == 'POST' and form.is_valid():
        form.save()
        for item in shopping_list.items.filter(per_person_quantity__isnull=False):
            item.recalculate()
        messages.success(request, 'Nombre de personnes mis à jour.')
        return redirect('shopping_list_detail', list_id=shopping_list.id)

    return render(
        request,
        'core/shopping_list_people.html',
        {
            'shopping_list': shopping_list,
            'form': form,
        },
    )


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
        item.delete()
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
    open_lists = ShoppingList.objects.filter(is_closed=False).order_by('-created_at')
    active_list = open_lists.first()
    if not active_list:
        messages.info(request, 'Aucune liste active. Créez-en une nouvelle.')
        return redirect('shopping_list_create')
    return redirect('shopping_list_detail', list_id=active_list.id)





