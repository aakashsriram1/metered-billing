from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import exceptions
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .authentication import ApiKeyAuthentication
from .models import ApiKey, UsageEvent, UsageWindow
from .serializers import UsageEventInputSerializer, UsageWindowSerializer


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


class UsageView(APIView):
    authentication_classes = [ApiKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        start, end = self.date_range(request)
        page = self.positive_int(request.query_params.get("page"), default=1, maximum=None)
        page_size = self.positive_int(request.query_params.get("page_size"), default=50, maximum=200)

        queryset = UsageWindow.objects.filter(
            customer=request.customer,
            window_start__gte=start,
            window_start__lt=end,
        )

        api_key_id = request.query_params.get("api_key_id")
        if api_key_id:
            if not ApiKey.objects.filter(id=api_key_id, customer=request.customer).exists():
                queryset = queryset.none()
            else:
                queryset = queryset.filter(api_key_id=api_key_id)

        queryset = queryset.order_by("-window_start")
        total = queryset.count()
        offset = (page - 1) * page_size
        results = queryset[offset : offset + page_size]

        return Response(
            {
                "results": UsageWindowSerializer(results, many=True).data,
                "page": page,
                "page_size": page_size,
                "total": total,
            }
        )

    def date_range(self, request):
        start_param = request.query_params.get("start")
        end_param = request.query_params.get("end")

        if start_param:
            start = self.parse_timestamp(start_param)
        else:
            now = timezone.now()
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        if end_param:
            end = self.parse_timestamp(end_param)
        else:
            end = timezone.now()

        return start, end

    def parse_timestamp(self, value):
        parsed = parse_datetime(value)
        if parsed is None:
            raise exceptions.ValidationError({"detail": f"Invalid timestamp: {value}"})
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed)
        return parsed

    def positive_int(self, value, default, maximum):
        if value is None:
            return default
        try:
            parsed = int(value)
        except ValueError:
            return default
        parsed = max(parsed, 1)
        if maximum is not None:
            parsed = min(parsed, maximum)
        return parsed
