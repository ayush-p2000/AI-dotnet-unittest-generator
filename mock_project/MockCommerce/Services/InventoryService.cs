using System;
using System.Collections.Generic;
using MockCommerce.Models;

namespace MockCommerce.Services
{
    public interface IInventoryService
    {
        bool CheckStock(OrderDto order);
    }

    public class InventoryService : IInventoryService
    {
        public bool CheckStock(OrderDto order)
        {
            if (order == null || order.Quantity <= 0)
            {
                return false;
            }
            return true;
        }
    }
}
