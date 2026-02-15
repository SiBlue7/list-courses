from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

UNIT_CHOICES = [
    ('g', 'g'),
    ('kg', 'kg'),
    ('ml', 'ml'),
    ('l', 'l'),
    ('unit', 'unité(s)'),
    ('cs', 'c. à s.'),
    ('cc', 'c. à c.'),
    ('pincee', 'pincée'),
]


class IngredientCategory(models.Model):
    name = models.CharField(max_length=120, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Ingredient(models.Model):
    name = models.CharField(max_length=200, unique=True)
    category = models.ForeignKey(
        IngredientCategory,
        on_delete=models.SET_NULL,
        related_name='ingredients',
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Recipe(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='recipes')
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class RecipeIngredient(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE, related_name='ingredients')
    ingredient = models.ForeignKey(Ingredient, on_delete=models.PROTECT, related_name='recipe_usages')
    quantity_per_person = models.DecimalField(max_digits=8, decimal_places=2)
    unit = models.CharField(max_length=30, blank=True, choices=UNIT_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['ingredient__name']

    def __str__(self):
        unit_label = self.get_unit_display()
        unit = f" {unit_label}" if unit_label else ''
        return f"{self.display_name} ({self.quantity_per_person}{unit})"

    @property
    def display_name(self):
        return self.ingredient.name


class ShoppingList(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='owned_lists')
    name = models.CharField(max_length=200)
    people_count = models.PositiveIntegerField(default=1)
    participants = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name='shared_lists', blank=True)
    is_closed = models.BooleanField(default=False)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    def close(self):
        self.is_closed = True
        self.closed_at = timezone.now()
        self.save(update_fields=['is_closed', 'closed_at'])


class ShoppingListItem(models.Model):
    shopping_list = models.ForeignKey(ShoppingList, on_delete=models.CASCADE, related_name='items')
    ingredient = models.ForeignKey(
        Ingredient,
        on_delete=models.SET_NULL,
        related_name='shopping_list_items',
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=200)
    unit = models.CharField(max_length=30, blank=True, choices=UNIT_CHOICES)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    per_person_quantity = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    checked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['checked', 'name']

    def __str__(self):
        unit_label = self.get_unit_display()
        unit = f" {unit_label}" if unit_label else ''
        return f"{self.display_name} ({self.quantity}{unit})"

    @property
    def display_name(self):
        if self.ingredient:
            return self.ingredient.name
        return self.name

    def save(self, *args, **kwargs):
        if self.ingredient:
            self.name = self.ingredient.name
        super().save(*args, **kwargs)

    def recalculate(self):
        if self.per_person_quantity is None:
            return
        people = max(self.shopping_list.people_count, 1)
        self.quantity = (Decimal(people) * self.per_person_quantity).quantize(Decimal('0.01'))
        self.save(update_fields=['quantity'])
