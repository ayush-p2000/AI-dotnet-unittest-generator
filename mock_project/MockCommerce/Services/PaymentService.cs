using System;
using System.Collections.Generic;

namespace MockCommerce.Services
{
    public interface IPaymentService
    {
        bool ProcessOrderPayment(decimal amount, string currency, string paymentMethod, bool isVip, bool hasLoyaltyCard, int riskScore);
    }

    public class PaymentService : IPaymentService
    {
public bool ProcessOrderPayment(
            decimal amount,
            string currency,
            string paymentMethod,
            bool isVip,
            bool hasLoyaltyCard,
            int riskScore)
        {
            if (amount <= 0 || !IsSupportedCurrency(currency))
            {
                return false;
            }

            return paymentMethod switch
            {
                "CreditCard" => IsValidCreditCardPayment(amount, isVip, hasLoyaltyCard, riskScore),
                "PayPal" => IsValidPayPalPayment(amount, riskScore),
                "Crypto" => IsValidCryptoPayment(isVip, riskScore),
                _ => false
            };
        }

        private static bool IsSupportedCurrency(string currency)
        {
            return currency is "USD" or "EUR";
        }

        private static bool IsValidCreditCardPayment(decimal amount, bool isVip, bool hasLoyaltyCard, int riskScore)
        {
            if (riskScore >= 50)
            {
                return false;
            }

            if (isVip)
            {
                return true;
            }

            if (hasLoyaltyCard)
            {
                return amount < 5000;
            }

            return amount < 1000;
        }

        private static bool IsValidPayPalPayment(decimal amount, int riskScore)
        {
            return riskScore < 40 && amount < 3000;
        }

        private static bool IsValidCryptoPayment(bool isVip, int riskScore)
        {
            return isVip && riskScore < 20;
        }
    }
}
