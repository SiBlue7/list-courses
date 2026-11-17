from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

from django.contrib.auth.models import User
from django.db import close_old_connections, connections
from django.test import Client, TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.urls import reverse

from .models import (
    HomeHubItemAddition, Ingredient, IngredientCategory, Recipe, RecipeIngredient, ShoppingList,
    ShoppingListItem, ShoppingListRecipe,
)


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


@override_settings(HOMEHUB_API_TOKEN='test-homehub-token')
class HomeHubIngredientTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner')
        self.shopping_list = ShoppingList.objects.create(name='Courses', owner=self.user)
        self.ingredient = Ingredient.objects.create(name='Riz')
        self.headers = {'Authorization': 'Bearer test-homehub-token'}
        self.client = Client(enforce_csrf_checks=True)
        self.payload = {'ingredient_id': self.ingredient.pk, 'quantity': '2.50', 'unit': 'g'}

    def post_item(self, key, payload=None, list_id=None, headers=None):
        return self.client.post(
            reverse('api_add_shopping_item', args=[list_id or self.shopping_list.pk]),
            data=self.payload if payload is None else payload,
            content_type='application/json',
            headers={'Idempotency-Key': str(key), **(self.headers if headers is None else headers)},
        )

    def test_search_paginates_matches_and_returns_allowed_units(self):
        Ingredient.objects.bulk_create([Ingredient(name=f'Riz {index:02}') for index in range(30)])
        Ingredient.objects.create(name='Pain')
        url = reverse('api_ingredients')
        self.assertEqual(self.client.get(url).status_code, 401)

        first = self.client.get(url, {'q': ' riZ '}, headers=self.headers).json()
        second = self.client.get(url, {'q': 'riZ', 'page': first['next_page']}, headers=self.headers).json()

        self.assertEqual(first['ingredients'][0], {'id': self.ingredient.pk, 'name': 'Riz'})
        self.assertEqual(len(first['ingredients']), 30)
        self.assertEqual(second['ingredients'][0]['name'], 'Riz 29')
        self.assertIsNone(second['next_page'])
        self.assertIn({'code': '', 'label': 'Sans unité'}, first['units'])
        self.assertEqual(self.client.get(url, {'page': 3}, headers=self.headers).status_code, 400)

    def test_addition_requires_token_and_leaves_site_csrf_in_place(self):
        self.client.force_login(self.user)
        self.assertEqual(self.post_item(uuid4(), headers={}).status_code, 401)
        self.assertEqual(self.post_item(uuid4(), headers={'Authorization': 'Bearer wrong'}).status_code, 401)
        site = reverse('shopping_list_detail', args=[self.shopping_list.pk])
        self.assertEqual(self.client.post(site, self.payload).status_code, 403)
        self.assertFalse(ShoppingListItem.objects.exists())

    def test_merges_with_site_additions_and_preserves_checked_status(self):
        site = reverse('shopping_list_detail', args=[self.shopping_list.pk])
        self.client = Client()
        self.client.force_login(self.user)
        self.assertEqual(self.client.post(site, self.payload).status_code, 302)
        item = ShoppingListItem.objects.get()
        item.checked = True
        item.save(update_fields=['checked'])

        result = self.post_item(uuid4())
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json(), {'list_id': self.shopping_list.pk})
        self.assertEqual(self.post_item(uuid4(), {**self.payload, 'unit': ''}).status_code, 200)
        item.refresh_from_db()
        self.assertEqual(item.quantity, Decimal('5.00'))
        self.assertTrue(item.checked)
        self.assertEqual(ShoppingListItem.objects.count(), 2)

    def test_replays_success_after_list_closes_and_rejects_key_conflicts(self):
        key = uuid4()
        self.assertEqual(self.post_item(key).status_code, 200)
        self.shopping_list.close()
        self.assertEqual(self.post_item(uuid4()).json(), {'error': 'active_list_changed'})
        replacement = ShoppingList.objects.create(name='Autres courses', owner=self.user)

        self.assertEqual(self.post_item(key).status_code, 200)
        for result in (
            self.post_item(key, {**self.payload, 'quantity': '4.00'}),
            self.post_item(key, list_id=replacement.pk),
        ):
            self.assertEqual(result.status_code, 409)
            self.assertEqual(result.json(), {'error': 'idempotency_conflict'})
        stale = self.post_item(uuid4())
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json(), {'error': 'active_list_changed'})
        self.assertEqual(ShoppingListItem.objects.get().quantity, Decimal('2.50'))

    def test_rejects_replaced_open_list_without_consuming_the_key(self):
        replacement = ShoppingList.objects.create(name='Autres courses', owner=self.user)
        key = uuid4()
        self.assertEqual(self.post_item(key).json(), {'error': 'active_list_changed'})
        self.assertFalse(HomeHubItemAddition.objects.exists())
        self.assertEqual(self.post_item(key, list_id=replacement.pk).status_code, 200)
        self.assertEqual(ShoppingListItem.objects.get().shopping_list, replacement)

    def test_rejects_invalid_requests_without_recording_an_addition(self):
        self.assertEqual(self.post_item('invalid').json(), {'error': 'invalid_idempotency_key'})
        self.assertEqual(self.post_item(uuid4(), '{').json(), {'error': 'invalid_json'})
        self.assertEqual(self.post_item(uuid4(), {**self.payload, 'quantity': 2}).json(), {'error': 'invalid_payload'})
        for field, value in (
            ('quantity', '0'), ('quantity', '0.001'), ('quantity', '100000000.00'),
            ('ingredient_id', self.ingredient.pk + 1), ('unit', 'invalid'),
        ):
            with self.subTest(field=field, value=value):
                response = self.post_item(uuid4(), {**self.payload, field: value})
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json()['fields'])
        self.assertFalse(HomeHubItemAddition.objects.exists())
        self.assertFalse(ShoppingListItem.objects.exists())

    def test_rejects_total_overflow_and_rolls_back_the_receipt(self):
        item = ShoppingListItem.objects.create(
            shopping_list=self.shopping_list, ingredient=self.ingredient, name='Riz',
            unit='g', quantity=Decimal('99999999.99'),
        )

        response = self.post_item(uuid4())
        self.assertEqual(response.status_code, 400)
        self.assertIn('quantity', response.json()['fields'])
        item.refresh_from_db()
        self.assertEqual(item.quantity, Decimal('99999999.99'))
        self.assertFalse(HomeHubItemAddition.objects.exists())


@override_settings(HOMEHUB_API_TOKEN='test-homehub-token')
class HomeHubRecipeTests(TestCase):
    def setUp(self):
        owner = User.objects.create_user(username='owner')
        self.shopping_list = ShoppingList.objects.create(name='Courses', owner=owner)
        self.recipe = Recipe.objects.create(name='Riz au curry', owner=owner)
        rice = Ingredient.objects.create(name='Riz')
        curry = Ingredient.objects.create(name='Curry')
        self.rice = RecipeIngredient.objects.create(
            recipe=self.recipe, ingredient=rice, quantity_per_person=Decimal('75.25'), unit='g',
        )
        self.curry = RecipeIngredient.objects.create(
            recipe=self.recipe, ingredient=curry, quantity_per_person=Decimal('0.50'), unit='cc',
        )
        self.client = Client(enforce_csrf_checks=True)
        self.headers = {'Authorization': 'Bearer test-homehub-token'}
        self.payload = {
            'recipe_id': self.recipe.id,
            'people_count': 3,
            'included_recipe_ingredient_ids': [self.rice.id],
        }

    def add_recipe(self, key, **changes):
        return self.client.post(
            reverse('api_add_recipe', args=[self.shopping_list.id]),
            {**self.payload, **changes},
            content_type='application/json',
            headers={**self.headers, 'Idempotency-Key': str(key)},
        )

    def test_search_and_preview_use_recipe_line_ids_and_scaled_quantities(self):
        search = reverse('api_recipes')
        detail = reverse('api_recipe_detail', args=[self.recipe.id])
        self.assertEqual(self.client.get(search).status_code, 401)
        self.assertEqual(self.client.get(detail).status_code, 401)
        self.assertEqual(
            self.client.get(search, {'q': ' RIZ '}, headers=self.headers).json(),
            {'recipes': [{'id': self.recipe.id, 'name': self.recipe.name}], 'next_page': None},
        )
        response = self.client.get(detail, {'people_count': 3}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['people_count'], 3)
        self.assertEqual(response.json()['ingredients'][1], {
            'id': self.rice.id, 'ingredient_id': self.rice.ingredient_id, 'name': 'Riz',
            'quantity_per_person': '75.25', 'quantity': '225.75', 'unit': 'g', 'unit_label': 'g',
        })
        self.assertEqual(self.client.get(detail, {'people_count': 0}, headers=self.headers).status_code, 400)
        missing = reverse('api_recipe_detail', args=[self.recipe.id + 1])
        self.assertEqual(self.client.get(missing, headers=self.headers).status_code, 404)

    def test_requires_token_and_rejects_bad_portions_and_foreign_or_empty_selections(self):
        write = reverse('api_add_recipe', args=[self.shopping_list.id])
        self.assertEqual(self.client.post(write, self.payload, content_type='application/json').status_code, 401)
        foreign_recipe = Recipe.objects.create(name='Autre', owner=self.recipe.owner)
        foreign = RecipeIngredient.objects.create(
            recipe=foreign_recipe, ingredient=self.rice.ingredient, quantity_per_person=1, unit='g',
        )
        self.assertEqual(self.add_recipe(uuid4(), recipe_id=foreign_recipe.id + 1).status_code, 404)
        for field, value in (
            ('people_count', 0), ('people_count', 2147483648),
            ('included_recipe_ingredient_ids', []), ('included_recipe_ingredient_ids', [foreign.id]),
            ('included_recipe_ingredient_ids', [True]),
        ):
            with self.subTest(field=field, value=value):
                response = self.add_recipe(uuid4(), **{field: value})
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.json()['fields'])
        self.assertFalse(ShoppingListRecipe.objects.exists())
        self.assertFalse(HomeHubItemAddition.objects.exists())

    def test_adds_selected_portions_once_and_replays_after_closure(self):
        item = ShoppingListItem.objects.create(
            shopping_list=self.shopping_list, ingredient=self.rice.ingredient, unit='g',
            quantity=Decimal('10.00'), checked=True,
        )
        key = uuid4()
        self.assertEqual(self.add_recipe(key).json(), {'list_id': self.shopping_list.id})
        entry = ShoppingListRecipe.objects.get()
        self.assertEqual(entry.people_count, 3)
        self.assertEqual(
            dict(entry.ingredients.values_list('ingredient_id', 'is_included')),
            {self.rice.ingredient_id: True, self.curry.ingredient_id: False},
        )
        self.shopping_list.close()
        self.assertEqual(self.add_recipe(key).status_code, 200)
        self.assertEqual(self.add_recipe(key, people_count=4).json(), {'error': 'idempotency_conflict'})
        manual = self.client.post(
            reverse('api_add_shopping_item', args=[self.shopping_list.id]),
            {'ingredient_id': self.rice.ingredient_id, 'quantity': '1.00', 'unit': 'g'},
            content_type='application/json',
            headers={**self.headers, 'Idempotency-Key': str(key)},
        )
        self.assertEqual(manual.json(), {'error': 'idempotency_conflict'})
        self.assertEqual(self.add_recipe(uuid4()).json(), {'error': 'active_list_changed'})
        item.refresh_from_db()
        self.assertEqual(item.quantity, Decimal('235.75'))
        self.assertTrue(item.checked)
        self.assertEqual(ShoppingListItem.objects.count(), 1)
        self.assertEqual(ShoppingListRecipe.objects.count(), 1)

    def test_rolls_back_all_recipe_contributions_when_a_total_overflows(self):
        item = ShoppingListItem.objects.create(
            shopping_list=self.shopping_list, ingredient=self.rice.ingredient, unit='g',
            quantity=Decimal('99999999.99'),
        )
        response = self.add_recipe(uuid4(), included_recipe_ingredient_ids=[self.curry.id, self.rice.id])
        self.assertEqual(response.status_code, 400)
        self.assertIn('quantity', response.json()['fields'])
        self.assertFalse(ShoppingListRecipe.objects.exists())
        self.assertFalse(HomeHubItemAddition.objects.exists())
        self.assertEqual(ShoppingListItem.objects.get().pk, item.pk)
        item.refresh_from_db()
        self.assertEqual(item.quantity, Decimal('99999999.99'))


@override_settings(HOMEHUB_API_TOKEN='test-homehub-token')
class HomeHubAdditionConcurrencyTests(TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_no_key_update')
    def test_concurrent_retries_and_distinct_additions_merge_into_one_row(self):
        owner = User.objects.create_user(username='owner')
        shopping_list = ShoppingList.objects.create(name='Courses', owner=owner)
        ingredient = Ingredient.objects.create(name='Riz')
        url = reverse('api_add_shopping_item', args=[shopping_list.pk])
        recipe_url = reverse('api_add_recipe', args=[shopping_list.pk])
        site_url = reverse('shopping_list_detail', args=[shopping_list.pk])
        payload = {'ingredient_id': ingredient.pk, 'quantity': '2.50', 'unit': 'g'}
        recipe = Recipe.objects.create(name='Riz', owner=owner)
        line = RecipeIngredient.objects.create(
            recipe=recipe, ingredient=ingredient, quantity_per_person=Decimal('2.50'), unit='g',
        )
        recipe_payload = {
            'recipe_id': recipe.pk, 'people_count': 2, 'included_recipe_ingredient_ids': [line.pk],
        }
        recipe_key = uuid4()
        barrier = Barrier(6, timeout=10)

        def add_item(key):
            close_old_connections()
            try:
                client = Client()
                if key is None:
                    client.force_login(owner)
                barrier.wait()
                if key is None:
                    return client.post(site_url, data=payload).status_code
                return client.post(
                    recipe_url if key == recipe_key else url,
                    data=recipe_payload if key == recipe_key else payload,
                    content_type='application/json',
                    headers={'Authorization': 'Bearer test-homehub-token', 'Idempotency-Key': str(key)},
                ).status_code
            finally:
                connections.close_all()

        repeated_key = uuid4()
        with ThreadPoolExecutor(max_workers=6) as pool:
            statuses = list(pool.map(add_item, [repeated_key, repeated_key, uuid4(), None, recipe_key, recipe_key]))
        self.assertEqual(statuses, [200, 200, 200, 302, 200, 200])
        self.assertEqual(ShoppingListItem.objects.get().quantity, Decimal('12.50'))
        self.assertEqual(ShoppingListRecipe.objects.count(), 1)
        self.assertEqual(HomeHubItemAddition.objects.count(), 3)


class ShoppingListUnitConversionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner')
        self.shopping_list = ShoppingList.objects.create(name='Courses', owner=self.user)
        self.client.force_login(self.user)

    def add_item(self, ingredient, quantity, unit):
        return self.client.post(
            reverse('shopping_list_detail', args=[self.shopping_list.id]),
            {'ingredient_id': ingredient.id, 'quantity': quantity, 'unit': unit},
        )

    def add_recipe(self, line):
        return self.client.post(
            reverse('shopping_list_add_recipes', args=[self.shopping_list.id]),
            {
                f'select_{line.recipe_id}': 'on',
                f'people_{line.recipe_id}': '2',
                f'include_{line.recipe_id}_{line.id}': 'on',
            },
        )

    def test_metric_units_group_and_legacy_recipe_removal_restores_manual_amount(self):
        rice = Ingredient.objects.create(name='Riz')
        recipe = Recipe.objects.create(name='Riz', owner=self.user)
        entry = ShoppingListRecipe.objects.create(
            shopping_list=self.shopping_list, recipe=recipe, recipe_name=recipe.name,
        )
        entry.ingredients.create(ingredient=rice, quantity_per_person=Decimal('1.00'), unit='kg')
        legacy = self.shopping_list.items.create(ingredient=rice, quantity=Decimal('1.00'), unit='kg')

        self.assertEqual(self.add_item(rice, '500', 'g').status_code, 302)
        legacy.refresh_from_db()
        self.assertEqual((legacy.quantity, legacy.unit, legacy.display_measure), (
            Decimal('1500'), 'g', (Decimal('1.5'), 'kg'),
        ))
        self.assertContains(
            self.client.get(reverse('shopping_list_detail', args=[self.shopping_list.id])), '1,5 kg',
        )
        response = self.client.post(
            reverse('shopping_list_recipe_remove', args=[self.shopping_list.id, entry.id]),
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.shopping_list.items.get().quantity, Decimal('500'))

        milk = Ingredient.objects.create(name='Lait')
        self.add_item(milk, '1', 'l')
        self.add_item(milk, '150', 'ml')
        self.assertEqual(self.shopping_list.items.get(ingredient=milk).display_measure, (Decimal('1.15'), 'l'))

    def test_household_conversion_uses_saved_factor_when_recipe_changes(self):
        salt = Ingredient.objects.create(name='Sel')
        edit_url = reverse('ingredient_edit', args=[salt.id])
        self.assertEqual(self.client.post(edit_url, {'name': salt.name, 'grams_per_teaspoon': '6'}).status_code, 302)
        recipe = Recipe.objects.create(name='Soupe', owner=self.user)
        line = recipe.ingredients.create(ingredient=salt, quantity_per_person=1, unit='cc')
        self.add_item(salt, '10', 'g')
        self.add_item(salt, '1', 'pincee')
        self.assertEqual(self.add_recipe(line).status_code, 302)
        entry = self.shopping_list.recipe_entries.get()
        added_line = entry.ingredients.get()
        self.assertEqual((added_line.unit, added_line.conversion_factor), ('cc', Decimal('6')))
        self.assertEqual(self.shopping_list.items.get(unit='g').quantity, Decimal('22'))
        self.assertTrue(self.shopping_list.items.get(unit='g').is_estimated)
        self.assertEqual(self.shopping_list.items.get(unit='pincee').quantity, 1)

        self.client.post(edit_url, {'name': salt.name, 'grams_per_teaspoon': '8'})
        response = self.client.post(
            reverse('shopping_list_recipe_update', args=[self.shopping_list.id, entry.id]),
            {'people_count': '3', f'include_{added_line.id}': 'on'},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.shopping_list.items.get(unit='g').quantity, Decimal('28'))
        self.client.post(reverse('shopping_list_recipe_remove', args=[self.shopping_list.id, entry.id]))
        self.assertEqual(self.shopping_list.items.get(unit='g').quantity, Decimal('10'))

    def test_removing_converted_item_excludes_its_recipe_contributions(self):
        salt = Ingredient.objects.create(name='Sel', grams_per_pinch=Decimal('0.40'))
        recipe = Recipe.objects.create(name='Soupe', owner=self.user)
        line = recipe.ingredients.create(ingredient=salt, quantity_per_person=1, unit='pincee')
        self.add_recipe(line)
        item = self.shopping_list.items.get()
        self.assertEqual((item.quantity, item.unit), (Decimal('0.80'), 'g'))
        self.client.post(reverse('shopping_list_remove_item', args=[self.shopping_list.id, item.id]))
        self.assertFalse(self.shopping_list.recipe_entries.get().ingredients.get().is_included)
        self.assertFalse(self.shopping_list.items.exists())

    def test_legacy_metric_rows_with_different_checked_statuses_stay_separate(self):
        rice = Ingredient.objects.create(name='Riz')
        self.shopping_list.items.create(ingredient=rice, quantity=Decimal('1.00'), unit='kg', checked=True)
        self.shopping_list.items.create(ingredient=rice, quantity=Decimal('500'), unit='g')
        self.add_item(rice, '150', 'g')
        self.assertEqual(
            list(self.shopping_list.items.order_by('checked').values_list('quantity', 'unit', 'checked')),
            [(Decimal('650'), 'g', False), (Decimal('1.00'), 'kg', True)],
        )


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
