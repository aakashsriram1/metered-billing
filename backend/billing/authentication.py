from dataclasses import dataclass

from django.conf import settings
from rest_framework import authentication, exceptions

from .models import ApiKey, hash_api_key


@dataclass
class OpsUser:
    token: str

    @property
    def is_authenticated(self):
        return True


class ApiKeyAuthentication(authentication.BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        authorization = request.headers.get("Authorization", "")
        parts = authorization.split()

        if len(parts) != 2 or parts[0] != self.keyword:
            raise exceptions.AuthenticationFailed("Missing or invalid API key")

        raw_key = parts[1]
        key_hash = hash_api_key(raw_key)

        try:
            api_key = ApiKey.objects.select_related("customer").get(
                key_hash=key_hash,
                revoked_at__isnull=True,
            )
        except ApiKey.DoesNotExist as exc:
            raise exceptions.AuthenticationFailed("Missing or invalid API key") from exc

        request.customer = api_key.customer
        request.api_key = api_key
        return api_key.customer, None

    def authenticate_header(self, request):
        return self.keyword


class OpsTokenAuthentication(authentication.BaseAuthentication):
    keyword = "X-Ops-Token"

    def authenticate(self, request):
        token = request.headers.get(self.keyword, "")
        if not token or token != settings.OPS_TOKEN:
            raise exceptions.AuthenticationFailed("Missing or invalid ops token")
        return OpsUser(token), None

    def authenticate_header(self, request):
        return self.keyword
