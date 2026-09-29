"""Health check: lets the apps and load balancer confirm the API (and database) are up."""
from django.db import connection
from drf_spectacular.utils import extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


class HealthView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=["Auth"],
        summary="Health check",
        description="GET /api/v1/health/ · No auth. Returns 200 when the API and database respond.",
        responses={200: inline_serializer("Health", {"status": serializers.CharField(), "database": serializers.CharField()})},
    )
    def get(self, request):
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return Response({"status": "ok", "database": "ok"})
