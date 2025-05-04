from django.contrib import admin
from django.urls import path, include, re_path
from django.http import JsonResponse
import boto3
from django.conf import settings
from django.conf.urls.static import static

from rest_framework import permissions

from drf_yasg.views import get_schema_view
from drf_yasg import openapi

schema_view = get_schema_view(
    openapi.Info(
        title="EngageX",
        default_version='v1',
        description="API documentation for EngageX",
        terms_of_service="https://www.engagex.com/terms/",
        contact=openapi.Contact(email="contact@engagex.com"),
        license=openapi.License(name="BSD License"),
    ),
    public=True,
    permission_classes=[permissions.AllowAny],
)


def home(request):
    return JsonResponse({"message": "Welcome to EngageX API V2 (Update from github v5 triggred by yusuf....)"})


def send_plain_email(request):
    ses_client = boto3.client(
        "ses",
        region_name="us-west-1",
    )
    CHARSET = "UTF-8"

    response = ses_client.send_email(
        Destination={
            "ToAddresses": ["engagex@axelcyber.com"],  # Change this
        },
        Message={
            "Body": {
                "Text": {
                    "Charset": CHARSET,
                    "Data": "Hello, axel cyber test!",
                }
            },
            "Subject": {
                "Charset": CHARSET,
                "Data": "Test Email from EngageX",
            },
        },
        Source="lukheebalo@gmail.com",  # Change this to an SES verified email
    )

    return JsonResponse({"message": "Email sent successfully!", "response": response})


urlpatterns = [

    # URL config for Swagger
    re_path(r'^swagger(?P<format>\.json|\.yaml)$', schema_view.without_ui(cache_timeout=0), name='schema-json'),
    path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),

    path("", home, name="home"),
    path("send-email/", send_plain_email),  # New endpoint
    path('admin/', admin.site.urls),
    path('users/', include('users.urls')),
    path('payments/', include('payments.urls')),
    path('sessions/', include('practice_sessions.urls')),

]

if settings.DEBUG:  # IMPORTANT: Only do this in development!
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
