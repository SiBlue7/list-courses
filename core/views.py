from decimal import Decimal

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render

from .forms import (
    AddRecipesForm,
    ManualItemForm,
    PeopleCountForm,
    RecipeForm,
    RecipeIngredientForm,
    RegistrationForm,
    ShoppingListForm,
)
from .models import Recipe, RecipeIngredient, ShoppingList, ShoppingListItem


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
    form = RecipeIngredientForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        ingredient = form.save(commit=False)
        ingredient.recipe = recipe
        ingredient.save()
        messages.success(request, 'Ingrédient ajouté.')
        return redirect('recipe_detail', recipe_id=recipe.id)
    return render(
        request,
        'core/recipe_detail.html',
        {'recipe': recipe, 'form': form},
    )


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
    manual_form = ManualItemForm(request.POST or None)

    if request.method == 'POST' and manual_form.is_valid():
        if shopping_list.is_closed:
            messages.warning(request, "La liste est clôturée, impossible d'ajouter des éléments.")
            return redirect('shopping_list_detail', list_id=shopping_list.id)

        ShoppingListItem.objects.create(
            shopping_list=shopping_list,
            name=manual_form.cleaned_data['name'],
            unit=manual_form.cleaned_data['unit'],
            quantity=manual_form.cleaned_data['quantity'],
            per_person_quantity=None,
        )
        messages.success(request, 'Ingrédient ajouté à la liste.')
        return redirect('shopping_list_detail', list_id=shopping_list.id)

    return render(
        request,
        'core/shopping_list_detail.html',
        {
            'shopping_list': shopping_list,
            'manual_form': manual_form,
        },
    )


@login_required
def shopping_list_add_recipes(request, list_id):
    shopping_list = _get_list_for_user(list_id)
    if shopping_list.is_closed:
        messages.warning(request, "La liste est clôturée, impossible d'ajouter des recettes.")
        return redirect('shopping_list_detail', list_id=shopping_list.id)

    recipes = Recipe.objects.all().prefetch_related('ingredients')
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
                for ingredient in recipe.ingredients.all():
                    per_person = (ingredient.quantity_per_person * ratio).quantize(Decimal('0.0001'))
                    existing = ShoppingListItem.objects.filter(
                        shopping_list=shopping_list,
                        name=ingredient.name,
                        unit=ingredient.unit,
                        per_person_quantity__isnull=False,
                    ).first()
                    if existing:
                        existing.per_person_quantity = (existing.per_person_quantity + per_person).quantize(
                            Decimal('0.0001')
                        )
                        existing.quantity = (Decimal(list_people) * existing.per_person_quantity).quantize(
                            Decimal('0.01')
                        )
                        existing.save(update_fields=['per_person_quantity', 'quantity'])
                    else:
                        quantity = (Decimal(list_people) * per_person).quantize(Decimal('0.01'))
                        ShoppingListItem.objects.create(
                            shopping_list=shopping_list,
                            name=ingredient.name,
                            unit=ingredient.unit,
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
