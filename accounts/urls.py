from django.urls import path
from . import views


urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("audit-log/", views.audit_log, name="audit_log"),
    path("api/system-metrics/", views.system_metrics, name="system_metrics"),
    path("api/account-usage/", views.account_usage, name="account_usage"),
    path("accounts/new/", views.create_account, name="create_account"),
    path("accounts/bulk-new/", views.bulk_create_accounts, name="bulk_create_accounts"),
    path("accounts/bulk-action/", views.bulk_account_action, name="bulk_account_action"),
    path("accounts/<int:pk>/edit/", views.edit_account, name="edit_account"),
    path("accounts/<int:pk>/extend/", views.extend_account, name="extend_account"),
    path("accounts/<int:pk>/traffic-reset/", views.reset_account_traffic, name="reset_account_traffic"),
    path("accounts/<int:pk>/password-view/", views.account_password, name="account_password"),
    path("settings/", views.panel_settings, name="panel_settings"),
    path("settings/web-address/stage/", views.stage_web_address, name="stage_web_address"),
    path("settings/web-address/confirm/", views.confirm_web_address, name="confirm_web_address"),
    path("settings/backups/", views.backup_settings, name="backup_settings"),
    path("settings/backups/create/", views.create_backup, name="create_backup"),
    path("settings/backups/download/<str:name>/", views.download_backup, name="download_backup"),
    path("settings/backups/restore/", views.restore_backup, name="restore_backup"),
    path("settings/backups/import-users/", views.import_users_backup, name="import_users_backup"),
    path("settings/admin-password/", views.change_admin_password, name="change_admin_password"),
    path("settings/ssh-port/stage/", views.stage_ssh_port, name="stage_ssh_port"),
    path("settings/ssh-port/finalize/", views.finalize_ssh_port, name="finalize_ssh_port"),
    path("settings/ssh-port/cancel/", views.cancel_ssh_port, name="cancel_ssh_port"),
    path("accounts/<int:pk>/disable/", views.disable_account, name="disable_account"),
    path("accounts/<int:pk>/enable/", views.enable_account, name="enable_account"),
    path("accounts/<int:pk>/password/", views.reset_password, name="reset_password"),
    path("accounts/<int:pk>/delete/", views.delete_account, name="delete_account"),
]
