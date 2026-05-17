from rest_framework import serializers


class UsageEventInputSerializer(serializers.Serializer):
    request_id = serializers.CharField(max_length=255)
    endpoint = serializers.CharField(max_length=255)
    units = serializers.IntegerField(min_value=1)
    timestamp = serializers.DateTimeField()
