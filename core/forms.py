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
        fields = ['name', 'people_count']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Ex: Courses semaine'}),
            'people_count': forms.NumberInput(attrs={'min': 1}),
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

    @property
    def recipe_rows(self):
        rows = []
        for recipe in self.recipes:
            rows.append(
                {
                    'recipe': recipe,
                    'select': self[f'select_{recipe.id}'],
                    'people': self[f'people_{recipe.id}'],
                }
            )
        return rows

    def selected_recipes(self):
        selected = []
        for recipe in self.recipes:
            if self.cleaned_data.get(f'select_{recipe.id}'):
                people = self.cleaned_data.get(f'people_{recipe.id}') or self.default_people
                selected.append((recipe, int(people)))
        return selected


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


class PeopleCountForm(forms.ModelForm):
    class Meta:
        model = ShoppingList
        fields = ['people_count']
        widgets = {
            'people_count': forms.NumberInput(attrs={'min': 1}),
        }
