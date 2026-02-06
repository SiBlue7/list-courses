from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import Recipe, RecipeIngredient, ShoppingList, UNIT_CHOICES

UNIT_CHOICES_WITH_EMPTY = [('', 'Sans unité')] + list(UNIT_CHOICES)


class RegistrationForm(UserCreationForm):
    class Meta:
        model = User
        fields = ['username', 'password1', 'password2']


class RecipeForm(forms.ModelForm):
    class Meta:
        model = Recipe
        fields = ['name']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Ex: Lasagnes maison'}),
        }


class RecipeIngredientForm(forms.ModelForm):
    class Meta:
        model = RecipeIngredient
        fields = ['name', 'quantity_per_person', 'unit']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'Ex: Tomates'}),
            'quantity_per_person': forms.NumberInput(attrs={'step': '0.01'}),
            'unit': forms.Select(choices=UNIT_CHOICES_WITH_EMPTY),
        }


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


class ManualItemForm(forms.Form):
    name = forms.CharField(max_length=200)
    quantity = forms.DecimalField(max_digits=10, decimal_places=2)
    unit = forms.ChoiceField(choices=UNIT_CHOICES_WITH_EMPTY, required=False)


class PeopleCountForm(forms.ModelForm):
    class Meta:
        model = ShoppingList
        fields = ['people_count']
        widgets = {
            'people_count': forms.NumberInput(attrs={'min': 1}),
        }
