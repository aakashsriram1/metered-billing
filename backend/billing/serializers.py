from rest_framework import serializers

from .models import UsageWindow


class UsageEventInputSerializer(serializers.Serializer):
    request_id = serializers.CharField(max_length=255)
    endpoint = serializers.CharField(max_length=255)
    units = serializers.IntegerField(min_value=1)
    timestamp = serializers.DateTimeField()


class UsageWindowSerializer(serializers.ModelSerializer):
    api_key_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = UsageWindow
        fields = ("window_start", "window_end", "api_key_id", "total_units", "event_count")
