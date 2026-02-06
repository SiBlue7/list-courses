from django.urls import path

from . import views

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('register/', views.register, name='register'),

    path('recipes/', views.recipe_list, name='recipe_list'),
    path('recipes/new/', views.recipe_create, name='recipe_create'),
    path('recipes/<int:recipe_id>/', views.recipe_detail, name='recipe_detail'),
    path('recipes/<int:recipe_id>/delete/', views.recipe_delete, name='recipe_delete'),
    path('ingredients/<int:ingredient_id>/delete/', views.recipe_ingredient_delete, name='recipe_ingredient_delete'),

    path('lists/new/', views.shopping_list_create, name='shopping_list_create'),
    path('lists/active/', views.shopping_list_active, name='shopping_list_active'),
    path('lists/archive/', views.shopping_list_archive, name='shopping_list_archive'),
    path('lists/<int:list_id>/', views.shopping_list_detail, name='shopping_list_detail'),
    path('lists/<int:list_id>/add-recipes/', views.shopping_list_add_recipes, name='shopping_list_add_recipes'),
    path('lists/<int:list_id>/people/', views.shopping_list_update_people, name='shopping_list_update_people'),
    path('lists/<int:list_id>/items/<int:item_id>/toggle/', views.shopping_list_toggle_item, name='shopping_list_toggle_item'),
    path('lists/<int:list_id>/items/<int:item_id>/remove/', views.shopping_list_remove_item, name='shopping_list_remove_item'),
    path('lists/<int:list_id>/close/', views.shopping_list_close, name='shopping_list_close'),
]
