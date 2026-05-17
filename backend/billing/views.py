from django.db import IntegrityError, transaction
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import ApiKeyAuthentication
from .models import UsageEvent
from .serializers import UsageEventInputSerializer


class EventIngestionView(APIView):
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        events = request.data.get("events", [])
        serializer = UsageEventInputSerializer(data=events, many=True)
        serializer.is_valid(raise_exception=True)

        accepted_count = 0
        duplicate_count = 0

        for event in serializer.validated_data:
            try:
                with transaction.atomic():
                    UsageEvent.objects.create(
                        request_id=event["request_id"],
                        customer=request.customer,
                        api_key=request.api_key,
                        endpoint=event["endpoint"],
                        units=event["units"],
                        timestamp=event["timestamp"],
                    )
                accepted_count += 1
            except IntegrityError:
                duplicate_count += 1

        return Response(
            {
                "accepted_count": accepted_count,
                "duplicate_count": duplicate_count,
                "total_received": len(serializer.validated_data),
            },
            status=status.HTTP_202_ACCEPTED,
        )
