from django.contrib import admin

from .models import Recipe, RecipeIngredient, ShoppingList, ShoppingListItem


class RecipeIngredientInline(admin.TabularInline):
    model = RecipeIngredient
    extra = 0


@admin.register(Recipe)
class RecipeAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'created_at')
    search_fields = ('name',)
    inlines = [RecipeIngredientInline]


class ShoppingListItemInline(admin.TabularInline):
    model = ShoppingListItem
    extra = 0


@admin.register(ShoppingList)
class ShoppingListAdmin(admin.ModelAdmin):
    list_display = ('name', 'owner', 'people_count', 'is_closed', 'created_at', 'closed_at')
    list_filter = ('is_closed',)
    inlines = [ShoppingListItemInline]


admin.site.register(ShoppingListItem)
admin.site.register(RecipeIngredient)
