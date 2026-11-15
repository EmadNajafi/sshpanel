from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path
from django.views.generic import RedirectView


urlpatterns = [
    # Django admin has its own login form, which Nginx's sign-in rate limit does
    # not cover. Send it to the rate-limited panel sign-in page instead.
    path("internal-admin/login/", RedirectView.as_view(pattern_name="login", query_string=True)),
    path("internal-admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(
        template_name="login.html", redirect_authenticated_user=True,
    ), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("accounts.urls")),
]
