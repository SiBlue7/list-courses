from decimal import Decimal

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from .models import (
    Ingredient,
    IngredientCategory,
    Recipe,
    ShoppingList,
    UNIT_CHOICES,
)

UNIT_CHOICES_WITH_EMPTY = [('', 'Sans unité')] + list(UNIT_CHOICES)


class RegistrationForm(UserCreationForm):
    class Meta:
        model = User
        fields = ['username', 'password1', 'password2']


class IngredientCategoryForm(forms.ModelForm):
    class Meta:
        model = IngredientCategory
        fields = ['name']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Ex: Fruits et légumes'}),
        }

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        duplicates = IngredientCategory.objects.filter(name__iexact=name)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise ValidationError('Cette catégorie existe déjà.')
        return name


class IngredientForm(forms.ModelForm):
    class Meta:
        model = Ingredient
        fields = ['name', 'category']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Ex: Tomates'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = IngredientCategory.objects.all().order_by('name')
        self.fields['category'].required = False
        self.fields['category'].empty_label = 'Sans catégorie'

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        duplicates = Ingredient.objects.filter(name__iexact=name)
        if self.instance.pk:
            duplicates = duplicates.exclude(pk=self.instance.pk)
        if duplicates.exists():
            raise ValidationError('Cet ingrédient existe déjà.')
        return name


class RecipeForm(forms.ModelForm):
    class Meta:
        model = Recipe
        fields = ['name']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Ex: Lasagnes maison'}),
        }


class RecipeIngredientQuickAddForm(forms.Form):
    ingredient_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput())
    quantity_per_person = forms.DecimalField(
        max_digits=8,
        decimal_places=2,
        min_value=Decimal('0.01'),
        widget=forms.NumberInput(attrs={'step': '0.01'}),
    )
    unit = forms.ChoiceField(choices=UNIT_CHOICES_WITH_EMPTY, required=False)

    def clean_ingredient_id(self):
        ingredient_id = self.cleaned_data['ingredient_id']
        ingredient = Ingredient.objects.filter(pk=ingredient_id).first()
        if ingredient is None:
            raise ValidationError('Ingrédient introuvable.')
        return ingredient


class ShoppingListForm(forms.ModelForm):
    class Meta:
        model = ShoppingList
        fields = ['name']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Ex: Courses semaine'}),
        }


class AddRecipesForm(forms.Form):
    def __init__(self, *args, recipes=None, default_people=1, **kwargs):
        super().__init__(*args, **kwargs)
        self.recipes = list(recipes) if recipes is not None else []
        self.default_people = max(int(default_people or 1), 1)

        for recipe in self.recipes:
            self.fields[f'select_{recipe.id}'] = forms.BooleanField(required=False)
            self.fields[f'people_{recipe.id}'] = forms.IntegerField(
                min_value=1,
                required=False,
                initial=self.default_people,
                widget=forms.NumberInput(attrs={'min': 1, 'class': 'compact'}),
            )
            for recipe_ingredient in recipe.ingredients.all():
                self.fields[f'include_{recipe.id}_{recipe_ingredient.id}'] = forms.BooleanField(
                    required=False,
                    initial=True,
                )

    def clean(self):
        cleaned_data = super().clean()
        for recipe in self.recipes:
            if not cleaned_data.get(f'select_{recipe.id}'):
                continue

            has_included_ingredient = any(
                cleaned_data.get(f'include_{recipe.id}_{recipe_ingredient.id}')
                for recipe_ingredient in recipe.ingredients.all()
            )
            if not has_included_ingredient:
                raise ValidationError(f"Sélectionnez au moins un ingrédient pour {recipe.name}.")

        return cleaned_data

    @property
    def recipe_rows(self):
        rows = []
        for recipe in self.recipes:
            ingredients = []
            for recipe_ingredient in recipe.ingredients.all():
                ingredients.append(
                    {
                        'ingredient': recipe_ingredient,
                        'include': self[f'include_{recipe.id}_{recipe_ingredient.id}'],
                    }
                )
            rows.append(
                {
                    'recipe': recipe,
                    'select': self[f'select_{recipe.id}'],
                    'people': self[f'people_{recipe.id}'],
                    'ingredients': ingredients,
                }
            )
        return rows

    def selected_recipes(self):
        selected = []
        for recipe in self.recipes:
            if self.cleaned_data.get(f'select_{recipe.id}'):
                people = self.cleaned_data.get(f'people_{recipe.id}') or self.default_people
                included_ids = [
                    recipe_ingredient.id
                    for recipe_ingredient in recipe.ingredients.all()
                    if self.cleaned_data.get(f'include_{recipe.id}_{recipe_ingredient.id}')
                ]
                selected.append((recipe, int(people), included_ids))
        return selected


class ShoppingListRecipeEntryForm(forms.Form):
    people_count = forms.IntegerField(
        min_value=1,
        label='Personnes',
        widget=forms.NumberInput(attrs={'min': 1, 'class': 'compact'}),
    )

    def __init__(self, *args, recipe_entry=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.recipe_entry = recipe_entry
        self.entry_ingredients = list(recipe_entry.ingredients.select_related('ingredient')) if recipe_entry else []

        if recipe_entry:
            self.fields['people_count'].initial = recipe_entry.people_count

        for entry_ingredient in self.entry_ingredients:
            self.fields[f'include_{entry_ingredient.id}'] = forms.BooleanField(
                required=False,
                initial=entry_ingredient.is_included,
            )

    def clean(self):
        cleaned_data = super().clean()
        if self.entry_ingredients and not any(
            cleaned_data.get(f'include_{entry_ingredient.id}') for entry_ingredient in self.entry_ingredients
        ):
            raise ValidationError('Sélectionnez au moins un ingrédient.')
        return cleaned_data

    @property
    def ingredient_rows(self):
        rows = []
        for entry_ingredient in self.entry_ingredients:
            rows.append(
                {
                    'ingredient': entry_ingredient,
                    'include': self[f'include_{entry_ingredient.id}'],
                }
            )
        return rows

    def included_ingredient_ids(self):
        return [
            entry_ingredient.id
            for entry_ingredient in self.entry_ingredients
            if self.cleaned_data.get(f'include_{entry_ingredient.id}')
        ]


class ManualItemQuickAddForm(forms.Form):
    ingredient_id = forms.IntegerField(min_value=1, widget=forms.HiddenInput())
    quantity = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal('0.01'),
        widget=forms.NumberInput(attrs={'step': '0.01'}),
    )
    unit = forms.ChoiceField(choices=UNIT_CHOICES_WITH_EMPTY, required=False)

    def clean_ingredient_id(self):
        ingredient_id = self.cleaned_data['ingredient_id']
        ingredient = Ingredient.objects.filter(pk=ingredient_id).first()
        if ingredient is None:
            raise ValidationError('Ingrédient introuvable.')
        return ingredient
