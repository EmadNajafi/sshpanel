from django.contrib.auth import views as auth_views
from django.urls import include, path

from accounts.password_policy import PanelLoginView


# Django's admin site is intentionally not routed: the panel provides every
# administrative action, and a second login form would bypass Nginx's
# rate limit on the panel's /login/ location.
urlpatterns = [
    path("login/", PanelLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("accounts.urls")),
]
