using System;

namespace MockCommerce.Services
{
    public interface IDiscountEngine
    {
        decimal CalculateDiscount(decimal totalAmount, string promoCode, int customerTier, bool isFirstPurchase, int totalPreviousOrders);
    }

    public class DiscountEngine : IDiscountEngine
    {
        public decimal CalculateDiscount(decimal totalAmount, string promoCode, int customerTier, bool isFirstPurchase, int totalPreviousOrders)
        {
            // S3776: Cognitive Complexity ~ 20 (allowed <= 15)
            decimal discount = 0m;

            if (totalAmount <= 0)
            {
                return 0m;
            }

            if (!string.IsNullOrEmpty(promoCode))
            {
                if (promoCode == "SUMMER20")
                {
                    if (totalAmount >= 100)
                    {
                        discount += totalAmount * 0.20m;
                    }
                    else
                    {
                        discount += 10m;
                    }
                }
                else if (promoCode == "VIP50")
                {
                    if (customerTier == 1)
                    {
                        discount += totalAmount * 0.50m;
                    }
                    else
                    {
                        discount += totalAmount * 0.10m;
                    }
                }
            }
            else
            {
                if (isFirstPurchase)
                {
                    discount += 15m;
                }
                else if (totalPreviousOrders > 10)
                {
                    if (customerTier <= 2)
                    {
                        discount += totalAmount * 0.15m;
                    }
                }
            }

            if (discount > totalAmount * 0.60m)
            {
                discount = totalAmount * 0.60m;
            }

            return discount;
        }
    }
}
