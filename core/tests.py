from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Ingredient, IngredientCategory, Recipe, RecipeIngredient, ShoppingList, ShoppingListItem, ShoppingListRecipe


@override_settings(HOMEHUB_API_TOKEN='test-homehub-token')
class ActiveShoppingListApiTests(TestCase):
    def setUp(self):
        self.url = reverse('api_active_shopping_list')
        self.headers = {'Authorization': 'Bearer test-homehub-token'}
        self.user = User.objects.create_user(username='owner')

    def test_requires_the_current_token_even_with_a_site_session(self):
        self.client.force_login(self.user)
        for headers in ({}, {'Authorization': 'Bearer invalid'}):
            with self.subTest(headers=headers):
                response = self.client.get(self.url, headers=headers)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.json(), {'error': 'unauthorized'})
                self.assertEqual(response.headers['WWW-Authenticate'], 'Bearer')

        for token in ('', 'rotated-test-token'):
            with self.subTest(token=token), self.settings(HOMEHUB_API_TOKEN=token):
                self.assertEqual(self.client.get(self.url, headers=self.headers).status_code, 401)

    def test_returns_no_active_list_when_all_lists_are_closed(self):
        ShoppingList.objects.create(name='Archives', owner=self.user, is_closed=True)

        response = self.client.get(self.url, headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'list': None})
        self.assertIn('no-store', response.headers['Cache-Control'])

    def test_returns_an_open_empty_list(self):
        active = ShoppingList.objects.create(name='Courses', owner=self.user)

        response = self.client.get(self.url, headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'list': {'id': active.pk, 'name': 'Courses', 'groups': []}})

    def test_returns_only_the_active_list_with_items_grouped_by_category(self):
        older = ShoppingList.objects.create(name='Autres courses', owner=self.user)
        active = ShoppingList.objects.create(name='Courses', owner=self.user)
        ShoppingList.objects.filter(pk=active.pk).update(created_at=older.created_at)
        ShoppingList.objects.create(name='Archives', owner=self.user, is_closed=True)
        ShoppingListItem.objects.create(shopping_list=older, name='Pain', quantity=1)
        category = IngredientCategory.objects.create(name='Fruits et légumes')
        ingredient = Ingredient.objects.create(name='Riz', category=category)
        rice = ShoppingListItem.objects.create(
            shopping_list=active, ingredient=ingredient, unit='g', quantity=Decimal('250.50'),
        )
        apples = ShoppingListItem.objects.create(
            shopping_list=active, name='Pommes', quantity=2, checked=True,
        )

        response = self.client.get(self.url, headers=self.headers)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'list': {
            'id': active.pk,
            'name': 'Courses',
            'groups': [
                {'label': 'Fruits et légumes', 'items': [{
                    'id': rice.pk, 'name': 'Riz', 'quantity': '250.50',
                    'unit': 'g', 'unit_label': 'g', 'checked': False,
                }]},
                {'label': 'Sans catégorie', 'items': [{
                    'id': apples.pk, 'name': 'Pommes', 'quantity': '2.00',
                    'unit': '', 'unit_label': '', 'checked': True,
                }]},
            ],
        }})

        self.client.force_login(self.user)
        self.assertRedirects(
            self.client.get(reverse('shopping_list_active')),
            reverse('shopping_list_detail', args=[active.pk]),
            fetch_redirect_response=False,
        )

    def test_rejects_write_methods(self):
        response = self.client.post(self.url, headers=self.headers)

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.headers['Allow'], 'GET')


class ShoppingListRecipeQuantityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='enzo', password='password')
        self.client.force_login(self.user)

    def test_added_recipe_quantities_do_not_depend_on_list_people_count(self):
        ingredient = Ingredient.objects.create(name='Riz')
        recipe = Recipe.objects.create(name='Riz au curry', owner=self.user)
        recipe_ingredient = RecipeIngredient.objects.create(
            recipe=recipe,
            ingredient=ingredient,
            quantity_per_person=Decimal('100.00'),
            unit='g',
        )
        shopping_list = ShoppingList.objects.create(name='Courses', owner=self.user, people_count=1)

        response = self.client.post(
            reverse('shopping_list_add_recipes', kwargs={'list_id': shopping_list.id}),
            {
                f'select_{recipe.id}': 'on',
                f'people_{recipe.id}': '2',
                f'include_{recipe.id}_{recipe_ingredient.id}': 'on',
            },
        )

        self.assertRedirects(response, reverse('shopping_list_detail', kwargs={'list_id': shopping_list.id}))
        item = ShoppingListItem.objects.get(shopping_list=shopping_list, ingredient=ingredient)
        self.assertEqual(item.quantity, Decimal('200.00'))
        self.assertIsNone(item.per_person_quantity)

        response = self.client.post(
            reverse('shopping_list_update_people', kwargs={'list_id': shopping_list.id}),
            {'people_count': '2'},
        )

        self.assertRedirects(response, reverse('shopping_list_detail', kwargs={'list_id': shopping_list.id}))
        item.refresh_from_db()
        self.assertEqual(item.quantity, Decimal('200.00'))

    def test_recipe_ingredient_can_be_excluded_when_adding_recipe(self):
        pasta = Ingredient.objects.create(name='Pates')
        cream = Ingredient.objects.create(name='Creme')
        recipe = Recipe.objects.create(name='Pates curry poulet', owner=self.user)
        pasta_recipe_ingredient = RecipeIngredient.objects.create(
            recipe=recipe,
            ingredient=pasta,
            quantity_per_person=Decimal('120.00'),
            unit='g',
        )
        cream_recipe_ingredient = RecipeIngredient.objects.create(
            recipe=recipe,
            ingredient=cream,
            quantity_per_person=Decimal('20.00'),
            unit='ml',
        )
        shopping_list = ShoppingList.objects.create(name='Courses', owner=self.user)

        response = self.client.post(
            reverse('shopping_list_add_recipes', kwargs={'list_id': shopping_list.id}),
            {
                f'select_{recipe.id}': 'on',
                f'people_{recipe.id}': '2',
                f'include_{recipe.id}_{pasta_recipe_ingredient.id}': 'on',
            },
        )

        self.assertRedirects(response, reverse('shopping_list_detail', kwargs={'list_id': shopping_list.id}))
        self.assertTrue(ShoppingListItem.objects.filter(shopping_list=shopping_list, ingredient=pasta).exists())
        self.assertFalse(ShoppingListItem.objects.filter(shopping_list=shopping_list, ingredient=cream).exists())

        recipe_entry = ShoppingListRecipe.objects.get(shopping_list=shopping_list, recipe=recipe)
        self.assertTrue(recipe_entry.ingredients.get(ingredient=pasta).is_included)
        self.assertFalse(recipe_entry.ingredients.get(ingredient=cream).is_included)
        self.assertEqual(ShoppingListItem.objects.get(shopping_list=shopping_list, ingredient=pasta).quantity, Decimal('240.00'))

    def test_recipe_entry_can_be_updated_after_manual_quantities_are_mixed_in(self):
        pasta = Ingredient.objects.create(name='Pates')
        cream = Ingredient.objects.create(name='Creme')
        recipe = Recipe.objects.create(name='Pates curry poulet', owner=self.user)
        pasta_recipe_ingredient = RecipeIngredient.objects.create(
            recipe=recipe,
            ingredient=pasta,
            quantity_per_person=Decimal('100.00'),
            unit='g',
        )
        cream_recipe_ingredient = RecipeIngredient.objects.create(
            recipe=recipe,
            ingredient=cream,
            quantity_per_person=Decimal('10.00'),
            unit='ml',
        )
        shopping_list = ShoppingList.objects.create(name='Courses', owner=self.user)

        self.client.post(
            reverse('shopping_list_add_recipes', kwargs={'list_id': shopping_list.id}),
            {
                f'select_{recipe.id}': 'on',
                f'people_{recipe.id}': '3',
                f'include_{recipe.id}_{pasta_recipe_ingredient.id}': 'on',
                f'include_{recipe.id}_{cream_recipe_ingredient.id}': 'on',
            },
        )
        self.client.post(
            reverse('shopping_list_detail', kwargs={'list_id': shopping_list.id}),
            {
                'ingredient_id': pasta.id,
                'quantity': '50',
                'unit': 'g',
            },
        )

        recipe_entry = ShoppingListRecipe.objects.get(shopping_list=shopping_list, recipe=recipe)
        pasta_entry_ingredient = recipe_entry.ingredients.get(ingredient=pasta)

        response = self.client.post(
            reverse(
                'shopping_list_recipe_update',
                kwargs={'list_id': shopping_list.id, 'entry_id': recipe_entry.id},
            ),
            {
                'people_count': '2',
                f'include_{pasta_entry_ingredient.id}': 'on',
            },
        )

        self.assertRedirects(response, reverse('shopping_list_detail', kwargs={'list_id': shopping_list.id}))
        self.assertEqual(ShoppingListItem.objects.get(shopping_list=shopping_list, ingredient=pasta).quantity, Decimal('250.00'))
        self.assertFalse(ShoppingListItem.objects.filter(shopping_list=shopping_list, ingredient=cream).exists())

        recipe_entry.refresh_from_db()
        self.assertEqual(recipe_entry.people_count, 2)
        self.assertTrue(recipe_entry.ingredients.get(ingredient=pasta).is_included)
        self.assertFalse(recipe_entry.ingredients.get(ingredient=cream).is_included)

    def test_recipe_entry_can_be_removed_without_losing_manual_quantity(self):
        pasta = Ingredient.objects.create(name='Pates')
        recipe = Recipe.objects.create(name='Pates curry', owner=self.user)
        pasta_recipe_ingredient = RecipeIngredient.objects.create(
            recipe=recipe,
            ingredient=pasta,
            quantity_per_person=Decimal('100.00'),
            unit='g',
        )
        shopping_list = ShoppingList.objects.create(name='Courses', owner=self.user)

        self.client.post(
            reverse('shopping_list_add_recipes', kwargs={'list_id': shopping_list.id}),
            {
                f'select_{recipe.id}': 'on',
                f'people_{recipe.id}': '3',
                f'include_{recipe.id}_{pasta_recipe_ingredient.id}': 'on',
            },
        )
        self.client.post(
            reverse('shopping_list_detail', kwargs={'list_id': shopping_list.id}),
            {
                'ingredient_id': pasta.id,
                'quantity': '50',
                'unit': 'g',
            },
        )

        recipe_entry = ShoppingListRecipe.objects.get(shopping_list=shopping_list, recipe=recipe)
        response = self.client.post(
            reverse(
                'shopping_list_recipe_remove',
                kwargs={'list_id': shopping_list.id, 'entry_id': recipe_entry.id},
            )
        )

        self.assertRedirects(response, reverse('shopping_list_detail', kwargs={'list_id': shopping_list.id}))
        self.assertFalse(ShoppingListRecipe.objects.filter(id=recipe_entry.id).exists())
        self.assertEqual(ShoppingListItem.objects.get(shopping_list=shopping_list, ingredient=pasta).quantity, Decimal('50.00'))

    def test_shopping_list_detail_exposes_item_progress(self):
        pasta = Ingredient.objects.create(name='Pates')
        rice = Ingredient.objects.create(name='Riz')
        shopping_list = ShoppingList.objects.create(name='Courses', owner=self.user)
        ShoppingListItem.objects.create(
            shopping_list=shopping_list,
            ingredient=pasta,
            name=pasta.name,
            unit='g',
            quantity=Decimal('100.00'),
            checked=True,
        )
        ShoppingListItem.objects.create(
            shopping_list=shopping_list,
            ingredient=rice,
            name=rice.name,
            unit='g',
            quantity=Decimal('200.00'),
            checked=False,
        )

        response = self.client.get(reverse('shopping_list_detail', kwargs={'list_id': shopping_list.id}))

        self.assertEqual(response.context['item_progress']['checked_count'], 1)
        self.assertEqual(response.context['item_progress']['total_count'], 2)
        self.assertEqual(response.context['item_progress']['remaining_count'], 1)
        self.assertEqual(response.context['item_groups'][0]['checked_count'], 1)
        self.assertEqual(response.context['item_groups'][0]['total_count'], 2)
