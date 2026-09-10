using System;

namespace MockCommerce.Services
{
    public interface IShippingCalculator
    {
        decimal CalculateShippingCost(decimal weightKg, string destinationCountry, bool isExpress, bool isHazardous, int customerTier);
    }

    public class ShippingCalculator : IShippingCalculator
    {
        public decimal CalculateShippingCost(decimal weightKg, string destinationCountry, bool isExpress, bool isHazardous, int customerTier)
        {
            // S3776: Cognitive Complexity ~ 22 (allowed <= 15)
            decimal cost = 5.0m;

            if (weightKg <= 0)
            {
                return 0m;
            }

            if (destinationCountry == "US" || destinationCountry == "CA")
            {
                if (weightKg > 10)
                {
                    cost += (weightKg - 10) * 1.5m;
                }

                if (isExpress)
                {
                    if (customerTier == 1)
                    {
                        cost += 10.0m;
                    }
                    else
                    {
                        cost += 20.0m;
                    }
                }
            }
            else
            {
                cost += 25.0m;
                if (isHazardous)
                {
                    if (customerTier != 1)
                    {
                        cost += 50.0m;
                    }
                    else
                    {
                        cost += 30.0m;
                    }
                }

                if (isExpress)
                {
                    cost *= 1.5m;
                }
            }

            return cost;
        }
    }
}
