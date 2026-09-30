from django.urls import path
from . import views


urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("accounts/new/", views.create_account, name="create_account"),
    path("accounts/<int:pk>/disable/", views.disable_account, name="disable_account"),
    path("accounts/<int:pk>/enable/", views.enable_account, name="enable_account"),
    path("accounts/<int:pk>/password/", views.reset_password, name="reset_password"),
    path("accounts/<int:pk>/delete/", views.delete_account, name="delete_account"),
]
