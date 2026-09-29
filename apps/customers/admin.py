from django.contrib import admin

from .models import CustomerProfile, EmergencyContact, SavedPlace

admin.site.register([CustomerProfile, SavedPlace, EmergencyContact])
