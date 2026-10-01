from django.urls import path
from . import views


urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("api/system-metrics/", views.system_metrics, name="system_metrics"),
    path("api/account-usage/", views.account_usage, name="account_usage"),
    path("accounts/new/", views.create_account, name="create_account"),
    path("accounts/<int:pk>/edit/", views.edit_account, name="edit_account"),
    path("accounts/<int:pk>/password-view/", views.account_password, name="account_password"),
    path("settings/", views.panel_settings, name="panel_settings"),
    path("settings/admin-password/", views.change_admin_password, name="change_admin_password"),
    path("settings/ssh-port/stage/", views.stage_ssh_port, name="stage_ssh_port"),
    path("settings/ssh-port/finalize/", views.finalize_ssh_port, name="finalize_ssh_port"),
    path("settings/ssh-port/cancel/", views.cancel_ssh_port, name="cancel_ssh_port"),
    path("accounts/<int:pk>/disable/", views.disable_account, name="disable_account"),
    path("accounts/<int:pk>/enable/", views.enable_account, name="enable_account"),
    path("accounts/<int:pk>/password/", views.reset_password, name="reset_password"),
    path("accounts/<int:pk>/delete/", views.delete_account, name="delete_account"),
]
