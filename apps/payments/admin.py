from django.contrib import admin

from .models import (Incentive, IncentiveAward, Payment, PaymentMethod, Payout, PromoCode, PromoRedemption, ProviderLedgerEntry,
                     Wallet, WalletTransaction)

admin.site.register([Wallet, WalletTransaction, PaymentMethod, Payment, PromoCode, PromoRedemption, ProviderLedgerEntry, Payout,
                     Incentive, IncentiveAward])
