from django.contrib import admin

from .models import FareQuote, FareRule, RideType, ServiceZone

admin.site.register([RideType, FareRule, ServiceZone, FareQuote])
